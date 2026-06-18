from datetime import timedelta

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

    def save(self, *args, **kwargs):
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

    summary_totals = models.JSONField[dict, dict](
        default=dict, help_text="Computed totals: reclaimable totals plus per-status breakdown"
    )

    class Meta:
        ordering = ["-as_of"]

    def __str__(self) -> str:
        return f"Scan {self.pk} ({self.status})"


class Kind(models.TextChoices):
    """File / blob type"""

    MEDIA = "media"
    SIDECAR = "sidecar"
    OTHER = "other"


class Tree(models.TextChoices):
    """
    A folder type

    Library and Torrents are the main recognized trees, other is for anything else
    """

    LIBRARY = "library"
    TORRENTS = "torrents"
    LOOSE = "loose"


class Blob(models.Model):
    """
    A unique (per scan) file discovered during a scan.
    Files can have multiple hard links (paths) via `Link`.
    """

    class Status(models.TextChoices):
        RECLAIMABLE = "reclaimable"
        SEEDING_HOLD = "seeding_hold"
        IN_LIBRARY = "in_library"
        IN_PROGRESS = "in_progress"

    # TODO: Convert all of these to on_delete=models.DB_CASCADE once Django 6.1 is out
    scan = models.ForeignKey(Scan, on_delete=models.CASCADE, related_name="blobs")
    st_dev = models.BigIntegerField()
    st_ino = models.BigIntegerField()
    size = models.BigIntegerField()
    nlink = models.PositiveIntegerField()
    links_found = models.PositiveIntegerField(
        help_text="Number of hard links found during the scan. "
        "May be > nlink if some links are outside of the scanned tree."
    )
    status = models.CharField(choices=Status, max_length=16)
    kind = models.CharField(choices=Kind, max_length=16)
    trees = models.JSONField[list[str], list[str]](
        default=list, help_text="Subset of Tree values this blob is reachable through"
    )

    torrent_tracked = models.BooleanField(
        default=False, help_text="Whether this blob is part of a torrent in qBittorrent"
    )
    seeding_met = models.BooleanField(
        null=True,
        help_text="Whether seeding requirements for the associated torrent are met. "
        "ANDed across owning torrents (if multiple, eg cross-seed). null when untracked.",
    )

    latest_seeding_start = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Latest completed_on (which is when seeding stats) for any torrent of this file",
    )
    seeding_end = models.DateTimeField(null=True, blank=True)
    orphan_reason = models.CharField(max_length=64, blank=True, default="")

    cross_seed = models.BooleanField(default=False)
    multi_link = models.BooleanField(default=False)
    partial_torrent = models.BooleanField(default=False)
    seedable_idle = models.BooleanField(default=False)
    links_outside_scope = models.BooleanField(default=False)

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


class Torrent(models.Model):
    """Per-scan snapshot of a qBittorrent torrent"""

    scan = models.ForeignKey(Scan, on_delete=models.CASCADE, related_name="torrents")
    hash = models.CharField(max_length=40)
    state = models.CharField(max_length=32)
    ratio = models.FloatField()
    completed_on = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the torrent completed downloading and seeding started",
    )

    seeding_time = models.DurationField(null=True, blank=True)
    content_path = models.TextField()
    save_path = models.TextField()

    seeding_met = models.BooleanField(default=False)
    seeding_end = models.DateTimeField(null=True, blank=True)
    partial_torrent = models.BooleanField(default=False)
    reclaim_if_removed_bytes = models.BigIntegerField(default=0)

    def __str__(self) -> str:
        return f"Torrent {self.hash}"


class BlobTorrent(models.Model):
    """Ownership link between a blob and a torrent within a scan"""

    scan = models.ForeignKey(Scan, on_delete=models.CASCADE, related_name="blob_torrents")
    blob = models.ForeignKey(Blob, on_delete=models.CASCADE, related_name="blob_torrents")
    torrent = models.ForeignKey(Torrent, on_delete=models.CASCADE, related_name="blob_torrents")
    file_index = models.IntegerField()

    def __str__(self) -> str:
        return f"Blob {self.blob_id} <-> Torrent {self.torrent_id}"
