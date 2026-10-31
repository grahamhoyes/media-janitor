import re

import pytest
from django.urls import reverse

from scanner.models import Blob, BlobTorrent, Tree
from web.drawers import DetailDrawerView
from web.tests.factories import make_blob, make_link, make_scan, make_torrent


@pytest.fixture
def logged_in_client(client, django_user_model):
    user = django_user_model.objects.create_user(username="tester", password="pw")
    client.force_login(user)
    return client


@pytest.fixture
def scan_objects():
    """A scan with one reclaimable blob served by one torrent"""
    scan = make_scan()
    blob = make_blob(scan, st_ino=1, size=5000, status=Blob.Status.RECLAIMABLE)
    make_link(blob, "torrents/movies/film.mkv", tree=Tree.TORRENTS)
    torrent = make_torrent(scan, hash_="a" * 40, name="Film Torrent")
    BlobTorrent.objects.create(scan=scan, blob=blob, torrent=torrent, file_index=0)
    return blob, torrent


def _drawer_toggle_checked(content: str) -> bool:
    """Whether the rendered drawer toggle checkbox carries the checked attribute"""
    start = content.index('id="main-drawer"')
    return re.search(r"\schecked[\s>]", content[start : content.index(">", start)]) is not None


@pytest.mark.parametrize(
    ("url", "kind", "pk", "expected"),
    [
        ("/files/", "blob", 7, "/files/?drawer=blob-7"),
        (
            "/files/?status=reclaimable&torrent=tracked",
            "blob",
            7,
            "/files/?status=reclaimable&torrent=tracked&drawer=blob-7",
        ),
        # Opening a drawer replaces the open one
        ("/files/?drawer=torrent-3&page=2", "blob", 7, "/files/?drawer=blob-7&page=2"),
    ],
)
def test_url_with(url, kind, pk, expected):
    # View() sets its keyword arguments as attributes, as setup() does for self.kwargs
    drawer = DetailDrawerView.registry[kind](kwargs={"pk": pk})
    assert drawer.url_with(url) == expected


@pytest.mark.django_db
def test_fragment_pushes_list_url_with_drawer(logged_in_client, scan_objects):
    blob, torrent = scan_objects
    response = logged_in_client.get(
        reverse("blob_detail", args=[blob.pk]),
        headers={
            "HX-Request": "true",
            "HX-Current-URL": f"http://testserver/files/?status=reclaimable&drawer=torrent-{torrent.pk}",
        },
    )
    assert response.status_code == 200
    assert response["HX-Push-Url"] == f"/files/?status=reclaimable&drawer=blob-{blob.pk}"


@pytest.mark.django_db
def test_fragment_without_current_url_does_not_push(logged_in_client, scan_objects):
    _, torrent = scan_objects
    response = logged_in_client.get(
        reverse("torrent_detail", args=[torrent.pk]), headers={"HX-Request": "true"}
    )
    assert response.status_code == 200
    assert "HX-Push-Url" not in response


@pytest.mark.django_db
def test_direct_visit_redirects_to_list(logged_in_client, scan_objects):
    blob, torrent = scan_objects
    response = logged_in_client.get(reverse("blob_detail", args=[blob.pk]))
    assert response.status_code == 302
    assert response["Location"] == f"{reverse('files')}?drawer=blob-{blob.pk}"

    response = logged_in_client.get(reverse("torrent_detail", args=[torrent.pk]))
    assert response.status_code == 302
    assert response["Location"] == f"{reverse('torrents')}?drawer=torrent-{torrent.pk}"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("list_name", "kind"),
    [("files", "blob"), ("torrents", "torrent"), ("files", "torrent"), ("torrents", "blob")],
)
def test_list_page_renders_open_drawer(logged_in_client, scan_objects, list_name, kind):
    blob, torrent = scan_objects
    pk = blob.pk if kind == "blob" else torrent.pk
    response = logged_in_client.get(f"{reverse(list_name)}?drawer={kind}-{pk}")
    assert response.status_code == 200
    content = response.content.decode()
    assert _drawer_toggle_checked(content)
    # The drawer's own content (its close control) is rendered into the page
    assert 'aria-label="Close"' in content


@pytest.mark.django_db
def test_list_page_without_drawer_is_closed(logged_in_client, scan_objects):
    content = logged_in_client.get(reverse("files")).content.decode()
    assert not _drawer_toggle_checked(content)
    assert 'aria-label="Close"' not in content


@pytest.mark.django_db
@pytest.mark.parametrize("value", ["blob-999999", "blob-abc", "blob", "nope-1", ""])
def test_list_page_ignores_invalid_drawer(logged_in_client, scan_objects, value):
    response = logged_in_client.get(f"{reverse('files')}?drawer={value}")
    assert response.status_code == 200
    assert not _drawer_toggle_checked(response.content.decode())


@pytest.mark.django_db
def test_list_controls_drop_drawer_param(logged_in_client, scan_objects):
    blob, _ = scan_objects
    response = logged_in_client.get(f"{reverse('files')}?status=reclaimable&drawer=blob-{blob.pk}")
    assert "drawer" not in response.context["clear_url"]
    # No list control (filter, sort, pagination, search) carries the drawer param
    assert "drawer=" not in response.content.decode()


@pytest.mark.django_db
def test_table_fragment_does_not_render_drawer(logged_in_client, scan_objects):
    blob, _ = scan_objects
    response = logged_in_client.get(
        f"{reverse('files')}?drawer=blob-{blob.pk}", headers={"HX-Request": "true"}
    )
    assert "drawer_html" not in response.context
    assert 'id="main-drawer"' not in response.content.decode()


@pytest.mark.django_db
def test_history_restore_request_renders_full_page(logged_in_client, scan_objects):
    """htmx's history cache miss request (with historyRestoreAsHxRequest off) gets the full page"""
    blob, _ = scan_objects
    response = logged_in_client.get(
        f"{reverse('files')}?drawer=blob-{blob.pk}",
        headers={"HX-History-Restore-Request": "true"},
    )
    content = response.content.decode()
    assert "<html" in content
    assert _drawer_toggle_checked(content)


@pytest.mark.django_db
@pytest.mark.parametrize("hx_request", [True, False])
def test_responses_vary_on_hx_request(logged_in_client, scan_objects, hx_request):
    headers = {"HX-Request": "true"} if hx_request else {}
    response = logged_in_client.get(reverse("files"), headers=headers)
    assert "HX-Request" in response["Vary"]
