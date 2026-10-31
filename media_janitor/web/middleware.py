from collections.abc import Callable

from django.http import HttpRequest, HttpResponse
from django.utils.cache import patch_vary_headers


def vary_on_htmx(get_response: Callable[[HttpRequest], HttpResponse]):
    """
    Add HX-Request to every response's Vary header

    Views return a fragment or a full page for the same URL depending on HX-Request. Without
    Vary, the browser's HTTP cache can serve a cached fragment for a normal navigation (such as
    Back after leaving the page).
    """

    def middleware(request: HttpRequest) -> HttpResponse:
        response = get_response(request)
        patch_vary_headers(response, ["HX-Request"])
        return response

    return middleware
