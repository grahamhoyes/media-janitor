from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from django_tasks.base import TaskResultStatus
from django_tasks_db.models import DBTaskResult

from scanner.tasks import is_scan_in_progress
from scanner.tasks import scan as scan_task
from web.tests.factories import make_scan


@pytest.fixture
def logged_in_client(client, django_user_model):
    user = django_user_model.objects.create_user(username="tester", password="pw")
    client.force_login(user)
    return client


def _set_task_status(status: TaskResultStatus) -> None:
    """Force the single queued task result into a given status"""
    DBTaskResult.objects.update(status=status)


# -- scan_in_progress -----------------------------------------------------------


@pytest.mark.django_db
def test_scan_in_progress_false_with_no_tasks():
    assert is_scan_in_progress() is False


@pytest.mark.django_db
def test_scan_in_progress_true_with_queued_task():
    # A queued (ready) task counts even before the worker picks it up
    scan_task.enqueue()
    assert DBTaskResult.objects.count() == 1
    assert is_scan_in_progress() is True


@pytest.mark.django_db
def test_scan_in_progress_true_with_running_task():
    scan_task.enqueue()
    _set_task_status(TaskResultStatus.RUNNING)
    assert is_scan_in_progress() is True


@pytest.mark.django_db
def test_scan_in_progress_false_when_task_finished():
    scan_task.enqueue()
    _set_task_status(TaskResultStatus.SUCCESSFUL)
    assert is_scan_in_progress() is False


# -- run_scan -------------------------------------------------------------------


@pytest.mark.django_db
def test_run_scan_enqueues_and_returns_spinner(logged_in_client):
    response = logged_in_client.post(reverse("run_scan"))
    assert response.status_code == 200
    # The task was enqueued onto the database queue
    assert DBTaskResult.objects.filter(task_path="scanner.tasks.scan").count() == 1

    content = response.content.decode()
    assert "animate-spin" in content
    # The spinner polls the status endpoint
    assert reverse("scan_indicator") in content


@pytest.mark.django_db
def test_run_scan_rejects_get(logged_in_client):
    response = logged_in_client.get(reverse("run_scan"))
    assert response.status_code == 405


def test_run_scan_requires_login(client):
    response = client.post(reverse("run_scan"))
    assert response.status_code == 302


# -- scan_indicator (poll) --------------------------------------------------------


@pytest.mark.django_db
def test_scan_indicator_spins_while_in_progress(logged_in_client):
    scan_task.enqueue()
    response = logged_in_client.get(reverse("scan_indicator"))
    assert response.status_code == 200
    assert "animate-spin" in response.content.decode()
    assert "HX-Refresh" not in response.headers


@pytest.mark.django_db
def test_scan_indicator_refreshes_when_done(logged_in_client):
    response = logged_in_client.get(reverse("scan_indicator"))
    assert response.status_code == 204
    assert response.headers["HX-Refresh"] == "true"


def test_scan_indicator_requires_login(client):
    response = client.get(reverse("scan_indicator"))
    assert response.status_code == 302


# -- header rendering -----------------------------------------------------------


@pytest.mark.django_db
def test_header_shows_run_button_when_idle(logged_in_client):
    response = logged_in_client.get(reverse("dashboard"))
    content = response.content.decode()
    assert 'aria-label="Run scan"' in content
    assert "animate-spin" not in content


@pytest.mark.django_db
def test_header_shows_spinner_when_scan_in_progress(logged_in_client):
    scan_task.enqueue()
    response = logged_in_client.get(reverse("dashboard"))
    content = response.content.decode()
    assert "animate-spin" in content
    assert 'aria-label="Scan in progress"' in content


@pytest.mark.django_db
def test_header_indicator_hidden_from_anonymous(client):
    scan_task.enqueue()
    response = client.get(reverse("login"))
    content = response.content.decode()
    assert 'aria-label="Run scan"' not in content
    assert 'aria-label="Scan in progress"' not in content


# -- scan_just_completed flash ---------------------------------------------------


def _icon_classes(content: str, element_id: str) -> str:
    """Extract the class attribute value for the given element id"""
    marker = f'id="{element_id}"'
    start = content.index(marker)
    class_start = content.index('class="', start) + len('class="')
    class_end = content.index('"', class_start)
    return content[class_start:class_end]


@pytest.mark.django_db
def test_header_flashes_success_when_scan_just_completed(logged_in_client):
    make_scan(finished_at=timezone.now())
    response = logged_in_client.get(reverse("dashboard"))
    content = response.content.decode()

    assert "hidden" in _icon_classes(content, "scan-icon-idle")
    assert "hidden" not in _icon_classes(content, "scan-icon-success")
    assert "text-success" in _icon_classes(content, "last-scan-text")
    # The revert timer is only emitted while the flash is showing
    assert 'getElementById("last-scan-text")' in content


@pytest.mark.django_db
def test_header_does_not_flash_when_scan_finished_long_ago(logged_in_client):
    make_scan(finished_at=timezone.now() - timedelta(minutes=5))
    response = logged_in_client.get(reverse("dashboard"))
    content = response.content.decode()

    assert "hidden" not in _icon_classes(content, "scan-icon-idle")
    assert "hidden" in _icon_classes(content, "scan-icon-success")
    assert "text-success" not in _icon_classes(content, "last-scan-text")
    assert 'getElementById("last-scan-text")' not in content


@pytest.mark.django_db
def test_header_does_not_flash_when_scan_has_no_finished_at(logged_in_client):
    make_scan(finished_at=None)
    response = logged_in_client.get(reverse("dashboard"))
    content = response.content.decode()

    assert "hidden" in _icon_classes(content, "scan-icon-success")
