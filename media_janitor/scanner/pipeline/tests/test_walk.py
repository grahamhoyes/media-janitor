import os
from pathlib import Path

import pytest

from scanner.pipeline.walk import FileRecord, WalkResult, walk


def _by_rel(result: WalkResult) -> dict[str, FileRecord]:
    return {r.rel: r for r in result.records}


def _build_tree(tmp_path: Path) -> Path:
    """
    Build a real share tree under tmp_path and return the share root

    Layout:
        torrents/Foo/foo.mkv        (hard-linked to media/movies/Foo (2020)/foo.mkv)
        media/movies/Foo (2020)/foo.mkv
        media/movies/Bar/bar.mkv    (single link)
        media/@eaDir/thumb.jpg      (ignored dir)
        media/movies/@eaDir/x.jpg   (nested ignored dir)
        torrents/#recycle/old.mkv   (ignored dir)
        other/loose.mkv             (top-level dir that is neither media nor torrents)
        media/movies/link.mkv       (symlink to bar.mkv)
    """
    share = tmp_path / "share"

    foo_torrent = share / "torrents" / "Foo"
    foo_torrent.mkdir(parents=True)
    foo_src = foo_torrent / "foo.mkv"
    foo_src.write_bytes(b"foo-content")

    foo_lib_dir = share / "media" / "movies" / "Foo (2020)"
    foo_lib_dir.mkdir(parents=True)
    os.link(foo_src, foo_lib_dir / "foo.mkv")

    bar_dir = share / "media" / "movies" / "Bar"
    bar_dir.mkdir(parents=True)
    bar = bar_dir / "bar.mkv"
    bar.write_bytes(b"bar")

    eadir = share / "media" / "@eaDir"
    eadir.mkdir(parents=True)
    (eadir / "thumb.jpg").write_bytes(b"thumb")

    nested_eadir = share / "media" / "movies" / "@eaDir"
    nested_eadir.mkdir(parents=True)
    (nested_eadir / "x.jpg").write_bytes(b"x")

    recycle = share / "torrents" / "#recycle"
    recycle.mkdir(parents=True)
    (recycle / "old.mkv").write_bytes(b"old")

    other = share / "other"
    other.mkdir(parents=True)
    (other / "loose.mkv").write_bytes(b"loose")

    os.symlink(bar, share / "media" / "movies" / "link.mkv")

    return share


def test_walk_clean_tree_records_and_no_errors(tmp_path):
    share = _build_tree(tmp_path)
    result = walk(share)

    assert result.stat_errors == 0
    rels = set(_by_rel(result))
    assert rels == {
        "torrents/Foo/foo.mkv",
        "media/movies/Foo (2020)/foo.mkv",
        "media/movies/Bar/bar.mkv",
        "other/loose.mkv",
    }


def test_walk_rel_is_share_relative_posix(tmp_path):
    share = _build_tree(tmp_path)
    by_rel = _by_rel(walk(share))

    assert "torrents/Foo/foo.mkv" in by_rel
    assert "media/movies/Foo (2020)/foo.mkv" in by_rel


def test_walk_hard_link_inode_dedupe_signal(tmp_path):
    share = _build_tree(tmp_path)
    by_rel = _by_rel(walk(share))

    a = by_rel["torrents/Foo/foo.mkv"]
    b = by_rel["media/movies/Foo (2020)/foo.mkv"]

    assert a.rel != b.rel
    assert (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)
    assert a.nlink == 2
    assert b.nlink == 2


def test_walk_ignore_dirs_skipped(tmp_path):
    share = _build_tree(tmp_path)
    rels = set(_by_rel(walk(share)))

    assert "media/@eaDir/thumb.jpg" not in rels
    assert "media/movies/@eaDir/x.jpg" not in rels
    assert "torrents/#recycle/old.mkv" not in rels


def test_walk_includes_files_outside_media_and_torrents(tmp_path):
    share = _build_tree(tmp_path)
    rels = set(_by_rel(walk(share)))

    assert "other/loose.mkv" in rels


def test_walk_symlink_skipped(tmp_path):
    share = _build_tree(tmp_path)
    rels = set(_by_rel(walk(share)))

    assert "media/movies/link.mkv" not in rels


def test_walk_mtime_is_timezone_aware(tmp_path):
    share = _build_tree(tmp_path)
    for record in walk(share).records:
        assert record.mtime.tzinfo is not None


def test_walk_size_and_nlink(tmp_path):
    share = _build_tree(tmp_path)
    by_rel = _by_rel(walk(share))

    bar = by_rel["media/movies/Bar/bar.mkv"]
    assert bar.size == len(b"bar")
    assert bar.nlink == 1


def test_walk_top_level_missing_raises(tmp_path):
    with pytest.raises(OSError):
        walk(tmp_path / "does_not_exist")


def test_walk_stat_error_counted(tmp_path, monkeypatch):
    share = _build_tree(tmp_path)

    real_stat = os.DirEntry.stat

    def flaky_stat(self, *args, **kwargs):
        if self.name == "bar.mkv":
            raise OSError("boom")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(os.DirEntry, "stat", flaky_stat)

    result = walk(share)
    assert result.stat_errors == 1
    assert "media/movies/Bar/bar.mkv" not in _by_rel(result)
