from django.http import QueryDict

from web.filters import BlobFilters, TorrentFilters


def _group(filterset, param):
    """
    The prepared group with the given param from a filterset's prepared_groups()

    :param filterset: a constructed FilterSet
    :param param: the query param naming the group
    """
    return next(group for group in filterset.prepared_groups() if group.param == param)


def _option(group, value):
    """
    The prepared option with the given value from a prepared group

    :param group: a prepared FilterGroup
    :param value: the option value to find
    """
    return next(option for option in group.options if option.value == value)


# --- Parsing ---


def test_unknown_values_dropped_at_parse():
    filters = BlobFilters(QueryDict("status=reclaimable&status=bogus&kind=nope"))
    assert filters.active_options["status"] == {"reclaimable"}
    assert filters.active_options["kind"] == set()


def test_q_trimmed_at_parse():
    filters = BlobFilters(QueryDict("q=%20%20hello%20%20"))
    assert filters.q == "hello"


def test_any_active_false_when_no_filters():
    assert BlobFilters(QueryDict("sort=size&page_size=2")).any_active is False


def test_any_active_true_with_selection():
    assert BlobFilters(QueryDict("status=reclaimable")).any_active is True


def test_any_active_true_with_search_only():
    assert BlobFilters(QueryDict("q=example")).any_active is True


def test_any_active_false_when_only_unknown_values():
    assert BlobFilters(QueryDict("status=bogus")).any_active is False


# --- Option toggle URLs ---


def test_option_toggle_url_preserves_sort_and_page_size():
    filters = BlobFilters(QueryDict("sort=size&dir=asc&page_size=2"))
    option = _option(_group(filters, "status"), "reclaimable")
    assert not option.active
    # Preserves the unrelated params in place and appends the toggled-on value
    assert option.url == "?sort=size&dir=asc&page_size=2&status=reclaimable"


def test_option_toggle_url_drops_page():
    filters = BlobFilters(QueryDict("page=3&page_size=2"))
    option = _option(_group(filters, "status"), "reclaimable")
    assert "page=3" not in option.url
    assert option.url == "?page_size=2&status=reclaimable"


def test_option_toggle_url_keeps_canonical_value_order():
    # in_library is already selected; toggling reclaimable on must emit the values in
    # STATUS_VOCAB order (reclaimable before in_library), not selection/request order.
    filters = BlobFilters(QueryDict("status=in_library"))
    option = _option(_group(filters, "status"), "reclaimable")
    assert option.url == "?status=reclaimable&status=in_library"


def test_active_option_toggle_url_removes_that_value():
    # Toggling an active option off drops just its value, keeping the rest of the group
    filters = BlobFilters(QueryDict("status=reclaimable&status=in_library"))
    option = _option(_group(filters, "status"), "reclaimable")
    assert option.active
    assert option.url == "?status=in_library"


def test_last_active_option_toggle_url_clears_the_param():
    filters = BlobFilters(QueryDict("status=reclaimable&sort=size"))
    option = _option(_group(filters, "status"), "reclaimable")
    assert option.url == "?sort=size"


# --- Clear URL ---


def test_clear_url_clears_all_group_params_and_q_keeps_sort_and_page_size():
    filters = BlobFilters(
        QueryDict("status=reclaimable&kind=media&torrent=tracked&q=x&sort=size&page_size=2")
    )
    url = filters.clear_url
    for param in ("status", "kind", "torrent", "q="):
        assert param not in url
    assert url == "?sort=size&page_size=2"


def test_clear_url_drops_page():
    filters = BlobFilters(QueryDict("status=reclaimable&page=4&sort=size"))
    assert "page=4" not in filters.clear_url
    assert filters.clear_url == "?sort=size"


# --- boolean_pair semantics ---


def test_boolean_pair_both_selected_is_active_but_no_op():
    filters = TorrentFilters(QueryDict("seeding=yes&seeding=no"))
    assert filters.active_options["seeding"] == {"yes", "no"}
    assert filters.any_active is True
