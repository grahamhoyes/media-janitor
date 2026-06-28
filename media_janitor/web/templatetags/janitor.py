from django import template
from django.core.paginator import Page
from django.template import Context
from django.urls import reverse

from scanner.models import Blob, Scan
from web import display

register = template.Library()

# Expose display helpers as filters. The vocabulary and logic live in web.display
# so views and tests can import them without the template layer.
register.filter("active_flags", display.active_flags)
register.filter("since", display.since)
register.filter("until", display.until)
register.filter("duration", display.duration)


# Binary unit suffixes, ordered smallest to largest
_BINARY_UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")


@register.filter
def binsize(value: int | float | None) -> str:
    """
    Format a byte count as binary units (B, KiB, MiB, ...), to one decimal place

    None or 0 renders as "0 B". Otherwise, the value is divided by 1024 until it is below
    1024 or the largest unit (PiB) is reached.

    This is similar to Django's builtin `filesizeformat` filter, but it uses SI-accurate
    *iB variants, since we're dealing with powers of 1024.

    :param value: number of bytes
    """
    if not value:
        return "0 B"

    n = float(value)
    i = 0
    while n >= 1024 and i < len(_BINARY_UNITS) - 1:
        n /= 1024
        i += 1
    return f"{n:.1f} {_BINARY_UNITS[i]}"


@register.inclusion_tag("media_janitor/fragments/status_badge.html")
def status_badge(blob: Blob) -> dict[str, str]:
    """
    Render the status badge for a blob

    :param blob: the blob whose status to render
    """
    return {
        "label": display.status_label(blob.status),
        "badge": display.status_badge_class(blob.status),
    }


@register.inclusion_tag("media_janitor/fragments/headline_band.html")
def headline_band(scan: Scan) -> dict[str, object]:
    """
    Render the headline band for a scan

    Shows the scan's reclaimable byte total and a proportional bar of all space by
    status. Callers must only invoke this when a scan exists.

    :param scan: the current scan to summarize
    """
    return {
        "reclaimable_bytes": scan.reclaimable_bytes,
        "segments": display.headline_segments(scan),
    }


@register.inclusion_tag("media_janitor/fragments/pagination_controls.html", takes_context=True)
def pagination_controls(
    context: Context,
    page: Page,
    page_param="page",
    size_param="page_size",
    route_name: str | None = None,
) -> dict:
    """
    Render pagination controls

    :param context: Request context, automatically inserted
    :param page: Page object
    :param page_param: Optional, URL query param containing the page number
    :param size_param: Optional, URL query param containing the page size
    :param route_name: Optional, URL route name. Defaults to the request URL.
    """
    request = context["request"]

    if route_name:
        url = reverse(route_name)
    else:
        url = request.path

    return {
        "page": page,
        "page_size": request.GET.get(size_param),
        "page_param": page_param,
        "size_param": size_param,
        "page_range": page.paginator.get_elided_page_range(page.number, on_each_side=2, on_ends=1),
        "url": url,
    }
