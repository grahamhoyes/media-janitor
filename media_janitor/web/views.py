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

from scanner.models import Blob, Config, Link, Scan, Torrent
from web import display, filters
from web.display import SortColumn
from web.filters import FilterState


class HtmxHttpRequest(HttpRequest):
    """An HttpRequest with the htmx details attached by django-htmx middleware"""

    htmx: HtmxDetails


class SortedListView(LoginRequiredMixin, View):
    """
    Shared sort and page-size query param machinery for paginated list views

    Subclasses define SORT_FIELDS (sort key to queryset field/annotation), DEFAULT_SORT,
    and SORT_DEFAULT_DIR (per-column first-click direction).
    """

    DEFAULT_PAGE_SIZE = 50

    # Sortable columns, mapped to the queryset field (or annotation) they order by
    SORT_FIELDS: ClassVar[dict[str, str]]
    DEFAULT_SORT: ClassVar[str]
    SORT_DEFAULT_DIR: ClassVar[dict[str, str]]

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
        if not sort or sort not in self.SORT_FIELDS:
            return None, None

        direction = request.GET.get("dir")
        if direction not in ("asc", "desc"):
            direction = self.SORT_DEFAULT_DIR[sort]

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
        for key in self.SORT_FIELDS:
            default_dir = self.SORT_DEFAULT_DIR[key]
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
    """
    Dense table of the current scan's blobs

    Returns the full page on a normal request and only the table fragment on an HTMX
    request, so filter/sort/page controls can swap the table alone.
    """

    # Sortable columns, mapped to the queryset field (or annotation) they order by. The
    # annotation-backed keys (name, status) get their annotation attached only when that
    # sort is active.
    SORT_FIELDS = {
        "size": "size",
        "name": "display_name",
        "status": "status_order",
    }

    DEFAULT_SORT = "size"

    SORT_DEFAULT_DIR = {
        "size": "desc",
        "name": "asc",
        "status": "asc",
    }

    # Direction used for the default (unsorted) ordering.
    DEFAULT_DIR = SORT_DEFAULT_DIR[DEFAULT_SORT]

    def get(self, request: HtmxHttpRequest) -> HttpResponse:
        """
        Render the reclaim list (full page) or its table fragment (HTMX request)

        :param request: the incoming request
        """
        scan = Scan.current()

        if scan is None:
            return render(request, self.get_template_name(request))

        page_size = self._coerce_page_size(request.GET.get("page_size"))
        sort, direction = self._resolve_sort(request)
        active_filters = filters.resolve_filters(request.GET)

        blobs = self._sorted_blobs(
            scan, sort or self.DEFAULT_SORT, direction or self.DEFAULT_DIR, active_filters
        )

        paginator = Paginator[Blob](blobs, page_size)
        # get_page() is forgiving: invalid or out-of-range pages return the first or last page
        page_obj = paginator.get_page(request.GET.get("page"))

        # matching_count is the filtered set, total_count is every blob in the scan.
        # When no filter is active the two are equal, so we skip the extra count query.
        any_filter = filters.filters_active(active_filters)
        matching_count = page_obj.paginator.count
        total_count = scan.blobs.count() if any_filter else matching_count

        context = {
            "page_obj": page_obj,
            "sort": sort,
            "dir": direction,
            "sort_columns": self._sort_columns(sort, direction),
            "status_chips": filters.status_chips(active_filters["statuses"]),
            "kind_chips": filters.kind_chips(active_filters["kinds"]),
            "flag_chips": filters.flag_chips(active_filters["flags"]),
            "torrent_chips": filters.torrent_chips(active_filters["torrents"]),
            "q": active_filters["q"],
            "any_filter": any_filter,
            "matching_count": matching_count,
            "total_count": total_count,
        }

        return render(request, self.get_template_name(request), context)

    def get_template_name(self, request: HtmxHttpRequest) -> str:
        """
        Choose the fragment template for an HTMX request and the full page otherwise

        :param request: the incoming request
        """
        if request.htmx:
            return "media_janitor/fragments/reclaim_table.html"
        return "media_janitor/reclaim.html"

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

    def _sorted_blobs(
        self, scan: Scan, sort: str, direction: str, active_filters: FilterState
    ) -> QuerySet[Blob]:
        """
        Build the filtered, ordered blob queryset for a scan

        Filters are applied before sorting and pagination. Annotations backing the name and
        status sorts are attached only when that sort is active. Every sort tie-breaks on pk
        so pagination slices are stable.

        :param scan: the scan whose blobs to list
        :param sort: a validated key from SORT_FIELDS
        :param direction: "asc" or "desc"
        :param active_filters: the validated active filters to narrow by
        """
        blobs = filters.apply_filters(
            scan.blobs.prefetch_related(Prefetch("links", queryset=Link.objects.order_by("path"))),
            active_filters,
        )

        if sort == "name":
            display_name = Subquery(
                Link.objects.filter(blob=OuterRef("pk")).order_by("path").values("name")[:1]
            )
            blobs = blobs.annotate(display_name=display_name)
        elif sort == "status":
            blobs = blobs.annotate(status_order=self._status_order_case())

        field = self.SORT_FIELDS[sort]
        prefix = "-" if direction == "desc" else ""
        return blobs.order_by(f"{prefix}{field}", "pk")


class TorrentListView(SortedListView):
    """
    Table of the current scan's torrents

    Returns the full page on a normal request, and a table fragment on an HTMX request.
    """

    SORT_FIELDS = {
        "reclaimable": "bytes_reclaimable_if_removed",
        "name": "name",
        "size": "size",
    }

    DEFAULT_SORT = "reclaimable"

    SORT_DEFAULT_DIR = {
        "reclaimable": "desc",
        "name": "asc",
        "size": "desc",
    }

    # Direction used for the default (unsorted) ordering.
    DEFAULT_DIR = SORT_DEFAULT_DIR[DEFAULT_SORT]

    def get(self, request: HtmxHttpRequest) -> HttpResponse:
        """
        Render the torrents list (full page) or its table fragment (HTMX request)

        :param request: the incoming request
        """
        scan = Scan.current()

        if scan is None:
            return render(request, self.get_template_name(request))

        page_size = self._coerce_page_size(request.GET.get("page_size"))
        sort, direction = self._resolve_sort(request)
        q = (request.GET.get("q") or "").strip()

        torrents = self._sorted_torrents(
            scan, sort or self.DEFAULT_SORT, direction or self.DEFAULT_DIR, q
        )

        paginator = Paginator[Torrent](torrents, page_size)
        # get_page() is forgiving: invalid or out-of-range pages return the first or last page
        page_obj = paginator.get_page(request.GET.get("page"))

        # matching_count is the searched set, total_count is every torrent in the scan.
        # When no search is active the two are equal, so we skip the extra count query.
        matching_count = page_obj.paginator.count
        total_count = scan.torrents.count() if q else matching_count

        context = {
            "page_obj": page_obj,
            "sort": sort,
            "dir": direction,
            "sort_columns": self._sort_columns(sort, direction),
            "q": q,
            "matching_count": matching_count,
            "total_count": total_count,
        }

        return render(request, self.get_template_name(request), context)

    def get_template_name(self, request: HtmxHttpRequest) -> str:
        """
        Choose the fragment template for an HTMX request and the full page otherwise

        :param request: the incoming request
        """
        if request.htmx:
            return "media_janitor/fragments/torrents_table.html"
        return "media_janitor/torrents.html"

    def _sorted_torrents(self, scan: Scan, sort: str, direction: str, q: str) -> QuerySet[Torrent]:
        """
        Build the searched, annotated, ordered torrent queryset for a scan

        Annotates each torrent with its blob count. Every sort tie-breaks on pk so
        pagination slices are stable.

        :param scan: the scan whose torrents to list
        :param sort: a validated key from SORT_FIELDS
        :param direction: "asc" or "desc"
        :param q: text search term over the torrent name, empty for no narrowing
        """
        torrents = scan.torrents.all()
        if q:
            torrents = torrents.filter(name__icontains=q)

        torrents = torrents.annotate(
            # distinct: a torrent can reference one blob through several file entries
            blob_count=Count("blobs", distinct=True),
        )

        field = self.SORT_FIELDS[sort]
        prefix = "-" if direction == "desc" else ""
        return torrents.order_by(f"{prefix}{field}", "pk")


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
