from abc import ABC, abstractmethod
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
from django.views.decorators.http import require_POST
from django.views.generic import View
from django_htmx.middleware import HtmxDetails

from scanner.models import Config, Link, Scan
from scanner.tasks import is_scan_in_progress
from scanner.tasks import scan as scan_task
from web import display
from web.filters import BlobFilters, FilterSet, TorrentFilters


class HtmxHttpRequest(HttpRequest):
    """An HttpRequest with the htmx details attached by django-htmx middleware"""

    htmx: HtmxDetails


class SortedListView(LoginRequiredMixin, View, ABC):
    """
    Generic view for filtered, sorted, and paginated list views

    Renders the full page on a normal request and only the table fragment on an HTMX
    request, so filter/sort/page controls can swap the table alone.


    Subclasses declare:
        - FILTERSET_CLASS: FilterSet class (filters, search, and sort)
        - PAGE_TEMPLATE: Template for full-page requests
        - FRAGMENT_TEMPLATE: Template for HTMX requests

    Subclasses implement:
        - base_queryset(): Queryset without any filtering applied (for counts)
        - get_queryset(): Queryset with filtering applied
    """

    DEFAULT_PAGE_SIZE = 50

    FILTERSET_CLASS: ClassVar[type[FilterSet]]

    PAGE_TEMPLATE: ClassVar[str]
    FRAGMENT_TEMPLATE: ClassVar[str]

    def get(self, request: HtmxHttpRequest) -> HttpResponse:
        """
        Render the list (full page) or its table fragment (HTMX request)

        :param request: the incoming request
        """
        scan = Scan.current()

        if scan is None:
            return render(request, self.get_template_name(request))

        page_size = self._coerce_page_size(request.GET.get("page_size"))
        filterset = self.FILTERSET_CLASS(request.GET)

        rows = filterset.order(self.get_queryset(scan, filterset))

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
            "sort": filterset.sort,
            "dir": filterset.direction,
            "sort_columns": filterset.sort_columns(),
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

    def get_queryset(self, scan: Scan, filterset: FilterSet) -> QuerySet:
        """
        Build the filtered queryset for a scan by applying the filterset to base_queryset()

        Sorting is applied by the base view after filtering. Override this method to
        attach annotations needed for sorting by the field in filterset.active_sort.

        :param scan: the scan whose rows to list
        :param filterset: the parsed filters and sort
        """
        return filterset.apply(self.base_queryset(scan))

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


class ReclaimListView(SortedListView):
    """Dense table of the current scan's blobs"""

    FILTERSET_CLASS = BlobFilters

    PAGE_TEMPLATE = "media_janitor/reclaim.html"
    FRAGMENT_TEMPLATE = "media_janitor/fragments/reclaim_table.html"

    @staticmethod
    def _status_order_case() -> Case:
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
        The scan's unfiltered blobs, with links prefetched for rendering

        The prefetch is lazy, so it costs nothing on the count() path (count() never
        populates the result cache that triggers prefetching).

        :param scan: the scan whose blobs to list
        """
        return scan.blobs.prefetch_related(
            Prefetch("links", queryset=Link.objects.order_by("path"))
        )

    def get_queryset(self, scan: Scan, filterset: FilterSet) -> QuerySet:
        """
        Build the filtered blob queryset for a scan

        Annotations backing the name and status sorts are attached only when that sort is
        active. The base view applies the ordering.

        :param scan: the scan whose blobs to list
        :param filterset: the parsed filters and sort
        """
        blobs = super().get_queryset(scan, filterset)

        if filterset.active_sort == "name":
            display_name = Subquery(
                Link.objects.filter(blob=OuterRef("pk")).order_by("path").values("name")[:1]
            )
            blobs = blobs.annotate(display_name=display_name)
        elif filterset.active_sort == "status":
            blobs = blobs.annotate(status_order=self._status_order_case())

        return blobs


class TorrentListView(SortedListView):
    """Table of the current scan's torrents"""

    FILTERSET_CLASS = TorrentFilters

    PAGE_TEMPLATE = "media_janitor/torrents.html"
    FRAGMENT_TEMPLATE = "media_janitor/fragments/torrents_table.html"

    def base_queryset(self, scan: Scan) -> QuerySet:
        """
        The scan's unfiltered torrents

        :param scan: the scan whose torrents to list
        """
        return scan.torrents.all()

    def get_queryset(self, scan: Scan, filterset: FilterSet) -> QuerySet:
        """
        Build the filtered, annotated torrent queryset for a scan

        Annotates each torrent with its blob count.

        :param scan: the scan whose torrents to list
        :param filterset: the parsed filters and sort
        """
        return (
            super()
            .get_queryset(scan, filterset)
            .annotate(
                # distinct: a torrent can reference one blob through several file entries
                blob_count=Count("blobs", distinct=True),
            )
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
@require_POST
def run_scan(request: HttpRequest) -> HttpResponse:
    """
    Enqueue a full-share scan and return the header control in its running state
    """
    if not is_scan_in_progress():
        scan_task.enqueue()

    return render(
        request,
        "media_janitor/fragments/scan_indicator.html",
        {"scan_in_progress": True},
    )


@login_required
def scan_indicator(request: HttpRequest) -> HttpResponse:
    """
    Poll endpoint for the header scan button and indicator

    While a scan is in progress this returns a spinning indicator, which keeps
    polling. Once no scan is in progress it responds with HX-Refresh so the page
    reloads onto the freshly published snapshot.
    """
    if is_scan_in_progress():
        return render(
            request,
            "media_janitor/fragments/scan_indicator.html",
            {"scan_in_progress": True},
        )

    response = HttpResponse(status=204)
    response["HX-Refresh"] = "true"
    return response


@login_required
def ping(request):
    """Tiny HTMX endpoint used by the scaffold to confirm partial swaps work."""
    return render(
        request,
        "media_janitor/fragments/ping.html",
        {"now": timezone.now()},
    )
