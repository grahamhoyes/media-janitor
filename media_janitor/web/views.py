from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone


@login_required
def dashboard(request):
    """Placeholder dashboard / app shell."""
    return render(request, "media_janitor/dashboard.html")


@login_required
def ping(request):
    """Tiny HTMX endpoint used by the scaffold to confirm partial swaps work."""
    return render(
        request,
        "media_janitor/fragments/ping.html",
        {"now": timezone.now()},
    )
