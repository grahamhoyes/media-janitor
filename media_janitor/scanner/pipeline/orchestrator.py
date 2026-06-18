"""
Scan orchestrator

Coordinates the full scan pipeline: take the single-scan advisory lock, open a
running Scan, guard against a vanished mount, gather the filesystem walk and the
download-client snapshot, derive the classification, and atomically publish the
result as a complete Scan (the swap). Older scans are pruned to a fixed window.
"""

import asyncio
import logging
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from scanner.clients.base import ClientSnapshot, DownloadClient
from scanner.clients.qbittorrent import QBittorrentClient
from scanner.models import Blob, BlobTorrent, Config, Link, Scan, Torrent
from scanner.pipeline.derive import DeriveResult, derive
from scanner.pipeline.lock import advisory_lock
from scanner.pipeline.seeding import SeedingReqs
from scanner.pipeline.walk import walk

logger = logging.getLogger(__name__)

# Number of most-recent scans to retain. Older scans (and their CASCADE-linked
# child rows) are pruned after a successful publish, with the caveat that the
# latest complete scan is always kept (see _prune_scans).
RETAIN_SCANS = 10


def run_scan(
    *,
    share_root: Path | None = None,
    client: DownloadClient | None = None,
) -> Scan | None:
    """
    Run a full scan and publish it as a complete snapshot

    Acquires the single-scan advisory lock; if another scan is already running
    this coalesces to a no-op and returns None without creating a Scan. On a
    missing mount or a download-client/derive/commit failure the Scan is marked
    failed and returned, leaving the previously published complete snapshot live.
    On success the new Scan is flipped to complete in a single transaction (the
    atomic swap) and returned.

    :param share_root: Share root to walk. Defaults to settings.SHARE_ROOT.
        Exists for test injection.
    :param client: Download client to gather from. Defaults to a
        QBittorrentClient built from settings. Exists for test injection.
    """
    with advisory_lock() as acquired:
        if not acquired:
            logger.info("scan skipped: another scan is already running")
            return None

        if share_root is None:
            share_root = Path(settings.SHARE_ROOT)

        config = Config.get()
        now = timezone.now()

        scan = Scan.objects.create(
            status=Scan.Status.RUNNING,
            as_of=now,
            seeding_min_days=config.seeding_min_days,
            seeding_min_ratio=config.seeding_min_ratio,
            quarantine_window=config.quarantine_window,
        )

        # Precondition guard: a vanished or empty mount must never be trusted, or
        # the walk would report an empty tree and reclassify the whole library as
        # reclaimable. Assert each configured root exists as a directory.
        missing = _missing_roots(share_root, config)
        if missing:
            logger.error(
                f"scan {scan.pk} failed: configured roots missing under {share_root}: "
                f"{', '.join(missing)}"
            )
            _mark_failed(scan)
            return scan

        try:
            walk_result = walk(share_root)
            if walk_result.stat_errors:
                logger.warning(
                    f"scan {scan.pk}: {walk_result.stat_errors} files skipped due to stat errors"
                )

            torrent_snapshot = _get_torrents(client)

            reqs = SeedingReqs(
                min_days=config.seeding_min_days,
                min_ratio=config.seeding_min_ratio,
            )
            result = derive(
                walk_result.records,
                torrent_snapshot.torrents,
                reqs,
                config.quarantine_window,
                library_roots=config.library_roots,
                torrent_roots=config.torrent_roots,
                now=now,
            )

            _commit(scan, result, torrent_snapshot)
        except Exception:
            # A download client outage (QBittorrentError) or any other
            # gather/derive/commit failure marks the scan failed and leaves the
            # prior complete snapshot live. We do not re-raise: that would crash
            # the db_worker.
            logger.exception("scan %s failed", scan.pk)
            _mark_failed(scan)
            return scan

        _prune_scans()
        logger.info("scan %s published as complete", scan.pk)
        return scan


def _missing_roots(share_root: Path, config: Config) -> list[str]:
    """
    Return configured library/torrent roots that are not directories under share_root

    :param share_root: Resolved share root path
    :param config: Scan policy holding library_roots and torrent_roots
    """
    missing: list[str] = []
    for rel in [*config.library_roots, *config.torrent_roots]:
        if not (share_root / rel).is_dir():
            missing.append(rel)
    return missing


def _get_torrents(client: DownloadClient | None) -> ClientSnapshot:
    """
    Gather a download-client snapshot, bridging the async client into sync code

    When no client is injected, one is built from settings and managed as an
    async context manager so its httpx client is closed. An injected client is
    assumed to be caller-managed, so it is only awaited.

    :param client: Optional injected download client (for tests)
    """
    if client is None:

        async def gather_owned() -> ClientSnapshot:
            async with QBittorrentClient.from_settings() as owned:
                return await owned.gather()

        return asyncio.run(gather_owned())

    return asyncio.run(client.gather())


def _commit(scan: Scan, result: DeriveResult, snapshot: ClientSnapshot) -> None:
    """
    Atomically insert all snapshot rows and flip the scan to complete

    Derive returns value objects referencing each other by Python identity. We
    insert Torrent then Blob rows and build id()-keyed maps to their PKs, then
    insert Link and BlobTorrent rows wired through those maps, all inside one
    transaction. The previous complete scan stays live until this commits.

    :param scan: The running Scan to publish
    :param result: Derived value objects and summary totals
    :param snapshot: The download-client snapshot (for the server version)
    """
    with transaction.atomic():
        torrent_objs = Torrent.objects.bulk_create(
            [
                Torrent(
                    scan=scan,
                    hash=t.hash,
                    state=t.state,
                    ratio=t.ratio,
                    completed_on=t.completed_on,
                    seeding_time=t.seeding_time,
                    content_path=t.content_path,
                    save_path=t.save_path,
                    seeding_met=t.seeding_met,
                    seeding_end=t.seeding_end,
                    partial_torrent=t.partial_torrent,
                    reclaim_if_removed_bytes=t.reclaim_if_removed_bytes,
                )
                for t in result.torrents
            ]
        )
        torrent_pk_by_id = {
            id(data): obj.pk for data, obj in zip(result.torrents, torrent_objs, strict=True)
        }

        blob_objs = Blob.objects.bulk_create(
            [
                Blob(
                    scan=scan,
                    st_dev=b.st_dev,
                    st_ino=b.st_ino,
                    size=b.size,
                    nlink=b.nlink,
                    links_found=b.links_found,
                    status=b.status.value,
                    kind=b.kind.value,
                    trees=b.trees,
                    torrent_tracked=b.torrent_tracked,
                    seeding_met=b.seeding_met,
                    latest_seeding_start=b.latest_seeding_start,
                    seeding_end=b.seeding_end,
                    orphan_reason=b.orphan_reason,
                    cross_seed=b.cross_seed,
                    multi_link=b.multi_link,
                    partial_torrent=b.partial_torrent,
                    seedable_idle=b.seedable_idle,
                    links_outside_scope=b.links_outside_scope,
                )
                for b in result.blobs
            ]
        )
        blob_pk_by_id = {
            id(data): obj.pk for data, obj in zip(result.blobs, blob_objs, strict=True)
        }

        Link.objects.bulk_create(
            [
                Link(
                    scan=scan,
                    blob_id=blob_pk_by_id[id(b)],
                    path=link.path,
                    name=link.name,
                    kind=link.kind.value,
                    tree=link.tree.value,
                    mtime=link.mtime,
                )
                for b in result.blobs
                for link in b.links
            ]
        )

        BlobTorrent.objects.bulk_create(
            [
                BlobTorrent(
                    scan=scan,
                    blob_id=blob_pk_by_id[id(bt.blob)],
                    torrent_id=torrent_pk_by_id[id(bt.torrent)],
                    file_index=bt.file_index,
                )
                for bt in result.blob_torrents
            ]
        )

        scan.summary_totals = result.summary_totals
        scan.qbittorrent_version = snapshot.server_version
        scan.status = Scan.Status.COMPLETE
        scan.save(update_fields=["summary_totals", "qbittorrent_version", "status"])


def _mark_failed(scan: Scan) -> None:
    """Flip a Scan to failed, persisting only the status"""
    scan.status = Scan.Status.FAILED
    scan.save(update_fields=["status"])


def _prune_scans() -> None:
    """
    Delete scans beyond the retained window, always keeping the latest complete one

    The most recent RETAIN_SCANS scans are retained. Should the latest complete
    scan fall outside that window (because newer failed/running scans crowd it
    out), it is retained anyway so the web tier always has a live snapshot.
    CASCADE removes the child rows of any deleted scan.
    """
    retained_ids = set(Scan.objects.order_by("-as_of").values_list("pk", flat=True)[:RETAIN_SCANS])

    latest_complete = Scan.objects.filter(status=Scan.Status.COMPLETE).order_by("-as_of").first()
    if latest_complete is not None:
        retained_ids.add(latest_complete.pk)

    Scan.objects.exclude(pk__in=retained_ids).delete()
