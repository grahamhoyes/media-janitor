from datetime import timedelta

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


def make_scan(status: Scan.Status = Scan.Status.COMPLETE, **kwargs) -> Scan:
    """
    Create a Scan

    :param status: lifecycle status, defaults to complete
    :param kwargs: field overrides (eg as_of, summary_totals)
    """
    defaults = {
        "status": status,
        "as_of": timezone.now(),
        "seeding_min_days": 14,
        "seeding_min_ratio": 1.0,
        "quarantine_window": timedelta(minutes=30),
    }
    defaults.update(kwargs)
    return Scan.objects.create(**defaults)


def make_blob(scan: Scan, st_ino: int = 1, **kwargs) -> Blob:
    """
    Create a Blob on a scan

    :param scan: the owning scan
    :param st_ino: inode number, unique within the scan
    :param kwargs: field overrides (eg status, kind, flags)
    """
    defaults = {
        "st_dev": 64,
        "st_ino": st_ino,
        "size": 1024,
        "nlink": 1,
        "links_found": 1,
        "status": Blob.Status.RECLAIMABLE,
        "kind": Kind.MEDIA,
    }
    defaults.update(kwargs)
    return Blob.objects.create(scan=scan, **defaults)


def make_link(blob: Blob, path: str, **kwargs) -> Link:
    """
    Create a Link naming a blob

    :param blob: the blob the link names
    :param path: share-relative path
    :param kwargs: field overrides (eg tree, kind, mtime)
    """
    defaults = {
        "name": path.rsplit("/", 1)[-1],
        "kind": Kind.MEDIA,
        "tree": Tree.LIBRARY,
        "mtime": timezone.now(),
    }
    defaults.update(kwargs)
    return Link.objects.create(scan=blob.scan, blob=blob, path=path, **defaults)


def make_torrent(scan: Scan, hash_: str = "a" * 40, **kwargs) -> Torrent:
    """
    Create a Torrent on a scan

    :param scan: the owning scan
    :param hash_: torrent info hash
    :param kwargs: field overrides
    """
    defaults = {
        "state": "stalledUP",
        "ratio": 2.0,
        "content_path": "torrents/example",
        "save_path": "torrents",
    }
    defaults.update(kwargs)
    return Torrent.objects.create(scan=scan, hash=hash_, **defaults)


def make_complete_scan() -> Scan:
    """
    Build a complete scan with a few blobs of varying status/kind/flags
    """
    scan = make_scan(
        summary_totals={
            "reclaimable_bytes": 5000,
            "by_status": {
                "reclaimable": {"count": 1, "bytes": 6000},
                "seeding_hold": {"count": 1, "bytes": 4000},
                "in_library": {"count": 1, "bytes": 2000},
                "in_progress": {"count": 1, "bytes": 1000},
            },
        },
    )

    reclaimable = make_blob(
        scan,
        st_ino=1,
        size=6000,
        status=Blob.Status.RECLAIMABLE,
        kind=Kind.MEDIA,
        torrent_tracked=True,
        seeding_met=True,
        cross_seed=True,
        links_outside_scope=True,
        trees=[Tree.TORRENTS],
    )
    seeding_hold = make_blob(
        scan,
        st_ino=2,
        size=4000,
        status=Blob.Status.SEEDING_HOLD,
        kind=Kind.MEDIA,
        torrent_tracked=True,
        seeding_met=False,
        partial_torrent=True,
        trees=[Tree.TORRENTS],
    )
    in_library = make_blob(
        scan,
        st_ino=3,
        size=2000,
        status=Blob.Status.IN_LIBRARY,
        kind=Kind.SIDECAR,
        seedable_idle=True,
        multi_link=True,
        trees=[Tree.LIBRARY],
    )
    in_progress = make_blob(
        scan,
        st_ino=4,
        size=1000,
        status=Blob.Status.IN_PROGRESS,
        kind=Kind.OTHER,
        trees=[Tree.LOOSE],
    )

    make_link(reclaimable, "torrents/movies/example.mkv", tree=Tree.TORRENTS)
    make_link(seeding_hold, "torrents/tv/show.mkv", tree=Tree.TORRENTS)
    make_link(in_library, "media/movies/example.nfo", tree=Tree.LIBRARY, kind=Kind.SIDECAR)
    make_link(in_progress, "loose/incoming.part", tree=Tree.LOOSE, kind=Kind.OTHER)

    torrent = make_torrent(scan, bytes_reclaimable_if_removed=6000)
    BlobTorrent.objects.create(scan=scan, blob=reclaimable, torrent=torrent, file_index=0)

    return scan
