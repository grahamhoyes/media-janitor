from datetime import timedelta

import pytest

from scanner.models import Config


@pytest.mark.django_db
def test_defaults_are_applied():
    config = Config.get()

    assert config.library_roots == ["media/movies", "media/tv"]
    assert config.torrent_roots == ["torrents"]
    assert config.seeding_min_days == 14
    assert config.seeding_min_ratio == 1.0
    assert config.quarantine_window == timedelta(minutes=30)


@pytest.mark.django_db
def test_load_returns_same_single_row():
    first = Config.get()
    second = Config.get()

    assert first.pk == second.pk == 1
    assert Config.objects.count() == 1


@pytest.mark.django_db
def test_saving_second_instance_does_not_create_second_row():
    Config.get()

    second = Config(library_roots=["media/other"])
    second.save()

    assert second.pk == 1
    assert Config.objects.count() == 1
    assert Config.objects.get(pk=1).library_roots == ["media/other"]
