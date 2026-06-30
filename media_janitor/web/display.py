from datetime import UTC, datetime, timedelta
from typing import TypedDict

from django.utils.timesince import timesince, timeuntil

from scanner.models import Blob, Scan

# Blab.Status display label and classes.
# The next line is a hack to make sure tailwind picks up these class names.
# class="badge-success badge-secondary badge-warning badge-info badge-neutral"
STATUS_VOCAB: dict[str, dict[str, str]] = {
    Blob.Status.RECLAIMABLE: {"label": "Reclaimable", "badge": "badge-success"},
    Blob.Status.LINKED_EXTERNALLY: {"label": "Linked externally", "badge": "badge-secondary"},
    Blob.Status.SEEDING_HOLD: {"label": "Seeding hold", "badge": "badge-warning"},
    Blob.Status.IN_LIBRARY: {"label": "In library", "badge": "badge-info"},
    Blob.Status.IN_PROGRESS: {"label": "In progress", "badge": "badge-neutral"},
}


# Background color class per Blob.Status, used by the headline band.
# The next line makes sure tailwind picks up these class names.
# class="bg-success bg-secondary bg-warning bg-info bg-neutral"
STATUS_BAR_CLASS: dict[str, str] = {
    Blob.Status.RECLAIMABLE: "bg-success",
    Blob.Status.LINKED_EXTERNALLY: "bg-secondary",
    Blob.Status.SEEDING_HOLD: "bg-warning",
    Blob.Status.IN_LIBRARY: "bg-info",
    Blob.Status.IN_PROGRESS: "bg-neutral",
}


# Flag display label and tooltip, in the order flags should be rendered. Each entry is
# (flag attribute on Blob, short label, tooltip explanation).
FLAG_VOCAB: list[tuple[str, str, str]] = [
    (
        "cross_seed",
        "Cross seed",
        "Served by more than one torrent",
    ),
    (
        "multi_link",
        "Multi link",
        "Has more than one hard link in the same tree",
    ),
    (
        "partial_torrent",
        "Partial torrent",
        "Owning torrent has blobs of mixed status",
    ),
    (
        "seedable_idle",
        "Could seed",
        "In library and torrents trees but not seeding",
    ),
    (
        "links_outside_scope",
        "Outside scope",
        "Has links outside the scanned directories",
    ),
]


def status_label(status: str) -> str:
    """Return the display label for a Blob.Status value"""
    return STATUS_VOCAB.get(status, {}).get("label", status)


def status_badge_class(status: str) -> str:
    """Return the DaisyUI badge class for a Blob.Status value"""
    return STATUS_VOCAB.get(status, {}).get("badge", "badge-neutral")


def active_flags(blob: Blob) -> list[tuple[str, str]]:
    """
    Return a blob's active flags as (label, tooltip) pairs, in vocabulary order

    Only flags whose attribute is truthy are included.

    :param blob: the blob whose flags to inspect
    """
    return [(label, tooltip) for attr, label, tooltip in FLAG_VOCAB if getattr(blob, attr)]


# Compact unit labels for the time helpers. %(num)d is the count Django fills in.
# We wrap Django's timesince/timeuntil, with more compact units and removing the
# comma from the separator.
_COMPACT_TIME_STRINGS = {
    "year": "%(num)dy",
    "month": "%(num)dmo",
    "week": "%(num)dw",
    "day": "%(num)dd",
    "hour": "%(num)dh",
    "minute": "%(num)dm",
}


def since(value: datetime | None, now: datetime | None = None) -> str:
    """
    Compact elapsed time since a past datetime, for example "3d 4h"

    Renders the two largest non-zero units. Sub-minute durations render as "0m".

    :param value: a datetime in the past
    :param now: current timestamp, optional to use real now
    """
    if value is None:
        return "-"
    return timesince(value, now=now, time_strings=_COMPACT_TIME_STRINGS).replace(", ", " ")


def until(value: datetime | None, now: datetime | None = None) -> str:
    """
    Compact remaining time until a future datetime, for example "5d 2h"

    Renders the two largest non-zero units. Sub-minute durations render as "0m".

    :param value: a datetime in the future
    :param now: current timestamp, optional to use real now
    """
    if value is None:
        return "-"
    return timeuntil(value, now=now, time_strings=_COMPACT_TIME_STRINGS).replace(", ", " ")


def duration(value: timedelta | None) -> str:
    """
    Compact duration, for example "3d 4h"

    Renders the two largest non-zero units. Sub-minute durations render as "0m".

    :param value: a timedelta
    """
    if value is None:
        return "-"

    now = datetime.now(UTC)
    return timeuntil(now + value, now, time_strings=_COMPACT_TIME_STRINGS).replace(", ", " ")


class StatusRow(TypedDict):
    key: str
    label: str
    badge: str
    count: int
    bytes: int


def status_breakdown(scan: Scan) -> list[StatusRow]:
    """
    Build the per-status breakdown rows for a scan, in vocabulary order.

    One row per Blob.Status, reading each status's count and bytes from the
    scan's status_totals. Missing or empty totals default to zero.

    :param scan: the scan whose status_totals drive the rows
    """
    status_totals = scan.status_totals or {}
    rows: list[StatusRow] = []
    for key, vocab in STATUS_VOCAB.items():
        entry = status_totals.get(key) or {}
        rows.append(
            {
                "key": key,
                "label": vocab["label"],
                "badge": vocab["badge"],
                "count": entry.get("count", 0),
                "bytes": entry.get("bytes", 0),
            }
        )
    return rows


class DashboardTotals(TypedDict):
    total_bytes: int
    total_count: int
    reclaimable_bytes: int


def dashboard_totals(scan: Scan) -> DashboardTotals:
    """
    Build the aggregate totals shown on the dashboard stats cards.

    total_bytes and total_count sum across every status in the scan's status_totals.
    reclaimable_bytes is the scan's reclaimable bucket. Missing or empty totals default
    to zero.

    :param scan: the scan whose status_totals drive the totals
    """
    status_totals = scan.status_totals or {}
    total_bytes = 0
    total_count = 0
    for key in STATUS_VOCAB:
        entry = status_totals.get(key) or {}
        total_bytes += entry.get("bytes", 0)
        total_count += entry.get("count", 0)
    return {
        "total_bytes": total_bytes,
        "total_count": total_count,
        "reclaimable_bytes": scan.reclaimable_bytes,
    }


class SortColumn(TypedDict):
    """
    The state of a sortable column in a table

    next_sort and next_dir are the sort and dir query params this column's next
    click should request. Both None means the next click clears the sort, dropping
    those params from the link (matching Django's querystring semantics, where None
    removes a param) and returning to the default ordering.
    """

    dir: str
    "Current sort direction, or empty string when not sorted on this column."
    next_sort: str | None
    """
    Next field to sort on, or None to stop sorting (removing the param from the link).

    Typically this will be the column in question, unless clearing the sort.
    """
    next_dir: str | None
    """
    Next direction to sort this column on, or None to clear the sort (removing the
    param from the link).
    """


class HeadlineSegment(TypedDict):
    key: str
    label: str
    bytes: int
    pct: float
    bar_class: str
    dot_class: str


def headline_segments(scan: Scan) -> list[HeadlineSegment]:
    """
    Build the ordered segments for the headline band's proportional bar.

    For example:

    +----- 20% ----+----- 19% ----+-------------- 42% --------------+---- 19% -----+
    | Reclaimable  | Seeding Hold |           In Library            | In Progress  |
    +--------------+--------------+---------------------------------+--------------+

    Each segment covers one Blob.Status, in vocabulary order (reclaimable,
    linked_externally, seeding_hold, in_library, in_progress), sized by that status's
    total bytes in the scan. Percentages are whole numbers of the total bytes across all
    statuses. Zero total yields 0 percent everywhere (no division by zero).

    :param scan: the scan whose status_totals drive the segments
    """
    status_totals = scan.status_totals or {}
    totals = {key: (status_totals.get(key) or {}).get("bytes", 0) for key in STATUS_VOCAB}
    grand_total = sum(totals.values())

    segments: list[HeadlineSegment] = []
    for key, vocab in STATUS_VOCAB.items():
        size = totals[key]
        pct = round(size / grand_total * 100, 2) if grand_total else 0.0
        segments.append(
            {
                "key": key,
                "label": vocab["label"],
                "bytes": size,
                "pct": pct,
                "bar_class": STATUS_BAR_CLASS[key],
                "dot_class": STATUS_BAR_CLASS[key],
            }
        )
    return segments
