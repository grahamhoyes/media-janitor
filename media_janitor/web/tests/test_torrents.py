import pytest
from django.urls import reverse

from scanner.models import Blob, BlobTorrent, Scan, Torrent, TorrentState
from web import display
from web.templatetags.janitor import binsize
from web.tests.factories import make_blob, make_link, make_scan, make_torrent


@pytest.fixture
def logged_in_client(client, django_user_model):
    user = django_user_model.objects.create_user(username="tester", password="pw")
    client.force_login(user)
    return client


def make_torrents_scan() -> Scan:
    """
    Build a scan with three torrents of varying reclaim math and states

    Charlie (6000 reclaimable-if-removed, size 2000, seeding, fully reclaimable) owns two
    reclaimable blobs, one cross-seeded with Alpha. Alpha (3000, size 8000, downloading,
    partial) also owns an in-library blob. Bravo (0, size 4000, stopped, none) owns no
    blobs. One untracked reclaimable blob exists outside any torrent.
    """
    scan = make_scan()

    charlie = make_torrent(
        scan,
        hash_="c" * 40,
        name="Charlie",
        state=TorrentState.SEEDING,
        reclaim_state=Torrent.ReclaimState.FULL,
        size=2000,
        bytes_reclaimable_if_removed=6000,
    )
    alpha = make_torrent(
        scan,
        hash_="a" * 40,
        name="Alpha",
        state=TorrentState.DOWNLOADING,
        reclaim_state=Torrent.ReclaimState.PARTIAL,
        size=8000,
        bytes_reclaimable_if_removed=3000,
    )
    make_torrent(
        scan,
        hash_="b" * 40,
        name="Bravo",
        state=TorrentState.STOPPED,
        size=4000,
        bytes_reclaimable_if_removed=0,
    )

    b1 = make_blob(scan, st_ino=1, size=6000, status=Blob.Status.RECLAIMABLE)
    b2 = make_blob(scan, st_ino=2, size=2000, status=Blob.Status.RECLAIMABLE, cross_seed=True)
    b3 = make_blob(scan, st_ino=3, size=1000, status=Blob.Status.IN_LIBRARY)
    b4 = make_blob(scan, st_ino=4, size=500, status=Blob.Status.RECLAIMABLE)

    make_link(b1, "torrents/c/one.mkv")
    make_link(b2, "torrents/s/two.mkv")
    make_link(b3, "media/a/three.mkv")
    make_link(b4, "loose/four.mkv")

    BlobTorrent.objects.create(scan=scan, blob=b1, torrent=charlie, file_index=0)
    BlobTorrent.objects.create(scan=scan, blob=b2, torrent=charlie, file_index=1)
    BlobTorrent.objects.create(scan=scan, blob=b2, torrent=alpha, file_index=0)
    BlobTorrent.objects.create(scan=scan, blob=b3, torrent=alpha, file_index=1)

    return scan


def _names(response):
    """Torrent names on the response's page, in display order"""
    return [torrent.name for torrent in response.context["page_obj"]]


@pytest.mark.django_db
def test_lists_torrents_default_sort_reclaimable_desc(logged_in_client):
    make_torrents_scan()
    response = logged_in_client.get(reverse("torrents"))
    assert response.status_code == 200
    assert _names(response) == ["Charlie", "Alpha", "Bravo"]


@pytest.mark.django_db
def test_blob_count_annotation(logged_in_client):
    make_torrents_scan()
    response = logged_in_client.get(reverse("torrents"))
    counts = {t.name: t.blob_count for t in response.context["page_obj"]}
    # The untracked blob counts toward no torrent; the cross-seed blob counts toward both
    assert counts == {"Charlie": 2, "Alpha": 2, "Bravo": 0}


@pytest.mark.django_db
def test_reclaim_state_stored_per_torrent(logged_in_client):
    make_torrents_scan()
    response = logged_in_client.get(reverse("torrents"))
    states = {t.name: t.reclaim_state for t in response.context["page_obj"]}
    # reclaim_state is now a stored column, not a runtime annotation
    assert states == {
        "Charlie": Torrent.ReclaimState.FULL,
        "Alpha": Torrent.ReclaimState.PARTIAL,
        "Bravo": Torrent.ReclaimState.NONE,
    }


@pytest.mark.django_db
def test_reclaim_state_badges_rendered(logged_in_client):
    make_torrents_scan()
    content = logged_in_client.get(reverse("torrents")).content.decode()
    # full -> Fully Reclaimable, partial -> Partial Torrent, none -> Not Reclaimable
    assert content.count("Fully Reclaimable") == 1
    assert content.count("Partially Reclaimable") == 1
    assert content.count("Not Reclaimable") == 1


@pytest.mark.django_db
def test_reclaimable_column_matches_model_field(logged_in_client):
    make_torrents_scan()
    response = logged_in_client.get(reverse("torrents"))
    content = response.content.decode()
    for torrent in response.context["page_obj"]:
        assert binsize(torrent.bytes_reclaimable_if_removed) in content


@pytest.mark.django_db
def test_no_hash_on_page(logged_in_client):
    make_torrents_scan()
    content = logged_in_client.get(reverse("torrents")).content.decode()
    for hash_ in ("a" * 40, "b" * 40, "c" * 40):
        assert hash_ not in content


@pytest.mark.django_db
def test_seeding_badges(logged_in_client):
    make_torrents_scan()
    content = logged_in_client.get(reverse("torrents")).content.decode()
    # Normalized states render their enum labels; STOPPED now shows a real "Stopped"
    # label rather than a raw client string.
    assert "Seeding" in content
    assert "Downloading" in content
    assert "Stopped" in content


def test_torrent_state_badge_vocabulary():
    assert display.torrent_state_badge(TorrentState.SEEDING.value) == {
        "label": "Seeding",
        "badge": "badge-success",
    }
    assert display.torrent_state_badge(TorrentState.DOWNLOADING.value) == {
        "label": "Downloading",
        "badge": "badge-info",
    }
    assert display.torrent_state_badge(TorrentState.CHECKING.value)["badge"] == "badge-info"
    assert display.torrent_state_badge(TorrentState.ERROR.value) == {
        "label": "Error",
        "badge": "badge-error",
    }
    assert display.torrent_state_badge(TorrentState.STOPPED.value)["label"] == "Stopped"


def test_torrent_state_badge_fallback_for_unknown_value():
    # A value that is not a TorrentState renders as-is in a muted badge
    assert display.torrent_state_badge("someFutureState") == {
        "label": "someFutureState",
        "badge": "badge-ghost",
    }


@pytest.mark.django_db
def test_sort_by_name(logged_in_client):
    make_torrents_scan()
    asc = logged_in_client.get(reverse("torrents"), {"sort": "name", "dir": "asc"})
    assert _names(asc) == ["Alpha", "Bravo", "Charlie"]

    desc = logged_in_client.get(reverse("torrents"), {"sort": "name", "dir": "desc"})
    assert _names(desc) == ["Charlie", "Bravo", "Alpha"]


@pytest.mark.django_db
def test_sort_by_size(logged_in_client):
    make_torrents_scan()
    desc = logged_in_client.get(reverse("torrents"), {"sort": "size", "dir": "desc"})
    assert _names(desc) == ["Alpha", "Bravo", "Charlie"]

    asc = logged_in_client.get(reverse("torrents"), {"sort": "size", "dir": "asc"})
    assert _names(asc) == ["Charlie", "Bravo", "Alpha"]


@pytest.mark.django_db
def test_sort_tie_break_on_pk(logged_in_client):
    scan = make_scan()
    torrents = [
        make_torrent(scan, hash_=str(i) * 40, name=f"T{i}", bytes_reclaimable_if_removed=1000)
        for i in range(1, 4)
    ]

    response = logged_in_client.get(reverse("torrents"))
    pks = [torrent.pk for torrent in response.context["page_obj"]]
    # Equal reclaimable bytes fall back to the stable pk tie-break (creation order)
    assert pks == [torrent.pk for torrent in torrents]


@pytest.mark.django_db
def test_invalid_sort_params_fall_back_to_default(logged_in_client):
    make_torrents_scan()
    response = logged_in_client.get(reverse("torrents"), {"sort": "bogus", "dir": "sideways"})
    assert response.status_code == 200
    assert response.context["sort"] is None
    assert _names(response) == ["Charlie", "Alpha", "Bravo"]


@pytest.mark.django_db
def test_search_matches_name_case_insensitive(logged_in_client):
    make_torrents_scan()
    response = logged_in_client.get(reverse("torrents"), {"q": "char"})
    assert _names(response) == ["Charlie"]
    assert response.context["matching_count"] == 1
    assert response.context["total_count"] == 3
    assert "matching, filtered from 3 total" in response.content.decode()


@pytest.mark.django_db
def test_search_empty_shows_search_message(logged_in_client):
    make_torrents_scan()
    response = logged_in_client.get(reverse("torrents"), {"q": "zzz"})
    assert _names(response) == []
    content = response.content.decode()
    assert "No torrents match the current search" in content
    assert "This scan has no torrents" not in content


@pytest.mark.django_db
def test_search_preserves_sort(logged_in_client):
    make_torrents_scan()
    response = logged_in_client.get(reverse("torrents"), {"q": "a", "sort": "name", "dir": "asc"})
    # Charlie, Alpha, and Bravo all contain an "a"; name asc orders them
    assert _names(response) == ["Alpha", "Bravo", "Charlie"]


@pytest.mark.django_db
def test_invalid_page_params_do_not_500(logged_in_client):
    make_torrents_scan()
    response = logged_in_client.get(reverse("torrents"), {"page": "abc", "page_size": "-1"})
    assert response.status_code == 200
    assert response.context["page_obj"].number == 1


@pytest.mark.django_db
def test_htmx_returns_only_fragment(logged_in_client):
    make_torrents_scan()
    response = logged_in_client.get(reverse("torrents"), HTTP_HX_REQUEST="true")
    content = response.content.decode()
    assert "<table" in content
    assert "Media Janitor" not in content


@pytest.mark.django_db
def test_normal_request_returns_full_page(logged_in_client):
    make_torrents_scan()
    content = logged_in_client.get(reverse("torrents")).content.decode()
    assert "Media Janitor" in content
    assert "<table" in content


@pytest.mark.django_db
def test_no_scan_renders_empty_state(logged_in_client):
    make_scan(status=Scan.Status.RUNNING)
    response = logged_in_client.get(reverse("torrents"))
    assert response.status_code == 200
    assert "No completed scan yet" in response.content.decode()


@pytest.mark.django_db
def test_zero_torrent_scan_renders_empty_table(logged_in_client):
    make_scan()  # complete scan with no torrents
    response = logged_in_client.get(reverse("torrents"))
    content = response.content.decode()
    assert "This scan has no torrents" in content
    assert "No completed scan yet" not in content


@pytest.mark.django_db
def test_query_count(logged_in_client, django_assert_num_queries):
    make_torrents_scan()
    # 1: session lookup
    # 2: auth user lookup
    # 3: context processor Scan.current()
    # 4: view Scan.current()
    # 5: paginator count
    # 6: page of torrents (blob_count inlined as an annotation)
    with django_assert_num_queries(6):
        logged_in_client.get(reverse("torrents"))


@pytest.mark.django_db
def test_search_query_count(logged_in_client, django_assert_num_queries):
    make_torrents_scan()
    # One extra query over the unsearched case: the scan-wide torrent total
    with django_assert_num_queries(7):
        logged_in_client.get(reverse("torrents"), {"q": "char"})


# --- Expanded blob rows fragment ---


def _torrent(scan, name):
    return scan.torrents.get(name=name)


@pytest.mark.django_db
def test_expand_control_points_at_blobs_fragment(logged_in_client):
    scan = make_torrents_scan()
    content = logged_in_client.get(reverse("torrents")).content.decode()
    for torrent in scan.torrents.all():
        assert reverse("torrent_blobs", args=[torrent.pk]) in content


@pytest.mark.django_db
def test_blobs_fragment_lists_blobs_size_desc(logged_in_client):
    scan = make_torrents_scan()
    charlie = _torrent(scan, "Charlie")
    response = logged_in_client.get(reverse("torrent_blobs", args=[charlie.pk]))
    assert response.status_code == 200
    assert [blob.size for blob in response.context["blobs"]] == [6000, 2000]
    assert response.context["blobs"][0].links.first().path == "torrents/c/one.mkv"


@pytest.mark.django_db
def test_blobs_fragment_empty_torrent(logged_in_client):
    scan = make_torrents_scan()
    bravo = _torrent(scan, "Bravo")
    response = logged_in_client.get(reverse("torrent_blobs", args=[bravo.pk]))
    assert response.context["blobs"].count() == 0
    assert "This torrent has no blobs in the scan" in response.content.decode()


@pytest.mark.django_db
def test_blobs_fragment_unknown_pk_404s(logged_in_client):
    make_torrents_scan()
    response = logged_in_client.get(reverse("torrent_blobs", args=[999999]))
    assert response.status_code == 404


@pytest.mark.django_db
def test_blobs_fragment_stale_scan_404s(logged_in_client):
    old_scan = make_torrents_scan()
    stale = _torrent(old_scan, "Charlie")
    make_scan()  # a newer complete scan becomes current

    response = logged_in_client.get(reverse("torrent_blobs", args=[stale.pk]))
    assert response.status_code == 404


@pytest.mark.django_db
def test_blobs_fragment_no_scan_404s(logged_in_client):
    make_scan(status=Scan.Status.RUNNING)
    response = logged_in_client.get(reverse("torrent_blobs", args=[1]))
    assert response.status_code == 404


@pytest.mark.django_db
def test_blobs_fragment_query_count(logged_in_client, django_assert_num_queries):
    scan = make_torrents_scan()
    charlie = _torrent(scan, "Charlie")
    # 1: session lookup
    # 2: auth user lookup
    # 3: context processor Scan.current()
    # 4: view Scan.current()
    # 5: torrent lookup
    # 6: page of blobs
    # 7: links prefetch
    with django_assert_num_queries(7):
        logged_in_client.get(reverse("torrent_blobs", args=[charlie.pk]))


@pytest.mark.django_db
def test_blob_referenced_by_multiple_file_entries_counts_once(logged_in_client):
    # The scan pipeline can link one blob to the same torrent through several file
    # entries (different file_index), so ownership rows are not unique per (blob, torrent)
    scan = make_scan()
    torrent = make_torrent(scan, bytes_reclaimable_if_removed=1000)
    blob = make_blob(scan, st_ino=1, size=1000)
    make_link(blob, "torrents/a/multi.mkv")
    BlobTorrent.objects.create(scan=scan, blob=blob, torrent=torrent, file_index=0)
    BlobTorrent.objects.create(scan=scan, blob=blob, torrent=torrent, file_index=1)

    page = logged_in_client.get(reverse("torrents"))
    assert [t.blob_count for t in page.context["page_obj"]] == [1]

    fragment = logged_in_client.get(reverse("torrent_blobs", args=[torrent.pk]))
    assert [b.pk for b in fragment.context["blobs"]] == [blob.pk]
    # Exactly one rendered row: the blob's drawer link appears once
    assert fragment.content.decode().count(reverse("blob_detail", args=[blob.pk])) == 1
