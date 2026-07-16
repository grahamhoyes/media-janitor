from datetime import timedelta
from urllib.parse import urlparse

from django.db import models


def default_library_roots() -> list[str]:
    return ["media/movies", "media/tv"]


def default_torrent_roots() -> list[str]:
    return ["torrents"]


class Config(models.Model):
    """Singleton holding non-secret scan policy"""

    library_roots = models.JSONField[list[str], list[str]](
        default=default_library_roots,
        help_text="List of paths to library root directories (eg movies, tv)",
    )

    torrent_roots = models.JSONField[list[str], list[str]](
        default=default_torrent_roots, help_text="List of paths to torrent root directories"
    )

    seeding_min_days = models.PositiveIntegerField(
        default=14, help_text="Minimum number of seeding days"
    )

    seeding_min_ratio = models.FloatField(default=1.0, help_text="Minimum seeding ratio")

    quarantine_window = models.DurationField(
        default=timedelta(minutes=30),
        help_text="Quarantine window wherein a newly created or modified link cannot be modified",
    )

    class Meta:
        verbose_name = "config"
        verbose_name_plural = "config"

    def __str__(self) -> str:
        return "Scan configuration"

    def save(self, *args, **kwargs) -> None:
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get(cls) -> Config:
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Scan(models.Model):
    """A snapshot of the filesystem and associated *arr/qBittorrent state"""

    class Status(models.TextChoices):
        RUNNING = "running"
        COMPLETE = "complete"
        FAILED = "failed"

    status = models.CharField(
        choices=Status,
        max_length=16,
        default=Status.RUNNING,
        help_text="Lifecycle status of the scan",
    )

    as_of = models.DateTimeField(help_text="When the scan began")

    seeding_min_days = models.PositiveIntegerField(
        help_text="Minimum seeding days, copied from Config at scan start"
    )

    seeding_min_ratio = models.FloatField(
        help_text="Minimum seeding ratio, copied from Config at scan start"
    )

    quarantine_window = models.DurationField(
        help_text="Quarantine window, copied from Config at scan start"
    )

    qbittorrent_version = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="qBittorrent application version observed during the scan",
    )

    status_totals = models.JSONField[dict, dict](
        default=dict,
        help_text="Per-status count and byte totals, keyed by Blob.Status value",
    )
    """
    Format:
    {
        [Blob.Status]: {
            "count": int,
            "bytes": int,
        }
    }
    """

    class Meta:
        ordering = ["-as_of"]

    def __str__(self) -> str:
        return f"Scan {self.pk} ({self.status})"

    @property
    def reclaimable_bytes(self) -> int:
        """Bytes freed by deleting all reclaimable-status blobs"""
        return (self.status_totals or {}).get(Blob.Status.RECLAIMABLE.value, {}).get("bytes", 0)

    @classmethod
    def current(cls) -> Scan | None:
        """
        Return the most recent completed scan, or None
        """

        return cls.objects.filter(status=Scan.Status.COMPLETE).order_by("-as_of").first()


class Kind(models.TextChoices):
    """File / blob type"""

    MEDIA = "media"
    SIDECAR = "sidecar"
    OTHER = "other"


class Tree(models.TextChoices):
    """
    A folder type

    Library and Torrents are the main recognized trees, loose is for anything else
    """

    LIBRARY = "library"
    TORRENTS = "torrents"
    # TODO: Rename to other?
    LOOSE = "loose"


class Blob(models.Model):
    """
    A unique (per scan) file discovered during a scan.
    Files can have multiple hard links (paths) via `Link`.
    """

    class Status(models.TextChoices):
        RECLAIMABLE = "reclaimable"
        """
        The blob is safe to delete because it has no library link, and is either
        not part of a torrent or its torrents have met their seeding requirements.
        """
        LINKED_EXTERNALLY = "linked_externally"
        """
        The blob would otherwise be reclaimable, but has hard links outside the
        scan scope, so deleting the links we can see frees no space.
        """
        SEEDING_HOLD = "seeding_hold"
        """
        The blob has no library link, but cannot be deleted because seeding
        requirements for an owning torrent are not met.
        """
        IN_LIBRARY = "in_library"
        """
        The blob has a link under a library root. It cannot be deleted without
        being removed from the library.
        """
        IN_PROGRESS = "in_progress"
        """
        The torrent owning this blob is downloading/checking/moving, or a link
        to it was recently modified
        """

    # TODO: Convert all of these to on_delete=models.DB_CASCADE once Django 6.1 is out
    scan = models.ForeignKey(Scan, on_delete=models.CASCADE, related_name="blobs")
    st_dev = models.BigIntegerField()
    st_ino = models.BigIntegerField()
    size = models.BigIntegerField()
    nlink = models.PositiveIntegerField()
    links_found = models.PositiveIntegerField(
        help_text="Number of hard links found during the scan. "
        "May be < nlink if some links are outside of the scanned tree."
    )
    status = models.CharField(choices=Status, max_length=20)
    kind = models.CharField(choices=Kind, max_length=16)
    trees = models.JSONField[list[str], list[str]](
        default=list, help_text="Subset of Tree values this blob is reachable through"
    )

    torrent_tracked = models.BooleanField(
        default=False,
        help_text="Whether this blob is part of a torrent in the torrent client",
    )
    seeding_met = models.BooleanField(
        null=True,
        help_text="Whether seeding requirements for the associated torrent are met. "
        "ANDed across owning torrents (if multiple, eg cross-seed). null when untracked.",
    )

    latest_seeding_start = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Latest completed_on (which is when seeding starts) for any torrent of this file",
    )
    seeding_end = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When seeding requirements of the associated torrent will be met if "
        "seeding_met is false",
    )
    orphan_reason = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Reason that a non-media file couldn't be linked to a media file "
        "and is safe for deletion",
    )

    cross_seed = models.BooleanField(
        default=False, help_text="Blob served by more than one torrent"
    )
    multi_link = models.BooleanField(
        default=False, help_text="Multiple hard links in the same tree"
    )
    could_seed = models.BooleanField(
        default=False,
        help_text="In the library and torrent trees, but isn't seeding. "
        "Covers the case of no owning torrent or an owning torrent that is stopped. "
        "Not set when the only owning torrents are in an error or other state.",
    )
    links_outside_scope = models.BooleanField(
        default=False,
        help_text="Link count could not be accounted for by only files under the scan root",
    )

    torrents = models.ManyToManyField["Torrent", "BlobTorrent"](
        "Torrent", through="BlobTorrent", related_name="blobs"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["scan", "st_dev", "st_ino"],
                name="uniq_blob_per_scan_inode",
            ),
        ]
        indexes = [
            models.Index(fields=["scan", "status"]),
        ]

    def __str__(self) -> str:
        return f"Blob {self.st_dev}:{self.st_ino} ({self.status})"


class Link(models.Model):
    """A path naming a blob, share-relative"""

    scan = models.ForeignKey(Scan, on_delete=models.CASCADE, related_name="links")
    blob = models.ForeignKey(Blob, on_delete=models.CASCADE, related_name="links")
    path = models.TextField(help_text="Share-relative path")

    name = models.TextField(help_text="Basename of the path")

    kind = models.CharField(choices=Kind, max_length=16)
    tree = models.CharField(choices=Tree, max_length=16)
    mtime = models.DateTimeField()

    def __str__(self) -> str:
        return f"Link {self.path}"


class TorrentState(models.TextChoices):
    """
    Normalized, client-agnostic torrent state

    Concrete download clients map their native state strings onto these members.
    """

    SEEDING = "seeding"
    DOWNLOADING = "downloading"
    CHECKING = "checking"
    MOVING = "moving"
    QUEUED = "queued"
    ERROR = "error"
    STOPPED = "stopped"
    OTHER = "other"

    @property
    def is_active(self) -> bool:
        """
        Whether the torrent is doing work other than seeding: downloading, checking,
        moving, or queued
        """
        return self in {
            TorrentState.DOWNLOADING,
            TorrentState.CHECKING,
            TorrentState.MOVING,
            TorrentState.QUEUED,
        }


class Torrent(models.Model):
    """Per-scan snapshot of a download client torrent"""

    class ReclaimState(models.TextChoices):
        FULL = "full"
        """
        Fully reclaimable: the torrent has at least one owned blob and every owned
        blob is reclaimable, so removing it frees all of them.
        """
        PARTIAL = "partial"
        """
        Partially reclaimable: the torrent owns both reclaimable and non-reclaimable
        blobs, so removing it frees only some of them.
        """
        NONE = "none"
        """
        Not reclaimable: no owned blob is reclaimable (or the torrent is empty).
        """

    scan = models.ForeignKey(Scan, on_delete=models.CASCADE, related_name="torrents")
    hash = models.CharField(max_length=40)
    state = models.CharField(max_length=32, choices=TorrentState)
    raw_state = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="Raw state as reported by the torrent client",
    )
    "Raw state as reported by the torrent client. For debugging purposes only, don't use in logic."
    name = models.TextField(help_text="Torrent name as reported by the torrent client")
    category = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Torrent category in the torrent client",
    )
    tracker = models.TextField(
        blank=True,
        default="",
        help_text="Currently-working tracker URL reported by the client",
    )
    private = models.BooleanField(
        default=False, help_text="Whether the torrent is from a private tracker"
    )
    ratio = models.FloatField()
    uploaded = models.BigIntegerField(default=0, help_text="Amount of data uploaded in bytes")
    completed_on = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the torrent completed downloading and seeding started",
    )
    added_on = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the torrent was added to the client",
    )
    last_activity = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last time the torrent had upload/download activity",
    )

    seeding_time = models.DurationField(
        null=True,
        blank=True,
        help_text="How long since the torrent started seeding (does not account for if "
        "the torrent was stopped)",
    )
    size = models.BigIntegerField(
        default=0, help_text="Total size in bytes of the torrent's selected files"
    )
    content_path = models.TextField()
    save_path = models.TextField()

    seeding_met = models.BooleanField(
        default=False, help_text="Whether seeding requirements have been met"
    )
    seeding_end = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When seeding requirements will be met if seeding_met is false",
    )
    reclaim_state = models.CharField(
        choices=ReclaimState,
        max_length=16,
        default=ReclaimState.NONE,
        help_text="Reclaim status of the torrent's owned blobs: full (all reclaimable), "
        "partial (a mix of reclaimable and non-reclaimable), or none (no reclaimable blob)",
    )
    bytes_reclaimable_if_removed = models.BigIntegerField(
        default=0,
        help_text="Bytes freed if this whole torrent is removed: the size of its reclaimable "
        "blobs whose only links belong to this torrent. Cross-seeded blobs and blobs with "
        "links outside the scan free nothing and are excluded.",
    )

    def __str__(self) -> str:
        return f"Torrent {self.hash}"

    def tracker_host(self) -> str:
        """
        Return just the scheme and hostname of the tracker (path removed)
        """
        parsed = urlparse(self.tracker)
        return f"{parsed.scheme}://{parsed.netloc}"


class BlobTorrent(models.Model):
    """Ownership link between a blob and a torrent within a scan"""

    scan = models.ForeignKey(Scan, on_delete=models.CASCADE, related_name="blob_torrents")
    blob = models.ForeignKey(Blob, on_delete=models.CASCADE, related_name="blob_torrents")
    torrent = models.ForeignKey(Torrent, on_delete=models.CASCADE, related_name="blob_torrents")
    file_index = models.IntegerField()

    def __str__(self) -> str:
        return f"Blob {self.blob_id} <-> Torrent {self.torrent_id}"
