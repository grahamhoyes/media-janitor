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


def make_scan() -> Scan:
    return Scan.objects.create(
        as_of=timezone.now(),
        seeding_min_days=14,
        seeding_min_ratio=1.0,
        quarantine_window=timedelta(minutes=30),
    )


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


@pytest.mark.django_db
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


@pytest.mark.django_db
def test_unique_inode_per_scan():
    scan = make_scan()
    make_blob(scan, st_ino=1)

    with pytest.raises(IntegrityError):
        make_blob(scan, st_ino=1)


@pytest.mark.django_db
def test_same_inode_allowed_across_scans():
    scan_a = make_scan()
    scan_b = make_scan()

    make_blob(scan_a, st_ino=1)
    make_blob(scan_b, st_ino=1)

    assert Blob.objects.filter(st_dev=64, st_ino=1).count() == 2
