from datetime import UTC, datetime, timedelta

import pytest

from scanner.clients.base import TorrentFile, TorrentSnapshot, TorrentState
from scanner.models import Blob, Kind, Tree
from scanner.pipeline.build import (
    ORPHANED_SIDECAR_REASON,
    BlobDraft,
    build_scan_model,
)
from scanner.pipeline.seeding import SeedingReqs
from scanner.pipeline.walk import FileRecord

REQS = SeedingReqs(min_days=14, min_ratio=2.0)
NOW = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
QUARANTINE = timedelta(minutes=30)
LIBRARY_ROOTS = ["media/movies", "media/tv"]
TORRENT_ROOTS = ["torrents"]

# An mtime safely outside the quarantine window for default records.
OLD_MTIME = NOW - timedelta(days=1)

_INO = iter(range(1000, 100000))


def rec(
    rel: str,
    *,
    size: int = 100,
    st_dev: int = 1,
    st_ino: int | None = None,
    nlink: int = 1,
    mtime: datetime = OLD_MTIME,
) -> FileRecord:
    """Build a FileRecord, auto-assigning a unique inode when not given"""
    if st_ino is None:
        st_ino = next(_INO)
    return FileRecord(rel=rel, size=size, st_dev=st_dev, st_ino=st_ino, nlink=nlink, mtime=mtime)


def torrent(
    files: list[TorrentFile],
    *,
    hash: str = "h",
    state: TorrentState = TorrentState.SEEDING,
    raw_state: str = "uploading",
    ratio: float = 0.0,
    completed_on: datetime | None = NOW - timedelta(days=1),
    seeding_time: timedelta | None = timedelta(days=1),
    content_path: str = "torrents/x",
    save_path: str = "torrents",
) -> TorrentSnapshot:
    """Build a TorrentSnapshot"""
    return TorrentSnapshot(
        hash=hash,
        state=state,
        raw_state=raw_state,
        ratio=ratio,
        completed_on=completed_on,
        seeding_time=seeding_time,
        content_path=content_path,
        save_path=save_path,
        files=files,
    )


def run(records, torrents=()):
    """Run build_scan_model with the standard fixtures"""
    return build_scan_model(
        list(records),
        list(torrents),
        REQS,
        QUARANTINE,
        library_roots=LIBRARY_ROOTS,
        torrent_roots=TORRENT_ROOTS,
        now=NOW,
    )


def blob_by_ino(result, st_ino) -> BlobDraft:
    return next(b for b in result.blobs if b.st_ino == st_ino)


# --- Dedupe + link assembly --------------------------------------------------


def test_dedupe_hardlinks_into_one_blob():
    result = run(
        [
            rec("media/movies/a.mkv", st_ino=5, nlink=2),
            rec("torrents/a.mkv", st_ino=5, nlink=2),
        ]
    )
    assert len(result.blobs) == 1
    blob = result.blobs[0]
    assert blob.links_found == 2
    assert {link.path for link in blob.links} == {"media/movies/a.mkv", "torrents/a.mkv"}
    assert blob.trees == sorted([Tree.LIBRARY.value, Tree.TORRENTS.value])
    assert blob.kind is Kind.MEDIA


def test_link_fields_populated():
    result = run([rec("media/tv/show/ep.srt", st_ino=7)])
    link = result.blobs[0].links[0]
    assert link.path == "media/tv/show/ep.srt"
    assert link.name == "ep.srt"
    assert link.kind is Kind.SIDECAR
    assert link.tree is Tree.LIBRARY


def test_blob_kind_prefers_media_then_sidecar_then_other():
    # MEDIA wins when any link is media (this would never really happen)
    media = run(
        [
            rec("media/movies/m.mkv", st_ino=10, nlink=2),
            rec("media/movies/m.txt", st_ino=10, nlink=2),
        ]
    ).blobs[0]
    assert media.kind is Kind.MEDIA
    # SIDECAR when no media but a sidecar present
    sidecar = run([rec("media/movies/s.srt", st_ino=11)]).blobs[0]
    assert sidecar.kind is Kind.SIDECAR
    # OTHER otherwise
    other = run([rec("media/movies/readme.txt", st_ino=12)]).blobs[0]
    assert other.kind is Kind.OTHER


# --- Loose file --------------------------------------------------------------


def test_loose_file_reclaimable_untracked():
    result = run([rec("random/loose.mkv", st_ino=20)])
    blob = result.blobs[0]
    assert blob.trees == [Tree.LOOSE.value]
    assert blob.torrent_tracked is False
    assert blob.seeding_met is None
    assert blob.status is Blob.Status.RECLAIMABLE


# --- in_library --------------------------------------------------------------


def test_in_library_blob():
    result = run([rec("media/movies/keep.mkv", st_ino=30)])
    assert result.blobs[0].status is Blob.Status.IN_LIBRARY


# --- in_progress -------------------------------------------------------------


def test_in_progress_active_torrent():
    f = TorrentFile(index=0, path="torrents/dl.mkv", size=100)
    result = run(
        [rec("torrents/dl.mkv", st_ino=40)],
        [torrent([f], state=TorrentState.IN_FLIGHT, raw_state="downloading")],
    )
    blob = result.blobs[0]
    assert blob.status is Blob.Status.IN_PROGRESS
    assert blob.torrent_tracked is True


def test_in_progress_quarantine():
    result = run([rec("torrents/fresh.mkv", st_ino=41, mtime=NOW - timedelta(minutes=5))])
    assert result.blobs[0].status is Blob.Status.IN_PROGRESS


# --- seeding_hold ------------------------------------------------------------


def test_seeding_hold_tracked_not_met():
    f = TorrentFile(index=0, path="torrents/hold.mkv", size=100)
    t = torrent([f], ratio=0.5, completed_on=NOW - timedelta(days=1))
    result = run([rec("torrents/hold.mkv", st_ino=50)], [t])
    blob = result.blobs[0]
    assert blob.status is Blob.Status.SEEDING_HOLD
    assert blob.seeding_met is False
    assert blob.latest_seeding_start == NOW - timedelta(days=1)
    assert blob.seeding_end == NOW - timedelta(days=1) + timedelta(days=14)


def test_reclaimable_tracked_seeding_met_by_ratio():
    f = TorrentFile(index=0, path="torrents/done.mkv", size=100)
    t = torrent([f], ratio=5.0, completed_on=NOW - timedelta(days=1))
    result = run([rec("torrents/done.mkv", st_ino=51)], [t])
    blob = result.blobs[0]
    assert blob.seeding_met is True
    assert blob.status is Blob.Status.RECLAIMABLE


# --- Torrent persistence -----------------------------------------------------


def test_torrent_data_persists_raw_state_and_seeding():
    f = TorrentFile(index=3, path="torrents/t.mkv", size=100)
    t = torrent([f], hash="abc", raw_state="stalledUP", ratio=5.0)
    result = run([rec("torrents/t.mkv", st_ino=60)], [t])
    assert len(result.torrents) == 1
    td = result.torrents[0]
    assert td.hash == "abc"
    assert td.state == "stalledUP"
    assert td.seeding_met is True
    # Single reclaimable, last-link blob: removing the torrent frees its bytes.
    assert td.bytes_reclaimable_if_removed == 100
    # blob_torrent carries the file index and object references
    assert len(result.blob_torrents) == 1
    bt = result.blob_torrents[0]
    assert bt.file_index == 3
    assert bt.torrent is td
    assert bt.blob is result.blobs[0]


def test_torrent_file_with_no_matching_blob_is_skipped():
    f = TorrentFile(index=0, path="torrents/missing.mkv", size=100)
    result = run([rec("media/movies/other.mkv", st_ino=61)], [torrent([f])])
    assert result.blob_torrents == []
    assert result.blobs[0].torrent_tracked is False


# --- cross_seed --------------------------------------------------------------


def test_cross_seed_two_torrents_one_blob():
    # Two torrents whose files resolve (via path) to the same hardlinked blob
    f1 = TorrentFile(index=0, path="torrents/clientA/movie.mkv", size=100)
    f2 = TorrentFile(index=0, path="torrents/clientB/movie.mkv", size=100)
    records = [
        rec("torrents/clientA/movie.mkv", st_ino=70, nlink=2),
        rec("torrents/clientB/movie.mkv", st_ino=70, nlink=2),
    ]
    t1 = torrent([f1], hash="A", ratio=5.0)
    t2 = torrent([f2], hash="B", ratio=5.0)
    result = run(records, [t1, t2])
    assert len(result.blobs) == 1
    blob = result.blobs[0]
    assert blob.cross_seed is True
    assert blob.torrent_tracked is True
    assert len(result.blob_torrents) == 2


def test_single_torrent_two_hardlinked_files_not_cross_seed():
    # One torrent lists two file entries that are hardlinks of the same inode
    # (eg after a hardlink dedupe tool collapsed identical files). The walk
    # dedupes them into a single blob with two links, both owned by the same
    # torrent. cross_seed means "served by more than one torrent", so a single
    # owner must not be counted twice.
    f1 = TorrentFile(index=0, path="torrents/pack/a.mkv", size=100)
    f2 = TorrentFile(index=1, path="torrents/pack/b.mkv", size=100)
    records = [
        rec("torrents/pack/a.mkv", st_ino=73, nlink=2),
        rec("torrents/pack/b.mkv", st_ino=73, nlink=2),
    ]
    t = torrent([f1, f2], hash="A", ratio=5.0)
    result = run(records, [t])
    assert len(result.blobs) == 1
    blob = result.blobs[0]
    assert blob.cross_seed is False
    assert blob.torrent_tracked is True
    # Both file entries still record a blob-torrent link (one per file index)
    assert len(result.blob_torrents) == 2


def test_cross_seed_seeding_met_is_and_across_owners():
    # One torrent met, one not -> blob seeding_met False
    f1 = TorrentFile(index=0, path="torrents/a/x.mkv", size=100)
    f2 = TorrentFile(index=0, path="torrents/b/x.mkv", size=100)
    records = [
        rec("torrents/a/x.mkv", st_ino=71, nlink=2),
        rec("torrents/b/x.mkv", st_ino=71, nlink=2),
    ]
    t_met = torrent([f1], hash="A", ratio=5.0, completed_on=NOW - timedelta(days=2))
    t_unmet = torrent([f2], hash="B", ratio=0.1, completed_on=NOW - timedelta(days=1))
    result = run(records, [t_met, t_unmet])
    blob = result.blobs[0]
    assert blob.seeding_met is False
    # latest_seeding_start is the max of the owners' completed_on
    assert blob.latest_seeding_start == NOW - timedelta(days=1)
    assert blob.status is Blob.Status.SEEDING_HOLD


def test_latest_seeding_start_none_when_all_owners_none():
    f1 = TorrentFile(index=0, path="torrents/a/y.mkv", size=100)
    f2 = TorrentFile(index=0, path="torrents/b/y.mkv", size=100)
    records = [
        rec("torrents/a/y.mkv", st_ino=72, nlink=2),
        rec("torrents/b/y.mkv", st_ino=72, nlink=2),
    ]
    t1 = torrent([f1], hash="A", ratio=5.0, completed_on=None)
    t2 = torrent([f2], hash="B", ratio=5.0, completed_on=None)
    result = run(records, [t1, t2])
    blob = result.blobs[0]
    assert blob.latest_seeding_start is None
    assert blob.seeding_end is None
    assert blob.seeding_met is True


# --- multi_link --------------------------------------------------------------


def test_multi_link_two_hardlinks_same_tree():
    result = run(
        [
            rec("media/movies/dir1/m.mkv", st_ino=80, nlink=2),
            rec("media/movies/dir2/m.mkv", st_ino=80, nlink=2),
        ]
    )
    blob = result.blobs[0]
    assert blob.multi_link is True
    assert blob.links_found == 2


# --- links_outside_scope -----------------------------------------------------


def test_links_outside_scope_excluded_from_reclaim():
    # nlink=3 but only 1 link found -> outside scope. The blob would otherwise be
    # reclaimable but is overridden to linked_externally and excluded from the
    # reclaim total.
    result = run([rec("random/orphan.mkv", st_ino=90, nlink=3)])
    blob = result.blobs[0]
    assert blob.links_outside_scope is True
    assert blob.status is Blob.Status.LINKED_EXTERNALLY
    assert result.summary_totals["reclaimable_bytes"] == 0
    # No reclaimable blob remains, and it lands in the linked_externally bucket.
    assert result.summary_totals["by_status"][Blob.Status.RECLAIMABLE.value]["count"] == 0
    assert result.summary_totals["by_status"][Blob.Status.LINKED_EXTERNALLY.value]["count"] == 1


def test_reclaimable_within_scope_counted():
    result = run([rec("random/loose.mkv", st_ino=91, size=500, nlink=1)])
    assert result.summary_totals["reclaimable_bytes"] == 500


@pytest.mark.parametrize(
    "nlink,found,expected",
    [
        (1, 1, False),
        (2, 1, True),
        (3, 3, False),
    ],
)
def test_links_outside_scope_flag(nlink, found, expected):
    # found is controlled by how many records share the inode
    records = [rec(f"random/f{i}.bin", st_ino=190, nlink=nlink) for i in range(found)]
    result = run(records)
    assert result.blobs[0].links_outside_scope is expected


# --- linked_externally -------------------------------------------------------


def test_linked_externally_tracked_seeding_met_outside_scope():
    # Torrent-tracked, seeding met, no library link: would be reclaimable, but a
    # link lives outside the scanned scope (nlink > links_found), so it is
    # overridden to linked_externally.
    f = TorrentFile(index=0, path="torrents/ext.mkv", size=100)
    t = torrent([f], hash="ext", ratio=5.0)
    result = run([rec("torrents/ext.mkv", st_ino=400, nlink=2)], [t])
    blob = blob_by_ino(result, 400)
    assert blob.seeding_met is True
    assert blob.links_outside_scope is True
    assert blob.status is Blob.Status.LINKED_EXTERNALLY


def test_linked_externally_untracked_outside_scope():
    # Untracked loose blob that would be reclaimable, but has links outside scope
    result = run([rec("random/loose.mkv", st_ino=401, nlink=2)])
    blob = blob_by_ino(result, 401)
    assert blob.torrent_tracked is False
    assert blob.links_outside_scope is True
    assert blob.status is Blob.Status.LINKED_EXTERNALLY


def test_reclaimable_when_all_links_in_scope():
    # The same shape with all links in scope (nlink == links_found) stays
    # reclaimable: the override only fires for outside-scope blobs.
    f = TorrentFile(index=0, path="torrents/in.mkv", size=100)
    t = torrent([f], hash="in", ratio=5.0)
    result = run([rec("torrents/in.mkv", st_ino=402, nlink=1)], [t])
    blob = blob_by_ino(result, 402)
    assert blob.links_outside_scope is False
    assert blob.status is Blob.Status.RECLAIMABLE


def test_no_reclaimable_blob_has_links_outside_scope():
    # A reclaimable blob never has outside-scope links
    records = [
        rec("random/a.mkv", st_ino=403, nlink=1),
        rec("random/b.mkv", st_ino=404, nlink=2),
        rec("media/movies/keep.mkv", st_ino=405, nlink=2),
    ]
    result = run(records)
    for blob in result.blobs:
        if blob.status is Blob.Status.RECLAIMABLE:
            assert blob.links_outside_scope is False


def test_partial_torrent_reclaimable_and_linked_externally_mix():
    # A torrent owns one reclaimable blob and one outside-scope blob (now
    # linked_externally). The two statuses differ, so the torrent is partial.
    f_recl = TorrentFile(index=0, path="torrents/pack/in.mkv", size=100)
    f_ext = TorrentFile(index=1, path="torrents/pack/out.mkv", size=100)
    records = [
        rec("torrents/pack/in.mkv", st_ino=410, nlink=1),
        rec("torrents/pack/out.mkv", st_ino=411, nlink=2),
    ]
    t = torrent([f_recl, f_ext], hash="pack", ratio=5.0)
    result = run(records, [t])
    in_scope = blob_by_ino(result, 410)
    ext = blob_by_ino(result, 411)
    assert in_scope.status is Blob.Status.RECLAIMABLE
    assert ext.status is Blob.Status.LINKED_EXTERNALLY
    assert in_scope.partial_torrent is True
    assert ext.partial_torrent is True
    assert result.torrents[0].partial_torrent is True


def test_linked_externally_override_is_per_blob_for_sidecars():
    # The override is based on a blob's own links_outside_scope. A reclaimable media
    # blob with a link outside scope becomes linked_externally, while a colocated
    # sidecar whose own links are fully in scope inherits the media's pre-override
    # reclaimable status and stays reclaimable.
    records = [
        rec("loose/dir/film.mkv", st_ino=420, nlink=2),
        rec("loose/dir/film.srt", st_ino=421, nlink=1),
    ]
    result = run(records)
    media = blob_by_ino(result, 420)
    sidecar = blob_by_ino(result, 421)
    assert media.kind is Kind.MEDIA
    assert media.links_outside_scope is True
    assert media.status is Blob.Status.LINKED_EXTERNALLY
    assert sidecar.kind is Kind.SIDECAR
    assert sidecar.links_outside_scope is False
    assert sidecar.status is Blob.Status.RECLAIMABLE
    assert sidecar.orphan_reason == ""


# --- partial_torrent ---------------------------------------------------------


def test_partial_torrent_season_pack_mixed_status():
    # A season pack: one episode in the library (kept), one only in torrents and
    # seeding-met (reclaimable) -> mixed statuses -> partial.
    f_keep = TorrentFile(index=0, path="torrents/pack/ep1.mkv", size=100)
    f_recl = TorrentFile(index=1, path="torrents/pack/ep2.mkv", size=100)
    records = [
        # ep1 is hardlinked into the library, so it stays.
        rec("torrents/pack/ep1.mkv", st_ino=100, nlink=2),
        rec("media/tv/show/ep1.mkv", st_ino=100, nlink=2),
        # ep2 only in torrents, seeding met -> reclaimable.
        rec("torrents/pack/ep2.mkv", st_ino=101, nlink=1),
    ]
    t = torrent([f_keep, f_recl], hash="pack", ratio=5.0)
    result = run(records, [t])
    ep1 = blob_by_ino(result, 100)
    ep2 = blob_by_ino(result, 101)
    assert ep1.status is Blob.Status.IN_LIBRARY
    assert ep2.status is Blob.Status.RECLAIMABLE
    assert ep1.partial_torrent is True
    assert ep2.partial_torrent is True
    assert result.torrents[0].partial_torrent is True


def test_not_partial_when_all_blobs_same_status():
    f1 = TorrentFile(index=0, path="torrents/pack/a.mkv", size=100)
    f2 = TorrentFile(index=1, path="torrents/pack/b.mkv", size=100)
    records = [
        rec("torrents/pack/a.mkv", st_ino=110),
        rec("torrents/pack/b.mkv", st_ino=111),
    ]
    t = torrent([f1, f2], hash="pack", ratio=5.0)
    result = run(records, [t])
    assert all(not b.partial_torrent for b in result.blobs)
    assert result.torrents[0].partial_torrent is False


def test_release_torrent_with_sample_subfolder_and_sidecar():
    # A typical scene release torrent: the main video is hardlinked into the
    # library, colocated .nfo and .srr sidecars ride along at the root, and a
    # Sample/ subfolder holds a sample video plus a couple of thumbnail images.
    # None of the Sample/ files are hardlinked into the library: the sample is an
    # ordinary MEDIA file and the thumbnails are OTHER, so the whole subfolder
    # stays reclaimable while the main video and its sidecars are kept. Seeding is
    # met via the time arm (long-seeded, low ratio), and the mix of kept and
    # reclaimable files makes the torrent partial.
    root = "torrents/tv/release"
    files = [
        TorrentFile(index=0, path=f"{root}/Sample/release-sample.mkv", size=121),
        TorrentFile(index=1, path=f"{root}/release.mkv", size=2450),
        TorrentFile(index=2, path=f"{root}/release.nfo", size=1),
        TorrentFile(index=3, path=f"{root}/release.srr", size=3),
        TorrentFile(index=4, path=f"{root}/Sample/thumb-01.jpg", size=10),
        TorrentFile(index=5, path=f"{root}/Sample/thumb-02.png", size=10),
    ]
    t = torrent(
        files,
        hash="release",
        raw_state="stalledUP",
        ratio=0.1,
        completed_on=NOW - timedelta(days=20),
        seeding_time=timedelta(days=20),
    )
    records = [
        # Main video, hardlinked into the library
        rec(f"{root}/release.mkv", st_ino=200, size=2450, nlink=2),
        rec("media/tv/show/episode.mkv", st_ino=200, size=2450, nlink=2),
        # Colocated sidecars at the torrent root
        rec(f"{root}/release.nfo", st_ino=201, size=1, nlink=1),
        rec(f"{root}/release.srr", st_ino=202, size=3, nlink=1),
        # Sample subfolder: a sample video plus thumbnail images, torrents-only
        rec(f"{root}/Sample/release-sample.mkv", st_ino=203, size=121, nlink=1),
        rec(f"{root}/Sample/thumb-01.jpg", st_ino=204, size=10, nlink=1),
        rec(f"{root}/Sample/thumb-02.png", st_ino=205, size=10, nlink=1),
    ]
    result = run(records, [t])

    main = blob_by_ino(result, 200)
    nfo = blob_by_ino(result, 201)
    srr = blob_by_ino(result, 202)
    sample = blob_by_ino(result, 203)
    thumbs = [blob_by_ino(result, 204), blob_by_ino(result, 205)]

    # The main video is kept (library hardlink wins)
    assert main.kind is Kind.MEDIA
    assert main.status is Blob.Status.IN_LIBRARY
    # The .nfo and .srr sidecars bind to the colocated main video and are kept
    assert nfo.kind is Kind.SIDECAR
    assert nfo.status is Blob.Status.IN_LIBRARY
    assert nfo.orphan_reason == ""
    assert srr.kind is Kind.SIDECAR
    assert srr.status is Blob.Status.IN_LIBRARY
    assert srr.orphan_reason == ""
    # The sample is just a MEDIA file with no library link -> reclaimable, even
    # though it is part of an actively seeding torrent (seeding is met)
    assert sample.kind is Kind.MEDIA
    assert sample.trees == [Tree.TORRENTS.value]
    assert sample.torrent_tracked is True
    assert sample.seeding_met is True
    assert sample.status is Blob.Status.RECLAIMABLE
    # The thumbnails are OTHER files (not sidecars), so they do not bind to the
    # sample; they are torrents-only and reclaimable in their own right
    for thumb in thumbs:
        assert thumb.kind is Kind.OTHER
        assert thumb.trees == [Tree.TORRENTS.value]
        assert thumb.status is Blob.Status.RECLAIMABLE
        assert thumb.orphan_reason == ""

    # Mixed statuses across the torrent -> every owned blob and the torrent are
    # flagged partial
    assert result.torrents[0].partial_torrent is True
    assert all(b.partial_torrent for b in (main, nfo, srr, sample, *thumbs))

    # The whole Sample/ subfolder frees space (sample + two thumbnails); the kept
    # main and its bound sidecars do not
    assert result.summary_totals["reclaimable_bytes"] == 121 + 10 + 10


# --- Sidecar binding ---------------------------------------------------------


def test_bound_sidecar_follows_media_status():
    # Sidecar colocated with an in_library media blob inherits in_library
    records = [
        rec("media/movies/film/film.mkv", st_ino=120),
        rec("media/movies/film/film.srt", st_ino=121),
    ]
    result = run(records)
    media = blob_by_ino(result, 120)
    sidecar = blob_by_ino(result, 121)
    assert media.status is Blob.Status.IN_LIBRARY
    assert sidecar.kind is Kind.SIDECAR
    assert sidecar.status is Blob.Status.IN_LIBRARY
    assert sidecar.orphan_reason == ""


def test_orphaned_sidecar_reclaimable():
    result = run([rec("media/movies/lonely/sub.srt", st_ino=130)])
    blob = result.blobs[0]
    assert blob.kind is Kind.SIDECAR
    assert blob.status is Blob.Status.RECLAIMABLE
    assert blob.orphan_reason == ORPHANED_SIDECAR_REASON


def test_sidecar_not_colocated_is_orphaned():
    # Media and sidecar in different directories -> sidecar orphaned
    records = [
        rec("media/movies/film/film.mkv", st_ino=140),
        rec("media/movies/subs/film.srt", st_ino=141),
    ]
    result = run(records)
    sidecar = blob_by_ino(result, 141)
    assert sidecar.status is Blob.Status.RECLAIMABLE
    assert sidecar.orphan_reason == ORPHANED_SIDECAR_REASON


def test_sidecar_prefers_kept_status_when_mixed():
    # Sidecar's directory (loose, so media blobs are reclaimable unless kept)
    # contains two media blobs: one kept via a library hardlink, one reclaimable.
    # Prefer the kept status so we do not reclaim the sidecar.
    records = [
        # kept media: a loose link in the sidecar's dir hardlinked into library
        rec("loose/dir/keep.mkv", st_ino=150, nlink=2),
        rec("media/movies/keep.mkv", st_ino=150, nlink=2),
        # reclaimable media: loose only, untracked, no library link
        rec("loose/dir/old.mkv", st_ino=151, nlink=1),
        # the sidecar in the same loose directory
        rec("loose/dir/sub.srt", st_ino=152),
    ]
    result = run(records)
    keep = blob_by_ino(result, 150)
    old = blob_by_ino(result, 151)
    sidecar = blob_by_ino(result, 152)
    assert keep.status is Blob.Status.IN_LIBRARY
    assert old.status is Blob.Status.RECLAIMABLE
    # Mixed colocated media; sidecar follows the kept one
    assert sidecar.status is Blob.Status.IN_LIBRARY
    assert sidecar.orphan_reason == ""


def test_sidecar_prefers_seeding_hold_over_reclaimable_when_mixed():
    # Sidecar colocated with two media blobs: one reclaimable (listed first), one
    # held for seeding. SEEDING_HOLD is kept on disk, so the sidecar must follow
    # it rather than be reclaimed alongside the reclaimable sibling.
    f = TorrentFile(index=0, path="loose/d/hold.mkv", size=100)
    t = torrent([f], hash="hold", ratio=0.1, completed_on=NOW - timedelta(days=1))
    records = [
        # reclaimable media (loose, untracked); listed first so order alone would
        # pick it without the kept-status preference.
        rec("loose/d/old.mkv", st_ino=155, nlink=1),
        # seeding-hold media (tracked, not met)
        rec("loose/d/hold.mkv", st_ino=156, nlink=1),
        # the sidecar in the same directory
        rec("loose/d/sub.srt", st_ino=157),
    ]
    result = run(records, [t])
    old = blob_by_ino(result, 155)
    hold = blob_by_ino(result, 156)
    sidecar = blob_by_ino(result, 157)
    assert old.status is Blob.Status.RECLAIMABLE
    assert hold.status is Blob.Status.SEEDING_HOLD
    assert sidecar.status is Blob.Status.SEEDING_HOLD
    assert sidecar.orphan_reason == ""


def test_self_held_sidecar_not_reclaimed_by_colocated_reclaimable_media():
    # A sidecar is itself owned by a torrent that has not met seeding, so deleting
    # it would harm its own still-seeding torrent. It is colocated only with a
    # reclaimable media blob owned by a different torrent. Binding must not discard
    # the sidecar's own seeding hold.
    sub = TorrentFile(index=0, path="torrents/d/sub.srt", size=50)
    sub_torrent = torrent([sub], hash="sub", ratio=0.1, completed_on=NOW - timedelta(days=1))
    media = TorrentFile(index=0, path="torrents/d/film.mkv", size=100)
    # A different torrent that has met seeding, so its media blob is reclaimable.
    media_torrent = torrent([media], hash="film", ratio=3.0, completed_on=NOW - timedelta(days=30))
    records = [
        rec("torrents/d/film.mkv", st_ino=160, nlink=1),
        rec("torrents/d/sub.srt", st_ino=161, nlink=1),
    ]
    result = run(records, [sub_torrent, media_torrent])
    film = blob_by_ino(result, 160)
    sidecar = blob_by_ino(result, 161)
    assert film.status is Blob.Status.RECLAIMABLE
    assert sidecar.kind is Kind.SIDECAR
    assert sidecar.status is Blob.Status.SEEDING_HOLD
    assert sidecar.orphan_reason == ""


def test_self_held_orphan_sidecar_not_reclaimed():
    # A sidecar owned by a torrent that has not met seeding, with no colocated
    # media. It must keep its own seeding hold rather than be marked an orphan and
    # reclaimed, which would harm its still-seeding torrent.
    sub = TorrentFile(index=0, path="torrents/lonely/sub.srt", size=50)
    sub_torrent = torrent([sub], hash="sub", ratio=0.1, completed_on=NOW - timedelta(days=1))
    result = run([rec("torrents/lonely/sub.srt", st_ino=162, nlink=1)], [sub_torrent])
    sidecar = blob_by_ino(result, 162)
    assert sidecar.kind is Kind.SIDECAR
    assert sidecar.status is Blob.Status.SEEDING_HOLD
    assert sidecar.orphan_reason == ""


# --- bytes_reclaimable_if_removed ------------------------------------------------


def test_reclaim_if_removed_all_reclaimable_last_link():
    # Every owned blob is reclaimable and last-link -> total is the sum of sizes.
    f1 = TorrentFile(index=0, path="torrents/pack/a.mkv", size=100)
    f2 = TorrentFile(index=1, path="torrents/pack/b.mkv", size=200)
    records = [
        rec("torrents/pack/a.mkv", st_ino=300, size=100, nlink=1),
        rec("torrents/pack/b.mkv", st_ino=301, size=200, nlink=1),
    ]
    t = torrent([f1, f2], hash="pack", ratio=5.0)
    result = run(records, [t])
    a = blob_by_ino(result, 300)
    b = blob_by_ino(result, 301)
    assert a.status is Blob.Status.RECLAIMABLE
    assert b.status is Blob.Status.RECLAIMABLE
    assert result.torrents[0].bytes_reclaimable_if_removed == 300


def test_reclaim_if_removed_season_pack_only_reclaimable_counted():
    # A season pack: ep1 hardlinked into the library (in_library, frees nothing),
    # ep2 only in torrents and seeding-met (reclaimable). Removing the torrent
    # frees only ep2's bytes.
    f_keep = TorrentFile(index=0, path="torrents/pack/ep1.mkv", size=100)
    f_recl = TorrentFile(index=1, path="torrents/pack/ep2.mkv", size=400)
    records = [
        rec("torrents/pack/ep1.mkv", st_ino=310, size=100, nlink=2),
        rec("media/tv/show/ep1.mkv", st_ino=310, size=100, nlink=2),
        rec("torrents/pack/ep2.mkv", st_ino=311, size=400, nlink=1),
    ]
    t = torrent([f_keep, f_recl], hash="pack", ratio=5.0)
    result = run(records, [t])
    ep1 = blob_by_ino(result, 310)
    ep2 = blob_by_ino(result, 311)
    assert ep1.status is Blob.Status.IN_LIBRARY
    assert ep2.status is Blob.Status.RECLAIMABLE
    assert result.torrents[0].bytes_reclaimable_if_removed == 400


def test_reclaim_if_removed_excludes_cross_seed():
    # Two torrents own the same reclaimable, hardlinked blob (cross-seed).
    # Removing either alone does not free the bytes, so neither counts it.
    f1 = TorrentFile(index=0, path="torrents/clientA/movie.mkv", size=100)
    f2 = TorrentFile(index=0, path="torrents/clientB/movie.mkv", size=100)
    records = [
        rec("torrents/clientA/movie.mkv", st_ino=320, size=100, nlink=2),
        rec("torrents/clientB/movie.mkv", st_ino=320, size=100, nlink=2),
    ]
    t1 = torrent([f1], hash="A", ratio=5.0)
    t2 = torrent([f2], hash="B", ratio=5.0)
    result = run(records, [t1, t2])
    blob = blob_by_ino(result, 320)
    assert blob.status is Blob.Status.RECLAIMABLE
    assert blob.cross_seed is True
    assert all(td.bytes_reclaimable_if_removed == 0 for td in result.torrents)


def test_reclaim_if_removed_excludes_links_outside_scope():
    # The torrent's blob would be reclaimable but has a link outside the scanned
    # tree (nlink > links_found), so it is overridden to linked_externally and
    # removing what we see frees nothing.
    f = TorrentFile(index=0, path="torrents/out.mkv", size=500)
    records = [rec("torrents/out.mkv", st_ino=330, size=500, nlink=3)]
    t = torrent([f], hash="out", ratio=5.0)
    result = run(records, [t])
    blob = blob_by_ino(result, 330)
    assert blob.status is Blob.Status.LINKED_EXTERNALLY
    assert blob.links_outside_scope is True
    assert result.torrents[0].bytes_reclaimable_if_removed == 0


def test_reclaim_if_removed_excludes_loose_hardlink_not_owned():
    # The torrent's blob is reclaimable and single-owner, but it has a second
    # in-scope hardlink in the loose tree that the torrent does not own. Removing
    # the torrent deletes only its torrents path; the inode survives via the loose
    # link, so no bytes are actually freed.
    f = TorrentFile(index=0, path="torrents/pack/a.mkv", size=100)
    records = [
        rec("torrents/pack/a.mkv", st_ino=340, size=100, nlink=2),
        rec("downloads/manual/a.mkv", st_ino=340, size=100, nlink=2),
    ]
    t = torrent([f], hash="pack", ratio=5.0)
    result = run(records, [t])
    blob = blob_by_ino(result, 340)
    assert blob.status is Blob.Status.RECLAIMABLE
    assert blob.cross_seed is False
    assert blob.links_outside_scope is False
    assert result.torrents[0].bytes_reclaimable_if_removed == 0


def test_reclaim_if_removed_excludes_unreferenced_torrents_hardlink():
    # The torrent's blob is reclaimable and single-owner, but it has a second
    # in-scope torrents-tree hardlink that the torrent client does not reference.
    # Removing the torrent deletes only the path it references; the inode survives
    # via the other link, so no bytes are actually freed.
    f = TorrentFile(index=0, path="torrents/clientA/movie.mkv", size=100)
    records = [
        rec("torrents/clientA/movie.mkv", st_ino=341, size=100, nlink=2),
        rec("torrents/clientB/movie.mkv", st_ino=341, size=100, nlink=2),
    ]
    t = torrent([f], hash="A", ratio=5.0)
    result = run(records, [t])
    blob = blob_by_ino(result, 341)
    assert blob.status is Blob.Status.RECLAIMABLE
    assert blob.cross_seed is False
    assert blob.links_outside_scope is False
    assert result.torrents[0].bytes_reclaimable_if_removed == 0


# --- summary_totals ----------------------------------------------------------


def test_summary_totals_shape_and_all_statuses_present():
    result = run([rec("media/movies/a.mkv", st_ino=160)])
    totals = result.summary_totals
    assert set(totals) == {"reclaimable_bytes", "by_status"}
    assert set(totals["by_status"]) == {s.value for s in Blob.Status}
    for bucket in totals["by_status"].values():
        assert set(bucket) == {"count", "bytes"}


def test_summary_totals_aggregates():
    result = run(
        [
            rec("media/movies/keep.mkv", st_ino=170, size=10),
            rec("random/loose1.mkv", st_ino=171, size=20),
            rec("random/loose2.mkv", st_ino=172, size=30),
        ]
    )
    by = result.summary_totals["by_status"]
    assert by[Blob.Status.IN_LIBRARY.value] == {"count": 1, "bytes": 10}
    assert by[Blob.Status.RECLAIMABLE.value] == {"count": 2, "bytes": 50}
    assert result.summary_totals["reclaimable_bytes"] == 50
