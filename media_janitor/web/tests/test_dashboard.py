import pytest
from django.urls import reverse

from scanner.models import Scan
from web.display import dashboard_totals, status_breakdown
from web.tests.factories import make_complete_scan, make_scan

# -- status_breakdown -----------------------------------------------------------


@pytest.mark.django_db
def test_status_breakdown_ordering_and_values():
    scan = make_complete_scan()
    rows = status_breakdown(scan)

    assert [row["key"] for row in rows] == [
        "reclaimable",
        "linked_externally",
        "seeding_hold",
        "in_library",
        "in_progress",
    ]
    assert [row["label"] for row in rows] == [
        "Reclaimable",
        "Linked Externally",
        "Seeding Hold",
        "In Library",
        "In Progress",
    ]

    by_key = {row["key"]: row for row in rows}
    assert by_key["reclaimable"]["count"] == 1
    assert by_key["reclaimable"]["bytes"] == 6000
    assert by_key["reclaimable"]["badge"] == "badge-success"
    assert by_key["linked_externally"]["bytes"] == 3000
    assert by_key["linked_externally"]["badge"] == "badge-secondary"
    assert by_key["seeding_hold"]["bytes"] == 4000
    assert by_key["in_library"]["bytes"] == 2000
    assert by_key["in_progress"]["bytes"] == 1000


def test_status_breakdown_empty_status_totals():
    # Built in memory: status_breakdown only reads status_totals
    scan = Scan(status_totals={})
    rows = status_breakdown(scan)
    assert len(rows) == 5
    assert all(row["count"] == 0 for row in rows)
    assert all(row["bytes"] == 0 for row in rows)


def test_status_breakdown_missing_keys_default_to_zero():
    scan = Scan(status_totals={"reclaimable": {"bytes": 100}})
    rows = status_breakdown(scan)
    by_key = {row["key"]: row for row in rows}
    assert by_key["reclaimable"]["bytes"] == 100
    assert by_key["reclaimable"]["count"] == 0
    assert by_key["seeding_hold"]["bytes"] == 0


# -- dashboard_totals -----------------------------------------------------------


@pytest.mark.django_db
def test_dashboard_totals_math():
    scan = make_complete_scan()
    totals = dashboard_totals(scan)

    # Sum of all status bytes / counts
    assert totals["total_bytes"] == 16000
    assert totals["total_count"] == 5
    assert totals["reclaimable_bytes"] == 6000


def test_dashboard_totals_empty_status_totals():
    scan = Scan(status_totals={})
    totals = dashboard_totals(scan)
    assert totals == {
        "total_bytes": 0,
        "total_count": 0,
        "reclaimable_bytes": 0,
    }


# -- view -----------------------------------------------------------------------


@pytest.fixture
def logged_in_client(client, django_user_model):
    user = django_user_model.objects.create_user(username="tester", password="pw")
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_dashboard_shows_scan_totals(logged_in_client):
    make_complete_scan()
    response = logged_in_client.get(reverse("dashboard"))
    assert response.status_code == 200

    content = response.content.decode()
    # Total content size: 16000 bytes
    assert "15.6 KiB" in content
    # Reclaimable: 6000 bytes
    assert "5.9 KiB" in content
    # Blob count (5) and torrent count (1) rendered as stat values
    assert '<div class="stat-value text-2xl">5</div>' in content
    assert '<div class="stat-value text-2xl">1</div>' in content


@pytest.mark.django_db
def test_dashboard_shows_status_breakdown(logged_in_client):
    make_complete_scan()
    content = logged_in_client.get(reverse("dashboard")).content.decode()
    for label in (
        "Reclaimable",
        "Linked Externally",
        "Seeding Hold",
        "In Library",
        "In Progress",
    ):
        assert label in content
    # Per-status sizes render
    assert "2.9 KiB" in content  # linked_externally 3000
    assert "3.9 KiB" in content  # seeding_hold 4000
    assert "2.0 KiB" in content  # in_library 2000


@pytest.mark.django_db
def test_dashboard_shows_scan_info(logged_in_client):
    make_complete_scan()
    content = logged_in_client.get(reverse("dashboard")).content.decode()
    # Seeding requirement and quarantine window from the scan
    assert "14 days / 1.0 ratio" in content
    assert "30m" in content


@pytest.mark.django_db
def test_dashboard_no_scan_renders_empty_state(logged_in_client):
    make_scan(status=Scan.Status.RUNNING)
    make_scan(status=Scan.Status.FAILED)

    response = logged_in_client.get(reverse("dashboard"))
    assert response.status_code == 200

    content = response.content.decode()
    assert "No completed scan yet" in content
    assert "enqueue_scan" in content


@pytest.mark.django_db
def test_dashboard_query_count(logged_in_client, django_assert_num_queries):
    make_complete_scan()
    # Session/auth lookups, the context processor's Scan.current(), the view's
    # Scan.current(), and the torrent count. Locked in to catch N+1 regressions.
    with django_assert_num_queries(5):
        logged_in_client.get(reverse("dashboard"))
