from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from scanner.models import Blob, BlobTorrent, Config, Scan, Tree
from web import display
from web.tests.factories import make_blob, make_link, make_scan, make_torrent


@pytest.fixture
def logged_in_client(client, django_user_model):
    user = django_user_model.objects.create_user(username="tester", password="pw")
    client.force_login(user)
    return client


def _torrent_with_blobs(scan: Scan) -> tuple:
    """A torrent with two blobs, each with a couple of links"""
    torrent = make_torrent(
        scan,
        hash_="a" * 40,
        name="Example Torrent",
        category="radarr",
        tracker="https://tracker.example/announce",
        content_path="torrents/movies/example",
        save_path="torrents/movies",
    )
    b1 = make_blob(scan, st_ino=1, size=6000, status=Blob.Status.RECLAIMABLE)
    b2 = make_blob(scan, st_ino=2, size=2000, status=Blob.Status.IN_LIBRARY)
    make_link(b1, "torrents/movies/example.mkv", tree=Tree.TORRENTS)
    make_link(b1, "media/movies/example.mkv", tree=Tree.LIBRARY)
    make_link(b2, "torrents/movies/example.nfo", tree=Tree.TORRENTS)
    BlobTorrent.objects.create(scan=scan, blob=b1, torrent=torrent, file_index=0)
    BlobTorrent.objects.create(scan=scan, blob=b2, torrent=torrent, file_index=1)
    return torrent, b1, b2


@pytest.mark.django_db
def test_renders_torrent_detail(logged_in_client):
    scan = make_scan()
    torrent, _, _ = _torrent_with_blobs(scan)

    response = logged_in_client.get(reverse("torrent_detail", args=[torrent.pk]))
    assert response.status_code == 200
    assert response.context["torrent"].pk == torrent.pk
    content = response.content.decode()
    assert "a" * 40 in content
    assert "tracker.example" in content
    assert "radarr" in content
    assert "torrents/movies/example" in content


@pytest.mark.django_db
def test_blobs_grouped_by_status_and_link_to_drawer(logged_in_client):
    scan = make_scan()
    torrent, b1, b2 = _torrent_with_blobs(scan)

    response = logged_in_client.get(reverse("torrent_detail", args=[torrent.pk]))
    groups = response.context["blob_groups"]

    # Groups appear in STATUS_VOCAB order, one per status present among the torrent's blobs
    assert [g["status"] for g in groups] == [Blob.Status.RECLAIMABLE, Blob.Status.IN_LIBRARY]
    reclaimable = next(g for g in groups if g["status"] == Blob.Status.RECLAIMABLE)
    assert [b.pk for b in reclaimable["blobs"]] == [b1.pk]
    in_library = next(g for g in groups if g["status"] == Blob.Status.IN_LIBRARY)
    assert [b.pk for b in in_library["blobs"]] == [b2.pk]

    content = response.content.decode()
    # Each status group is labeled
    assert display.status_label(Blob.Status.RECLAIMABLE) in content
    assert display.status_label(Blob.Status.IN_LIBRARY) in content
    # Each blob still links to its own blob-detail drawer
    assert reverse("blob_detail", args=[b1.pk]) in content
    assert reverse("blob_detail", args=[b2.pk]) in content


@pytest.mark.django_db
def test_blobs_by_status_preserves_within_group_ordering(logged_in_client):
    """blobs_by_status() should keep the caller's ordering within a status group, not re-sort it"""
    scan = make_scan()
    torrent = make_torrent(scan, hash_="d" * 40)
    small = make_blob(scan, st_ino=3, size=1000, status=Blob.Status.RECLAIMABLE)
    large = make_blob(scan, st_ino=4, size=9000, status=Blob.Status.RECLAIMABLE)
    make_link(small, "torrents/movies/small.mkv", tree=Tree.TORRENTS)
    make_link(large, "torrents/movies/large.mkv", tree=Tree.TORRENTS)
    # Create the smaller blob's link first, so a group that re-sorted by pk or insertion
    # order (instead of preserving the caller's size-desc ordering) would fail this assertion.
    BlobTorrent.objects.create(scan=scan, blob=small, torrent=torrent, file_index=0)
    BlobTorrent.objects.create(scan=scan, blob=large, torrent=torrent, file_index=1)

    response = logged_in_client.get(reverse("torrent_detail", args=[torrent.pk]))
    groups = response.context["blob_groups"]

    reclaimable = next(g for g in groups if g["status"] == Blob.Status.RECLAIMABLE)
    assert [b.pk for b in reclaimable["blobs"]] == [large.pk, small.pk]


@pytest.mark.django_db
def test_no_blobs(logged_in_client):
    scan = make_scan()
    torrent = make_torrent(scan, hash_="b" * 40)

    response = logged_in_client.get(reverse("torrent_detail", args=[torrent.pk]))
    assert list(response.context["blobs"]) == []
    assert response.context["blob_groups"] == []
    assert "This torrent has no blobs" in response.content.decode()


@pytest.mark.django_db
def test_blob_referenced_by_multiple_file_entries_counts_once(logged_in_client):
    scan = make_scan()
    torrent = make_torrent(scan, hash_="c" * 40)
    blob = make_blob(scan, st_ino=1, size=1000)
    make_link(blob, "torrents/a/multi.mkv")
    BlobTorrent.objects.create(scan=scan, blob=blob, torrent=torrent, file_index=0)
    BlobTorrent.objects.create(scan=scan, blob=blob, torrent=torrent, file_index=1)

    response = logged_in_client.get(reverse("torrent_detail", args=[torrent.pk]))
    assert [b.pk for b in response.context["blobs"]] == [blob.pk]


@pytest.mark.django_db
def test_404_for_unknown_pk(logged_in_client):
    make_scan()
    response = logged_in_client.get(reverse("torrent_detail", args=[999999]))
    assert response.status_code == 404


@pytest.mark.django_db
def test_404_for_torrent_not_in_current_scan(logged_in_client):
    old = make_scan(as_of=timezone.now() - timedelta(days=1))
    old_torrent = make_torrent(old, hash_="d" * 40)
    # A newer complete scan makes `old` no longer current
    make_scan(as_of=timezone.now())

    response = logged_in_client.get(reverse("torrent_detail", args=[old_torrent.pk]))
    assert response.status_code == 404


@pytest.mark.django_db
def test_404_when_no_completed_scan(logged_in_client):
    running = make_scan(status=Scan.Status.RUNNING)
    torrent = make_torrent(running, hash_="e" * 40)
    response = logged_in_client.get(reverse("torrent_detail", args=[torrent.pk]))
    assert response.status_code == 404


@pytest.mark.django_db
def test_login_required(client):
    scan = make_scan()
    torrent = make_torrent(scan, hash_="f" * 40)
    response = client.get(reverse("torrent_detail", args=[torrent.pk]))
    assert response.status_code == 302


@pytest.mark.django_db
def test_no_n_plus_one(logged_in_client, django_assert_num_queries):
    scan = make_scan()
    torrent, b1, b2 = _torrent_with_blobs(scan)
    make_link(b1, "loose/extra.mkv", tree=Tree.LOOSE)
    make_link(b2, "loose/extra2.mkv", tree=Tree.LOOSE)

    # Ensure the singleton Config row exists so the view's Config.get() is a single SELECT
    # rather than a get_or_create that also runs an insert on first use.
    Config.get()

    # 1 session, 2 auth user, 3 context processor Scan.current(), 4-5 context processor
    # scan_in_progress() (DBTaskResult check, then Scan running check), 6 view Scan.current(),
    # 7 the torrent, 8 the blobs, 9 links prefetch, 10 Config.get().
    with django_assert_num_queries(10):
        logged_in_client.get(reverse("torrent_detail", args=[torrent.pk]))
