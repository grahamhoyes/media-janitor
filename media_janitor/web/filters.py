from typing import TypedDict

from django.db.models import Q, QuerySet
from django.http import QueryDict

from scanner.models import Blob, Kind
from web.display import FLAG_VOCAB, STATUS_VOCAB, status_label


class FilterState(TypedDict):
    """
    The validated, active filter selections parsed from the request query params

    statuses, kinds, and flags hold only values present in their respective vocabularies
    (unknown values are dropped). q is the stripped text search term, empty when absent.
    """

    statuses: set[str]
    kinds: set[str]
    flags: set[str]
    q: str


class FilterChip(TypedDict):
    """
    One toggleable filter chip, with its post-click selection precomputed

    next_values is the list of values for this chip's param after toggling it, in canonical
    vocabulary order, ready to feed the querystring tag. btn is the DaisyUI button color
    class to use when the chip is active (its semantic color for status, btn-primary for
    kinds and flags).
    """

    value: str
    label: str
    btn: str
    active: bool
    next_values: list[str]


def resolve_filters(params: QueryDict) -> FilterState:
    """
    Parse and validate the filter params from a request query dict

    Repeated status / kind / flag params are collected and silently filtered down to known
    vocabulary values, so an unknown value never filters and never errors. q is trimmed.

    :param params: a request QueryDict (request.GET)
    """
    return {
        "statuses": {s for s in params.getlist("status") if s in STATUS_VOCAB},
        "kinds": {k for k in params.getlist("kind") if k in Kind.values},
        "flags": {f for f in params.getlist("flag") if f in FLAG_VOCAB},
        "q": (params.get("q") or "").strip(),
    }


def filters_active(filters: FilterState) -> bool:
    """Whether any filter is currently applied"""
    return bool(filters["statuses"] or filters["kinds"] or filters["flags"] or filters["q"])


def apply_filters(qs: QuerySet[Blob], filters: FilterState) -> QuerySet[Blob]:
    """
    Narrow a blob queryset by the active filters

    Statuses OR within themselves (status__in), kinds OR within themselves, flags AND (each
    selected flag must be True), and the text search matches a link name or path
    case-insensitively. AND across the four filter types. The text search joins the links
    relation, so distinct() collapses the duplicate blob rows it can produce.

    :param qs: the blob queryset to narrow
    :param filters: the validated active filters
    """
    if filters["statuses"]:
        qs = qs.filter(status__in=filters["statuses"])
    if filters["kinds"]:
        qs = qs.filter(kind__in=filters["kinds"])
    for flag in filters["flags"]:
        qs = qs.filter(**{flag: True})
    if filters["q"]:
        qs = qs.filter(
            Q(links__name__icontains=filters["q"]) | Q(links__path__icontains=filters["q"])
        ).distinct()
    return qs


def _build_chips(options: list[tuple[str, str, str]], selected: set[str]) -> list[FilterChip]:
    """
    Build the toggle chips for one filter param

    Each chip records whether its value is currently selected and the precomputed
    next_values list (the selection after toggling this chip), in the canonical order of
    options so the resulting querystring is stable.

    :param options: ordered (value, label, active button class) tuples for this vocabulary
    :param selected: the currently selected values for this param
    """
    all_values = [value for value, _label, _btn in options]
    chips: list[FilterChip] = []
    for value, label, btn in options:
        active = value in selected
        toggled = selected - {value} if active else selected | {value}
        next_values = [v for v in all_values if v in toggled]
        chips.append(
            {
                "value": value,
                "label": label,
                "btn": btn,
                "active": active,
                "next_values": next_values,
            }
        )
    return chips


def status_chips(selected: set[str]) -> list[FilterChip]:
    """Build the status filter chips, colored by the status vocabulary when active"""
    options = [(key, status_label(key), vocab["btn"]) for key, vocab in STATUS_VOCAB.items()]
    return _build_chips(options, selected)


def kind_chips(selected: set[str]) -> list[FilterChip]:
    """Build the kind filter chips, using the primary color when active"""
    # The btn-primary class is compiled via the safelist comment in web.display.
    options = [(value, label, "btn-primary") for value, label in Kind.choices]
    return _build_chips(options, selected)


def flag_chips(selected: set[str]) -> list[FilterChip]:
    """Build the flag filter chips (FLAG_VOCAB order), using the primary color when active"""
    options = [(attr, props["label"], "btn-primary") for attr, props in FLAG_VOCAB.items()]
    return _build_chips(options, selected)
