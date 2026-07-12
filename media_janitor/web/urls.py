"""URL routes for the web (user-facing) app."""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("reclaim/", views.ReclaimListView.as_view(), name="reclaim"),
    path("torrents/", views.TorrentListView.as_view(), name="torrents"),
    path("torrents/<int:pk>/blobs/", views.torrent_blobs, name="torrent_blobs"),
    path("blob/<int:pk>/", views.blob_detail, name="blob_detail"),
    path("_ping/", views.ping, name="ping"),
]
