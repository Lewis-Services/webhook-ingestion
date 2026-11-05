"""Processing response contract and blocked-state builder."""

from __future__ import annotations


def build_blocked_result(
    run_id: str, mode: str, pending_count: int, blocker: str, correlation_id: str
) -> dict:
    return {
        "run_id": run_id,
        "mode": mode,
        "status": "blocked",
        "file_path": None,
        "record_count": 0,
        "pending_count": pending_count,
        "error_count": 0,
        "blockers": [blocker],
        "correlation_id": correlation_id,
    }
