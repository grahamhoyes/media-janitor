import pytest

from scanner.clients.base import TorrentState
from scanner.models import Blob, Tree
from scanner.pipeline.classify import classify_status, compute_flags


def status_kwargs(
    *,
    link_trees=(Tree.LOOSE,),
    owner_states=(),
    seeding_met=None,
    in_quarantine=False,
) -> dict:
    """Build classify_status kwargs with sensible defaults, overriding per case"""
    return {
        "link_trees": tuple(link_trees),
        "torrent_states": tuple(owner_states),
        "seeding_met": seeding_met,
        "in_quarantine": in_quarantine,
    }


def flags_kwargs(
    *,
    link_trees=(Tree.LOOSE,),
    owner_states=(),
    nlink=1,
    links_found=1,
) -> dict:
    """Build compute_flags kwargs with sensible defaults, overriding per case"""
    return {
        "link_trees": tuple(link_trees),
        "torrent_states": tuple(owner_states),
        "nlink": nlink,
        "links_found": links_found,
    }


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        # 1. in_progress via active owner, guard wins over a library link
        (
            status_kwargs(
                link_trees=(Tree.LIBRARY,),
                owner_states=(TorrentState.IN_FLIGHT,),
                seeding_met=False,
            ),
            Blob.Status.IN_PROGRESS,
        ),
        # 1. in_progress via in_quarantine, guard wins over a library link
        (
            status_kwargs(link_trees=(Tree.LIBRARY,), in_quarantine=True),
            Blob.Status.IN_PROGRESS,
        ),
        # 2. in_library: library link, no active owner, not quarantined (library + torrent)
        (
            status_kwargs(
                link_trees=(Tree.LIBRARY, Tree.TORRENTS),
                owner_states=(TorrentState.SEEDING,),
                seeding_met=False,
            ),
            Blob.Status.IN_LIBRARY,
        ),
        # 2. in_library: library-only
        (
            status_kwargs(link_trees=(Tree.LIBRARY,)),
            Blob.Status.IN_LIBRARY,
        ),
        # 3a. reclaimable: no library link, untracked, loose blob
        (
            status_kwargs(link_trees=(Tree.LOOSE,)),
            Blob.Status.RECLAIMABLE,
        ),
        # 3a. reclaimable: no library link, untracked, orphaned torrents-only blob
        (
            status_kwargs(link_trees=(Tree.TORRENTS,)),
            Blob.Status.RECLAIMABLE,
        ),
        # 3b. reclaimable: no library link, tracked, seeding_met True
        (
            status_kwargs(
                link_trees=(Tree.TORRENTS,),
                owner_states=(TorrentState.SEEDING,),
                seeding_met=True,
            ),
            Blob.Status.RECLAIMABLE,
        ),
        # 3c. seeding_hold: no library link, tracked, seeding_met False
        (
            status_kwargs(
                link_trees=(Tree.TORRENTS,),
                owner_states=(TorrentState.SEEDING,),
                seeding_met=False,
            ),
            Blob.Status.SEEDING_HOLD,
        ),
        # defensive: no library link, tracked, seeding_met None -> seeding_hold
        (
            status_kwargs(
                link_trees=(Tree.TORRENTS,),
                owner_states=(TorrentState.SEEDING,),
                seeding_met=None,
            ),
            Blob.Status.SEEDING_HOLD,
        ),
    ],
)
def test_classify_status(kwargs, expected):
    assert classify_status(**kwargs) == expected


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        # cross_seed true: two owners
        (
            flags_kwargs(owner_states=(TorrentState.SEEDING, TorrentState.SEEDING)),
            True,
        ),
        # cross_seed false: one owner
        (flags_kwargs(owner_states=(TorrentState.SEEDING,)), False),
        # cross_seed false: zero owners
        (flags_kwargs(owner_states=()), False),
    ],
)
def test_cross_seed(kwargs, expected):
    assert compute_flags(**kwargs).cross_seed == expected


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        # multi_link true: two links in the SAME tree
        (flags_kwargs(link_trees=(Tree.TORRENTS, Tree.TORRENTS)), True),
        # multi_link false: two links in DIFFERENT trees
        (flags_kwargs(link_trees=(Tree.LIBRARY, Tree.TORRENTS)), False),
        # multi_link false: single link
        (flags_kwargs(link_trees=(Tree.TORRENTS,)), False),
    ],
)
def test_multi_link(kwargs, expected):
    assert compute_flags(**kwargs).multi_link == expected


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        # true: library + torrents links, no owners at all
        (flags_kwargs(link_trees=(Tree.LIBRARY, Tree.TORRENTS)), True),
        # true: library + torrents links, owner STOPPED
        (
            flags_kwargs(
                link_trees=(Tree.LIBRARY, Tree.TORRENTS),
                owner_states=(TorrentState.STOPPED,),
            ),
            True,
        ),
        # false: a SEEDING owner present
        (
            flags_kwargs(
                link_trees=(Tree.LIBRARY, Tree.TORRENTS),
                owner_states=(TorrentState.SEEDING,),
            ),
            False,
        ),
        # false: an IN_FLIGHT owner present
        (
            flags_kwargs(
                link_trees=(Tree.LIBRARY, Tree.TORRENTS),
                owner_states=(TorrentState.IN_FLIGHT,),
            ),
            False,
        ),
        # false: no torrents link
        (flags_kwargs(link_trees=(Tree.LIBRARY,)), False),
        # false: no library link
        (flags_kwargs(link_trees=(Tree.TORRENTS,)), False),
    ],
)
def test_seedable_idle(kwargs, expected):
    assert compute_flags(**kwargs).seedable_idle == expected


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        # true: links_found < nlink
        (flags_kwargs(nlink=3, links_found=2), True),
        # false: equal
        (flags_kwargs(nlink=2, links_found=2), False),
    ],
)
def test_links_outside_scope(kwargs, expected):
    assert compute_flags(**kwargs).links_outside_scope == expected
