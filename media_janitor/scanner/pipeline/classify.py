from collections import Counter
from dataclasses import dataclass

from scanner.clients import TorrentState
from scanner.models import Blob, Tree


@dataclass(frozen=True)
class BlobFlags:
    """The five independent boolean flags stored on a Blob"""

    cross_seed: bool
    "Blob served by more than one torrent"
    multi_link: bool
    "Multiple hard links in the same tree"
    partial_torrent: bool
    """
    The torrent(s) that owns this blob has blobs with different status

    For example, a season pack might have some episodes IN_LIBRARY while
    others were replaced by higher quality versions and are hence RECLAIMABLE.
    """
    seedable_idle: bool
    """
    In the library and torrent tree, but isn't seeding.

    May have no torrent, or torrent may be stopped.
    """
    links_outside_scope: bool
    "Link count could not be accounted for by only files in the scan"


def derive_status(
    *,
    link_trees: tuple[Tree, ...],
    torrent_states: tuple[TorrentState, ...],
    seeding_met: bool | None,
    in_quarantine: bool,
) -> Blob.Status:
    """
    Classify a blob into a single status

    :param link_trees: The Tree of each link (one entry per link, duplicates expected)
    :param torrent_states: State of each owning torrent (empty when untracked)
    :param seeding_met: Whether seeding requirements are met across all owning torrents.
        None when untracked
    :param in_quarantine: Any link mtime within the quarantine window
    """
    torrent_tracked = bool(torrent_states)
    has_library_link = Tree.LIBRARY in link_trees
    has_active_torrent = any(s is TorrentState.IN_FLIGHT for s in torrent_states)

    if has_active_torrent or in_quarantine:
        return Blob.Status.IN_PROGRESS

    if has_library_link:
        return Blob.Status.IN_LIBRARY

    # Anything beyond this point has is not in the library, so it can
    # be reclaimed if requirements are met

    if not torrent_tracked:
        return Blob.Status.RECLAIMABLE
    if seeding_met:
        return Blob.Status.RECLAIMABLE

    return Blob.Status.SEEDING_HOLD


def compute_flags(
    *,
    link_trees: tuple[Tree, ...],
    torrent_states: tuple[TorrentState, ...],
    partial_torrent: bool,
    nlink: int,
    links_found: int,
) -> BlobFlags:
    """
    Compute independent boolean flags for a blob

    :param link_trees: The Tree of each link (one entry per link, duplicates expected)
    :param torrent_states: State of each owning torrent (empty when untracked)
    :param partial_torrent: The torrent(s) that owns this blob has blobs with different status
    :param nlink: Hard link count from stat
    :param links_found: Links discovered within the scanned scope
    """
    has_library_link = Tree.LIBRARY in link_trees
    has_torrents_link = Tree.TORRENTS in link_trees
    has_active_torrent = any(s is TorrentState.IN_FLIGHT for s in torrent_states)
    has_seeding_torrent = any(s is TorrentState.SEEDING for s in torrent_states)

    tree_counts = Counter(link_trees)

    return BlobFlags(
        cross_seed=len(torrent_states) > 1,
        multi_link=any(count > 1 for count in tree_counts.values()),
        partial_torrent=partial_torrent,
        seedable_idle=(
            has_library_link
            and has_torrents_link
            and not has_seeding_torrent
            and not has_active_torrent
        ),
        links_outside_scope=links_found < nlink,
    )
