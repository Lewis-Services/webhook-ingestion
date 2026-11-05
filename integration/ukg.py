"""Approved Employee Changes integration boundary.

No concrete client is provided until the tenant-approved URL, auth, request,
pagination, and response contracts are supplied. This prevents accidental calls
to an unapproved UKG endpoint or guessed query parameters.
"""

from __future__ import annotations

from typing import Any, Protocol


class EmployeeChangesProvider(Protocol):
    def scheduled_changes(self, start_utc: str, end_utc: str) -> list[dict[str, Any]]:
        """Fetch changes with the documented scheduled query contract."""
        ...

    def all_changes(self) -> list[dict[str, Any]]:
        """Fetch all permitted pages for the controlled expedite operation."""
        ...


class EmployeeChangesContractMissing(RuntimeError):
    """Raised until the approved tenant API contract is implemented/configured."""


def webhook_employee_key(event: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return the employee-matching pair (EeId, CoId), never the EventId."""
    return event.get("ee_id"), event.get("co_id")


def employee_changes_key(record: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return the approved Employee Changes pair (employeeId, companyId)."""
    return record.get("employeeId"), record.get("companyId")


def matches_employee_change(event: dict[str, Any], record: dict[str, Any]) -> bool:
    """Match a webhook event to one endpoint record using both identifiers."""
    webhook_key = webhook_employee_key(event)
    endpoint_key = employee_changes_key(record)
    return all(webhook_key) and webhook_key == endpoint_key


def create_employee_changes_provider() -> EmployeeChangesProvider:
    """Fail closed; there is intentionally no guessed default endpoint."""
    raise EmployeeChangesContractMissing(
        "Provide the approved Employee Changes URL, auth, and request contract"
    )
