from django import template

from scanner.models import Blob
from web import display

register = template.Library()

# Expose display helpers as filters. The vocabulary and logic live in web.display
# so views and tests can import them without the template layer.
register.filter("active_flags", display.active_flags)
register.filter("since", display.since)
register.filter("until", display.until)


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
