from scanner.constants import (
    SIDECAR_EXTENSIONS,
    VIDEO_EXTENSIONS,
)


def test_extension_sets_use_leading_dots_and_lowercase():
    for ext in VIDEO_EXTENSIONS | SIDECAR_EXTENSIONS:
        assert ext.startswith(".")
        assert ext == ext.lower()


def test_video_and_sidecar_are_disjoint():
    assert VIDEO_EXTENSIONS.isdisjoint(SIDECAR_EXTENSIONS)
