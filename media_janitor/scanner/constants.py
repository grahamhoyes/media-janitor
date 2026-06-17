"""Static classification sets used by the scan pipeline."""

# Directory names to skip entirely during the filesystem walk.
IGNORE_DIRS: frozenset[str] = frozenset(
    {
        # Synology internal directories
        "#recycle",
        "#snapshot",
        "@eaDir",
    }
)

# Video container extensions, lowercase with leading dot.
VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".mkv",
        ".mp4",
        ".avi",
        ".mov",
        ".wmv",
        ".flv",
        ".m4v",
        ".mpg",
        ".mpeg",
        ".ts",
        ".m2ts",
        ".webm",
        ".vob",
        ".divx",
    }
)

# Subtitle/metadata companion extensions, lowercase with leading dot.
SIDECAR_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".srt",
        ".sub",
        ".idx",
        ".ass",
        ".ssa",
        ".vtt",
        ".nfo",
    }
)
