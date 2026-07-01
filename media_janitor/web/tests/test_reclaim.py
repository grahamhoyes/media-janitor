from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import formats, timezone

from scanner.models import Blob, Kind, Scan
from web.tests.factories import make_blob, make_complete_scan, make_link, make_scan


@pytest.fixture
def logged_in_client(client, django_user_model):
    user = django_user_model.objects.create_user(username="tester", password="pw")
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_lists_blobs_sorted_by_size_desc(logged_in_client):
    make_complete_scan()
    response = logged_in_client.get(reverse("reclaim"))
    assert response.status_code == 200

    sizes = [blob.size for blob in response.context["page_obj"]]
    assert sizes == [6000, 4000, 3000, 2000, 1000]


@pytest.mark.django_db
def test_htmx_returns_only_fragment(logged_in_client):
    make_complete_scan()
    response = logged_in_client.get(reverse("reclaim"), HTTP_HX_REQUEST="true")
    content = response.content.decode()

    # Fragment has the table but none of the page chrome (navbar brand).
    assert "<table" in content
    assert "Media Janitor" not in content


@pytest.mark.django_db
def test_normal_request_returns_full_page(logged_in_client):
    make_complete_scan()
    response = logged_in_client.get(reverse("reclaim"))
    content = response.content.decode()

    # Full page carries the navbar brand.
    assert "Media Janitor" in content
    assert "<table" in content


@pytest.mark.django_db
def test_pagination_splits_rows(logged_in_client):
    make_complete_scan()

    page1 = logged_in_client.get(reverse("reclaim"), {"page": 1, "page_size": 2})
    sizes1 = [blob.size for blob in page1.context["page_obj"]]
    assert sizes1 == [6000, 4000]
    assert "Showing 1 to 2 of 5" in page1.content.decode()

    page2 = logged_in_client.get(reverse("reclaim"), {"page": 2, "page_size": 2})
    sizes2 = [blob.size for blob in page2.context["page_obj"]]
    assert sizes2 == [3000, 2000]


@pytest.mark.django_db
def test_invalid_params_do_not_500(logged_in_client):
    scan = make_complete_scan()

    blobs = [
        Blob(
            scan=scan,
            st_dev=1,
            st_ino=i + 100,
            size=1024,
            nlink=1,
            links_found=1,
            status=Blob.Status.RECLAIMABLE,
            kind=Kind.MEDIA,
        )
        for i in range(150)
    ]
    Blob.objects.bulk_create(blobs)

    # Out-of-range page clamps to a valid page; bad page_size falls back to default
    response = logged_in_client.get(reverse("reclaim"), {"page": "999", "page_size": "-3"})
    assert response.status_code == 200

    content = response.content.decode()
    # Last page, page size returns to the default of 100
    assert "Showing 101 to 155 of 155" in content
    assert len(response.context["page_obj"].object_list) == 55

    response = logged_in_client.get(reverse("reclaim"), {"page": "abc", "page_size": "xyz"})
    content = response.content.decode()
    assert response.status_code == 200
    assert "Showing 1 to 100 of 155" in content
    assert response.context["page_obj"].number == 1


@pytest.mark.django_db
def test_query_count(logged_in_client, django_assert_num_queries):
    make_complete_scan()
    # 1: session lookup
    # 2: auth user lookup
    # 3: context processor Scan.current()
    # 4: view Scan.current()
    # 5: paginator count
    # 6: page of blobs
    # 7: links prefetch for the page
    with django_assert_num_queries(7):
        logged_in_client.get(reverse("reclaim"))


@pytest.mark.django_db
def test_sort_by_status_uses_vocabulary_order(logged_in_client):
    make_complete_scan()
    # Vocabulary order: reclaimable, linked_externally, seeding_hold, in_library, in_progress
    # which maps to sizes 6000, 3000, 4000, 2000, 1000.
    asc = logged_in_client.get(reverse("reclaim"), {"sort": "status", "dir": "asc"})
    assert [b.size for b in asc.context["page_obj"]] == [6000, 3000, 4000, 2000, 1000]

    desc = logged_in_client.get(reverse("reclaim"), {"sort": "status", "dir": "desc"})
    assert [b.size for b in desc.context["page_obj"]] == [1000, 2000, 4000, 3000, 6000]


@pytest.mark.django_db
def test_sort_by_name_uses_display_link(logged_in_client):
    make_complete_scan()
    # Display names sort as: example.mkv (6000), example.nfo (2000), external.mkv (3000),
    # incoming.part (1000), show.mkv (4000).
    asc = logged_in_client.get(reverse("reclaim"), {"sort": "name", "dir": "asc"})
    assert [b.size for b in asc.context["page_obj"]] == [6000, 2000, 3000, 1000, 4000]

    desc = logged_in_client.get(reverse("reclaim"), {"sort": "name", "dir": "desc"})
    assert [b.size for b in desc.context["page_obj"]] == [4000, 1000, 3000, 2000, 6000]


@pytest.mark.django_db
def test_name_sort_picks_lowest_path_link(logged_in_client):
    scan = make_scan()
    # This blob's lowest path resolves to name "aaa.mkv", which should sort it first even
    # though it also has a later link named "zzz.mkv".
    first = make_blob(scan, st_ino=1, size=1000)
    make_link(first, "media/a/aaa.mkv")
    make_link(first, "torrents/z/zzz.mkv")
    second = make_blob(scan, st_ino=2, size=2000)
    make_link(second, "media/b/bbb.mkv")

    response = logged_in_client.get(reverse("reclaim"), {"sort": "name", "dir": "asc"})
    assert [b.size for b in response.context["page_obj"]] == [1000, 2000]


@pytest.mark.django_db
def test_sort_tie_break_on_pk(logged_in_client):
    scan = make_scan()
    blobs = [make_blob(scan, st_ino=i, size=1000) for i in range(1, 4)]

    response = logged_in_client.get(reverse("reclaim"), {"sort": "size", "dir": "desc"})
    pks = [blob.pk for blob in response.context["page_obj"]]
    # Equal sizes fall back to the stable pk tie-break (creation order)
    assert pks == [blob.pk for blob in blobs]


@pytest.mark.django_db
def test_invalid_sort_params_fall_back_to_default(logged_in_client):
    make_complete_scan()
    response = logged_in_client.get(reverse("reclaim"), {"sort": "bogus", "dir": "sideways"})
    assert response.status_code == 200
    # Invalid sort drops to the unsorted state (None), which still orders size desc
    assert response.context["sort"] is None
    assert response.context["dir"] is None
    sizes = [blob.size for blob in response.context["page_obj"]]
    assert sizes == [6000, 4000, 3000, 2000, 1000]


@pytest.mark.django_db
def test_sort_persists_across_pagination(logged_in_client):
    make_complete_scan()
    # Size ascending: [1000, 2000, 3000, 4000, 6000]; page 2 with size 2 is [3000, 4000]
    page2 = logged_in_client.get(
        reverse("reclaim"),
        {"sort": "size", "dir": "asc", "page_size": 2, "page": 2},
    )
    assert [b.size for b in page2.context["page_obj"]] == [3000, 4000]


@pytest.mark.django_db
def test_sort_links_reset_page_and_preserve_params(logged_in_client):
    make_complete_scan()
    # On page 2 with a custom page size, the sort header links must keep page_size, set the
    # new sort, and drop the page param (resetting to page 1). Ampersands are HTML-escaped.
    response = logged_in_client.get(
        reverse("reclaim"),
        {"page_size": 2, "page": 2},
    )
    content = response.content.decode()
    # Clicking Status (currently inactive) defaults to ascending, keeps page_size, and drops
    # the page param. The href value ends right after the page_size override.
    assert 'page_size=2&amp;sort=status&amp;dir=asc"' in content


@pytest.mark.django_db
def test_active_sort_header_flips_direction(logged_in_client):
    make_complete_scan()
    response = logged_in_client.get(reverse("reclaim"), {"sort": "size", "dir": "desc"})
    content = response.content.decode()
    # The active Size header links to the opposite direction
    assert "sort=size&amp;dir=asc" in content


@pytest.mark.django_db
def test_size_sort_three_state_cycle(logged_in_client):
    make_complete_scan()

    # State 1 (none): no params. Size column inactive, first click sorts at its default (desc)
    none = logged_in_client.get(reverse("reclaim"))
    col = none.context["sort_columns"]["size"]
    assert col["dir"] == ""
    assert (col["next_sort"], col["next_dir"]) == ("size", "desc")
    assert [b.size for b in none.context["page_obj"]] == [6000, 4000, 3000, 2000, 1000]

    # State 2 (default dir): size desc. Next click flips to the other direction (asc)
    desc = logged_in_client.get(reverse("reclaim"), {"sort": "size", "dir": "desc"})
    col = desc.context["sort_columns"]["size"]
    assert col["dir"] == "desc"
    assert (col["next_sort"], col["next_dir"]) == ("size", "asc")

    # State 3 (other dir): size asc. Next click clears the sort (empty sort/dir)
    asc = logged_in_client.get(reverse("reclaim"), {"sort": "size", "dir": "asc"})
    col = asc.context["sort_columns"]["size"]
    assert col["dir"] == "asc"
    assert (col["next_sort"], col["next_dir"]) == (None, None)
    assert [b.size for b in asc.context["page_obj"]] == [1000, 2000, 3000, 4000, 6000]


@pytest.mark.django_db
def test_text_column_cycle_starts_ascending(logged_in_client):
    make_complete_scan()
    # Name/Status default to ascending: none -> asc -> desc -> none
    none = logged_in_client.get(reverse("reclaim"))
    assert none.context["sort_columns"]["status"]["next_dir"] == "asc"

    asc = logged_in_client.get(reverse("reclaim"), {"sort": "status", "dir": "asc"})
    assert asc.context["sort_columns"]["status"]["next_dir"] == "desc"

    desc = logged_in_client.get(reverse("reclaim"), {"sort": "status", "dir": "desc"})
    col = desc.context["sort_columns"]["status"]
    assert (col["next_sort"], col["next_dir"]) == (None, None)


@pytest.mark.django_db
def test_clear_sort_link_drops_sort_params(logged_in_client):
    make_complete_scan()
    # When a column is at its second direction, its header link clears sort and dir so the
    # only thing left is the surviving page_size param.
    response = logged_in_client.get(
        reverse("reclaim"), {"sort": "size", "dir": "asc", "page_size": 2}
    )
    content = response.content.decode()
    # The Size header (active asc) clears to just page_size; no sort= or dir= remain on it
    assert 'href="?page_size=2"' in content


@pytest.mark.django_db
def test_query_count_name_sort(logged_in_client, django_assert_num_queries):
    make_complete_scan()
    # The name sort adds a correlated Subquery annotation for the display-link name, but it
    # is inlined into the page SELECT, so the query count matches the default (see
    # test_query_count for the per-query breakdown).
    with django_assert_num_queries(7):
        logged_in_client.get(reverse("reclaim"), {"sort": "name", "dir": "asc"})


@pytest.mark.django_db
def test_no_scan_renders_empty_state(logged_in_client):
    make_scan(status=Scan.Status.RUNNING)
    make_scan(status=Scan.Status.FAILED)

    response = logged_in_client.get(reverse("reclaim"))
    assert response.status_code == 200
    assert "No completed scan yet" in response.content.decode()


@pytest.mark.django_db
def test_zero_blob_scan_renders_empty_table(logged_in_client):
    make_scan()  # complete scan with no blobs
    response = logged_in_client.get(reverse("reclaim"))
    assert response.status_code == 200
    content = response.content.decode()
    assert "This scan has no blobs" in content
    # Not the no-scan empty state.
    assert "No completed scan yet" not in content


@pytest.mark.django_db
def test_extra_link_indicator(logged_in_client):
    scan = make_scan()
    multi = make_blob(scan, st_ino=1, size=5000)
    make_link(multi, "torrents/a/one.mkv")
    make_link(multi, "media/a/one.mkv")
    single = make_blob(scan, st_ino=2, size=1000)
    make_link(single, "torrents/b/two.mkv")

    response = logged_in_client.get(reverse("reclaim"))
    content = response.content.decode()
    assert "+1 more" in content

    by_size = {blob.size: blob for blob in response.context["page_obj"]}
    # Display link is the lowest path
    assert by_size[5000].sorted_links[0].path == "media/a/one.mkv"


@pytest.mark.django_db
def test_seeding_column_met(logged_in_client):
    scan = make_scan()
    blob = make_blob(scan, st_ino=1, status=Blob.Status.IN_LIBRARY, seeding_met=True)
    make_link(blob, "media/a/met.mkv")

    content = logged_in_client.get(reverse("reclaim")).content.decode()
    assert "Yes" in content


@pytest.mark.django_db
def test_seeding_column_pending_shows_date(logged_in_client):
    scan = make_scan()
    end = timezone.now() + timedelta(days=3)
    blob = make_blob(
        scan,
        st_ino=1,
        status=Blob.Status.SEEDING_HOLD,
        seeding_met=False,
        seeding_end=end,
    )
    make_link(blob, "torrents/a/pending.mkv")

    content = logged_in_client.get(reverse("reclaim")).content.decode()
    assert formats.date_format(end, "DATE_FORMAT") in content in content
    assert "Yes" not in content


@pytest.mark.django_db
def test_seeding_column_untracked_shows_dash(logged_in_client):
    scan = make_scan()
    blob = make_blob(
        scan,
        st_ino=1,
        status=Blob.Status.RECLAIMABLE,
        seeding_met=None,
        seeding_end=None,
    )
    make_link(blob, "torrents/a/loose.mkv")

    content = logged_in_client.get(reverse("reclaim")).content.decode()
    assert "Yes" not in content


# --- Filtering (T7) ---


def _sizes(response):
    """Sizes of the blobs on the response's page, in display order"""
    return [blob.size for blob in response.context["page_obj"]]


@pytest.mark.django_db
def test_filter_status_single(logged_in_client):
    make_complete_scan()
    response = logged_in_client.get(reverse("reclaim"), {"status": "reclaimable"})
    assert _sizes(response) == [6000]


@pytest.mark.django_db
def test_filter_status_multiple_is_or(logged_in_client):
    make_complete_scan()
    response = logged_in_client.get(reverse("reclaim"), {"status": ["reclaimable", "in_library"]})
    # OR within status, default size desc order
    assert _sizes(response) == [6000, 2000]


@pytest.mark.django_db
def test_filter_kind(logged_in_client):
    make_complete_scan()
    response = logged_in_client.get(reverse("reclaim"), {"kind": "sidecar"})
    assert _sizes(response) == [2000]


@pytest.mark.django_db
def test_filter_kind_multiple_is_or(logged_in_client):
    make_complete_scan()
    response = logged_in_client.get(reverse("reclaim"), {"kind": ["sidecar", "other"]})
    assert _sizes(response) == [2000, 1000]


@pytest.mark.django_db
def test_filter_single_flag(logged_in_client):
    make_complete_scan()
    # cross_seed sits on the reclaimable blob (6000). All flags share one filter code path,
    # so one representative flag covers it.
    # (AND semantics are in test_filter_multiple_flags_are_anded)
    assert _sizes(logged_in_client.get(reverse("reclaim"), {"flag": "cross_seed"})) == [6000]


@pytest.mark.django_db
def test_filter_text_search_matches_name(logged_in_client):
    make_complete_scan()
    # Only the reclaimable blob's link is named example.mkv
    response = logged_in_client.get(reverse("reclaim"), {"q": "example.mkv"})
    assert _sizes(response) == [6000]


@pytest.mark.django_db
def test_filter_text_search_matches_path(logged_in_client):
    make_complete_scan()
    # The seeding_hold blob's link path is torrents/tv/show.mkv
    response = logged_in_client.get(reverse("reclaim"), {"q": "tv/show"})
    assert _sizes(response) == [4000]


@pytest.mark.django_db
def test_filter_text_search_distinct(logged_in_client):
    scan = make_scan()
    blob = make_blob(scan, st_ino=1, size=1000)
    # Two links both match the term; the blob must still appear once
    make_link(blob, "torrents/a/duplicate.mkv")
    make_link(blob, "media/a/duplicate.mkv")

    response = logged_in_client.get(reverse("reclaim"), {"q": "duplicate"})
    assert _sizes(response) == [1000]


@pytest.mark.django_db
def test_filter_combines_status_and_flag(logged_in_client):
    make_complete_scan()
    # AND across types: reclaimable AND cross_seed both describe the 6000 blob
    match = logged_in_client.get(
        reverse("reclaim"), {"status": "reclaimable", "flag": "cross_seed"}
    )
    assert _sizes(match) == [6000]

    # reclaimable does not carry partial_torrent, so the combination is empty
    empty = logged_in_client.get(
        reverse("reclaim"), {"status": "reclaimable", "flag": "partial_torrent"}
    )
    assert _sizes(empty) == []


@pytest.mark.django_db
def test_filter_multiple_flags_are_anded(logged_in_client):
    make_complete_scan()
    # The in_library blob (2000) carries both seedable_idle and multi_link
    both = logged_in_client.get(reverse("reclaim"), {"flag": ["seedable_idle", "multi_link"]})
    assert _sizes(both) == [2000]

    # No blob carries both seedable_idle and cross_seed
    none = logged_in_client.get(reverse("reclaim"), {"flag": ["seedable_idle", "cross_seed"]})
    assert _sizes(none) == []


@pytest.mark.django_db
def test_invalid_filter_values_are_ignored(logged_in_client):
    make_complete_scan()
    response = logged_in_client.get(
        reverse("reclaim"), {"status": "bogus", "kind": "nope", "flag": "not_a_flag"}
    )
    assert response.status_code == 200
    # Unknown values drop out, leaving no active filter and the full list
    assert response.context["any_filter"] is False
    assert _sizes(response) == [6000, 4000, 3000, 2000, 1000]


@pytest.mark.django_db
def test_result_count_unfiltered_shows_total(logged_in_client):
    make_complete_scan()
    response = logged_in_client.get(reverse("reclaim"))
    assert response.context["total_count"] == 5
    assert response.context["any_filter"] is False
    content = response.content.decode()
    # The total is emphasized in its own span, followed by the "total" label
    assert ">5</span>" in content
    assert "total" in content
    assert "matching" not in content


@pytest.mark.django_db
def test_result_count_filtered_shows_matching_and_total(logged_in_client):
    make_complete_scan()
    response = logged_in_client.get(reverse("reclaim"), {"status": "reclaimable"})
    assert response.context["matching_count"] == 1
    assert response.context["total_count"] == 5
    content = response.content.decode()
    # The matching count is emphasized in its own span; the tail text stays contiguous
    assert ">1</span>" in content
    assert "matching, filtered from 5 total" in content


@pytest.mark.django_db
def test_clear_filters_link_drops_filters_keeps_sort_and_page_size(logged_in_client):
    make_complete_scan()
    response = logged_in_client.get(
        reverse("reclaim") + "?sort=size&dir=asc&page_size=2&status=reclaimable"
    )
    content = response.content.decode()
    # The clear link strips every filter param but preserves sort, dir, and page_size
    assert 'href="?sort=size&amp;dir=asc&amp;page_size=2"' in content


@pytest.mark.django_db
def test_filter_chip_preserves_sort_and_resets_page(logged_in_client):
    make_complete_scan()
    response = logged_in_client.get(reverse("reclaim") + "?page=2&page_size=2&sort=name&dir=asc")
    content = response.content.decode()
    # The reclaimable status chip link keeps sort/dir/page_size, drops page, and adds status
    assert 'href="?page_size=2&amp;sort=name&amp;dir=asc&amp;status=reclaimable"' in content


@pytest.mark.django_db
def test_filter_query_count(logged_in_client, django_assert_num_queries):
    make_complete_scan()
    # One extra query over the unfiltered case (test_query_count): the scan-wide total
    with django_assert_num_queries(8):
        logged_in_client.get(reverse("reclaim"), {"status": "reclaimable"})


@pytest.mark.django_db
def test_filtered_empty_shows_filter_message(logged_in_client):
    make_complete_scan()
    # A flag no blob carries empties the table; the message must reflect the filter, not an
    # empty scan.
    response = logged_in_client.get(reverse("reclaim"), {"kind": "sidecar", "flag": "cross_seed"})
    assert _sizes(response) == []
    content = response.content.decode()
    assert "No blobs match the current filters" in content
    assert "This scan has no blobs" not in content
