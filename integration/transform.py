"""New-hire validation and JDE mapping boundary, parameterized by the approved spec."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class ValidationIssue:
    field: str
    reason: str


class NewHireMapper(Protocol):
    def validate(self, employee: Mapping[str, Any]) -> list[ValidationIssue]:
        """Validate employee data against the approved JDE specification."""
        ...

    def to_jde_record(self, employee: Mapping[str, Any]) -> Mapping[str, Any]:
        """Map an eligible employee to the approved JDE record fields."""
        ...


class JdeMappingMissing(RuntimeError):
    """Raised until required fields, formats, and defaults are approved."""


def validate_required_values(
    employee: Mapping[str, Any], required_paths: Sequence[str]
) -> list[ValidationIssue]:
    """Generic configured-path validation; it does not define JDE fields."""
    issues: list[ValidationIssue] = []
    for path in required_paths:
        value: Any = employee
        for token in path.split("."):
            value = value.get(token) if isinstance(value, Mapping) else None
            if value is None:
                break
        if value is None or (isinstance(value, str) and not value.strip()):
            issues.append(ValidationIssue(field=path, reason="missing_required_value"))
    return issues


def create_new_hire_mapper() -> NewHireMapper:
    """Fail closed rather than emit guessed fields or defaults."""
    raise JdeMappingMissing("Provide the approved JDE new-hire file specification")
