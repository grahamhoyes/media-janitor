import pytest
from django.urls import reverse

from scanner.models import Scan
from web.display import headline_segments
from web.tests.factories import make_complete_scan, make_scan

# -- headline_segments ----------------------------------------------------------


@pytest.mark.django_db
def test_headline_segments_ordering_labels_and_pcts():
    scan = make_complete_scan()
    segments = headline_segments(scan)

    keys = [seg["key"] for seg in segments]
    assert keys == ["reclaimable", "seeding_hold", "in_library", "in_progress"]

    labels = [seg["label"] for seg in segments]
    assert labels == ["Reclaimable", "Seeding hold", "In library", "In progress"]

    by_key = {seg["key"]: seg for seg in segments}
    # Some wiggle room on the sum due to rounding errors, which we're fine with
    # since it's just cosmetic
    assert sum(x["pct"] for x in by_key.values()) == pytest.approx(100, abs=0.02)

    assert by_key["reclaimable"]["bytes"] == 6000

    assert by_key["reclaimable"]["bar_class"] == "bg-success"
    assert by_key["seeding_hold"]["dot_class"] == "bg-warning"


def test_headline_segments_zero_total_no_division_error():
    # Constructed in memory (no DB) since headline_segments only reads summary_totals.
    scan = Scan(
        summary_totals={
            "reclaimable_bytes": 0,
            "by_status": {
                "reclaimable": {"count": 0, "bytes": 0},
                "seeding_hold": {"count": 0, "bytes": 0},
                "in_library": {"count": 0, "bytes": 0},
                "in_progress": {"count": 0, "bytes": 0},
            },
        }
    )
    segments = headline_segments(scan)
    assert [seg["key"] for seg in segments] == [
        "reclaimable",
        "seeding_hold",
        "in_library",
        "in_progress",
    ]
    assert all(seg["pct"] == 0 for seg in segments)
    assert all(seg["bytes"] == 0 for seg in segments)


def test_headline_segments_empty_summary_totals():
    scan = Scan(summary_totals={})
    segments = headline_segments(scan)
    assert len(segments) == 4
    assert all(seg["pct"] == 0 for seg in segments)
    assert all(seg["bytes"] == 0 for seg in segments)


# -- shell through HTTP ---------------------------------------------------------


@pytest.fixture
def logged_in_client(client, django_user_model):
    user = django_user_model.objects.create_user(username="tester", password="pw")
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_dashboard_renders_navbar_band_and_stamp(logged_in_client):
    make_complete_scan()
    response = logged_in_client.get(reverse("dashboard"))
    assert response.status_code == 200

    content = response.content.decode()
    assert "Dashboard" in content
    assert "Reclaim summary" in content
    assert "Reclaim list" in content
    # Reclaimable amount (binsize of 5000)
    assert "4.9 KiB" in content
    assert "Last scan" in content


@pytest.mark.django_db
def test_dashboard_without_scan_omits_band(logged_in_client):
    # Only non-complete scans exist, so current_scan is None
    make_scan(status=Scan.Status.RUNNING)
    make_scan(status=Scan.Status.FAILED)

    response = logged_in_client.get(reverse("dashboard"))
    assert response.status_code == 200

    content = response.content.decode()
    assert "reclaimable" not in content
    assert "Last scan" not in content
    # Navbar still renders.
    assert "Media Janitor" in content


@pytest.mark.django_db
def test_login_page_hides_scan_data_from_anonymous(client):
    # A completed scan exists, but the public login page (which uses the same base template)
    # must not leak storage totals or the scan stamp to anonymous visitors
    make_complete_scan()
    response = client.get(reverse("login"))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Sign in" in content
    assert "Last scan" not in content
    # Headline band absent: its reclaimable figure (binsize of 5000) does not render.
    assert "4.9 KiB" not in content
