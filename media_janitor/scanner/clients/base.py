"""
Client-agnostic download client abstraction

The scan pipeline consumes download-client state through this abstraction only.
A concrete client (e.g. QBittorrentClient) is responsible for talking to its
backend, normalizing native torrent states onto the TorrentState enum, and
converting client-native absolute paths into share-relative paths.
"""

import abc
from dataclasses import dataclass
from datetime import datetime, timedelta

from scanner.models import TorrentState


@dataclass(frozen=True)
class TorrentFile:
    """A single file within a torrent"""

    index: int
    path: str  # share-relative path of the file
    size: int


@dataclass(frozen=True)
class TorrentSnapshot:
    """A snapshot of a single torrent"""

    hash: str
    state: TorrentState

    # Client-native state, saved on Torrent.raw_state for debugging only.
    # The normalized TorrentState in `state` is what the pipeline and web layer use.
    raw_state: str

    name: str
    category: str
    tracker: str
    private: bool

    ratio: float
    uploaded: int
    "Total bytes uploaded"
    completed_on: datetime | None
    added_on: datetime | None
    last_activity: datetime | None
    seeding_time: timedelta | None
    size: int
    "Size in bytes"
    content_path: str  # share-relative
    save_path: str  # share-relative
    files: list[TorrentFile]


@dataclass(frozen=True)
class ClientSnapshot:
    """A normalized snapshot of a download client and its torrents"""

    server_version: str
    torrents: list[TorrentSnapshot]


class DownloadClient(abc.ABC):
    """Abstract download client.

    Implementations gather a normalized ClientSnapshot; the pipeline never
    sees client-native states or paths.
    """

    @abc.abstractmethod
    async def gather(self) -> ClientSnapshot:
        """Connect to the client and return a normalized snapshot"""
        raise NotImplementedError
