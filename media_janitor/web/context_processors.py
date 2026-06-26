"""Template context processors for the web app"""

from django.http import HttpRequest

from scanner.models import Scan


def current_scan(request: HttpRequest) -> dict[str, Scan | None]:
    """Expose the latest complete scan to every template as `current_scan`"""
    return {"current_scan": Scan.current()}
