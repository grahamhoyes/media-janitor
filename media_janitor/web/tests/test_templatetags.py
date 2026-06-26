from datetime import timedelta

import pytest
from django.utils import timezone

from scanner.models import Blob, Kind
from web.display import (
    active_flags,
    since,
    status_badge_class,
    status_label,
    until,
)
from web.templatetags.janitor import binsize, status_badge

# -- binsize --------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, "0 B"),
        (None, "0 B"),
        (512, "512.0 B"),
        (1024, "1.0 KiB"),
        (1536, "1.5 KiB"),
        (1048576, "1.0 MiB"),
        (1024**3 + 1024**2 * 257, "1.3 GiB"),
        (1024**4, "1.0 TiB"),
        (1024**5, "1.0 PiB"),
        # Beyond the largest unit it stays in PiB rather than overflowing
        (1024**6, "1024.0 PiB"),
    ],
)
def test_binsize(value, expected):
    assert binsize(value) == expected


# -- since / until --------------------------------------------------------------


def test_since_renders_two_compact_units():
    now = timezone.now()
    value = now - timedelta(days=3, hours=4)
    assert since(value, now) == "3d 4h"


def test_until_renders_two_compact_units():
    now = timezone.now()
    value = now + timedelta(days=5, hours=2)
    assert until(value, now) == "5d 2h"


def test_since_collapses_separator_to_space():
    now = timezone.now()
    value = now - timedelta(days=1, hours=2)
    result = since(value, now)
    assert "," not in result
    assert result == "1d 2h"


def test_since_sub_minute_is_zero_minutes():
    now = timezone.now()
    value = now - timedelta(seconds=30)
    assert since(value, now) == "0m"


def test_since_and_until_none_render_dash():
    assert since(None) == "-"
    assert until(None) == "-"


# -- status vocabulary ----------------------------------------------------------


def test_status_label_and_badge():
    assert status_label(Blob.Status.RECLAIMABLE) == "Reclaimable"
    assert status_badge_class(Blob.Status.RECLAIMABLE) == "badge-success"


def test_status_badge_inclusion_tag():
    blob = Blob(status=Blob.Status.SEEDING_HOLD)
    ctx = status_badge(blob)
    assert ctx == {"label": "Seeding hold", "badge": "badge-warning"}


# -- flag vocabulary ------------------------------------------------------------


def _bare_blob(**flags) -> Blob:
    return Blob(
        st_dev=64,
        st_ino=1,
        size=1,
        nlink=1,
        links_found=1,
        status=Blob.Status.RECLAIMABLE,
        kind=Kind.MEDIA,
        **flags,
    )


def test_active_flags_empty():
    assert active_flags(_bare_blob()) == []


def test_active_flags_returns_truthy_in_order():
    blob = _bare_blob(
        links_outside_scope=True,
        cross_seed=True,
        partial_torrent=True,
    )
    labels = [label for label, _ in active_flags(blob)]
    # Order follows FLAG_VOCAB, not the order the flags were set.
    assert labels == ["cross-seed", "partial-torrent", "outside-scope"]


def test_active_flags_includes_tooltips():
    blob = _bare_blob(multi_link=True)
    assert active_flags(blob) == [
        ("multi-link", "Has more than one hard link in the same tree"),
    ]
