import threading

import pytest
from django.db import connection, connections

from scanner.pipeline.lock import SCAN_LOCK_KEY, advisory_lock


def _try_acquire_other_session() -> bool:
    """
    Try to take the scan lock from a separate database session, returning success

    Session-level advisory locks are re-entrant within a single session, so taking
    the lock again on the test's own connection would always succeed and prove
    nothing. Django opens a distinct connection (and thus a distinct Postgres
    session) per thread, so running the contending acquire in a worker thread gives
    a proper second session. The lock is released and the connection closed before
    returning, so a successful acquire never leaks.
    """
    result: dict[str, bool] = {}

    def worker() -> None:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(%s)", [SCAN_LOCK_KEY])
                acquired = bool(cursor.fetchone()[0])
                result["acquired"] = acquired
                if acquired:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [SCAN_LOCK_KEY])
        finally:
            connections.close_all()

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    return result["acquired"]


@pytest.mark.django_db(transaction=True)
def test_second_acquisition_fails_while_held():
    with advisory_lock() as acquired:
        assert acquired is True
        # A different session cannot take the lock while we hold it.
        assert _try_acquire_other_session() is False


@pytest.mark.django_db(transaction=True)
def test_lock_reacquirable_after_release():
    with advisory_lock() as acquired:
        assert acquired is True

    # Once the holder exits the context, another session can take it.
    assert _try_acquire_other_session() is True


@pytest.mark.django_db(transaction=True)
def test_not_acquired_path_does_not_unlock():
    # Hold the lock from another session, then confirm our context yields False
    # and does not blow up trying to release a lock it never held.
    holder_ready = threading.Event()
    release = threading.Event()
    holder_result: dict[str, bool] = {}

    def holder() -> None:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(%s)", [SCAN_LOCK_KEY])
                holder_result["acquired"] = bool(cursor.fetchone()[0])
                holder_ready.set()
                release.wait(timeout=5)
                cursor.execute("SELECT pg_advisory_unlock(%s)", [SCAN_LOCK_KEY])
        finally:
            connections.close_all()

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        holder_ready.wait(timeout=5)
        assert holder_result["acquired"] is True

        with advisory_lock() as acquired:
            assert acquired is False
    finally:
        release.set()
        thread.join()
