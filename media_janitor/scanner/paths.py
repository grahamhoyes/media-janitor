"""Path helpers for classifying files and normalizing paths"""

from collections.abc import Iterable
from pathlib import PurePosixPath

from scanner.constants import SIDECAR_EXTENSIONS, VIDEO_EXTENSIONS
from scanner.models import Kind, Tree


def kind_for(name: str) -> Kind:
    """Classify a basename by its extension (case-insensitive)"""
    suffix = PurePosixPath(name).suffix.lower()
    if suffix in VIDEO_EXTENSIONS:
        return Kind.MEDIA
    if suffix in SIDECAR_EXTENSIONS:
        return Kind.SIDECAR
    return Kind.OTHER


def _is_within(path: PurePosixPath, root: PurePosixPath) -> bool:
    """True if path equals root or is nested under it, by path segments"""
    root_parts = root.parts
    return path.parts[: len(root_parts)] == root_parts


def tree_for(
    rel: str,
    *,
    library_roots: Iterable[str],
    torrent_roots: Iterable[str],
) -> Tree:
    """Classify a share-relative path against the configured root lists

    Returns Tree.LIBRARY if rel is within any library root, Tree.TORRENTS if
    within any torrent root, otherwise Tree.LOOSE. Containment is checked by
    path segments, so root "media/movies" does not match "media/movies-extra".
    """
    path = PurePosixPath(rel)
    if any(_is_within(path, PurePosixPath(root)) for root in library_roots):
        return Tree.LIBRARY
    if any(_is_within(path, PurePosixPath(root)) for root in torrent_roots):
        return Tree.TORRENTS
    return Tree.LOOSE
