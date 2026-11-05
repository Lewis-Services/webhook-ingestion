# SQL MVP design for UKG to JDE

These are Azure SQL Database / SQL Server T-SQL design artifacts. No database platform is configured in this repository. Azure SQL Database remains the implementation assumption; the scripts have not been executed. DDL is guarded with `IF ... IS NULL` checks and does not drop objects. The scripts define the clean target schema, not an automatic migration of an earlier draft schema. If that earlier draft was applied in an environment, review and prepare a separate migration before using these scripts there.

## Objects and architecture mapping

| Object | Purpose | Architecture role |
| --- | --- | --- |
| `Control.IntegrationRun` | Processing attempt, mode/window, timings, counts, and safe error summary. | Function and later ADF run coordination. |
| `Control.WebhookEventInbox` | Confirmed webhook fields plus retry, status, and run metadata. `EventId` is the primary key. | Durable webhook event-control store. |
| `Bronze.UkgEmployeeChangeRaw` | Append-only endpoint record with run/ingestion metadata and only confirmed identifiers; optional source JSON requires retention approval. | Reusable source layer, separate from webhook metadata. |
| `Silver.EmployeeCurrent` | Canonical identity/audit table keyed by `(CompanyId, EmployeeId)`. | Reusable employee-data layer. No other endpoint attributes are assumed. |
| `Control.JdeFileDelivery` | Staged/pickup/archive paths, counts, state, and timestamps; file bytes remain in file storage. | Function staging and later ADF delivery tracking. |

DDL is in `ddl/`; representative statements and clearly marked templates are in `dml/`.

## Confirmed event schema

`Control.WebhookEventInbox` stores only:

| Webhook field | SQL column | SQL type |
| --- | --- | --- |
| `EventId` | `EventId` | `BIGINT`, primary key |
| `EventTime` | `EventTime` | `DATETIMEOFFSET(3)` |
| `EventType` | `EventType` | `NVARCHAR(100)` |
| `Operation` | `Operation` | `NVARCHAR(50)` |
| `Payload.EeId` | `EeId` | `NVARCHAR(50)` |
| `Payload.CoId` | `CoId` | `NVARCHAR(20)` |

The table also has system-managed `ReceivedAtUtc`, `ProcessingStatus`, `AttemptCount`, `LastAttemptAtUtc`, `ProcessedAtUtc`, `IntegrationRunId`, and `SafeErrorCode`. It does **not** store `Payload.PersonId`, `Payload.IntegrationRecordId`, `Tenant`, or the complete webhook body. This integration is confirmed to use a single UKG tenant, and `EventId` is the confirmed event-level deduplication key. A duplicate insert leaves all existing processing metadata unchanged.

The exact UKG subscription-test object is handled after HMAC verification and JSON parsing, before normal employee-event parsing. It creates a row in this same table with `EventType='SubscriptionTestConnection'`, `Operation='Test'`, current UTC `EventTime`, NULL `EeId`, `CoId`, and `IntegrationRunId`, and `ProcessingStatus='Unsupported'`. The static test message, raw request body, and HMAC headers are not stored. Since the payload has no `EventId`, the Function generates a new synthetic key per attempt from the integration's reserved negative `BIGINT` range (`-9000000000000000000` through `-8000000000000000001`) and retries with a different key if the primary key is already occupied. The schema has a signed `BIGINT` primary key and no nonnegative-value check; the reservation assumes UKG event IDs are nonnegative. Confirm that assumption against UKG's event contract. Pending readers filter to `Received`, `Pending`, or `Failed`, so `Unsupported` test rows are not claimed.

## Confirmed employee matching contract

Webhook `Payload.EeId` maps to the approved Employee Changes endpoint's `employeeId`; `Payload.CoId` maps to `companyId`. Match on both values together where appropriate. `EventId` identifies/deduplicates the webhook event only; never use it as an employee key. The helper in `integration/ukg.py` encodes this pairwise comparison.

The endpoint response schema is otherwise unavailable. Bronze/Silver must not gain guessed response columns. The current Bronze scaffold contains only the two confirmed identifiers and run/ingestion metadata; `SourceRecordJson` is nullable and must remain `NULL` unless raw endpoint record retention is approved. Silver contains only the identity pair and source/ingestion metadata until the full endpoint contract and downstream data requirements are supplied.

## Idempotency, retries, and states

- `EventId` is the inbox primary key. `sql/dml/10_webhook_event_insert.sql` checks the key under update/serializable locks and inserts only when absent. On duplicate, it does not update or reset event status, counters, timestamps, error code, or run association.
- `sql/dml/30_claim_pending_events.sql` claims eligible rows with `UPDLOCK`/`READPAST`, increments attempts, records `LastAttemptAtUtc`, and associates `IntegrationRunId`. Confirm `READPAST` behavior with the target database isolation settings.
- Supported statuses are `Received`, `Pending`, `Processing`, `Processed`, `Unsupported`, and `Failed`. Unsupported event types remain visible for later routing.
- Bronze is append-only by application/permissions. Repeated pages could create duplicate Bronze records until the Employee Changes response supplies a stable record key.
- JDE delivery has unique delivery key, staged path, and pickup path. SQL prevents duplicate tracking/concurrent claims; exactly-once physical handoff still requires deterministic file naming and verified transfer behavior.

## Retention, PII, and access

The inbox retains the exact six webhook fields above and operational metadata. `EeId` and `CoId` are employee identifiers and need an owner-approved classification, access model, and retention period. The Bronze JSON field may contain employee PII: leave it `NULL` until the owner approves purpose, exact content, access, retention, and deletion. Do not put credentials, signatures, full request bodies, or employee data in safe error codes. Grant the Function identity `INSERT` on `Control.WebhookEventInbox` for webhook and subscription-test rows, plus the existing least-privilege `SELECT`/`UPDATE` access needed to count and claim inbox rows and `INSERT`/`SELECT`/`UPDATE` access on `Control.IntegrationRun` for run lifecycle. No schema or grant change is included here. Make Bronze append-only if enabled.

The Function SQL repository maps the confirmed envelope to inbox columns and does not retain the full body. SQL persistence requires `SQL_CONNECTION_STRING` and the DDL to be applied to the approved database before webhook acknowledgements can succeed. No database connection or schema execution has occurred in this repository iteration.

## DML and later ADF contract

- `dml/10_webhook_event_insert.sql`: insert with duplicate no-op by `EventId`.
- `dml/20_run_lifecycle.sql`: start and complete/fail a run.
- `dml/30_claim_pending_events.sql`: claim pending events and transition their status.
- `dml/40_bronze_append_template.sql`: non-executable template until the endpoint page/record schema is supplied; it uses only `employeeId` and `companyId`.
- `dml/50_silver_upsert_template.sql`: non-executable template keyed by the confirmed pair, pending validated endpoint records and attributes.
- `dml/60_jde_delivery.sql`: register and track delivery state without copying file contents into SQL.

Later ADF work should call `POST /api/ukg/process` with `{"mode":"scheduled"}` every three hours, or `{"mode":"expedite"}` for a controlled manual run. When the Function returns `ready`, ADF should transfer/verify the staged file and update `Control.JdeFileDelivery`. ADF must not receive employee rows or file contents. Authentication and transfer/recovery details remain to be designed with the pipeline.

## Remaining assumptions and questions

1. Azure SQL Database is assumed; confirm target/version, schema ownership, deployment/migration runner, and SQL identity.
2. Confirm the endpoint's representation and normalization for `employeeId`/`companyId`; the selected widths align to the webhook key fields.
3. Supply the rest of the approved Employee Changes response schema, auth, pagination, window semantics, rate limits, and retry behavior. Do not call another UKG endpoint.
4. Decide whether Bronze JSON is retained; classify it, approve retention, and define access/deletion policy.
5. Confirm JDE mapping, eligibility rules, cross-run duplicate onboarding identity, staging/UBE pickup storage (Azure Files vs ADLS), ready-marker/atomic publish, and archive ownership/path.
6. Decide whether Fabric consumes the SQL MVP or replaces it later; retain a clear operational control transaction boundary.
