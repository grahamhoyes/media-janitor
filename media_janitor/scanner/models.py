from datetime import timedelta

from django.db import models


def default_library_roots() -> list[str]:
    return ["media/movies", "media/tv"]


def default_torrent_roots() -> list[str]:
    return ["torrents"]


class Config(models.Model):
    """Singleton holding non-secret scan policy."""

    library_roots = models.JSONField[list[str], list[str]](default=default_library_roots)
    "List of paths to library root directories (eg movies, tv)"

    torrent_roots = models.JSONField[list[str], list[str]](default=default_torrent_roots)
    "List of paths to torrent root directories"

    seeding_min_days = models.PositiveIntegerField(default=14)
    "Minimum number of seeding days"

    seeding_min_ratio = models.FloatField(default=1.0)
    "Minimum seeding ratio"

    quarantine_window = models.DurationField(default=timedelta(minutes=30))
    "Quarantine window wherein a newly created or modified link cannot be modified"

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
