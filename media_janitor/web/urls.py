"""URL routes for the web (user-facing) app."""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("reclaim/", views.ReclaimListView.as_view(), name="reclaim"),
    path("_ping/", views.ping, name="ping"),
]
