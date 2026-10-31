from abc import ABC, abstractmethod
from typing import Any, ClassVar
from urllib.parse import urlsplit, urlunsplit

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpRequest, HttpResponse, QueryDict
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.generic import View
from django_htmx.http import push_url

from scanner.models import Scan
from web.http import HtmxHttpRequest


class DetailDrawerView(LoginRequiredMixin, View, ABC):
    """
    Base view for a detail drawer fragment, opened on a list page by the drawer query param

    An HTMX request renders the fragment and pushes the current page's URL with the drawer
    param set. That URL is a full page (see SortedListView) which renders the drawer already
    open, so refresh, Back after leaving the page, and shared links all work. A direct
    (non-HTMX) visit redirects to the drawer's list page with the drawer open.

    Subclasses declare:
        - KIND: Drawer kind, used in the drawer param value ("<kind>-<pk>")
        - TEMPLATE: Drawer fragment template
        - LIST_URL_NAME: List page that a direct visit to the fragment URL redirects to

    Subclasses implement:
        - get_context(): Template context for the object being shown (self.pk)
    """

    # Query param opening a drawer on a list page, as "<kind>-<pk>" (such as "blob-12")
    PARAM = "drawer"
    # Drawer kind to its view class, filled by __init_subclass__
    registry: ClassVar[dict[str, type[DetailDrawerView]]] = {}

    KIND: ClassVar[str]
    TEMPLATE: ClassVar[str]
    LIST_URL_NAME: ClassVar[str]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.registry[cls.KIND] = cls

    @property
    def pk(self) -> int:
        """Primary key of the object to show"""
        return self.kwargs["pk"]

    @abstractmethod
    def get_context(self, scan: Scan) -> dict[str, Any]:
        """
        Build the drawer's template context for the object being shown (self.pk)

        Scope the lookup to the scan so a stale or unknown pk raises Http404.

        :param scan: the scan the object must belong to
        """
        raise NotImplementedError

    def get(self, request: HtmxHttpRequest, **kwargs: Any) -> HttpResponse:
        """
        Render the drawer fragment, pushing the current page's URL with the drawer param set

        :param request: the incoming request
        """
        if not request.htmx:
            return redirect(self.url_with(reverse(self.LIST_URL_NAME)))

        scan = Scan.current()
        if scan is None:
            raise Http404("No completed scan")

        response = render(request, self.TEMPLATE, self.get_context(scan))
        if current_url := request.htmx.current_url_abs_path:
            push_url(response, self.url_with(current_url))
        return response

    def url_with(self, url: str) -> str:
        """
        Return the URL with its drawer param set to this drawer, replacing any other

        :param url: URL (typically a list page) to open the drawer on
        """
        parts = urlsplit(url)
        query = QueryDict(parts.query, mutable=True)
        query[self.PARAM] = f"{self.KIND}-{self.pk}"
        return urlunsplit(parts._replace(query=query.urlencode()))

    def render_html(self, scan: Scan) -> str | None:
        """
        Render the drawer's contents for inclusion in a full page

        :param scan: the scan the object must belong to
        :return: the drawer HTML, or None when the object is not in the scan (such as a link
            from before the latest scan), so the page renders with the drawer closed
        """
        try:
            context = self.get_context(scan)
        except Http404:
            return None
        return render_to_string(self.TEMPLATE, context, self.request)

    @classmethod
    def pop_from(cls, request: HttpRequest) -> DetailDrawerView | None:
        """
        Strip the drawer param from request.GET, returning the drawer it requested

        :param request: the incoming request, whose GET is replaced with a stripped copy
        :return: the requested drawer, set up for this request, or None if absent or invalid
        """
        params = request.GET.copy()
        kind, _, pk = params.pop(cls.PARAM, [""])[-1].partition("-")
        request.GET = params  # type: ignore[assignment]

        if kind in cls.registry and pk.isdigit():
            # Set up the view as as_view() would, so self.request and self.kwargs are
            # available on this path just like when it is serving its own URL
            view = cls.registry[kind]()
            view.setup(request, pk=int(pk))
            return view
        return None
