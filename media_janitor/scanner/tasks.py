"""
Background tasks for the scanner.
"""

from django.tasks import task
from django_tasks.base import TaskResultStatus
from django_tasks_db.models import DBTaskResult

from scanner.models import Scan
from scanner.pipeline.orchestrator import run_scan


@task
def scan() -> None:
    """Run a full-share scan and publish a snapshot"""
    run_scan()


def is_scan_in_progress() -> bool:
    """
    Whether a scan is queued or currently running

    Checks the database task queue for a scan task that is ready (enqueued, not yet
    picked up) or running. Scans should go through the task queue, but in case one
    is triggered directly in-process we also check for running Scans.
    """
    return (
        DBTaskResult.objects.filter(
            task_path=scan.module_path,
            status__in=[TaskResultStatus.READY, TaskResultStatus.RUNNING],
        ).exists()
        or Scan.objects.filter(status=Scan.Status.RUNNING).exists()
    )
