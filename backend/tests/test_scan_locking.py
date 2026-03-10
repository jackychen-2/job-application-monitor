from __future__ import annotations

from job_monitor.api import scan


def test_scan_and_sse_share_same_scope_lock() -> None:
    scope = (123, 456)

    scan._user_scan_locks.clear()

    regular_lock = scan._get_scan_lock(scope)
    sse_lock = scan._get_sse_scan_lock(scope)

    assert regular_lock is sse_lock


def test_different_scopes_get_different_locks() -> None:
    scan._user_scan_locks.clear()

    first = scan._get_scan_lock((1, 1))
    second = scan._get_scan_lock((1, 2))

    assert first is not second
