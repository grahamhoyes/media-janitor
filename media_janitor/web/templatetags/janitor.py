from django import template
from django.core.paginator import Page

from scanner.models import Blob, Scan
from web import display

register = template.Library()

# Expose display helpers as filters. The vocabulary and logic live in web.display
# so views and tests can import them without the template layer.
register.filter("active_flags", display.active_flags)
register.filter("since", display.since)
register.filter("until", display.until)
register.filter("duration", display.duration)


@register.inclusion_tag("media_janitor/fragments/sort_header.html")
def sort_header(
    sort_state: display.SortColumn,
    label: str,
    hx_target: str | None = None,
    th_class: str = "",
) -> dict[str, object]:
    """
    Render one sortable table header cell

    The link points at the column's next sort action (next_sort / next_dir from the view),
    resetting the page. When hx_target is set the click swaps that target over HTMX,
    otherwise it is a plain full-page GET. The direction indicator only shows for the active
    column (when sort_state.dir is not empty).

    :param sort_state: the column's sorting state
    :param label: the visible header text
    :param hx_target: optional CSS selector for an HTMX swap target. Vanilla link when None
    :param th_class: optional extra classes for the th, e.g. width or alignment
    """
    return {
        "sort_state": sort_state,
        "label": label,
        "hx_target": hx_target,
        "th_class": th_class,
    }


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


@register.inclusion_tag("media_janitor/fragments/pagination_controls.html")
def pagination_controls(
    page: Page,
    hx_target: str | None = None,
) -> dict:
    """
    Render generic pagination controls

    The template builds every link with the builtin querystring tag, preserving the current
    query params (sort, page size, filters) and only overriding the page number, so the
    control works with no JavaScript. When hx_target is given, the links also carry hx-*
    attributes so a click swaps that target's contents in place instead of reloading.

    :param page: the paginator Page instance
    :param hx_target: optional CSS selector for an HTMX swap target; plain links when None
    """
    items: list[dict] = []
    for entry in page.paginator.get_elided_page_range(page.number, on_each_side=2, on_ends=1):
        if entry == page.paginator.ELLIPSIS:
            items.append({"ellipsis": True, "label": entry})
        else:
            items.append({"number": entry, "current": entry == page.number})

    return {
        "page": page,
        "hx_target": hx_target,
        "items": items,
        "prev_number": page.previous_page_number() if page.has_previous() else None,
        "next_number": page.next_page_number() if page.has_next() else None,
    }
