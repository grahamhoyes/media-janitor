import pytest

from scanner.models import Kind, Tree
from scanner.paths import kind_for, tree_for

LIBRARY_ROOTS = ["media/movies", "media/tv"]
TORRENT_ROOTS = ["torrents"]


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Movie.mkv", Kind.MEDIA),
        ("Episode.mp4", Kind.MEDIA),
        ("Subtitles.srt", Kind.SIDECAR),
        ("info.nfo", Kind.SIDECAR),
        ("archive.zip", Kind.OTHER),
        ("README", Kind.OTHER),
        ("Movie.MKV", Kind.MEDIA),
        ("Subtitles.SRT", Kind.SIDECAR),
    ],
)
def test_kind_for(name, expected):
    assert kind_for(name) == expected


@pytest.mark.parametrize(
    "rel,expected",
    [
        ("media/movies/Foo (2020)/Foo.mkv", Tree.LIBRARY),
        ("media/tv/Show/S01E01.mkv", Tree.LIBRARY),
        ("media/movies", Tree.LIBRARY),
        ("torrents/Foo/Foo.mkv", Tree.TORRENTS),
        ("torrents", Tree.TORRENTS),
        ("downloads/loose.mkv", Tree.LOOSE),
        ("media/movies-extra/Foo.mkv", Tree.LOOSE),
        ("media/movies-extra", Tree.LOOSE),
    ],
)
def test_tree_for(rel, expected):
    assert tree_for(rel, library_roots=LIBRARY_ROOTS, torrent_roots=TORRENT_ROOTS) == expected
