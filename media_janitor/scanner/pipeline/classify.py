from collections import Counter
from dataclasses import dataclass

from scanner.clients import TorrentState
from scanner.models import Blob, Tree


@dataclass(frozen=True)
class BlobFacts:
    """Per-blob facts that classification and flags are derived from"""

    link_trees: tuple[Tree, ...]
    "The Tree of each link (one entry per link, duplicates expected)"
    torrent_states: tuple[TorrentState, ...]
    "State of each owning torrent (empty when untracked)"
    seeding_met: bool | None
    "Whether seeding requirements are met across all owning torrents. None when untracked"
    in_quarantine: bool
    "Any link mtime within the quarantine window"
    partial_torrent: bool
    "The torrent(s) that owns this blob has blobs with different status"
    nlink: int
    "Hard link count from stat"
    links_found: int
    "Links discovered within the scanned scope"


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


def derive_status(facts: BlobFacts) -> Blob.Status:
    """Classify a blob into a single status"""

    torrent_tracked = bool(facts.torrent_states)
    has_library_link = Tree.LIBRARY in facts.link_trees
    has_active_torrent = any(s is TorrentState.IN_FLIGHT for s in facts.torrent_states)

    if has_active_torrent or facts.in_quarantine:
        return Blob.Status.IN_PROGRESS

    if has_library_link:
        return Blob.Status.IN_LIBRARY

    # Anything beyond this point has is not in the library, so it can
    # be reclaimed if requirements are met

    if not torrent_tracked:
        return Blob.Status.RECLAIMABLE
    if facts.seeding_met:
        return Blob.Status.RECLAIMABLE

    return Blob.Status.SEEDING_HOLD


def compute_flags(facts: BlobFacts) -> BlobFlags:
    """Compute independent boolean flags for a blob"""
    has_library_link = Tree.LIBRARY in facts.link_trees
    has_torrents_link = Tree.TORRENTS in facts.link_trees
    has_active_torrent = any(s is TorrentState.IN_FLIGHT for s in facts.torrent_states)
    has_seeding_torrent = any(s is TorrentState.SEEDING for s in facts.torrent_states)

    tree_counts = Counter(facts.link_trees)

    return BlobFlags(
        cross_seed=len(facts.torrent_states) > 1,
        multi_link=any(count > 1 for count in tree_counts.values()),
        partial_torrent=facts.partial_torrent,
        seedable_idle=(
            has_library_link
            and has_torrents_link
            and not has_seeding_torrent
            and not has_active_torrent
        ),
        links_outside_scope=facts.links_found < facts.nlink,
    )
