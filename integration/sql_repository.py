"""Parameterized SQL Server persistence with stage-based safe diagnostics."""

from __future__ import annotations

import logging
import os
import re
import secrets
from contextlib import closing
from typing import Any

from integration.diagnostics import log_stage

logger = logging.getLogger("ukg_jde.sql")

# Reserved synthetic IDs for UKG subscription-test rows. They remain within
# signed BIGINT bounds and are separate from ordinary nonnegative UKG IDs.
SUBSCRIPTION_TEST_EVENT_ID_MIN = -9_000_000_000_000_000_000
SUBSCRIPTION_TEST_EVENT_ID_COUNT = 1_000_000_000_000_000_000
SUBSCRIPTION_TEST_ID_COLLISION_RETRIES = 5


class SQLNotConfigured(RuntimeError):
    """Raised when no SQL Server connection string is configured."""


class SQLStageError(RuntimeError):
    """Safe SQL failure metadata. Does not retain the original exception text."""

    def __init__(
        self,
        stage: str,
        safe_category: str,
        exception_class: str,
        sqlstate: str | None,
        native_error_number: int | None,
    ):
        super().__init__("SQL operation failed")
        self.stage = stage
        self.safe_category = safe_category
        self.exception_class = exception_class
        self.sqlstate = sqlstate
        self.native_error_number = native_error_number


def _sql_error_details(exc: Exception) -> tuple[str | None, int | None, str]:
    """Extract only driver codes and map them to a safe category."""
    if isinstance(exc, ModuleNotFoundError):
        return None, None, "odbc_dependency_missing"
    sqlstate: str | None = None
    native_number: int | None = None
    text_args = [arg for arg in getattr(exc, "args", ()) if isinstance(arg, str)]

    for value in text_args:
        if re.fullmatch(r"[0-9A-Z]{5}", value):
            sqlstate = value
            break
    if sqlstate is None:
        for value in text_args:
            match = re.search(r"\[([0-9A-Z]{5})\]", value)
            if match:
                sqlstate = match.group(1)
                break

    for value in getattr(exc, "args", ()):
        if isinstance(value, int) and not isinstance(value, bool):
            native_number = value
            break
    if native_number is None:
        for value in text_args:
            match = re.search(r"\((\d{1,8})\)\s+\([A-Za-z]", value)
            if match:
                native_number = int(match.group(1))
                break

    if isinstance(exc, TimeoutError) or sqlstate in {"HYT00", "HYT01"} or native_number in {258}:
        category = "connection_timeout"
    elif sqlstate == "28000" or native_number in {229, 230, 262, 916, 18456}:
        category = "permission_denied"
    elif sqlstate == "42S02" or native_number in {207, 208}:
        category = "invalid_object"
    elif native_number in {2601, 2627} or (sqlstate == "23000" and native_number is None):
        category = "duplicate_key"
    elif sqlstate == "23000":
        category = "constraint_violation"
    elif sqlstate in {"22003", "22007", "22018"} or native_number in {241, 242, 245, 8114, 8115, 8116}:
        category = "conversion_error"
    elif isinstance(exc, ConnectionError) or (sqlstate and sqlstate.startswith("08")):
        category = "connection_failure"
    else:
        category = "unknown_sql_error"
    return sqlstate, native_number, category


def log_available_odbc_drivers_once() -> None:
    """Log the worker's ODBC driver inventory once when the app starts."""
    try:
        import pyodbc

        driver_names = ",".join(pyodbc.drivers())
        log_stage(
            logger,
            logging.INFO,
            "odbc_driver_inventory",
            stage="startup",
            driver_names=driver_names,
            safe_error_category="none" if driver_names else "no_odbc_drivers_available",
        )
    except Exception as exc:
        log_stage(
            logger,
            logging.WARNING,
            "odbc_driver_inventory_unavailable",
            stage="startup",
            exception_class=type(exc).__name__,
            safe_error_category="odbc_inventory_unavailable",
        )


class SQLRepository:
    def __init__(self, connection_string: str):
        self.connection_string = connection_string

    @classmethod
    def from_environment(cls) -> "SQLRepository":
        connection_string = os.getenv("SQL_CONNECTION_STRING")
        if not connection_string:
            raise SQLNotConfigured("SQL_CONNECTION_STRING is not configured")
        return cls(connection_string)

    @staticmethod
    def _log_failure(
        exc: Exception,
        stage: str,
        correlation_id: str | None,
        operation: str,
    ) -> SQLStageError:
        sqlstate, native_number, category = _sql_error_details(exc)
        log_stage(
            logger,
            logging.ERROR,
            "sql_operation_failed",
            correlation_id,
            stage=stage,
            operation=operation,
            exception_class=type(exc).__name__,
            sqlstate=sqlstate,
            native_error_number=native_number,
            safe_error_category=category,
        )
        return SQLStageError(stage, category, type(exc).__name__, sqlstate, native_number)

    def _connect(self, correlation_id: str | None, operation: str):
        try:
            import pyodbc

            connection = pyodbc.connect(self.connection_string, timeout=10, autocommit=False)
        except Exception as exc:
            raise self._log_failure(exc, "connect", correlation_id, operation) from None

        log_stage(
            logger,
            logging.INFO,
            "sql_connection_opened",
            correlation_id,
            stage="connect",
            operation=operation,
        )
        self._log_development_identity(connection, correlation_id, operation)
        return connection

    def _log_development_identity(
        self, connection: Any, correlation_id: str | None, operation: str
    ) -> None:
        is_development = os.getenv("APP_ENVIRONMENT", "").casefold() == "development"
        opted_in = os.getenv("SQL_DIAGNOSTIC_IDENTITY", "").casefold() in {"1", "true", "yes"}
        if not (is_development and opted_in):
            return
        try:
            cursor = self._execute(
                connection, "SELECT DB_NAME(), USER_NAME();", (), correlation_id, "development_identity"
            )
            row = self._fetchone(cursor, correlation_id, "development_identity")
            if row is None:
                log_stage(
                    logger, logging.WARNING, "sql_development_identity_unavailable", correlation_id,
                    stage="fetch", safe_error_category="empty_identity_result",
                )
                self._commit(connection, correlation_id, "development_identity")
                return
            log_stage(
                logger,
                logging.INFO,
                "sql_development_identity",
                correlation_id,
                stage="development_diagnostic",
                database_name=row[0],
                database_user=row[1],
            )
            self._commit(connection, correlation_id, "development_identity")
        except SQLStageError as exc:
            # This opt-in diagnostic must never block the actual database operation.
            self._rollback(connection, correlation_id, "development_identity")
            log_stage(
                logger,
                logging.WARNING,
                "sql_development_identity_unavailable",
                correlation_id,
                stage=exc.stage,
                safe_error_category=exc.safe_category,
            )
        except Exception as exc:
            self._rollback(connection, correlation_id, "development_identity")
            log_stage(
                logger,
                logging.WARNING,
                "sql_development_identity_unavailable",
                correlation_id,
                stage="development_diagnostic",
                exception_class=type(exc).__name__,
                safe_error_category="diagnostic_result_error",
            )

    def _execute(
        self,
        connection: Any,
        statement: str,
        parameters: tuple[Any, ...],
        correlation_id: str | None,
        operation: str,
    ) -> Any:
        try:
            cursor = connection.cursor()
            cursor.execute(statement, parameters)
        except Exception as exc:
            raise self._log_failure(exc, "execute", correlation_id, operation) from None
        log_stage(
            logger,
            logging.INFO,
            "sql_statement_executed",
            correlation_id,
            stage="execute",
            operation=operation,
        )
        return cursor

    def _fetchone(self, cursor: Any, correlation_id: str | None, operation: str):
        try:
            return cursor.fetchone()
        except Exception as exc:
            raise self._log_failure(exc, "fetch", correlation_id, operation) from None

    def _fetchall(self, cursor: Any, correlation_id: str | None, operation: str):
        try:
            return cursor.fetchall()
        except Exception as exc:
            raise self._log_failure(exc, "fetch", correlation_id, operation) from None

    def _commit(self, connection: Any, correlation_id: str | None, operation: str) -> None:
        try:
            connection.commit()
        except Exception as exc:
            raise self._log_failure(exc, "commit", correlation_id, operation) from None
        log_stage(
            logger,
            logging.INFO,
            "sql_transaction_committed",
            correlation_id,
            stage="commit",
            operation=operation,
        )

    def _rollback(self, connection: Any, correlation_id: str | None, operation: str) -> None:
        try:
            connection.rollback()
            log_stage(
                logger,
                logging.INFO,
                "sql_transaction_rolled_back",
                correlation_id,
                stage="rollback",
                operation=operation,
            )
        except Exception as exc:
            self._log_failure(exc, "rollback", correlation_id, operation)

    def record_event(self, event: dict[str, Any], correlation_id: str | None = None) -> bool:
        """Insert known envelope fields only; return False for a duplicate."""
        operation = "record_event"
        sql = """
        SET NOCOUNT ON;
        SET XACT_ABORT ON;
        DECLARE @ExistingEventId BIGINT;
        DECLARE @Inserted BIT = 0;

        SELECT @ExistingEventId = EventId
        FROM Control.WebhookEventInbox WITH (UPDLOCK, HOLDLOCK)
        WHERE EventId = ?;

        IF @ExistingEventId IS NULL
        BEGIN
            INSERT Control.WebhookEventInbox
            (
                EventId, EventTime, EventType, Operation, EeId, CoId
            )
            VALUES
            (
                ?, CONVERT(DATETIMEOFFSET(3), ?), ?, ?, ?, ?
            );
            SET @Inserted = 1;
        END;

        SELECT @Inserted AS Inserted;
        """
        parameters = (
            event["event_id"], event["event_id"], event["event_time"],
            event["event_type"], event["operation"], event["ee_id"], event["co_id"],
        )
        connection = self._connect(correlation_id, operation)
        try:
            cursor = self._execute(connection, sql, parameters, correlation_id, operation)
            row = self._fetchone(cursor, correlation_id, operation)
            inserted = bool(row[0])
            self._commit(connection, correlation_id, operation)
        except SQLStageError:
            self._rollback(connection, correlation_id, operation)
            raise
        finally:
            connection.close()

        log_stage(
            logger,
            logging.INFO,
            "webhook_event_inserted" if inserted else "webhook_event_duplicate",
            correlation_id,
            stage="insert_outcome",
            outcome="inserted" if inserted else "duplicate",
        )
        return inserted

    def record_subscription_test(self, correlation_id: str | None = None) -> None:
        """Insert a non-actionable subscription-test row with a fresh synthetic key."""
        statement = """
        SET XACT_ABORT ON;
        INSERT Control.WebhookEventInbox
        (
            EventId, EventTime, EventType, Operation, EeId, CoId, ProcessingStatus,
            IntegrationRunId
        )
        VALUES
        (
            ?, TODATETIMEOFFSET(SYSUTCDATETIME(), '+00:00'),
            N'SubscriptionTestConnection', N'Test', NULL, NULL, N'Unsupported', NULL
        );
        """
        seen_ids: set[int] = set()
        for attempt in range(1, SUBSCRIPTION_TEST_ID_COLLISION_RETRIES + 1):
            event_id = SUBSCRIPTION_TEST_EVENT_ID_MIN + secrets.randbelow(
                SUBSCRIPTION_TEST_EVENT_ID_COUNT
            )
            while event_id in seen_ids:
                event_id = SUBSCRIPTION_TEST_EVENT_ID_MIN + secrets.randbelow(
                    SUBSCRIPTION_TEST_EVENT_ID_COUNT
                )
            seen_ids.add(event_id)
            try:
                self._execute_transaction(
                    statement,
                    (event_id,),
                    correlation_id,
                    "record_subscription_test",
                )
            except SQLStageError as exc:
                if exc.safe_category == "duplicate_key" and attempt < SUBSCRIPTION_TEST_ID_COLLISION_RETRIES:
                    log_stage(
                        logger,
                        logging.WARNING,
                        "subscription_test_synthetic_id_collision",
                        correlation_id,
                        stage="execute",
                        attempt=attempt,
                        safe_error_category="duplicate_key_retrying",
                    )
                    continue
                raise

            log_stage(
                logger,
                logging.INFO,
                "subscription_test_row_inserted",
                correlation_id,
                stage="insert_outcome",
                processing_status="Unsupported",
                safe_error_category="none",
            )
            return

        # Every attempt either returned or raised; this is a defensive guard.
        raise SQLStageError(
            "execute", "synthetic_id_retry_exhausted", "SyntheticIdError", None, None
        )

    def _execute_transaction(
        self,
        statement: str,
        parameters: tuple[Any, ...],
        correlation_id: str | None,
        operation: str,
    ) -> int:
        connection = self._connect(correlation_id, operation)
        try:
            cursor = self._execute(connection, statement, parameters, correlation_id, operation)
            rowcount = cursor.rowcount
            self._commit(connection, correlation_id, operation)
            return rowcount
        except SQLStageError:
            self._rollback(connection, correlation_id, operation)
            raise
        finally:
            connection.close()

    def start_run(self, run_id: str, mode: str, correlation_id: str | None = None) -> None:
        self._execute_transaction(
            "INSERT Control.IntegrationRun (RunId, SourceSystem, ProcessingMode) VALUES (?, N'UKG', ?);",
            (run_id, mode),
            correlation_id,
            "start_run",
        )

    def count_pending(self, correlation_id: str | None = None) -> int:
        operation = "count_pending"
        connection = self._connect(correlation_id, operation)
        try:
            cursor = self._execute(
                connection,
                "SELECT COUNT_BIG(*) FROM Control.WebhookEventInbox WHERE ProcessingStatus IN (N'Received', N'Pending', N'Failed');",
                (), correlation_id, operation,
            )
            row = self._fetchone(cursor, correlation_id, operation)
            count = int(row[0])
            self._commit(connection, correlation_id, operation)
            return count
        except SQLStageError:
            self._rollback(connection, correlation_id, operation)
            raise
        finally:
            connection.close()

    def get_pending_events(
        self, limit: int = 100, correlation_id: str | None = None
    ) -> list[dict[str, Any]]:
        operation = "get_pending_events"
        connection = self._connect(correlation_id, operation)
        try:
            cursor = self._execute(
                connection,
                """SELECT TOP (?) EventId, EventTime, EventType, Operation, EeId, CoId
                   FROM Control.WebhookEventInbox
                   WHERE ProcessingStatus IN (N'Received', N'Pending', N'Failed')
                   ORDER BY ProcessingStatus, EventTime, EventId;""",
                (limit,), correlation_id, operation,
            )
            rows = self._fetchall(cursor, correlation_id, operation)
            pending = [
                {
                    "event_id": row.EventId,
                    "event_time": row.EventTime,
                    "event_type": row.EventType,
                    "operation": row.Operation,
                    "ee_id": row.EeId,
                    "co_id": row.CoId,
                }
                for row in rows
            ]
            self._commit(connection, correlation_id, operation)
            return pending
        except SQLStageError:
            self._rollback(connection, correlation_id, operation)
            raise
        finally:
            connection.close()

    def finish_blocked_run(
        self, run_id: str, pending_count: int, safe_summary: str, correlation_id: str | None = None
    ) -> None:
        statement = """UPDATE Control.IntegrationRun
            SET RunStatus = N'Blocked', RunCompletedAt = TODATETIMEOFFSET(SYSUTCDATETIME(), '+00:00'),
                PendingCount = ?, ErrorCount = 0, SafeErrorSummary = ?
            WHERE RunId = ? AND RunStatus = N'Running';"""
        count = self._execute_transaction(
            statement, (pending_count, safe_summary, run_id), correlation_id, "finish_blocked_run"
        )
        if count != 1:
            log_stage(
                logger, logging.ERROR, "sql_run_state_not_updated", correlation_id,
                stage="execute", operation="finish_blocked_run", safe_error_category="invalid_state",
            )
            raise SQLStageError("execute", "invalid_state", "DatabaseStateError", None, None)

    def fail_run(
        self, run_id: str, safe_summary: str, correlation_id: str | None = None
    ) -> None:
        statement = """UPDATE Control.IntegrationRun
            SET RunStatus = N'Failed', RunCompletedAt = TODATETIMEOFFSET(SYSUTCDATETIME(), '+00:00'),
                ErrorCount = 1, SafeErrorSummary = ?
            WHERE RunId = ? AND RunStatus = N'Running';"""
        self._execute_transaction(statement, (safe_summary, run_id), correlation_id, "fail_run")
