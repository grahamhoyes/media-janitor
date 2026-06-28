from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import render
from django.utils import timezone

from scanner.models import Blob, Scan
from web import display

DEFAULT_PAGE_SIZE = 100


def _coerce_page_size(raw: str | None) -> int:
    """
    Resolve the page size query param, falling back to the default

    Anything that is not a positive integer falls back to DEFAULT_PAGE_SIZE.

    :param raw: the raw page_size query value, or None when absent
    """
    try:
        value = int(raw)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return DEFAULT_PAGE_SIZE
    return value if value > 0 else DEFAULT_PAGE_SIZE


@login_required
def reclaim_list(request):
    """
    Dense table of the current scan's blobs, sorted by size descending

    Returns the full page on a normal request and only the table fragment on an HTMX
    request, so filter/sort/page controls can swap the table alone.

    :param request: the incoming request
    """
    scan = Scan.current()

    if scan is None:
        return render(request, "media_janitor/reclaim.html")

    page_size = _coerce_page_size(request.GET.get("page_size"))

    blobs = scan.blobs.order_by("-size", "pk").prefetch_related("links")

    paginator = Paginator[Blob](blobs, page_size)
    # get_page() is forgiving: invalid or out-of-range pages return the first or last page
    page_obj = paginator.get_page(request.GET.get("page"))

    for blob in page_obj:
        # Pick a deterministic display link (lowest path) over the prefetched links so
        # rendering a row issues no extra queries.
        links = sorted(blob.links.all(), key=lambda link: link.path)
        # Attached for the template only, the model has no such fields
        blob.sorted_links = links  # type: ignore[attr-defined]

    context = {
        "page_obj": page_obj,
    }

    template = (
        "media_janitor/fragments/reclaim_table.html"
        if request.htmx
        else "media_janitor/reclaim.html"
    )
    return render(request, template, context)


@login_required
def dashboard(request):
    """Overview of the current scan: stats cards, scan info, and a per-status breakdown"""

    # We don't need to pass the scan as context since all views have current_scan
    # via a context preprocessor
    scan = Scan.current()

    if scan is None:
        return render(request, "media_janitor/dashboard.html")

    return render(
        request,
        "media_janitor/dashboard.html",
        {
            "totals": display.dashboard_totals(scan),
            "torrent_count": scan.torrents.count(),
            "breakdown": display.status_breakdown(scan),
        },
    )


@login_required
def ping(request):
    """Tiny HTMX endpoint used by the scaffold to confirm partial swaps work."""
    return render(
        request,
        "media_janitor/fragments/ping.html",
        {"now": timezone.now()},
    )
