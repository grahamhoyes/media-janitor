import pytest

from scanner.clients.base import TorrentState
from scanner.models import Blob, Tree
from scanner.pipeline.classify import BlobFacts, compute_flags, derive_status


def make_facts(
    *,
    link_trees=(Tree.LOOSE,),
    owner_states=(),
    seeding_met=None,
    in_quarantine=False,
    partial_torrent=False,
    nlink=1,
    links_found=1,
) -> BlobFacts:
    """Build a BlobFacts with sensible defaults, overriding fields per case"""
    return BlobFacts(
        link_trees=tuple(link_trees),
        torrent_states=tuple(owner_states),
        seeding_met=seeding_met,
        in_quarantine=in_quarantine,
        partial_torrent=partial_torrent,
        nlink=nlink,
        links_found=links_found,
    )


@pytest.mark.parametrize(
    "facts,expected",
    [
        # 1. in_progress via active owner, guard wins over a library link
        (
            make_facts(
                link_trees=(Tree.LIBRARY,),
                owner_states=(TorrentState.IN_FLIGHT,),
                seeding_met=False,
            ),
            Blob.Status.IN_PROGRESS,
        ),
        # 1. in_progress via in_quarantine, guard wins over a library link
        (
            make_facts(link_trees=(Tree.LIBRARY,), in_quarantine=True),
            Blob.Status.IN_PROGRESS,
        ),
        # 2. in_library: library link, no active owner, not quarantined (library + torrent)
        (
            make_facts(
                link_trees=(Tree.LIBRARY, Tree.TORRENTS),
                owner_states=(TorrentState.SEEDING,),
                seeding_met=False,
            ),
            Blob.Status.IN_LIBRARY,
        ),
        # 2. in_library: library-only
        (
            make_facts(link_trees=(Tree.LIBRARY,)),
            Blob.Status.IN_LIBRARY,
        ),
        # 3a. reclaimable: no library link, untracked, loose blob
        (
            make_facts(link_trees=(Tree.LOOSE,)),
            Blob.Status.RECLAIMABLE,
        ),
        # 3a. reclaimable: no library link, untracked, orphaned torrents-only blob
        (
            make_facts(link_trees=(Tree.TORRENTS,)),
            Blob.Status.RECLAIMABLE,
        ),
        # 3b. reclaimable: no library link, tracked, seeding_met True
        (
            make_facts(
                link_trees=(Tree.TORRENTS,),
                owner_states=(TorrentState.SEEDING,),
                seeding_met=True,
            ),
            Blob.Status.RECLAIMABLE,
        ),
        # 3c. seeding_hold: no library link, tracked, seeding_met False
        (
            make_facts(
                link_trees=(Tree.TORRENTS,),
                owner_states=(TorrentState.SEEDING,),
                seeding_met=False,
            ),
            Blob.Status.SEEDING_HOLD,
        ),
        # defensive: no library link, tracked, seeding_met None -> seeding_hold
        (
            make_facts(
                link_trees=(Tree.TORRENTS,),
                owner_states=(TorrentState.SEEDING,),
                seeding_met=None,
            ),
            Blob.Status.SEEDING_HOLD,
        ),
    ],
)
def test_derive_status(facts, expected):
    assert derive_status(facts) == expected


@pytest.mark.parametrize(
    "facts,expected",
    [
        # cross_seed true: two owners
        (
            make_facts(owner_states=(TorrentState.SEEDING, TorrentState.SEEDING)),
            True,
        ),
        # cross_seed false: one owner
        (make_facts(owner_states=(TorrentState.SEEDING,)), False),
        # cross_seed false: zero owners
        (make_facts(owner_states=()), False),
    ],
)
def test_cross_seed(facts, expected):
    assert compute_flags(facts).cross_seed == expected


@pytest.mark.parametrize(
    "facts,expected",
    [
        # multi_link true: two links in the SAME tree
        (make_facts(link_trees=(Tree.TORRENTS, Tree.TORRENTS)), True),
        # multi_link false: two links in DIFFERENT trees
        (make_facts(link_trees=(Tree.LIBRARY, Tree.TORRENTS)), False),
        # multi_link false: single link
        (make_facts(link_trees=(Tree.TORRENTS,)), False),
    ],
)
def test_multi_link(facts, expected):
    assert compute_flags(facts).multi_link == expected


@pytest.mark.parametrize("value", [True, False])
def test_partial_torrent_passthrough(value):
    assert compute_flags(make_facts(partial_torrent=value)).partial_torrent == value


@pytest.mark.parametrize(
    "facts,expected",
    [
        # true: library + torrents links, no owners at all
        (make_facts(link_trees=(Tree.LIBRARY, Tree.TORRENTS)), True),
        # true: library + torrents links, owner STOPPED
        (
            make_facts(
                link_trees=(Tree.LIBRARY, Tree.TORRENTS),
                owner_states=(TorrentState.STOPPED,),
            ),
            True,
        ),
        # false: a SEEDING owner present
        (
            make_facts(
                link_trees=(Tree.LIBRARY, Tree.TORRENTS),
                owner_states=(TorrentState.SEEDING,),
            ),
            False,
        ),
        # false: an IN_FLIGHT owner present
        (
            make_facts(
                link_trees=(Tree.LIBRARY, Tree.TORRENTS),
                owner_states=(TorrentState.IN_FLIGHT,),
            ),
            False,
        ),
        # false: no torrents link
        (make_facts(link_trees=(Tree.LIBRARY,)), False),
        # false: no library link
        (make_facts(link_trees=(Tree.TORRENTS,)), False),
    ],
)
def test_seedable_idle(facts, expected):
    assert compute_flags(facts).seedable_idle == expected


@pytest.mark.parametrize(
    "facts,expected",
    [
        # true: links_found < nlink
        (make_facts(nlink=3, links_found=2), True),
        # false: equal
        (make_facts(nlink=2, links_found=2), False),
    ],
)
def test_links_outside_scope(facts, expected):
    assert compute_flags(facts).links_outside_scope == expected
