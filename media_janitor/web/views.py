from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Case, IntegerField, OuterRef, QuerySet, Subquery, Value, When
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.generic import View
from django_htmx.middleware import HtmxDetails

from scanner.models import Blob, Link, Scan
from web import display
from web.display import SortColumn


class HtmxHttpRequest(HttpRequest):
    """An HttpRequest with the htmx details attached by django-htmx middleware"""

    htmx: HtmxDetails


class ReclaimListView(LoginRequiredMixin, View):
    """
    Dense table of the current scan's blobs

    Returns the full page on a normal request and only the table fragment on an HTMX
    request, so filter/sort/page controls can swap the table alone.
    """

    DEFAULT_PAGE_SIZE = 100

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
            return render(request, "media_janitor/reclaim.html")

        page_size = self._coerce_page_size(request.GET.get("page_size"))
        sort, direction = self._resolve_sort(request)

        blobs = self._sorted_blobs(scan, sort or self.DEFAULT_SORT, direction or self.DEFAULT_DIR)

        paginator = Paginator[Blob](blobs, page_size)
        # get_page() is forgiving: invalid or out-of-range pages return the first or last page
        page_obj = paginator.get_page(request.GET.get("page"))

        for blob in page_obj:
            # Sort the prefetched links without needing another query
            links = sorted(blob.links.all(), key=lambda link: link.path)
            # Attached for the template only, the model has no such fields
            blob.sorted_links = links  # type: ignore[attr-defined]

        context = {
            "page_obj": page_obj,
            "sort": sort,
            "dir": direction,
            "sort_columns": self._sort_columns(sort, direction),
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
        if sort not in self.SORT_FIELDS:
            return None, None

        direction = request.GET.get("dir")
        if direction not in ("asc", "desc"):
            direction = self.SORT_DEFAULT_DIR[sort]

        return sort, direction

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

    def _sorted_blobs(self, scan: Scan, sort: str, direction: str) -> QuerySet[Blob]:
        """
        Build the ordered blob queryset for a scan

        Annotations backing the name and status sorts are attached only when that sort is
        active. Every sort tie-breaks on pk so pagination slices are stable.

        :param scan: the scan whose blobs to list
        :param sort: a validated key from SORT_FIELDS
        :param direction: "asc" or "desc"
        """
        blobs = scan.blobs.prefetch_related("links")

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
