"""
qBittorrent download client

Supported qBittorrent versions: 5.2+

All qBittorrent-specific knowledge lives here. Native state strings are converted
to normalized objects from base.py for the rest of the pipeline.
"""

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath

import httpx
from django.conf import settings
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .base import (
    ClientSnapshot,
    DownloadClient,
    TorrentFile,
    TorrentSnapshot,
    TorrentState,
)

logger = logging.getLogger(__name__)

# qBittorrent 5.2 torrent states. See:
# https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-(qBittorrent-5.0)#get-torrent-list

# Downloading-ish: torrent is doing work or waiting to download.
_IN_FLIGHT_STATES: frozenset[str] = frozenset(
    {
        "downloading",
        "metaDL",
        "forcedMetaDL",
        "stalledDL",
        "checkingDL",
        "forcedDL",
        "queuedDL",
        "allocating",
        "checkingResumeData",
        "checkingUP",
        "moving",
    }
)

# Uploading-ish: torrent has completed and is actively seeding (or could be)
_SEEDING_STATES: frozenset[str] = frozenset(
    {
        "uploading",
        "stalledUP",
        "forcedUP",
        "queuedUP",
    }
)

# Stopped: torrent is not running (replaced pausedDL/pausedUP in qBittorrent 5.0)
_STOPPED_STATES: frozenset[str] = frozenset(
    {
        "stoppedDL",
        "stoppedUP",
    }
)

# Bound concurrency of per-torrent files requests to avoid hammering the API
_FILES_CONCURRENCY = 8

# A scan makes hundreds of individual requests (one torrents/files call per
# torrent). Retry transient timeouts a few times so one flaky connection doesn't
# fail the whole scan.
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 1.0
_HTTP_TIMEOUT = 10.0

_retry_on_timeout = retry(
    retry=retry_if_exception_type(httpx.TimeoutException),
    stop=stop_after_attempt(_MAX_ATTEMPTS),
    wait=wait_exponential(multiplier=_RETRY_BACKOFF_SECONDS),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)


class QBittorrentError(Exception):
    """Raised when the qBittorrent API cannot be reached or returns an error"""


class QBittorrentClient(DownloadClient):
    """Async qBittorrent WebUI client"""

    def __init__(
        self,
        *,
        host: str,
        api_key: str,
        data_root: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """
        :param host: qBittorrent API hostname, including scheme
        :param api_key: API key
        :param data_root: Path where the media share is mounted for qBittorrent
        :param client: Optional, httpx client. If given, the caller is responsible for closing it.
            Headers on the client are modified to inject the API key.
        """
        self._host = host
        self._api_key = api_key
        self._data_root = data_root

        # Track ownership: only a client we create internally is ours to close.
        # We don't close injected (caller-owned) clients.
        self._owns_client = client is None

        if client is None:
            client = httpx.AsyncClient(
                base_url=host,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=_HTTP_TIMEOUT,
            )
        else:
            client.headers.update({"Authorization": f"Bearer {api_key}"})
        self._client = client

    async def __aenter__(self) -> QBittorrentClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        # Close only a self-created client
        if self._owns_client:
            await self._client.aclose()

    @classmethod
    def from_settings(cls) -> QBittorrentClient:
        """Build a client from QBIT_* Django settings"""
        return cls(
            host=settings.QBIT_HOST,
            api_key=settings.QBIT_API_KEY,
            data_root=settings.QBIT_DATA_ROOT,
        )

    @staticmethod
    def _normalize_state(raw: str) -> TorrentState:
        """Map a qBittorrent 5.2 native state string onto a normalized TorrentState"""
        if raw in _IN_FLIGHT_STATES:
            return TorrentState.IN_FLIGHT
        if raw in _SEEDING_STATES:
            return TorrentState.SEEDING
        if raw in _STOPPED_STATES:
            return TorrentState.STOPPED

        return TorrentState.OTHER

    def _to_relative(self, qbit_path: str) -> str:
        """
        Convert a qBittorrent absolute path to a share-relative path

        qBittorrent reports paths under its container mount (data_root); strip
        that prefix and return a clean relative path with no leading slash.
        Containment is checked by path segments, so data_root "/data" does not
        match "/database". Raises ValueError if qbit_path is not under data_root.
        """
        path = PurePosixPath(qbit_path)
        root = PurePosixPath(self._data_root)
        root_parts = root.parts
        if path.parts[: len(root_parts)] != root_parts:
            raise ValueError(f"{qbit_path!r} is not under data_root {self._data_root!r}")
        return str(path.relative_to(root))

    @staticmethod
    def _epoch_to_datetime(value: int) -> datetime | None:
        """Convert epoch seconds to a UTC-aware datetime. <= 0 means undefined"""
        if value <= 0:
            return None
        return datetime.fromtimestamp(value, tz=UTC)

    @staticmethod
    def _seconds_to_timedelta(value: int) -> timedelta | None:
        """Convert a seconds count to a timedelta. <= 0 means undefined"""
        if value <= 0:
            return None
        return timedelta(seconds=value)

    @_retry_on_timeout
    async def _request(self, url: str, params: dict[str, str] | None) -> httpx.Response:
        return await self._client.get(url, params=params)

    async def _get(self, url: str, params: dict[str, str] | None = None) -> httpx.Response:
        """
        GET an API endpoint, retrying transient timeouts

        Raises QBittorrentError on errors

        :param url: API path to GET
        :param params: Query parameters
        """
        start = time.monotonic()
        try:
            response = await self._request(url, params)
        except httpx.HTTPError as exc:
            elapsed = time.monotonic() - start
            logger.warning(
                f"qBittorrent request failed: GET {url} params={params} "
                f"elapsed={elapsed:.2f}s error={exc!r}"
            )
            raise QBittorrentError(
                f"qBittorrent request to {url} failed after {elapsed:.2f}s: {exc!r}"
            ) from exc
        if response.status_code in (401, 403):
            raise QBittorrentError(
                f"qBittorrent authentication failed (status={response.status_code}). "
                "Check QBIT_API_KEY"
            )
        if response.status_code != 200:
            raise QBittorrentError(
                f"qBittorrent request to {url} failed (status={response.status_code})"
            )
        return response

    async def _gather_files(
        self,
        torrent_hash: str,
        save_path: str,
        semaphore: asyncio.Semaphore,
    ) -> list[TorrentFile]:
        """
        Fetch and normalize the file list for a single torrent

        We fetch files for every torrent (not just multi-file ones) because the
        per-file index/path data is needed downstream (BlobTorrent.file_index,
        inode/path correlation) and torrents/info has no reliable single-file
        flag.

        Each file's "name" from torrents/files is relative to the save path, which
        is the parent dir the content is stored under, not the torrent's own folder.
        For multi-file torrents the "name" therefore already includes the torrent
        root folder, e.g. save_path "/data/torrents/tv" + name "Show.S01/Show.S01E01.mkv".
        So the absolute path is save_dir / name, which we then convert to share-relative.
        Joining onto content_path/root_path instead would double the root folder.

        :param torrent_hash: Torrent hash
        :param save_path: The parent directory the torrent's content is saved under,
            from qBittorrent's perspective (includes the data root).
        :param semaphore: Semaphore to limit request concurrency

        """
        async with semaphore:
            response = await self._get("/api/v2/torrents/files", params={"hash": torrent_hash})
        files_data = response.json()
        save_dir = PurePosixPath(save_path)
        files: list[TorrentFile] = []
        for entry in files_data:
            abs_path = save_dir / entry["name"]
            files.append(
                TorrentFile(
                    index=entry["index"],
                    path=self._to_relative(str(abs_path)),
                    size=entry["size"],
                )
            )
        return files

    async def _gather_torrent(
        self,
        t: dict,
        semaphore: asyncio.Semaphore,
    ) -> TorrentSnapshot | None:
        """
        Build a snapshot for a single torrent, or skip it if out of scope

        :param t: A torrent entry from the torrents/info response
        :param semaphore: Semaphore to limit files request concurrency
        :return: The torrent snapshot, or None if the torrent should be ignored
        """
        try:
            # Either of these failing indicate that the torrent was saved outside the data
            # root. We can safely ignore them, as they won't be part of the share scan either.
            save_path = self._to_relative(t["save_path"])
            content_path = self._to_relative(t["content_path"])
        except ValueError:
            logger.info(f"Skipping torrent {t['hash']} outside data root", exc_info=True)
            return None

        try:
            files = await self._gather_files(t["hash"], t["save_path"], semaphore)
        except QBittorrentError:
            logger.warning(f"failed fetching files for torrent {t['hash']} (save_path={save_path})")
            raise

        raw_state = t["state"]
        return TorrentSnapshot(
            hash=t["hash"],
            state=self._normalize_state(raw_state),
            raw_state=raw_state,
            name=t["name"],
            category=t["category"],
            tracker=t["tracker"],
            # private may be absent before the torrent's metadata is fetched
            private=t.get("private", False),
            ratio=t["ratio"],
            # NOTE: the API field is "completion_on" (epoch seconds).
            completed_on=self._epoch_to_datetime(t["completion_on"]),
            added_on=self._epoch_to_datetime(t["added_on"]),
            last_activity=self._epoch_to_datetime(t["last_activity"]),
            seeding_time=self._seconds_to_timedelta(t["seeding_time"]),
            size=t["size"],
            content_path=content_path,
            save_path=save_path,
            files=files,
        )

    async def gather(self) -> ClientSnapshot:
        gather_start = time.monotonic()

        version_response = await self._get("/api/v2/app/version")
        server_version = version_response.text.strip()
        logger.info(f"qBittorrent server version={server_version}")

        info_response = await self._get("/api/v2/torrents/info")
        torrents_data = info_response.json()
        logger.info(
            f"qBittorrent reported {len(torrents_data)} torrents. "
            f"Fetching files with concurrency={_FILES_CONCURRENCY}"
        )

        semaphore = asyncio.Semaphore(_FILES_CONCURRENCY)
        results = await asyncio.gather(*(self._gather_torrent(t, semaphore) for t in torrents_data))

        torrents = [t for t in results if t is not None]

        elapsed = time.monotonic() - gather_start
        logger.info(
            f"qBittorrent gather finished: {len(torrents)}/{len(torrents_data)} torrents "
            f"in {elapsed:.2f}s"
        )

        return ClientSnapshot(server_version=server_version, torrents=torrents)
