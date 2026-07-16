from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import (
    Case,
    Count,
    IntegerField,
    OuterRef,
    Prefetch,
    QuerySet,
    Subquery,
    Value,
    When,
)
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.generic import View
from django_htmx.middleware import HtmxDetails

from scanner.models import Config, Link, Scan
from web import display
from web.display import SortColumn
from web.filters import BlobFilters, FilterSet, TorrentFilters


class HtmxHttpRequest(HttpRequest):
    """An HttpRequest with the htmx details attached by django-htmx middleware"""

    htmx: HtmxDetails


@dataclass(frozen=True)
class SortField:
    """
    A sortable field definition

    Different from display.SortColumn, which is the state that gets passed to the templates
    and depends on the actual active sort.

    :param field: queryset field or annotation to order by
    :param default_dir: direction ("asc" or "desc") applied when the column is first sorted
    """

    field: str
    default_dir: str


class SortedListView(LoginRequiredMixin, View, ABC):
    """
    Generic view for filtered, sorted, and paginated list views

    Renders the full page on a normal request and only the table fragment on an HTMX
    request, so filter/sort/page controls can swap the table alone.

    Subclasses declare:
        - SORTS: Sort key to its SortField (field/annotation and default direction)
        - DEFAULT_SORT: Default sort key, applied when no valid sort is requested
        - FILTERSET_CLASS: Filterset class
        - PAGE_TEMPLATE: Template for full-page requests
        - FRAGMENT_TEMPLATE: Template for HTMX requests

    Subclasses implement:
        - base_queryset(): Queryset without any filtering applied (for counts)
        - get_queryset(): Queryset with filtering applied
    """

    DEFAULT_PAGE_SIZE = 50

    # Sortable query params, mapped to the queryset field (or annotation) and default direction
    # they order by
    SORTS: ClassVar[dict[str, SortField]]
    DEFAULT_SORT: ClassVar[str]

    FILTERSET_CLASS: ClassVar[type[FilterSet]]

    PAGE_TEMPLATE: ClassVar[str]
    FRAGMENT_TEMPLATE: ClassVar[str]

    @property
    def default_dir(self) -> str:
        """Direction used for the default (unsorted) ordering"""
        return self.SORTS[self.DEFAULT_SORT].default_dir

    def get(self, request: HtmxHttpRequest) -> HttpResponse:
        """
        Render the list (full page) or its table fragment (HTMX request)

        :param request: the incoming request
        """
        scan = Scan.current()

        if scan is None:
            return render(request, self.get_template_name(request))

        page_size = self._coerce_page_size(request.GET.get("page_size"))
        sort, direction = self._resolve_sort(request)
        filterset = self.FILTERSET_CLASS(request.GET)

        # sort/direction are None when nothing valid is requested; fall back to the defaults
        # for building the queryset, but keep the originals for the header/indicator state.
        active_sort = sort or self.DEFAULT_SORT
        active_dir = direction or self.default_dir

        rows = self.get_queryset(scan, active_sort, filterset)
        rows = self._apply_sort(rows, active_sort, active_dir)

        paginator = Paginator(rows, page_size)
        # get_page() is forgiving: invalid or out-of-range pages return the first or last page
        page_obj = paginator.get_page(request.GET.get("page"))

        # matching_count is the filtered set, total_count is every row in the scan.
        # When no filter is active the two are equal, so we skip the extra count query.
        any_filter = filterset.any_active
        matching_count = page_obj.paginator.count
        total_count = self.base_queryset(scan).count() if any_filter else matching_count

        context = {
            "page_obj": page_obj,
            "sort": sort,
            "dir": direction,
            "sort_columns": self._sort_columns(sort, direction),
            "filter_groups": filterset.prepared_groups(),
            "q": filterset.q,
            "any_filter": any_filter,
            "clear_url": filterset.clear_url,
            "matching_count": matching_count,
            "total_count": total_count,
        }

        return render(request, self.get_template_name(request), context)

    def get_template_name(self, request: HtmxHttpRequest) -> str:
        """
        Choose the fragment template for an HTMX request and the full page otherwise

        :param request: the incoming request
        """
        return self.FRAGMENT_TEMPLATE if request.htmx else self.PAGE_TEMPLATE

    @abstractmethod
    def base_queryset(self, scan: Scan) -> QuerySet:
        """
        The scan's unfiltered rows, used for the scan-wide total count

        :param scan: the scan whose rows to list
        """
        raise NotImplementedError

    @abstractmethod
    def get_queryset(self, scan: Scan, sort: str, filterset: FilterSet) -> QuerySet:
        """
        Build the filtered queryset for a scan

        The base view applies the ordering afterwards, so this only needs
        to annotate whatever field the active sort's SortField references.

        :param scan: the scan whose rows to list
        :param sort: a validated key from SORTS
        :param filterset: the parsed filters
        """
        raise NotImplementedError

    def _apply_sort(self, qs: QuerySet, sort: str, direction: str) -> QuerySet:
        """
        Order a queryset by a sort column, tie-breaking on pk so pagination is stable

        :param qs: the queryset to order
        :param sort: a validated key from SORTS
        :param direction: "asc" or "desc"
        """
        field = self.SORTS[sort].field
        prefix = "-" if direction == "desc" else ""
        return qs.order_by(f"{prefix}{field}", "pk")

    def _coerce_page_size(self, raw: str | None) -> int:
        """
        Resolve the page size query param, falling back to the default

        Anything that is not a positive integer falls back to DEFAULT_PAGE_SIZE.

        :param raw: the raw page_size query value, or None when absent
        """
        try:
            value = int(raw)  # type: ignore[arg-type]
        except TypeError, ValueError:
            return self.DEFAULT_PAGE_SIZE
        return value if value > 0 else self.DEFAULT_PAGE_SIZE

    def _resolve_sort(self, request: HttpRequest) -> tuple[str | None, str | None]:
        """
        Resolve the active (sort, dir) pair from the request query params

        Returns (None, None) when no valid sort is requested. A valid sort key with a
        missing or malformed direction falls back to that column's default direction.

        :param request: the incoming request
        """
        sort = request.GET.get("sort")
        if not sort or sort not in self.SORTS:
            return None, None

        direction = request.GET.get("dir")
        if direction not in ("asc", "desc"):
            direction = self.SORTS[sort].default_dir

        return sort, direction

    def _sort_columns(self, sort: str | None, direction: str | None) -> dict[str, SortColumn]:
        """
        Build the three-state header state for each sortable column

        Clicking a column cycles none -> default direction -> other direction -> none. Each
        column records its current direction (dir, empty when the column is not the active
        sort, which also drives whether the indicator shows), and the (next_sort, next_dir)
        its header link should request next. The clearing step emits None for both so the
        querystring tag drops the params, returning to the default ordering.

        :param sort: the active sort key, or None when unsorted
        :param direction: the active sort direction, or None when unsorted
        """
        columns: dict[str, SortColumn] = {}
        for key, spec in self.SORTS.items():
            default_dir = spec.default_dir
            other_dir = "asc" if default_dir == "desc" else "desc"
            active = key == sort

            next_sort: str | None
            next_dir: str | None
            if not active:
                # Not sorted, so sort by the default direction
                next_sort, next_dir = key, default_dir
            elif direction == default_dir:
                # Sorted by the default direction, so sort the other way
                next_sort, next_dir = key, other_dir
            else:
                # Sorted by the second direction, so clear the sort. None drops the
                # sort/dir params from the header link, returning to the default ordering.
                next_sort, next_dir = None, None

            columns[key] = {
                "dir": (direction or "") if active else "",
                "next_sort": next_sort,
                "next_dir": next_dir,
            }
        return columns


class ReclaimListView(SortedListView):
    """Dense table of the current scan's blobs"""

    # Sortable columns, mapped to the queryset field (or annotation) and default direction
    # they order by. The annotation-backed keys (name, status) get their annotation attached
    # only when that sort is active.
    SORTS = {
        "size": SortField("size", "desc"),
        "name": SortField("display_name", "asc"),
        "status": SortField("status_order", "asc"),
    }

    DEFAULT_SORT = "size"

    FILTERSET_CLASS = BlobFilters

    PAGE_TEMPLATE = "media_janitor/reclaim.html"
    FRAGMENT_TEMPLATE = "media_janitor/fragments/reclaim_table.html"

    def _status_order_case(self) -> Case:
        """
        Build a Case expression ranking blobs by the display status vocabulary order

        This makes sure the important statuses are first when ascending (Reclaimable,
        then Linked Externally, etc) rather than an alphabetical sort.
        """
        whens = [
            When(status=key, then=Value(index)) for index, key in enumerate(display.STATUS_VOCAB)
        ]
        return Case(*whens, default=Value(len(display.STATUS_VOCAB)), output_field=IntegerField())

    def base_queryset(self, scan: Scan) -> QuerySet:
        """
        The scan's unfiltered blobs

        :param scan: the scan whose blobs to list
        """
        return scan.blobs.all()

    def get_queryset(self, scan: Scan, sort: str, filterset: FilterSet) -> QuerySet:
        """
        Build the filtered blob queryset for a scan

        Annotations backing the name and status sorts are attached only when that sort is
        active. The base view applies the ordering.

        :param scan: the scan whose blobs to list
        :param sort: a validated key from SORTS
        :param filterset: the parsed filters
        """
        blobs = filterset.apply(
            self.base_queryset(scan).prefetch_related(
                Prefetch("links", queryset=Link.objects.order_by("path"))
            )
        )

        if sort == "name":
            display_name = Subquery(
                Link.objects.filter(blob=OuterRef("pk")).order_by("path").values("name")[:1]
            )
            blobs = blobs.annotate(display_name=display_name)
        elif sort == "status":
            blobs = blobs.annotate(status_order=self._status_order_case())

        return blobs


class TorrentListView(SortedListView):
    """Table of the current scan's torrents"""

    SORTS = {
        "reclaimable": SortField("bytes_reclaimable_if_removed", "desc"),
        "name": SortField("name", "asc"),
        "size": SortField("size", "desc"),
    }

    DEFAULT_SORT = "reclaimable"

    FILTERSET_CLASS = TorrentFilters

    PAGE_TEMPLATE = "media_janitor/torrents.html"
    FRAGMENT_TEMPLATE = "media_janitor/fragments/torrents_table.html"

    def base_queryset(self, scan: Scan) -> QuerySet:
        """
        The scan's unfiltered torrents

        :param scan: the scan whose torrents to list
        """
        return scan.torrents.all()

    def get_queryset(self, scan: Scan, sort: str, filterset: FilterSet) -> QuerySet:
        """
        Build the filtered, annotated torrent queryset for a scan

        Annotates each torrent with its blob count. The base view applies the ordering.

        :param scan: the scan whose torrents to list
        :param sort: a validated key from SORTS
        :param filterset: the parsed filters
        """
        return filterset.apply(self.base_queryset(scan)).annotate(
            # distinct: a torrent can reference one blob through several file entries
            blob_count=Count("blobs", distinct=True),
        )


@login_required
def torrent_blobs(request: HttpRequest, pk: int) -> HttpResponse:
    """
    Render the expanded blob rows fragment for one torrent of the current scan

    Scoped to the current scan so a stale or unknown pk 404s.

    :param request: the incoming request
    :param pk: primary key of the torrent whose blobs to show
    """
    scan = Scan.current()
    if scan is None:
        raise Http404("No completed scan")

    torrent = get_object_or_404(scan.torrents, pk=pk)
    # distinct: a torrent can reference one blob through several file entries
    blobs = (
        torrent.blobs.distinct()
        .order_by("-size", "pk")
        .prefetch_related(Prefetch("links", queryset=Link.objects.order_by("path")))
    )

    return render(
        request,
        "media_janitor/fragments/torrent_blobs.html",
        {"blobs": blobs},
    )


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
def blob_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """
    Render the blob detail drawer fragment for one blob of the current scan

    Scoped to the current scan so a stale or unknown pk 404s the same way the reclaim list
    only ever shows the current scan.

    :param request: the incoming request
    :param pk: primary key of the blob to show
    """
    scan = Scan.current()
    if scan is None:
        raise Http404("No completed scan")

    blob = get_object_or_404(scan.blobs.prefetch_related("links", "torrents"), pk=pk)

    return render(
        request,
        "media_janitor/fragments/blob_detail.html",
        {
            "blob": blob,
            "link_groups": display.links_by_tree(blob.links.all()),
            # Sorted for stable output. The prefetch cache means no extra query.
            "torrents": sorted(blob.torrents.all(), key=lambda torrent: torrent.hash),
            "flags": display.active_flags(blob),
            "links_outside": blob.nlink - blob.links_found,
            "config": Config.get(),
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
