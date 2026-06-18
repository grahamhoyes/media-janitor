"""Tests for the qBittorrent download client.

Async tests run under pytest-asyncio (asyncio_mode = "auto"), so async test
functions are awaited directly. All HTTP traffic is faked with httpx.MockTransport.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from scanner.clients.base import TorrentState
from scanner.clients.qbittorrent import QBittorrentClient, QBittorrentError

HOST = "http://qbit.local:8080"
API_KEY = "test-api-key"
DATA_ROOT = "/data"

# A single-file torrent (files/name has no root folder) and a multi-file torrent
# (files/name includes the torrent root folder)
SINGLE_FILE_TORRENT = {
    "hash": "aaaa",
    "state": "uploading",
    "ratio": 2.5,
    "completion_on": 1_700_000_000,
    "seeding_time": 3600,
    "content_path": "/data/torrents/single/a.mkv",
    "save_path": "/data/torrents/single",
}
MULTI_FILE_TORRENT = {
    "hash": "bbbb",
    "state": "stoppedUP",
    "ratio": 0.3,
    "completion_on": 0,  # undefined -> None
    "seeding_time": 0,  # undefined -> None
    "content_path": "/data/torrents/show",
    "save_path": "/data/torrents",
}

FILES_BY_HASH = {
    "aaaa": [{"index": 0, "name": "a.mkv", "size": 1000}],
    "bbbb": [
        {"index": 0, "name": "show/S01E01.mkv", "size": 2000},
        {"index": 1, "name": "show/S01E02.mkv", "size": 3000},
    ],
}


def _happy_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/api/v2/app/version":
        return httpx.Response(200, text="v5.2.0")
    if path == "/api/v2/torrents/info":
        return httpx.Response(200, json=[SINGLE_FILE_TORRENT, MULTI_FILE_TORRENT])
    if path == "/api/v2/torrents/files":
        torrent_hash = request.url.params["hash"]
        return httpx.Response(200, json=FILES_BY_HASH[torrent_hash])
    raise AssertionError(f"unexpected path {path}")


def _make_client(handler) -> QBittorrentClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport, base_url=HOST)
    return QBittorrentClient(
        host=HOST,
        api_key=API_KEY,
        data_root=DATA_ROOT,
        client=http_client,
    )


async def test_gather_happy_path():
    client = _make_client(_happy_handler)
    snapshot = await client.gather()

    assert snapshot.server_version == "v5.2.0"
    assert len(snapshot.torrents) == 2

    single = snapshot.torrents[0]
    assert single.hash == "aaaa"
    assert single.state == TorrentState.SEEDING
    assert single.raw_state == "uploading"
    assert single.ratio == 2.5
    assert single.completed_on == datetime.fromtimestamp(1_700_000_000, tz=UTC)
    assert single.seeding_time == timedelta(seconds=3600)
    assert single.content_path == "torrents/single/a.mkv"
    assert single.save_path == "torrents/single"
    assert len(single.files) == 1
    assert single.files[0].index == 0
    assert single.files[0].size == 1000
    assert single.files[0].path == "torrents/single/a.mkv"

    multi = snapshot.torrents[1]
    assert multi.hash == "bbbb"
    assert multi.state == TorrentState.STOPPED
    assert multi.raw_state == "stoppedUP"
    assert multi.completed_on is None  # completion_on == 0
    assert multi.seeding_time is None  # seeding_time == 0
    assert multi.content_path == "torrents/show"
    assert multi.save_path == "torrents"
    assert [f.path for f in multi.files] == [
        "torrents/show/S01E01.mkv",
        "torrents/show/S01E02.mkv",
    ]
    assert [f.index for f in multi.files] == [0, 1]
    assert [f.size for f in multi.files] == [2000, 3000]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("downloading", TorrentState.IN_FLIGHT),
        ("metaDL", TorrentState.IN_FLIGHT),
        ("stalledDL", TorrentState.IN_FLIGHT),
        ("moving", TorrentState.IN_FLIGHT),
        # checkingUP is a recheck in progress, not active seeding
        ("checkingUP", TorrentState.IN_FLIGHT),
        ("uploading", TorrentState.SEEDING),
        ("stalledUP", TorrentState.SEEDING),
        ("forcedUP", TorrentState.SEEDING),
        ("stoppedDL", TorrentState.STOPPED),
        ("stoppedUP", TorrentState.STOPPED),
        ("error", TorrentState.OTHER),
        ("missingFiles", TorrentState.OTHER),
        ("unknown", TorrentState.OTHER),
        # Legacy paused states that were replaced with stopped in 5.0
        ("pausedUP", TorrentState.OTHER),
        ("pausedDL", TorrentState.OTHER),
    ],
)
def test_normalize_state(raw, expected):
    assert QBittorrentClient._normalize_state(raw) == expected


@pytest.mark.parametrize("status", [401, 403])
async def test_bad_api_key_raises(status):
    def handler(request: httpx.Request) -> httpx.Response:
        # A bad/missing key surfaces on the first authenticated request
        if request.url.path == "/api/v2/app/version":
            return httpx.Response(status, text="Forbidden")
        raise AssertionError(f"unexpected path {request.url.path}")

    client = _make_client(handler)
    with pytest.raises(QBittorrentError):
        await client.gather()


async def test_authorization_header_set_on_injected_client():
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.headers["authorization"])
        return _happy_handler(request)

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport, base_url=HOST)
    client = QBittorrentClient(host=HOST, api_key=API_KEY, data_root=DATA_ROOT, client=http_client)
    assert http_client.headers["Authorization"] == f"Bearer {API_KEY}"

    await client.gather()
    assert captured
    assert all(header == f"Bearer {API_KEY}" for header in captured)


async def test_authorization_header_set_on_self_created_client():
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.headers["authorization"])
        return _happy_handler(request)

    client = QBittorrentClient(host=HOST, api_key=API_KEY, data_root=DATA_ROOT)
    # Swap in a mock transport on the self-created client
    client._client._transport = httpx.MockTransport(handler)
    assert client._client.headers["Authorization"] == f"Bearer {API_KEY}"
    async with client:
        await client.gather()

    assert captured
    assert all(header == f"Bearer {API_KEY}" for header in captured)


async def test_connection_error_propagates():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = _make_client(handler)
    with pytest.raises(QBittorrentError):
        await client.gather()


@pytest.mark.parametrize(
    "qbit_path,expected",
    [
        ("/data/torrents/x", "torrents/x"),
        ("/data/torrents/Show/S01E01.mkv", "torrents/Show/S01E01.mkv"),
        ("/data/x", "x"),
    ],
)
def test_to_relative(qbit_path, expected):
    client = QBittorrentClient(host=HOST, api_key=API_KEY, data_root=DATA_ROOT)
    assert client._to_relative(qbit_path) == expected


def test_to_relative_not_under_root_raises():
    client = QBittorrentClient(host=HOST, api_key=API_KEY, data_root=DATA_ROOT)
    with pytest.raises(ValueError):
        client._to_relative("/other/torrents/x")


def test_to_relative_segment_aware():
    # data_root DATA_ROOT must not match "/database"
    client = QBittorrentClient(host=HOST, api_key=API_KEY, data_root=DATA_ROOT)
    with pytest.raises(ValueError):
        client._to_relative("/database/x")


async def test_injected_client_not_closed_on_aexit():
    # An injected (caller-owned) client must outlive the context manager
    transport = httpx.MockTransport(_happy_handler)
    injected = httpx.AsyncClient(transport=transport, base_url=HOST)

    client = QBittorrentClient(
        host=HOST,
        api_key=API_KEY,
        data_root=DATA_ROOT,
        client=injected,
    )
    async with client:
        pass

    assert not injected.is_closed


async def test_self_created_client_closed_on_aexit():
    # A self-created client is owned by the instance and must be closed
    client = QBittorrentClient(host=HOST, api_key=API_KEY, data_root=DATA_ROOT)
    async with client:
        # Grab the internal client reference without making network calls.
        grabbed = client._client

    assert grabbed.is_closed


async def test_torrent_outside_data_root_skipped():
    # A torrent stored outside data_root is out of scope: it is skipped (and not
    # files-fetched), while in-scope torrents still appear. The scan does not raise.
    outside_torrent = {
        "hash": "cccc",
        "state": "uploading",
        "ratio": 1.0,
        "completion_on": 1_700_000_000,
        "seeding_time": 3600,
        "content_path": "/other/torrents/x.mkv",
        "save_path": "/other/torrents",
    }
    files_requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v2/app/version":
            return httpx.Response(200, text="v5.2.0")
        if path == "/api/v2/torrents/info":
            return httpx.Response(200, json=[SINGLE_FILE_TORRENT, outside_torrent])
        if path == "/api/v2/torrents/files":
            torrent_hash = request.url.params["hash"]
            files_requested.append(torrent_hash)
            return httpx.Response(200, json=FILES_BY_HASH[torrent_hash])
        raise AssertionError(f"unexpected path {path}")

    client = _make_client(handler)
    snapshot = await client.gather()

    hashes = {t.hash for t in snapshot.torrents}
    assert hashes == {"aaaa"}
    # The out-of-scope torrent is skipped before any files request is made
    assert files_requested == ["aaaa"]


async def test_missing_expected_field_raises():
    # A missing expected field signals a broken API contract and must fail the scan
    malformed_torrent = {k: v for k, v in SINGLE_FILE_TORRENT.items() if k != "completion_on"}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v2/app/version":
            return httpx.Response(200, text="v5.2.0")
        if path == "/api/v2/torrents/info":
            return httpx.Response(200, json=[malformed_torrent])
        if path == "/api/v2/torrents/files":
            return httpx.Response(200, json=FILES_BY_HASH["aaaa"])
        raise AssertionError(f"unexpected path {path}")

    client = _make_client(handler)
    with pytest.raises(KeyError):
        await client.gather()


async def test_files_request_uses_raw_save_path():
    # Confirm the file name is joined onto the raw save_path before conversion,
    # not onto the already-relative content_path
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v2/app/version":
            return httpx.Response(200, text="v5.0.0")
        if path == "/api/v2/torrents/info":
            return httpx.Response(200, json=[SINGLE_FILE_TORRENT])
        if path == "/api/v2/torrents/files":
            captured.append(request.url.params["hash"])
            return httpx.Response(200, json=FILES_BY_HASH["aaaa"])
        raise AssertionError(path)

    client = _make_client(handler)
    snapshot = await client.gather()
    assert captured == ["aaaa"]
    assert snapshot.torrents[0].files[0].path == "torrents/single/a.mkv"
