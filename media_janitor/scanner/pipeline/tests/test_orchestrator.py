import os
import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest
from django.db import connection, connections

from scanner.clients.base import (
    ClientSnapshot,
    DownloadClient,
    TorrentFile,
    TorrentSnapshot,
    TorrentState,
)
from scanner.models import Blob, BlobTorrent, Link, Scan, Torrent
from scanner.pipeline.lock import SCAN_LOCK_KEY
from scanner.pipeline.orchestrator import RETAIN_SCANS, _prune_scans, run_scan

pytestmark = pytest.mark.django_db(transaction=True)


FOO_CONTENT = b"foo-content-payload"
LOOSE_CONTENT = b"loose-orphan-file"


class FakeClient(DownloadClient):
    """A DownloadClient that returns a canned snapshot or raises"""

    def __init__(self, snapshot: ClientSnapshot | None = None, *, error: Exception | None = None):
        self._snapshot = snapshot
        self._error = error
        self.calls = 0

    async def gather(self) -> ClientSnapshot:
        self.calls += 1
        if self._error is not None:
            raise self._error
        assert self._snapshot is not None
        return self._snapshot


def _build_share(tmp_path: Path) -> Path:
    """
    Build a real share tree under tmp_path and return the share root

    Layout:
        media/movies/        (library root, required to exist)
        media/tv/            (library root, required to exist)
        torrents/Foo/foo.mkv (hard-linked into the library: in_library blob)
        media/movies/Foo (2020)/foo.mkv  (the library hardlink)
        loose/orphan.mkv     (loose, untracked: reclaimable)
    """
    share = tmp_path / "share"

    (share / "media" / "tv").mkdir(parents=True)

    foo_torrent = share / "torrents" / "Foo"
    foo_torrent.mkdir(parents=True)
    foo_src = foo_torrent / "foo.mkv"
    foo_src.write_bytes(FOO_CONTENT)

    foo_lib_dir = share / "media" / "movies" / "Foo (2020)"
    foo_lib_dir.mkdir(parents=True)
    os.link(foo_src, foo_lib_dir / "foo.mkv")

    loose_dir = share / "loose"
    loose_dir.mkdir(parents=True)
    (loose_dir / "orphan.mkv").write_bytes(LOOSE_CONTENT)

    # Age every file well outside the quarantine window so freshly written files
    # are not held as in_progress.
    old = time.time() - 7 * 24 * 3600
    for path in share.rglob("*"):
        if path.is_file():
            os.utime(path, (old, old))

    return share


def _foo_torrent_snapshot() -> TorrentSnapshot:
    """A seeding torrent owning the foo.mkv content via its torrents-tree path"""
    return TorrentSnapshot(
        hash="abc123",
        state=TorrentState.SEEDING,
        raw_state="uploading",
        ratio=10.0,
        completed_on=None,
        seeding_time=None,
        content_path="torrents/Foo",
        save_path="torrents",
        files=[TorrentFile(index=0, path="torrents/Foo/foo.mkv", size=len(FOO_CONTENT))],
    )


def _snapshot() -> ClientSnapshot:
    return ClientSnapshot(server_version="5.2.0", torrents=[_foo_torrent_snapshot()])


def test_publishes_complete_snapshot_with_reclaimable_totals(tmp_path):
    share = _build_share(tmp_path)
    client = FakeClient(_snapshot())

    scan = run_scan(share_root=share, client=client)

    assert scan is not None
    assert scan.status == Scan.Status.COMPLETE
    assert scan.qbittorrent_version == "5.2.0"
    assert client.calls == 1

    # foo.mkv is hard-linked into the library and torrent-tracked: in_library, kept.
    # orphan.mkv is loose and untracked: reclaimable.
    assert scan.summary_totals["reclaimable_bytes"] == len(LOOSE_CONTENT)

    # Two unique inodes -> two blobs (foo.mkv collapsed across its two links).
    assert Blob.objects.filter(scan=scan).count() == 2
    # Three links total: foo.mkv x2 (torrent + library) and the loose orphan.
    assert Link.objects.filter(scan=scan).count() == 3
    assert Torrent.objects.filter(scan=scan).count() == 1
    assert BlobTorrent.objects.filter(scan=scan).count() == 1

    foo_blob = Blob.objects.get(scan=scan, status=Blob.Status.IN_LIBRARY)
    assert foo_blob.torrent_tracked is True
    assert foo_blob.links_found == 2

    orphan_blob = Blob.objects.get(scan=scan, status=Blob.Status.RECLAIMABLE)
    assert orphan_blob.size == len(LOOSE_CONTENT)
    assert orphan_blob.torrent_tracked is False


def test_qbit_outage_marks_failed_and_keeps_prior_complete(tmp_path):
    share = _build_share(tmp_path)

    # First, publish a healthy complete snapshot.
    good = run_scan(share_root=share, client=FakeClient(_snapshot()))
    assert good is not None
    assert good.status == Scan.Status.COMPLETE
    prior_blob_ids = set(Blob.objects.values_list("pk", flat=True))

    # Now a scan where the client raises (outage).
    failing = run_scan(
        share_root=share,
        client=FakeClient(error=RuntimeError("qbit unreachable")),
    )
    assert failing is not None
    assert failing.status == Scan.Status.FAILED

    # No new blob rows were committed for the failed scan.
    assert Blob.objects.filter(scan=failing).count() == 0
    assert set(Blob.objects.values_list("pk", flat=True)) == prior_blob_ids

    # The previously published complete scan is still the latest complete one.
    latest_complete = Scan.objects.filter(status=Scan.Status.COMPLETE).order_by("-as_of").first()
    assert latest_complete is not None
    assert latest_complete.pk == good.pk


def test_missing_root_marks_failed_without_walking(tmp_path):
    share = _build_share(tmp_path)
    # Remove a configured library root so the precondition guard trips.
    (share / "media" / "tv").rmdir()

    client = FakeClient(_snapshot())
    scan = run_scan(share_root=share, client=client)

    assert scan is not None
    assert scan.status == Scan.Status.FAILED
    # The guard short-circuits before any gather or row creation.
    assert client.calls == 0
    assert Blob.objects.filter(scan=scan).count() == 0


def test_prune_retains_window_and_latest_complete(tmp_path):
    share = _build_share(tmp_path)

    # One complete scan, then enough failed scans to push it out of the window.
    # Pruning runs only after a successful publish, so a final complete scan
    # triggers the prune over the now-large backlog.
    complete = run_scan(share_root=share, client=FakeClient(_snapshot()))
    assert complete is not None

    for _ in range(RETAIN_SCANS + 2):
        run_scan(share_root=share, client=FakeClient(error=RuntimeError("boom")))

    final = run_scan(share_root=share, client=FakeClient(_snapshot()))
    assert final is not None
    assert final.status == Scan.Status.COMPLETE

    # The latest complete scan is retained, and the backlog is pruned to the
    # window. The earlier complete scan is now well outside the window and is
    # not the latest complete, so it is allowed to be pruned.
    assert Scan.objects.filter(pk=final.pk).exists()
    assert not Scan.objects.filter(pk=complete.pk).exists()
    assert Scan.objects.count() == RETAIN_SCANS


def test_lock_held_by_other_session_coalesces_to_noop(tmp_path):
    # Hold the single-scan advisory lock from another Postgres session, then
    # confirm run_scan coalesces to a no-op: it returns None and creates no Scan.
    share = _build_share(tmp_path)
    client = FakeClient(_snapshot())

    holder_ready = threading.Event()
    release = threading.Event()
    holder_result: dict[str, bool] = {}

    def holder() -> None:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(%s)", [SCAN_LOCK_KEY])
                holder_result["acquired"] = bool(cursor.fetchone()[0])
                holder_ready.set()
                release.wait(timeout=5)
                cursor.execute("SELECT pg_advisory_unlock(%s)", [SCAN_LOCK_KEY])
        finally:
            connections.close_all()

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        holder_ready.wait(timeout=5)
        assert holder_result["acquired"] is True

        before = Scan.objects.count()
        scan = run_scan(share_root=share, client=client)

        # The lock was held elsewhere, so the run is a no-op: no return value, no
        # new Scan row, and the no-op short-circuits before any gather.
        assert scan is None
        assert Scan.objects.count() == before
        assert client.calls == 0
    finally:
        release.set()
        thread.join()


def test_prune_protects_latest_complete_outside_window(tmp_path):
    share = _build_share(tmp_path)

    # One complete scan, followed by enough strictly-later failed scans to crowd
    # it out of the retention window.
    complete = run_scan(share_root=share, client=FakeClient(_snapshot()))
    assert complete is not None

    for i in range(1, RETAIN_SCANS + 6):
        failed = run_scan(share_root=share, client=FakeClient(error=RuntimeError("boom")))
        assert failed is not None
        # Force a strictly increasing as_of so the failed scans clearly sort after
        # the complete one and push it past RETAIN_SCANS.
        Scan.objects.filter(pk=failed.pk).update(as_of=complete.as_of + timedelta(seconds=i))

    # Failed scans never prune, so invoke the prune directly.
    _prune_scans()

    # The lone complete scan is the latest complete one, so it is retained even
    # though RETAIN_SCANS newer failed scans would otherwise crowd it out.
    assert Scan.objects.filter(pk=complete.pk).exists()
