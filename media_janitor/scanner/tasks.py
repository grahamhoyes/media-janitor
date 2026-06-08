"""
Background tasks for the scanner.
"""

import logging

from django.tasks import task

logger = logging.getLogger(__name__)


@task
def scan() -> str:
    """Placeholder full-share scan task."""
    logger.info("scan task invoked")
    return "scan: noop"
