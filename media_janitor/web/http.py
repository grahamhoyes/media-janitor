from django.http import HttpRequest
from django_htmx.middleware import HtmxDetails


class HtmxHttpRequest(HttpRequest):
    """An HttpRequest with the htmx details attached by django-htmx middleware"""

    htmx: HtmxDetails
