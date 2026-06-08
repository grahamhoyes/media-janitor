"""URL routes for the web (user-facing) app."""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("_ping/", views.ping, name="ping"),
]
