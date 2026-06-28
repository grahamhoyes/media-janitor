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
    assert "This scan has no blobs." in content
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
    assert by_size[5000].extra_link_count == 1
    assert by_size[1000].extra_link_count == 0
    # Display link is the lowest path
    assert by_size[5000].display_link.path == "media/a/one.mkv"


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
