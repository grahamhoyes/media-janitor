from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import PurePosixPath

from scanner.clients.base import TorrentSnapshot, TorrentState
from scanner.models import Blob, Kind, Tree
from scanner.paths import kind_for, tree_for
from scanner.pipeline.classify import classify_status, compute_flags
from scanner.pipeline.seeding import SeedingReqs, evaluate_seeding
from scanner.pipeline.walk import FileRecord

# Statuses that mean a blob is being kept, so a colocated sidecar must follow it
# rather than be reclaimed. Everything except RECLAIMABLE is kept on disk:
# SEEDING_HOLD blobs stay until their torrent ages out, so their sidecars must
# stay too.
_KEPT_STATUSES: frozenset[Blob.Status] = frozenset(
    {Blob.Status.IN_LIBRARY, Blob.Status.IN_PROGRESS, Blob.Status.SEEDING_HOLD}
)

ORPHANED_SIDECAR_REASON = "orphaned_sidecar"


@dataclass(frozen=True)
class LinkDraft:
    """A path naming a blob, share-relative"""

    path: str
    "Path relative to the share root"

    name: str
    kind: Kind
    tree: Tree
    mtime: datetime


@dataclass
class TorrentDraft:
    """Per-scan snapshot of a torrent"""

    hash: str
    state: str
    "Raw client state string, to be persisted on Torrent.state"

    normalized_state: TorrentState
    """
    Normalized torrent state. Used internally only, not persisted.
    """
    ratio: float
    completed_on: datetime | None
    """
    When the torrent completed downloading, ie when seeding started
    """
    seeding_time: timedelta | None
    content_path: str
    save_path: str
    seeding_met: bool
    seeding_end: datetime | None
    partial_torrent: bool = False
    # Bytes freed by removing this torrent: its reclaimable, last-link blobs.
    # Computed during the build after blob flags are set.
    bytes_reclaimable_if_removed: int = 0
    owned_blobs: list[BlobDraft] = field(default_factory=list)
    "Blobs owned by this torrent. Used internally only, not persisted."


@dataclass
class BlobDraft:
    """A unique (per scan) file discovered during a scan"""

    st_dev: int
    st_ino: int
    size: int
    nlink: int
    links_found: int
    kind: Kind
    trees: list[str]
    torrent_tracked: bool
    seeding_met: bool | None
    latest_seeding_start: datetime | None
    seeding_end: datetime | None
    links: list[LinkDraft]
    status: Blob.Status = Blob.Status.RECLAIMABLE
    orphan_reason: str = ""
    cross_seed: bool = False
    multi_link: bool = False
    partial_torrent: bool = False
    seedable_idle: bool = False
    links_outside_scope: bool = False
    owner_torrents: list[TorrentDraft] = field(default_factory=list)
    "Torrents owning this blob. Used internally only, not persisted."


@dataclass(frozen=True)
class BlobTorrentDraft:
    """Ownership link between a blob and a torrent within a scan"""

    blob: BlobDraft
    torrent: TorrentDraft
    file_index: int


@dataclass(frozen=True)
class ScanModel:
    blobs: list[BlobDraft]
    torrents: list[TorrentDraft]
    blob_torrents: list[BlobTorrentDraft]
    summary_totals: dict = field(default_factory=dict)


def _blob_kind(links: list[LinkDraft]) -> Kind:
    """
    Determine a blob's kind from its links

    A blob is MEDIA if any link is media, else SIDECAR if any link is a
    sidecar, else OTHER.

    kind is based off extension, there's no realistic case where different
    links to the same blob will have different kinds.
    """
    if any(link.kind is Kind.MEDIA for link in links):
        return Kind.MEDIA
    if any(link.kind is Kind.SIDECAR for link in links):
        return Kind.SIDECAR
    return Kind.OTHER


def _parent(path: str) -> str:
    """Share-relative parent directory of a path"""
    return str(PurePosixPath(path).parent)


def build_scan_model(
    records: list[FileRecord],
    torrents: list[TorrentSnapshot],
    reqs: SeedingReqs,
    quarantine_window: timedelta,
    *,
    library_roots: list[str],
    torrent_roots: list[str],
    now: datetime,
) -> ScanModel:
    """
    Assemble blob/link/torrent/blob-torrent value objects and scan totals

    Pure transformation with no database, settings, or I/O access. The caller
    maps the returned value objects onto Blob/Link/Torrent/BlobTorrent rows.

    :param records: Files discovered by the filesystem walk
    :param torrents: Torrent snapshots from the download client
    :param reqs: Seeding requirements copied from Config
    :param quarantine_window: Window in which a recently modified link is held
    :param library_roots: Share-relative library root paths (for classification)
    :param torrent_roots: Share-relative torrent root paths (for classification)
    :param now: Scan timestamp, used for seeding and quarantine evaluation
    """
    # Dedupe scanned files (ie links) into blobs by (st_dev, st_ino)
    by_inode: dict[tuple[int, int], list[FileRecord]] = defaultdict(list)
    for rec in records:
        by_inode[(rec.st_dev, rec.st_ino)].append(rec)

    blobs: list[BlobDraft] = []

    for (st_dev, st_ino), recs in by_inode.items():
        links = [
            _link_for(rec, library_roots=library_roots, torrent_roots=torrent_roots) for rec in recs
        ]
        # size and nlink agree across hardlinks; take the max to be robust to
        # any transient stat disagreement during the walk.
        size = max(rec.size for rec in recs)
        nlink = max(rec.nlink for rec in recs)
        blob = BlobDraft(
            st_dev=st_dev,
            st_ino=st_ino,
            size=size,
            nlink=nlink,
            links_found=len(links),
            kind=_blob_kind(links),
            trees=sorted({link.tree.value for link in links}),
            links=links,
            # The rest get set below
            torrent_tracked=False,
            seeding_met=None,
            latest_seeding_start=None,
            seeding_end=None,
        )
        blobs.append(blob)

    # Correlate torrents to blobs by share-relative path. The download client
    # exposes a path/index/size per torrent file but no inode, so the path is the
    # only available join key. Build a path -> blob index from every link.
    # Hardlinks and cross-seed copies that name the same content collapsed to a
    # single blob during the inode dedupe above, so two torrents naming the same
    # content yield two owners on one blob (a cross-seed).
    blob_by_path: dict[str, BlobDraft] = {}
    for blob in blobs:
        for link in blob.links:
            # Note reference semantics: two paths can point to the same BlobDraft object.
            # Important for below when we mutate blob.torrent_tracked - if there are two
            # links to a blob but only one of them is referenced by the torrent client,
            # the blob will still have torrent_tracked set on both path refs here.
            blob_by_path[link.path] = blob

    # Correlate torrents to blobs and evaluate per-torrent seeding
    torrent_data: list[TorrentDraft] = []
    blob_torrents: list[BlobTorrentDraft] = []

    for t in torrents:
        seeding = evaluate_seeding(t.completed_on, t.ratio, reqs, now)
        td = TorrentDraft(
            hash=t.hash,
            state=t.raw_state,
            normalized_state=t.state,
            ratio=t.ratio,
            completed_on=t.completed_on,
            seeding_time=t.seeding_time,
            content_path=t.content_path,
            save_path=t.save_path,
            seeding_met=seeding.met,
            seeding_end=seeding.end,
        )
        torrent_data.append(td)

        for file in t.files:
            blob_for_path = blob_by_path.get(file.path)
            if blob_for_path is None:
                continue
            blob_for_path.torrent_tracked = True
            blob_torrents.append(
                BlobTorrentDraft(blob=blob_for_path, torrent=td, file_index=file.index)
            )
            # cross-link in both directions
            blob_for_path.owner_torrents.append(td)
            td.owned_blobs.append(blob_for_path)

    # Roll up per-blob seeding, then classify each blob's status from it. Both are
    # per-blob with no cross-blob dependency, so they share one pass. Sidecars are
    # bound in a later pass and may inherit a media blob's status, so they are
    # classified provisionally here.
    for blob in blobs:
        if blob.owner_torrents:
            # A blob has met seeding requirements if all owning torrents have met theirs
            blob.seeding_met = all(t.seeding_met for t in blob.owner_torrents)
            starts = [t.completed_on for t in blob.owner_torrents if t.completed_on is not None]
            if starts:
                blob.latest_seeding_start = max(starts)
                blob.seeding_end = blob.latest_seeding_start + timedelta(days=reqs.min_days)
        # else the torrent is untracked - seeding_met None, datetimes None (already defaulted)

        in_quarantine = any(now - link.mtime < quarantine_window for link in blob.links)
        blob.status = classify_status(
            link_trees=tuple(link.tree for link in blob.links),
            torrent_states=tuple(t.normalized_state for t in blob.owner_torrents),
            seeding_met=blob.seeding_met,
            in_quarantine=in_quarantine,
        )

    # Sidecar binding (directory-level colocation).
    # A media link contributes its parent directory to a media-blob index. A
    # sidecar blob binds to a media blob sharing the parent directory of any of
    # the sidecar's links. A bound sidecar inherits its media blob's status; if
    # it colocates with media blobs of differing status, prefer a kept status so
    # a sidecar whose media is staying is never reclaimed. An orphaned sidecar
    # (no colocated media link) becomes reclaimable with an orphan_reason.
    media_blobs_by_dir: dict[str, list[BlobDraft]] = defaultdict(list)
    for blob in blobs:
        for link in blob.links:
            if link.kind is Kind.MEDIA:
                media_blobs_by_dir[_parent(link.path)].append(blob)

    for blob in blobs:
        if blob.kind is not Kind.SIDECAR:
            continue
        # Media blobs colocated with this sidecar blob
        colocated: list[BlobDraft] = []
        for link in blob.links:
            colocated.extend(media_blobs_by_dir.get(_parent(link.path), []))
        if not colocated:
            blob.status = Blob.Status.RECLAIMABLE
            blob.orphan_reason = ORPHANED_SIDECAR_REASON
            continue
        kept = [m for m in colocated if m.status in _KEPT_STATUSES]
        # Take the status from any of its colocated files, preferring kept statuses
        blob.status = kept[0].status if kept else colocated[0].status

    # partial_torrent: a torrent is partial when its owned blobs do not all share
    # the same status. Statuses are now final (including sidecars), so mark every
    # owned blob and the torrent itself accordingly.
    for td in torrent_data:
        if len({b.status for b in td.owned_blobs}) > 1:
            td.partial_torrent = True
            for b in td.owned_blobs:
                b.partial_torrent = True

    # Compute flags
    for blob in blobs:
        flags = compute_flags(
            link_trees=tuple(link.tree for link in blob.links),
            torrent_states=tuple(t.normalized_state for t in blob.owner_torrents),
            nlink=blob.nlink,
            links_found=blob.links_found,
        )
        blob.cross_seed = flags.cross_seed
        blob.multi_link = flags.multi_link
        blob.seedable_idle = flags.seedable_idle
        blob.links_outside_scope = flags.links_outside_scope

    # bytes_reclaimable_if_removed: bytes freed if this whole torrent is removed.
    # Sum the size of owned blobs that are reclaimable and whose only links belong
    # to this torrent (not cross-seeded, no links outside the scan). Example: a
    # 10-file season pack with 6 episodes still hardlinked into the library and 4
    # orphaned reclaims only the 4. Dedupe owned blobs by identity so a blob listed
    # under multiple file entries is not double-counted. Runs after flags, since it
    # reads cross_seed and links_outside_scope.
    for td in torrent_data:
        seen: set[int] = set()
        total = 0
        for blob in td.owned_blobs:
            if id(blob) in seen:
                continue
            seen.add(id(blob))
            if (
                blob.status is Blob.Status.RECLAIMABLE
                and not blob.links_outside_scope
                and not blob.cross_seed
            ):
                total += blob.size
        td.bytes_reclaimable_if_removed = total

    summary_totals = _summary_totals(blobs)

    return ScanModel(
        blobs=blobs,
        torrents=torrent_data,
        blob_torrents=blob_torrents,
        summary_totals=summary_totals,
    )


def _link_for(
    rec: FileRecord,
    *,
    library_roots: list[str],
    torrent_roots: list[str],
) -> LinkDraft:
    """Build a LinkDraft from a FileRecord"""
    name = PurePosixPath(rec.rel).name
    return LinkDraft(
        path=rec.rel,
        name=name,
        kind=kind_for(name),
        tree=tree_for(rec.rel, library_roots=library_roots, torrent_roots=torrent_roots),
        mtime=rec.mtime,
    )


def _summary_totals(blobs: list[BlobDraft]) -> dict:
    """
    Build the scan summary totals

    Reclaimable bytes sum the size of RECLAIMABLE blobs, excluding any whose
    links_outside_scope is True (deleting our links would not free space while
    other hardlinks live outside the scan). by_status lists every Blob.Status
    (all four are always present, with zeros when absent) keyed by its value
    string. stat_errors are intentionally not included: they live on WalkResult.
    """
    by_status: dict[str, dict[str, int]] = {
        status.value: {"count": 0, "bytes": 0} for status in Blob.Status
    }
    reclaimable_bytes = 0
    for blob in blobs:
        bucket = by_status[blob.status.value]
        bucket["count"] += 1
        bucket["bytes"] += blob.size
        if blob.status is Blob.Status.RECLAIMABLE and not blob.links_outside_scope:
            reclaimable_bytes += blob.size

    return {
        "reclaimable_bytes": reclaimable_bytes,
        "by_status": by_status,
    }
