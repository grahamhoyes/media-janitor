import pytest
from django.urls import reverse
from django_tasks.base import TaskResultStatus
from django_tasks_db.models import DBTaskResult

from scanner.tasks import is_scan_in_progress
from scanner.tasks import scan as scan_task


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
