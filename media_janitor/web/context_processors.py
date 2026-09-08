from datetime import timedelta
from typing import Any

from django.http import HttpRequest
from django.utils import timezone

from scanner.models import Scan
from scanner.tasks import is_scan_in_progress

# How recently a scan must have finished for a page load to show a success message.
# The header will show a message for 5s; see the scan_just_completed section in base.html.
JUST_COMPLETED_WINDOW = timedelta(seconds=10)


def current_scan(request: HttpRequest) -> dict[str, Any]:
    """
    Expose scan state to every template

    `current_scan` is the latest complete scan (or None). `scan_in_progress` reports
    whether a scan is queued or running. `scan_just_completed` reports whether it
    finished recently enough to flash the header green.
    """
    scan = Scan.current()

    return {
        "current_scan": scan,
        "scan_in_progress": is_scan_in_progress() if request.user.is_authenticated else False,
        "scan_just_completed": bool(
            scan and scan.finished_at and timezone.now() - scan.finished_at < JUST_COMPLETED_WINDOW
        ),
    }
