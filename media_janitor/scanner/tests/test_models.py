from datetime import timedelta

import pytest
from django.db import IntegrityError
from django.utils import timezone

from scanner.models import (
    Blob,
    BlobTorrent,
    Kind,
    Link,
    Scan,
    Torrent,
    Tree,
)

pytestmark = pytest.mark.django_db()


def make_scan(status: Scan.Status = Scan.Status.COMPLETE, **kwargs) -> Scan:
    defaults = {
        "status": status,
        "as_of": timezone.now(),
        "seeding_min_days": 14,
        "seeding_min_ratio": 1.0,
        "quarantine_window": timedelta(minutes=30),
    }
    defaults.update(kwargs)
    return Scan.objects.create(**defaults)


def make_blob(scan: Scan, st_ino: int = 1) -> Blob:
    return Blob.objects.create(
        scan=scan,
        st_dev=64,
        st_ino=st_ino,
        size=1024,
        nlink=1,
        links_found=1,
        status=Blob.Status.RECLAIMABLE,
        kind=Kind.MEDIA,
    )


def test_relations_resolve():
    scan = make_scan()
    blob = make_blob(scan)

    Link.objects.create(
        scan=scan,
        blob=blob,
        path="media/movies/example.mkv",
        name="example.mkv",
        kind=Kind.MEDIA,
        tree=Tree.LIBRARY,
        mtime=timezone.now(),
    )
    torrent = Torrent.objects.create(
        scan=scan,
        hash="a" * 40,
        state="stalledUP",
        ratio=2.0,
        content_path="torrents/example",
        save_path="torrents",
    )
    BlobTorrent.objects.create(scan=scan, blob=blob, torrent=torrent, file_index=0)

    assert scan.blobs.count() == 1
    assert blob.links.count() == 1
    assert blob.blob_torrents.count() == 1
    assert blob.blob_torrents.get().torrent == torrent
    assert torrent.blob_torrents.get().blob == blob


def test_unique_inode_per_scan():
    scan = make_scan()
    make_blob(scan, st_ino=1)

    with pytest.raises(IntegrityError):
        make_blob(scan, st_ino=1)


def test_same_inode_allowed_across_scans():
    scan_a = make_scan()
    scan_b = make_scan()

    make_blob(scan_a, st_ino=1)
    make_blob(scan_b, st_ino=1)

    assert Blob.objects.filter(st_dev=64, st_ino=1).count() == 2


def test_get_most_recent_complete_scan():
    now = timezone.now()
    older = make_scan(status=Scan.Status.COMPLETE, as_of=now - timedelta(hours=2))
    newer = make_scan(status=Scan.Status.COMPLETE, as_of=now - timedelta(hours=1))

    # Newer scans that are not complete should be ignored
    make_scan(status=Scan.Status.RUNNING, as_of=now - timedelta(minutes=30))
    make_scan(status=Scan.Status.FAILED, as_of=now)

    current = Scan.current()

    assert current == newer
    assert current != older


def test_get_current_scan_returns_none_when_none_complete():
    make_scan(status=Scan.Status.RUNNING)
    make_scan(status=Scan.Status.FAILED)

    assert Scan.current() is None
