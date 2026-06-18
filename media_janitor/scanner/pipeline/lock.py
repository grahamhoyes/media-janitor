import contextlib
from collections.abc import Iterator

from django.db import connection

# Fixed bigint key identifying the single-scan advisory lock. Any other holder of
# this exact key (across every session against the same database) contends for it.
SCAN_LOCK_KEY = 0x6D6A736E  # "mjsn" (media janitor scan)


@contextlib.contextmanager
def advisory_lock() -> Iterator[bool]:
    """
    Hold the single-scan Postgres advisory lock for the duration of the block

    Uses a session-level lock (pg_try_advisory_lock / pg_advisory_unlock) rather
    than the transaction-scoped _xact_ variants, so the lock is held across the
    whole scan without keeping a database transaction open. Acquisition is
    non-blocking: if another session already holds the lock, this yields False
    immediately instead of waiting, letting the caller coalesce (no-op).

    The yielded value reports whether the lock was acquired. Only acquired locks
    are released on exit; unlocking a lock we never held would log a Postgres
    warning and return false. Usage:

        with advisory_lock() as acquired:
            if not acquired:
                return  # another scan is already running
            ...
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [SCAN_LOCK_KEY])
        acquired = bool(cursor.fetchone()[0])

    try:
        yield acquired
    finally:
        if acquired:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [SCAN_LOCK_KEY])
