import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from scanner.constants import IGNORE_DIRS


@dataclass(frozen=True)
class FileRecord:
    """A single regular file discovered during the walk"""

    rel: str
    "Path relative to the share root"
    size: int
    st_dev: int
    st_ino: int
    nlink: int
    mtime: datetime


@dataclass(frozen=True)
class WalkResult:
    """Outcome of a walk: the discovered files plus a count of skipped files"""

    records: list[FileRecord]
    stat_errors: int


def walk(share_root: Path) -> WalkResult:
    """
    Walk the entire share tree under share_root and return the discovered files

    Symlinks, sockets, fifos, and devices are skipped, only regular files are
    recorded. Directories whose basename is in IGNORE_DIRS are not descended into.

    Per-file errors are tolerated: if classifying or stat-ing an entry raises OSError, it
    is counted in stat_errors and skipped, and the walk continues. Errors opening a
    directory (os.scandir) are not caught: if share_root itself (or any directory beneath
    it) cannot be opened, the OSError propagates and aborts the walk. An unreadable or
    absent share root means the mount is not intact, so a partial tree is not reported.
    """
    records: list[FileRecord] = []
    stat_errors = 0

    # Stack entries are tuples of absolute path (for stat operations) and share-relative path
    stack: list[tuple[Path, PurePosixPath]] = [(share_root, PurePosixPath("."))]

    while stack:
        abs_dir, rel_dir = stack.pop()
        with os.scandir(abs_dir) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name in IGNORE_DIRS:
                            continue
                        stack.append((Path(entry.path), rel_dir / entry.name))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    stat_errors += 1
                    continue

                records.append(
                    FileRecord(
                        rel=str(rel_dir / entry.name),
                        size=st.st_size,
                        st_dev=st.st_dev,
                        st_ino=st.st_ino,
                        nlink=st.st_nlink,
                        mtime=datetime.fromtimestamp(st.st_mtime, tz=UTC),
                    )
                )

    return WalkResult(records=records, stat_errors=stat_errors)
