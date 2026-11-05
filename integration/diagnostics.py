"""Structured, privacy-safe diagnostic logging helpers."""

from __future__ import annotations

import logging
from typing import Any


def log_stage(
    logger: logging.Logger,
    level: int,
    event: str,
    correlation_id: str | None = None,
    **fields: Any,
) -> None:
    """Emit Application Insights custom dimensions without formatting secrets."""
    dimensions: dict[str, Any] = {"event": event}
    if correlation_id:
        dimensions["correlation_id"] = correlation_id
    dimensions.update(fields)
    logger.log(level, event, extra={"custom_dimensions": dimensions})
