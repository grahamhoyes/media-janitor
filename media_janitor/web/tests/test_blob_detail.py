from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from scanner.models import Blob, BlobTorrent, Config, Kind, Scan, Tree
from web.tests.factories import make_blob, make_link, make_scan, make_torrent


@pytest.fixture
def logged_in_client(client, django_user_model):
    user = django_user_model.objects.create_user(username="tester", password="pw")
    client.force_login(user)
    return client


def _multi_tree_blob(scan: Scan) -> Blob:
    """A blob with links across all three trees, including a sidecar link"""
    blob = make_blob(
        scan, st_ino=1, size=5000, status=Blob.Status.IN_LIBRARY, nlink=3, links_found=3
    )
    make_link(blob, "media/movies/film.mkv", tree=Tree.LIBRARY)
    make_link(blob, "media/movies/film.nfo", tree=Tree.LIBRARY, kind=Kind.SIDECAR)
    make_link(blob, "torrents/movies/film.mkv", tree=Tree.TORRENTS)
    make_link(blob, "loose/stray.mkv", tree=Tree.LOOSE)
    return blob


@pytest.mark.django_db
def test_renders_blob_detail(logged_in_client):
    scan = make_scan()
    blob = _multi_tree_blob(scan)

    response = logged_in_client.get(reverse("blob_detail", args=[blob.pk]))
    assert response.status_code == 200
    assert response.context["blob"].pk == blob.pk
    content = response.content.decode()
    # Status reasoning is present, not just the badge label
    assert "Has a link under a library root" in content


@pytest.mark.django_db
def test_links_grouped_by_tree(logged_in_client):
    scan = make_scan()
    blob = _multi_tree_blob(scan)

    response = logged_in_client.get(reverse("blob_detail", args=[blob.pk]))
    groups = response.context["link_groups"]

    # Groups appear in Tree vocabulary order and only for trees that have links
    assert [g["tree"] for g in groups] == [Tree.LIBRARY, Tree.TORRENTS, Tree.LOOSE]
    library = next(g for g in groups if g["tree"] == Tree.LIBRARY)
    assert {link.path for link in library["links"]} == {
        "media/movies/film.mkv",
        "media/movies/film.nfo",
    }
    torrents = next(g for g in groups if g["tree"] == Tree.TORRENTS)
    assert [link.path for link in torrents["links"]] == ["torrents/movies/film.mkv"]


@pytest.mark.django_db
def test_sidecar_kind_surfaced(logged_in_client):
    scan = make_scan()
    blob = _multi_tree_blob(scan)

    content = logged_in_client.get(reverse("blob_detail", args=[blob.pk])).content.decode()
    # The sidecar link's kind is surfaced (media links are not badged)
    assert "Sidecar" in content


@pytest.mark.django_db
def test_torrents_listed_and_link_to_drawer(logged_in_client):
    scan = make_scan()
    blob = make_blob(scan, st_ino=1, status=Blob.Status.SEEDING_HOLD, seeding_met=False)
    make_link(blob, "torrents/movies/film.mkv", tree=Tree.TORRENTS)

    t1 = make_torrent(scan, hash_="a" * 40, ratio=1.5, seeding_time=timedelta(days=2))
    t2 = make_torrent(scan, hash_="b" * 40, ratio=0.3, seeding_met=False)
    BlobTorrent.objects.create(scan=scan, blob=blob, torrent=t1, file_index=0)
    BlobTorrent.objects.create(scan=scan, blob=blob, torrent=t2, file_index=0)

    response = logged_in_client.get(reverse("blob_detail", args=[blob.pk]))
    assert {t.pk for t in response.context["torrents"]} == {t1.pk, t2.pk}
    content = response.content.decode()
    assert "a" * 40 in content
    assert "b" * 40 in content
    # Each torrent links to its torrent drawer
    assert f"/torrent/{t1.pk}/" in content
    assert f"/torrent/{t2.pk}/" in content


@pytest.mark.django_db
def test_no_torrents(logged_in_client):
    scan = make_scan()
    blob = make_blob(scan, st_ino=1, status=Blob.Status.RECLAIMABLE)
    make_link(blob, "loose/stray.mkv", tree=Tree.LOOSE)

    response = logged_in_client.get(reverse("blob_detail", args=[blob.pk]))
    assert list(response.context["torrents"]) == []
    assert "Not part of any torrent" in response.content.decode()


@pytest.mark.django_db
def test_outside_scope_note_when_links_found_below_nlink(logged_in_client):
    scan = make_scan()
    blob = make_blob(
        scan,
        st_ino=1,
        status=Blob.Status.LINKED_EXTERNALLY,
        nlink=3,
        links_found=1,
        links_outside_scope=True,
    )
    make_link(blob, "torrents/movies/film.mkv", tree=Tree.TORRENTS)

    response = logged_in_client.get(reverse("blob_detail", args=[blob.pk]))
    assert response.context["links_outside"] == 2
    content = response.content.decode()
    assert "would not free space" in content
    assert "1 of 3" in content


@pytest.mark.django_db
def test_no_outside_scope_note_when_accounted(logged_in_client):
    scan = make_scan()
    blob = make_blob(scan, st_ino=1, nlink=2, links_found=2)
    make_link(blob, "media/a/one.mkv", tree=Tree.LIBRARY)
    make_link(blob, "torrents/a/one.mkv", tree=Tree.TORRENTS)

    response = logged_in_client.get(reverse("blob_detail", args=[blob.pk]))
    assert response.context["links_outside"] == 0
    assert "would not free space" not in response.content.decode()


@pytest.mark.django_db
def test_orphan_reason_shown_when_set(logged_in_client):
    scan = make_scan()
    blob = make_blob(
        scan,
        st_ino=1,
        kind=Kind.OTHER,
        status=Blob.Status.RECLAIMABLE,
        orphan_reason="No colocated media file",
    )
    make_link(blob, "loose/readme.txt", tree=Tree.LOOSE, kind=Kind.OTHER)

    content = logged_in_client.get(reverse("blob_detail", args=[blob.pk])).content.decode()
    assert "No colocated media file" in content


@pytest.mark.django_db
def test_flags_with_explanations(logged_in_client):
    scan = make_scan()
    blob = make_blob(scan, st_ino=1, cross_seed=True)
    make_link(blob, "torrents/a/one.mkv", tree=Tree.TORRENTS)

    response = logged_in_client.get(reverse("blob_detail", args=[blob.pk]))
    assert ("Cross Seed", "Served by more than one torrent") in response.context["flags"]
    content = response.content.decode()
    assert "Cross Seed" in content
    assert "Served by more than one torrent" in content


@pytest.mark.django_db
def test_404_for_unknown_pk(logged_in_client):
    make_scan()
    response = logged_in_client.get(reverse("blob_detail", args=[999999]))
    assert response.status_code == 404


@pytest.mark.django_db
def test_404_for_blob_not_in_current_scan(logged_in_client):
    old = make_scan(as_of=timezone.now() - timedelta(days=1))
    old_blob = make_blob(old, st_ino=1)
    make_link(old_blob, "media/a/old.mkv", tree=Tree.LIBRARY)
    # A newer complete scan makes `old` no longer current
    make_scan(as_of=timezone.now())

    response = logged_in_client.get(reverse("blob_detail", args=[old_blob.pk]))
    assert response.status_code == 404


@pytest.mark.django_db
def test_404_when_no_completed_scan(logged_in_client):
    running = make_scan(status=Scan.Status.RUNNING)
    blob = make_blob(running, st_ino=1)
    response = logged_in_client.get(reverse("blob_detail", args=[blob.pk]))
    assert response.status_code == 404


@pytest.mark.django_db
def test_login_required(client):
    scan = make_scan()
    blob = make_blob(scan, st_ino=1)
    response = client.get(reverse("blob_detail", args=[blob.pk]))
    assert response.status_code == 302


@pytest.mark.django_db
def test_no_n_plus_one(logged_in_client, django_assert_num_queries):
    scan = make_scan()
    blob = _multi_tree_blob(scan)
    for i, h in enumerate(("a", "b", "c")):
        torrent = make_torrent(scan, hash_=h * 40)
        BlobTorrent.objects.create(scan=scan, blob=blob, torrent=torrent, file_index=i)

    # Ensure the singleton Config row exists so the view's Config.get() is a single SELECT
    # rather than a get_or_create that also runs a savepoint + insert on first use.
    Config.get()

    # 1 session, 2 auth user, 3 context processor Scan.current(), 4 view Scan.current(),
    # 5 the blob, 6 links prefetch, 7 torrents prefetch, 8 Config.get(). Independent of the
    # link/torrent counts, which is the no-N+1 property under test.
    with django_assert_num_queries(8):
        logged_in_client.get(reverse("blob_detail", args=[blob.pk]))
