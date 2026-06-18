"""
Background tasks for the scanner.
"""

from django.tasks import task

from scanner.pipeline.orchestrator import run_scan


@task
def scan() -> None:
    """Run a full-share scan and publish a snapshot"""
    run_scan()
