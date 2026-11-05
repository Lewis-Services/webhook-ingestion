"""UKG-to-JDE Azure Functions entry points (Python v2 programming model)."""

from __future__ import annotations

import json
import hashlib
import logging
import os
import re
import uuid
from datetime import datetime

import azure.functions as func
from svix.webhooks import Webhook, WebhookVerificationError

from integration.diagnostics import log_stage
from integration.processing import build_blocked_result
from integration.sql_repository import (
    SQLNotConfigured,
    SQLRepository,
    SQLStageError,
    log_available_odbc_drivers_once,
)

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)
logger = logging.getLogger("ukg_jde")
log_available_odbc_drivers_once()


def _correlation_id(req: func.HttpRequest) -> str:
    candidate = req.headers.get("x-correlation-id", "")
    if candidate and len(candidate) <= 100 and re.fullmatch(r"[A-Za-z0-9._-]+", candidate):
        return candidate
    return str(uuid.uuid4())


def _json_response(payload: dict, status_code: int) -> func.HttpResponse:
    return func.HttpResponse(
        body=json.dumps(payload, separators=(",", ":")),
        status_code=status_code,
        mimetype="application/json",
    )


def _parse_event(payload: object) -> tuple[dict | None, str | None]:
    if not isinstance(payload, dict):
        return None, "invalid_envelope"
    payload_fields = payload.get("Payload")
    if not isinstance(payload_fields, dict):
        return None, "invalid_payload_envelope"

    event_id = payload.get("EventId")
    event_time = payload.get("EventTime")
    event_type = payload.get("EventType")
    operation = payload.get("Operation")
    if event_id is None:
        return None, "missing_event_id"
    if isinstance(event_id, bool) or not isinstance(event_id, int) or not -(2**63) <= event_id < 2**63:
        return None, "invalid_event_id"
    if not isinstance(event_time, str):
        return None, "missing_event_time"
    try:
        parsed_time = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
    except ValueError:
        return None, "invalid_event_time"
    if parsed_time.tzinfo is None or parsed_time.utcoffset() is None:
        return None, "invalid_event_time"
    if not isinstance(event_type, str) or not event_type or len(event_type) > 100:
        return None, "invalid_event_type"
    if not isinstance(operation, str) or not operation or len(operation) > 50:
        return None, "invalid_operation"

    def optional_text(source: dict, key: str, max_length: int) -> str | None | bool:
        value = source.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or len(value) > max_length:
            return False
        return value

    ee_id = optional_text(payload_fields, "EeId", 50)
    co_id = optional_text(payload_fields, "CoId", 20)
    if any(value is False for value in (ee_id, co_id)):
        return None, "invalid_employee_key"

    event = {
        "event_id": event_id,
        "event_time": parsed_time.isoformat(timespec="milliseconds"),
        "event_type": event_type,
        "operation": operation,
        "ee_id": ee_id,
        "co_id": co_id,
    }
    if not ee_id or not co_id:
        # Keep the existing acceptance behavior while recording why this row
        # cannot match an employee until its keys are present.
        return event, "missing_employee_key"
    return event, None


def _is_ukg_subscription_test(payload: object) -> bool:
    """Match only UKG's exact subscription-test notification object."""
    return payload == {
        "Test": "Subscription Test Connection",
        "message": "Hi, This is subscription test connection API.",
    }


@app.route(route="ukg/webhook", methods=["POST"])
def ukg_webhook(req: func.HttpRequest) -> func.HttpResponse:
    """Verify and durably record a UKG webhook before acknowledging it."""
    correlation_id = _correlation_id(req)
    log_stage(
        logger, logging.INFO, "webhook_request_received", correlation_id,
        stage="request", method="POST", route="/api/ukg/webhook",
    )

    headers = {key.lower(): value for key, value in req.headers.items()}
    header_presence = {
        "webhook_id_present": bool(headers.get("webhook-id")),
        "webhook_timestamp_present": bool(headers.get("webhook-timestamp")),
        "webhook_signature_present": bool(headers.get("webhook-signature")),
    }
    log_stage(
        logger, logging.INFO, "webhook_signature_headers_checked", correlation_id,
        stage="signature_headers", **header_presence,
    )

    secret = os.getenv("UKG_WEBHOOK_SECRET")
    if not secret:
        log_stage(
            logger, logging.ERROR, "webhook_signature_verification_unavailable", correlation_id,
            stage="configuration", safe_error_category="webhook_secret_not_configured",
        )
        return _json_response({"status": "unavailable", "correlation_id": correlation_id}, 503)

    raw_body = req.get_body()
    try:
        # UKG documents the Svix scheme with `webhook-*` header names.
        Webhook(secret).verify(raw_body, headers)
    except (WebhookVerificationError, ValueError, TypeError):
        log_stage(
            logger, logging.WARNING, "webhook_signature_verification_failed", correlation_id,
            stage="signature_verification", safe_error_category="signature_invalid",
            **header_presence,
        )
        return _json_response({"status": "unauthorized", "correlation_id": correlation_id}, 401)
    except Exception as exc:
        log_stage(
            logger, logging.ERROR, "webhook_signature_verification_failed", correlation_id,
            stage="signature_verification", exception_class=type(exc).__name__,
            safe_error_category="signature_verifier_error", **header_presence,
        )
        # Preserve a generic server failure without letting a verifier error
        # message (which may contain header details) reach platform logs.
        raise RuntimeError("webhook signature verifier failure") from None
    log_stage(
        logger, logging.INFO, "webhook_signature_verification_passed", correlation_id,
        stage="signature_verification", safe_error_category="none", **header_presence,
    )

    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        log_stage(
            logger, logging.WARNING, "webhook_envelope_parsing_failed", correlation_id,
            stage="envelope_parsing", safe_reason_code="invalid_json",
        )
        return _json_response({"status": "invalid_payload", "correlation_id": correlation_id}, 400)

    if _is_ukg_subscription_test(payload):
        try:
            SQLRepository.from_environment().record_subscription_test(correlation_id)
        except SQLNotConfigured:
            log_stage(
                logger, logging.ERROR, "webhook_test_notification_persistence_failed", correlation_id,
                stage="configuration", safe_error_category="sql_connection_string_not_configured",
            )
            return _json_response(
                {"status": "unavailable", "correlation_id": correlation_id},
                503,
            )
        except SQLStageError as exc:
            log_stage(
                logger, logging.ERROR, "webhook_test_notification_persistence_failed", correlation_id,
                stage=exc.stage, safe_error_category=exc.safe_category,
                exception_class=exc.exception_class, sqlstate=exc.sqlstate,
                native_error_number=exc.native_error_number,
            )
            return _json_response(
                {"status": "persistence_failed", "correlation_id": correlation_id},
                503,
            )
        except Exception as exc:
            log_stage(
                logger, logging.ERROR, "webhook_test_notification_persistence_failed", correlation_id,
                stage="unknown", exception_class=type(exc).__name__,
                safe_error_category="unexpected_sql_repository_error",
            )
            return _json_response(
                {"status": "persistence_failed", "correlation_id": correlation_id},
                503,
            )
        log_stage(
            logger, logging.INFO, "webhook_test_notification_accepted", correlation_id,
            stage="subscription_test", safe_outcome="persisted_as_unsupported",
        )
        return _json_response(
            {"status": "test_notification_received", "correlation_id": correlation_id},
            200,
        )

    event, reason_code = _parse_event(payload)
    if event is None:
        log_stage(
            logger, logging.WARNING, "webhook_envelope_parsing_failed", correlation_id,
            stage="envelope_parsing", safe_reason_code=reason_code or "invalid_envelope",
        )
        return _json_response({"status": "invalid_payload", "correlation_id": correlation_id}, 400)
    log_stage(
        logger,
        logging.INFO,
        "webhook_envelope_parsing_passed",
        correlation_id,
        stage="envelope_parsing",
        safe_reason_code="none",
        employee_key_complete=reason_code != "missing_employee_key",
    )
    if reason_code == "missing_employee_key":
        log_stage(
            logger, logging.WARNING, "webhook_employee_key_missing", correlation_id,
            stage="employee_key_validation", safe_reason_code="missing_employee_key",
        )

    try:
        inserted = SQLRepository.from_environment().record_event(event, correlation_id)
    except SQLNotConfigured:
        log_stage(
            logger, logging.ERROR, "webhook_sql_persistence_failed", correlation_id,
            stage="configuration", safe_error_category="sql_connection_string_not_configured",
        )
        return _json_response(
            {"status": "unavailable", "correlation_id": correlation_id},
            503,
        )
    except SQLStageError as exc:
        log_stage(
            logger, logging.ERROR, "webhook_sql_persistence_failed", correlation_id,
            stage=exc.stage, safe_error_category=exc.safe_category,
            exception_class=exc.exception_class, sqlstate=exc.sqlstate,
            native_error_number=exc.native_error_number,
        )
        return _json_response(
            {"status": "persistence_failed", "correlation_id": correlation_id},
            503,
        )
    except Exception as exc:
        log_stage(
            logger, logging.ERROR, "webhook_sql_persistence_failed", correlation_id,
            stage="unknown", exception_class=type(exc).__name__,
            safe_error_category="unexpected_sql_repository_error",
        )
        return _json_response(
            {"status": "persistence_failed", "correlation_id": correlation_id},
            503,
        )
    event_uid_tag = hashlib.sha256(str(event["event_id"]).encode("utf-8")).hexdigest()[:12]
    outcome = "inserted" if inserted else "duplicate"
    log_stage(
        logger, logging.INFO, "webhook_sql_insert_outcome", correlation_id,
        stage="insert_outcome", outcome=outcome, event_uid_tag=event_uid_tag,
    )
    return _json_response(
        {"status": "accepted", "duplicate": not inserted, "correlation_id": correlation_id}, 200
    )


@app.route(route="ukg/process", methods=["POST"])
def process_ukg_events(req: func.HttpRequest) -> func.HttpResponse:
    """ADF/manual processing contract; operational blockers are returned explicitly."""
    correlation_id = _correlation_id(req)
    log_stage(
        logger, logging.INFO, "processing_request_received", correlation_id,
        stage="request", method="POST", route="/api/ukg/process",
    )
    try:
        body = req.get_json()
    except ValueError:
        return _json_response({"status": "invalid_request", "correlation_id": correlation_id}, 400)
    if not isinstance(body, dict) or body.get("mode") not in {"scheduled", "expedite"}:
        return _json_response(
            {"status": "invalid_request", "message": "mode must be scheduled or expedite", "correlation_id": correlation_id},
            400,
        )

    mode = body["mode"]
    run_id = str(uuid.uuid4())
    try:
        repository = SQLRepository.from_environment()
        repository.start_run(run_id, mode, correlation_id)
    except SQLNotConfigured:
        log_stage(
            logger, logging.ERROR, "processing_sql_unavailable", correlation_id,
            stage="configuration", run_id=run_id,
            safe_error_category="sql_connection_string_not_configured",
        )
        return _json_response(
            build_blocked_result(run_id, mode, 0, "sql_not_configured", correlation_id), 503
        )
    except Exception as exc:
        log_stage(
            logger, logging.ERROR, "processing_sql_run_start_failed", correlation_id,
            stage=getattr(exc, "stage", "unknown"), run_id=run_id,
            safe_error_category=getattr(exc, "safe_category", "sql_run_start_failed"),
        )
        return _json_response(
            build_blocked_result(run_id, mode, 0, "sql_run_start_failed", correlation_id), 503
        )

    try:
        pending_count = repository.count_pending(correlation_id)
        blocker = "ukg_api_contract_and_jde_mapping_required"
        repository.finish_blocked_run(run_id, pending_count, blocker, correlation_id)
    except Exception as exc:
        log_stage(
            logger, logging.ERROR, "processing_sql_run_finalize_failed", correlation_id,
            stage=getattr(exc, "stage", "unknown"), run_id=run_id,
            safe_error_category=getattr(exc, "safe_category", "sql_run_finalization_failed"),
        )
        try:
            repository.fail_run(run_id, "sql_run_finalization_failed", correlation_id)
        except Exception:
            log_stage(
                logger, logging.ERROR, "processing_sql_run_failure_update_failed", correlation_id,
                stage="unknown", run_id=run_id,
                safe_error_category="sql_run_failure_update_failed",
            )
        return _json_response(
            build_blocked_result(run_id, mode, 0, "sql_run_finalization_failed", correlation_id), 503
        )

    # No guessed UKG API calls or JDE output: approved endpoint and file mapping
    # remain blockers. The run and inbox state are held in SQL.
    result = build_blocked_result(run_id, mode, pending_count, blocker, correlation_id)
    log_stage(
        logger, logging.INFO, "processing_blocked", correlation_id,
        stage="processing", run_id=run_id, mode=mode, pending_count=pending_count,
        safe_error_category=blocker,
    )
    return _json_response(result, 200)
