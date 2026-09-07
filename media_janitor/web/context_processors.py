from typing import Any

from django.http import HttpRequest

from scanner.models import Scan
from scanner.tasks import is_scan_in_progress


def current_scan(request: HttpRequest) -> dict[str, Any]:
    """
    Expose scan state to every template

    `current_scan` is the latest complete scan (or None). `scan_in_progress` reports
    whether a scan is queued or running.
    """
    return {
        "current_scan": Scan.current(),
        "scan_in_progress": is_scan_in_progress() if request.user.is_authenticated else False,
    }
