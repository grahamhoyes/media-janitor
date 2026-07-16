from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from typing import ClassVar, Literal

from django.db.models import Q, QuerySet
from django.http import QueryDict

from scanner.models import Kind, TorrentState
from web.display import FLAG_VOCAB, RECLAIM_STATE_VOCAB, STATUS_VOCAB, SortColumn, status_label

type ApplyStrategy = Callable[[QuerySet, set[str]], QuerySet]


@dataclass(frozen=True)
class SortField:
    """
    A sortable field definition

    Different from SortColumn, which is the per-request header state passed to the templates
    and depends on the active sort.

    :param field: queryset field or annotation to order by
    :param default_dir: direction ("asc" or "desc") applied when the column is first sorted
    """

    field: str
    default_dir: Literal["asc", "desc"]


@dataclass(frozen=True, slots=True)
class FilterOption:
    """
    A value in a filter group

    value, label, and btn are declared statically. active and url are empty
    on the declaration and filled in per request by FilterSet before the option is rendered.
    """

    value: str
    "The value this option filters for"
    label: str
    "Human-readable label shown on the option"
    # The next line makes sure tailwind picks up the class names
    # class="btn-primary"
    btn: str = "btn-primary"
    "DaisyUI button color class applied when the option is active (chips widget only)"

    # Filled in per request by FilterSet._prepare_group
    active: bool = False
    "Whether this option's value is currently selected"
    url: str = ""
    """
    Querystring this option's link should request: the current query params with this
    option's value toggled in or out of its param and the page cleared.
    """


@dataclass(frozen=True)
class FilterGroup:
    """
    A multi-value query param filter

    Static apart from `options`, whose active/url fields are filled in per request.
    """

    param: str
    "The query param this group reads and writes"
    label: str
    "Group label shown before its options in the filter bar"
    tooltip: str
    "Hover text explaining the group's filter semantics"
    options: tuple[FilterOption, ...]
    "The ordered options (values to filter by)"
    apply: ApplyStrategy
    "Filters a queryset by the selected values. Only called when at least one is selected."
    widget: Literal["chips", "dropdown"] = "chips"
    """
    Filter bar rendering: "chips" for a joined button group, "dropdown" for a checkbox
    dropdown (better for long lists of options).
    """

    @property
    def selected_count(self) -> int:
        """How many of the group's options are currently selected"""
        return sum(option.active for option in self.options)


def in_field(field: str) -> ApplyStrategy:
    """
    Apply strategy: the field must equal one of the selected values (ORed)

    :param field: the model field the values filter against
    """

    def apply(qs: QuerySet, selected: set[str]) -> QuerySet:
        return qs.filter(**{f"{field}__in": selected})

    return apply


def all_selected() -> ApplyStrategy:
    """Apply strategy: each selected value names a boolean field that must be True (ANDed)"""

    def apply(qs: QuerySet, selected: set[str]) -> QuerySet:
        return qs.filter(**{flag: True for flag in selected})

    return apply


def boolean_field(field: str, true_value: str) -> ApplyStrategy:
    """
    Apply strategy: two strings options that map to a boolean field

    Selecting one option filters to rows where the field matches it. Selecting
    both applies no narrowing (ORing both is the same as applying neither,
    and ANDing both boolean options makes no sense).

    :param field: the boolean model field the pair filters against
    :param true_value: the option value that maps to the field being True
    """

    def apply(qs: QuerySet, selected: set[str]) -> QuerySet:
        if len(selected) == 1:
            return qs.filter(**{field: true_value in selected})
        return qs

    return apply


class FilterSet(ABC):
    """
    Parses, applies, and renders the filters, text search, and sort for one list view

    Subclasses must:
        - Declare `groups`: The filter definitions
        - Declare `sorts`: The sortable query params, mapped to a SortField definition
        - Declare `default_sort`: The sort key applied when none is requested
        - Implement `search`: Applies a search string
    """

    groups: ClassVar[tuple[FilterGroup, ...]]
    # Sortable query params, mapped to the queryset field (or annotation) and default
    # direction they order by
    sorts: ClassVar[dict[str, SortField]]
    # Default sort key, applied when no valid sort is requested
    default_sort: ClassVar[str]

    def __init__(self, params: QueryDict) -> None:
        """
        :param params: a request QueryDict (request.GET)
        """
        self._params = params
        # Map valid query parameters to their values (values are a set, since filters
        # can have multiple values)
        self.active_options: dict[str, set[str]] = {}
        for group in self.groups:
            valid_values = {option.value for option in group.options}
            self.active_options[group.param] = {
                value for value in params.getlist(group.param) if value in valid_values
            }
        self.q = (params.get("q") or "").strip()
        # Current sort/dir, None when nothing valid is requested or unsorted.
        self.sort, self.direction = self._resolve_sort()

    @abstractmethod
    def search(self, qs: QuerySet, q: str) -> QuerySet:
        """
        Filter a queryset by the text search term

        :param qs: the queryset to search
        :param q: the non-empty, stripped search term
        """
        raise NotImplementedError

    def apply(self, qs: QuerySet) -> QuerySet:
        """
        Filter a queryset by every active filter group and the text search (ANDed)

        Groups with no selected values apply no filtering.

        :param qs: the queryset to narrow
        """
        for group in self.groups:
            selected = self.active_options[group.param]
            if selected:
                qs = group.apply(qs, selected)
        if self.q:
            qs = self.search(qs, self.q)
        return qs

    def _resolve_sort(self) -> tuple[str | None, str | None]:
        """
        Resolve the active (sort, dir) pair from the query params

        Returns (None, None) when no valid sort is requested. A valid sort key with a
        missing or malformed direction falls back to that column's default direction.
        """
        sort = self._params.get("sort")
        if not sort or sort not in self.sorts:
            return None, None

        direction = self._params.get("dir")
        if direction not in ("asc", "desc"):
            direction = self.sorts[sort].default_dir

        return sort, direction

    @property
    def default_dir(self) -> str:
        """Direction used for the default (unsorted) ordering"""
        return self.sorts[self.default_sort].default_dir

    @property
    def active_sort(self) -> str:
        """The sort key to order by, falling back to the default when none is requested"""
        return self.sort or self.default_sort

    @property
    def active_dir(self) -> str:
        """The direction to order by, falling back to the default when none is requested"""
        return self.direction or self.default_dir

    def order(self, qs: QuerySet) -> QuerySet:
        """
        Order a queryset by the active sort column, tie-breaking on pk so pagination is stable

        The queryset must already carry any annotation the active sort's SortField references.

        :param qs: the queryset to order
        """
        field = self.sorts[self.active_sort].field
        prefix = "-" if self.active_dir == "desc" else ""
        return qs.order_by(f"{prefix}{field}", "pk")

    def sort_columns(self) -> dict[str, SortColumn]:
        """
        Build the three-state header state for each sortable column

        Clicking a column cycles none -> default direction -> other direction -> none. Each
        column records its current direction (dir, empty when the column is not the active
        sort, which also drives whether the indicator shows), and the (next_sort, next_dir)
        its header link should request next. The clearing step emits None for both so the
        querystring tag drops the params, returning to the default ordering.
        """
        columns: dict[str, SortColumn] = {}
        for key, spec in self.sorts.items():
            default_dir = spec.default_dir
            other_dir = "asc" if default_dir == "desc" else "desc"
            active = key == self.sort

            next_sort: str | None
            next_dir: str | None
            if not active:
                # Not sorted, so sort by the default direction
                next_sort, next_dir = key, default_dir
            elif self.direction == default_dir:
                # Sorted by the default direction, so sort the other way
                next_sort, next_dir = key, other_dir
            else:
                # Sorted by the second direction, so clear the sort. None drops the
                # sort/dir params from the header link, returning to the default ordering.
                next_sort, next_dir = None, None

            columns[key] = {
                "dir": (self.direction or "") if active else "",
                "next_sort": next_sort,
                "next_dir": next_dir,
            }
        return columns

    @property
    def any_active(self) -> bool:
        """Whether any filter group or the text search is currently applied"""
        return bool(self.q) or any(self.active_options.values())

    @property
    def clear_url(self) -> str:
        """Querystring clearing every filter param and the search, keeping sort and page size"""
        overrides: dict[str, list[str] | None] = {group.param: None for group in self.groups}
        overrides["q"] = None
        return self._build_url(overrides)

    def prepared_groups(self) -> list[FilterGroup]:
        """Build the groups for the filter bar template, with options filled in"""
        return [self._prepare_group(group) for group in self.groups]

    def _prepare_group(self, group: FilterGroup) -> FilterGroup:
        """
        Return a copy of the group with each option's active flag and toggle URL filled in

        Toggled selections keep the order of the group's options so the resulting
        querystring is stable.

        :param group: the group whose options to prepare
        """
        active_values = self.active_options[group.param]
        all_values = [option.value for option in group.options]
        prepared: list[FilterOption] = []
        for option in group.options:
            active = option.value in active_values
            # Current values with this value toggled (removed if it's active, added if it's not)
            toggled = active_values - {option.value} if active else active_values | {option.value}
            next_values = [value for value in all_values if value in toggled]
            prepared.append(
                dataclass_replace(
                    option, active=active, url=self._build_url({group.param: next_values})
                )
            )
        return dataclass_replace(group, options=tuple(prepared))

    def _build_url(self, overrides: dict[str, list[str] | None]) -> str:
        """
        Build a querystring with params in `overrides` replaced

        Every other query param is preserved. The page param is always dropped since a
        filter change invalidates the page number. An override of None or [] removes that
        param entirely.

        :param overrides: param name to its full new value list, or None to remove it
        """
        query = self._params.copy()
        # Always remove the page, since changing filters should bring us back to the first page
        query.pop("page", None)
        for param, values in overrides.items():
            query.pop(param, None)
            if values:
                query.setlist(param, values)
        return f"?{query.urlencode()}"


class BlobFilters(FilterSet):
    """Filters for the reclaim list's blobs"""

    # The name and status columns order by annotations the reclaim view attaches only when
    # that sort is active (display_name, status_order)
    sorts = {
        "size": SortField("size", "desc"),
        "name": SortField("display_name", "asc"),
        "status": SortField("status_order", "asc"),
    }
    default_sort = "size"

    groups = (
        FilterGroup(
            param="status",
            label="Status",
            tooltip="File status (ORed: shows any of the selected statuses)",
            options=tuple(
                FilterOption(
                    value=key,
                    label=status_label(key),
                    btn=vocab["btn"],
                )
                for key, vocab in STATUS_VOCAB.items()
            ),
            apply=in_field("status"),
        ),
        FilterGroup(
            param="kind",
            label="Kind",
            tooltip="File kind (ORed: shows any of the selected kinds)",
            options=tuple(FilterOption(value=value, label=label) for value, label in Kind.choices),
            apply=in_field("kind"),
        ),
        FilterGroup(
            param="flag",
            label="Flags",
            tooltip="File flags (ANDed: shows files with all of the selected flags)",
            options=tuple(
                FilterOption(value=attr, label=props["label"]) for attr, props in FLAG_VOCAB.items()
            ),
            apply=all_selected(),
        ),
        FilterGroup(
            param="torrent",
            label="Torrent",
            tooltip=(
                "Whether the file belongs to a torrent "
                "(ORed: shows files of either selected option)"
            ),
            options=(
                FilterOption(value="tracked", label="Tracked"),
                FilterOption(value="untracked", label="Untracked"),
            ),
            apply=boolean_field(field="torrent_tracked", true_value="tracked"),
        ),
    )

    def search(self, qs: QuerySet, q: str) -> QuerySet:
        """
        Match a link name or path case-insensitively

        The search joins against links, so distinct() is required to avoid duplicate
        blobs for blobs that have multiple links.

        :param qs: the blob queryset to narrow
        :param q: the non-empty, stripped search term
        """
        return qs.filter(Q(links__name__icontains=q) | Q(links__path__icontains=q)).distinct()


class TorrentFilters(FilterSet):
    """Filters for the torrents list"""

    sorts = {
        "reclaimable": SortField("bytes_reclaimable_if_removed", "desc"),
        "name": SortField("name", "asc"),
        "size": SortField("size", "desc"),
        "uploaded": SortField("uploaded", "desc"),
        "ratio": SortField("ratio", "desc"),
    }
    default_sort = "reclaimable"

    groups = (
        FilterGroup(
            param="state",
            label="State",
            tooltip="Torrent state (ORed: shows torrents in any of the selected states)",
            options=tuple(
                FilterOption(value=value, label=label) for value, label in TorrentState.choices
            ),
            apply=in_field("state"),
            widget="dropdown",
        ),
        FilterGroup(
            param="reclaim",
            label="Reclaimable",
            tooltip=(
                "Whether removing the torrent reclaims space "
                "(ORed: shows any of the selected options)"
            ),
            options=tuple(
                FilterOption(
                    value=key,
                    label=vocab["short_label"],
                    btn=vocab["btn"],
                )
                for key, vocab in RECLAIM_STATE_VOCAB.items()
            ),
            apply=in_field("reclaim_state"),
        ),
        FilterGroup(
            param="seeding",
            label="Seeding Met",
            tooltip=(
                "Whether seeding requirements have been met (ORed: shows either selected option)"
            ),
            options=(
                FilterOption(value="yes", label="Yes"),
                FilterOption(value="no", label="No"),
            ),
            apply=boolean_field("seeding_met", true_value="yes"),
        ),
    )

    def search(self, qs: QuerySet, q: str) -> QuerySet:
        """
        Match the torrent name case-insensitively

        :param qs: the torrent queryset to narrow
        :param q: the non-empty, stripped search term
        """
        return qs.filter(name__icontains=q)
