"""
Client-agnostic download client abstraction

The scan pipeline consumes download-client state through this abstraction only.
A concrete client (e.g. QBittorrentClient) is responsible for talking to its
backend, normalizing native torrent states onto the TorrentState enum, and
converting client-native absolute paths into share-relative paths.
"""

import abc
import enum
from dataclasses import dataclass
from datetime import datetime, timedelta


class TorrentState(enum.Enum):
    """
    Torrent state

    Concrete clients map their native state strings onto these members
    """

    IN_FLIGHT = "in_flight"
    "Downloading, moving, checking, etc"

    SEEDING = "seeding"
    STOPPED = "stopped"

    OTHER = "other"
    "Missing, error, or unknown states that are not treated as seeding"


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

    # client-native state, saved in Torrent.state.
    # we persist the raw state for debugging. `TorrentState` is for
    # internal logic only.
    raw_state: str

    ratio: float
    completed_on: datetime | None
    seeding_time: timedelta | None
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
