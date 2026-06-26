from datetime import datetime
from typing import TypedDict

from django.utils.timesince import timesince, timeuntil

from scanner.models import Blob, Scan

# Blab.Status display label and classes.
# The next line is a hack to make sure tailwind picks up these class names.
# class="badge-success badge-warning badge-info badge-neutral"
STATUS_VOCAB: dict[str, dict[str, str]] = {
    Blob.Status.RECLAIMABLE: {"label": "Reclaimable", "badge": "badge-success"},
    Blob.Status.SEEDING_HOLD: {"label": "Seeding hold", "badge": "badge-warning"},
    Blob.Status.IN_LIBRARY: {"label": "In library", "badge": "badge-info"},
    Blob.Status.IN_PROGRESS: {"label": "In progress", "badge": "badge-neutral"},
}


# Background color class per Blob.Status, used by the headline band.
# The next line makes sure tailwind picks up these class names.
# class="bg-success bg-warning bg-info bg-neutral"
STATUS_BAR_CLASS: dict[str, str] = {
    Blob.Status.RECLAIMABLE: "bg-success",
    Blob.Status.SEEDING_HOLD: "bg-warning",
    Blob.Status.IN_LIBRARY: "bg-info",
    Blob.Status.IN_PROGRESS: "bg-neutral",
}


# Flag display label and tooltip, in the order flags should be rendered. Each entry is
# (flag attribute on Blob, short label, tooltip explanation).
FLAG_VOCAB: list[tuple[str, str, str]] = [
    (
        "cross_seed",
        "cross-seed",
        "Served by more than one torrent",
    ),
    (
        "multi_link",
        "multi-link",
        "Has more than one hard link in the same tree",
    ),
    (
        "partial_torrent",
        "partial-torrent",
        "Owning torrent has blobs of mixed status",
    ),
    (
        "seedable_idle",
        "seedable-idle",
        "In library and torrents trees but not seeding",
    ),
    (
        "links_outside_scope",
        "outside-scope",
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

    Each segment covers one Blob.Status, in vocabulary order (reclaimable, seeding_hold,
    in_library, in_progress), sized by that status's total bytes in the scan. Percentages
    are whole numbers of the total bytes across all statuses. Zero total yields 0 percent
    everywhere (no division by zero).

    :param scan: the scan whose summary_totals drive the segments
    """
    by_status = (scan.summary_totals or {}).get("by_status", {})
    totals = {key: (by_status.get(key) or {}).get("bytes", 0) for key in STATUS_VOCAB}
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
