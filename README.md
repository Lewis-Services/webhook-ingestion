# UKG to JDE Function App and SQL design

This repository contains the Python v2 Azure Functions foundation and a proposed Azure SQL persistence design. The SQL database is not provisioned in the repository. Function operations require the approved SQL schema and `SQL_CONNECTION_STRING`; event/run records use SQL, and the webhook handler stores only known envelope fields. No ADF pipeline is included.

## Functions and contracts

Azure Functions v2 HTTP routes are prefixed with `/api` by the host.

### `ukg_webhook`

- `POST /api/ukg/webhook`
- Function key authorization plus UKG webhook signature verification.
- Verifies the original request bytes, parses the documented sample envelope, and inserts known fields into `Control.WebhookEventInbox`. It does not store the full webhook body.
- Returns `200` only after SQL insert or duplicate detection succeeds. A repeated `EventId` returns `200` with `duplicate: true` without changing the existing row; invalid signatures return `401`, malformed/unsupported envelopes return `400`, and missing SQL configuration or persistence failures return `503` for delivery retry.
- `EventId` is the primary key and event-level deduplication key for this single-tenant integration. It is not an employee matching key.

The inbox retains `EventId`, `EventTime`, `EventType`, `Operation`, `Payload.EeId`, and `Payload.CoId`, plus system-managed processing metadata. It intentionally omits `Payload.PersonId`, `Payload.IntegrationRecordId`, `Tenant`, and the complete webhook body.

Request: UKG JSON event plus signed headers `webhook-id`, `webhook-timestamp`, and `webhook-signature`.

Response:

```json
{"status":"accepted","duplicate":false,"correlation_id":"..."}
```

UKG's subscription endpoint test is recognized after the same mandatory HMAC verification and JSON parsing, before employee-event parsing. Only the exact two-field object (`Test` = `Subscription Test Connection`, `message` = `Hi, This is subscription test connection API.`) matches. The Function inserts a non-actionable row in `Control.WebhookEventInbox` and returns `200` with `{"status":"test_notification_received","correlation_id":"..."}` only after the SQL transaction commits. The row uses `EventType='SubscriptionTestConnection'`, `Operation='Test'`, the current UTC time, NULL employee keys and run ID, and `ProcessingStatus='Unsupported'`. It gets a fresh synthetic `EventId` from a reserved negative `BIGINT` range; primary-key collisions are retried with another fresh value. The table stores neither the static message, request body, nor HMAC headers. Pending-event queries include only `Received`, `Pending`, and `Failed`, so the `Unsupported` test row is not claimed. Near-matches and other unrecognized shapes continue to return `400`; persistence failures return the existing generic `503` response.

### `process_ukg_events`

- `POST /api/ukg/process`
- Function key authorization. Send the key using `x-functions-key` or the Azure Functions `code` query parameter. Keep this key separate from UKG's HMAC secret.
- Request body:

```json
{"mode":"scheduled"}
```

or

```json
{"mode":"expedite"}
```

- `scheduled` is intended for the three-hour ADF cadence and the approved parameterized Employee Changes request.
- `expedite` is intended for a controlled manual run with full pagination on that same approved endpoint. The observed freshness difference needs confirmation with UKG.
- Both modes create a SQL `IntegrationRun`, count pending inbox records, and currently finish with a compact `blocked` response. No Employee Changes URL/auth/query/pagination schema or JDE mapping is available, so no UKG call or output file is produced. Invalid requests return `400`; SQL failures return `503`.

Example current processing response:

```json
{
  "run_id":"...",
  "mode":"scheduled",
  "status":"blocked",
  "file_path":null,
  "record_count":0,
  "pending_count":1,
  "error_count":0,
  "blockers":["ukg_api_contract_and_jde_mapping_required"],
  "correlation_id":"..."
}
```

## SQL persistence and states

Use the SQL objects described in [sql/README.md](sql/README.md), applying the reviewed DDL in order: `sql/ddl/001_schemas_and_control.sql`, then `sql/ddl/002_bronze_silver_scaffold.sql`. Those scripts are design artifacts and have not been executed. They define a clean target schema; if an earlier draft was applied in any environment, prepare a separate reviewed migration first. `Control.WebhookEventInbox` stores the supplied envelope fields, `Control.IntegrationRun` tracks process attempts, and delivery state will use `Control.JdeFileDelivery`. The current processing route leaves inbox events pending and records a blocked run until the approved UKG API and JDE mapping are implemented.

Employee matching is separate from event deduplication: webhook `Payload.EeId` maps to Employee Changes `employeeId`, and `Payload.CoId` maps to `companyId`. Match on both values together. The approved endpoint's other employee fields remain unknown and are not modeled as confirmed columns.

The eventual staged path is `staging/jde/new-hire/yyyy/MM/dd/{run_id}/...`, separate from the UBE pickup path. Staging/pickup storage type, ready-marker behavior, and archive path remain unresolved. SQL tracks file paths and status; it does not contain JDE file contents. The optional Bronze JSON column must remain `NULL` unless raw employee-change retention is approved.

## Configuration

Configure Function App settings or local settings outside source control:

| Setting | Purpose |
| --- | --- |
| `SQL_CONNECTION_STRING` | Connection to the approved SQL Server/Azure SQL database. Use a Key Vault reference or an approved managed identity connection pattern; grant only required table permissions. |
| `UKG_WEBHOOK_SECRET` | HMAC secret configured on the UKG Webhooks subscription. Required; missing secret fails closed. Store through Key Vault references. |
| `FUNCTIONS_WORKER_RUNTIME` | Set to `python`. |
| `FUNCTIONS_EXTENSION_VERSION` | Set to `~4`. |

The deployment must include a compatible Microsoft ODBC driver for `pyodbc`. The Function SQL identity needs `INSERT` on `Control.WebhookEventInbox` for normal and subscription-test deliveries. The existing processing path also requires its configured read/update access to inbox rows and insert/update/read access to `Control.IntegrationRun`. Never log the connection string, credentials, signature values, or webhook body.

## Security and readiness

UKG's [Webhooks User Guide](https://developer.ukg.com/proplatform/docs/ukg-webhooks-user-guide) directs consumers to the Svix verification scheme with `svix` header names changed to `webhook`. This project verifies the raw request bytes. Configure and test the secret/header representation with a UKG test event before production.

The process endpoint currently uses a Function key. Before ADF production use, configure an approved service-to-service identity, preferably ADF managed identity through Function App authentication where supported. Manual expedite needs a controlled operator identity. SQL roles should allow the Function to insert/read/update only the required control rows; Bronze should be append-only if enabled.

Correlation IDs may be supplied with `x-correlation-id` when they contain at most 100 ASCII letters, digits, periods, underscores, or hyphens; otherwise the Function generates one. Logs contain operational metadata only, not employee IDs, event bodies, credentials, or signature values.

## Diagnostics

Function and SQL logs use Application Insights custom dimensions for `correlation_id`, stage, outcome, and safe error category. Signature logs include presence booleans for `webhook-id`, `webhook-timestamp`, and `webhook-signature`, never their values. Envelope failures log reason codes; missing employee match keys are logged as a warning while retaining the existing HTTP acceptance behavior. SQL failures log only exception class, SQLSTATE, native error number when available, failure stage (`connect`, `execute`, `fetch`, `commit`, or `rollback`), and mapped category such as `permission_denied`, `invalid_object`, `duplicate_key`, `conversion_error`, or `connection_timeout`. They do not include exception messages, SQL text, parameters, or connection details. Available ODBC driver names are logged once when the Function worker starts.

The SQL database/user identity check is disabled by default. To enable it for development only, set both `APP_ENVIRONMENT=development` and `SQL_DIAGNOSTIC_IDENTITY=true`; it logs the connected database name and database user, without the server name or credentials. Do not enable this diagnostic in production.

## Blockers and next iterations

1. Confirm the endpoint's representation and normalization for `employeeId` and `companyId`; the inbox widths are `NVARCHAR(50)` and `NVARCHAR(20)` to align with the confirmed webhook keys.
2. Supply the approved Employee Changes URL, auth method, scheduled query parameters/window semantics, full-pagination pattern, remaining response schema, and retry/rate-limit expectations. Do not call other UKG REST endpoints.
3. Supply JDE columns, formats/defaults, file naming, new-hire eligibility, and cross-run duplicate transaction identity.
4. Confirm database platform/version, SQL deployment process, storage for staging and UBE pickup (Azure Files vs ADLS), ready-marker/atomic publish, and archive ownership/path.
5. Decide Bronze JSON retention, employee-data classification, access, and retention duration. Silver currently contains identity/audit scaffolding only.
6. Build the later ADF pipeline to invoke `POST /api/ukg/process` with `{"mode":"scheduled"}` every three hours using the approved identity. Transfer only a staged file returned with status `ready`, then update `Control.JdeFileDelivery` after copy/move verification.

## Design artifacts

- [Preliminary architecture diagram](docs/architecture/ukg-to-jde-new-hire.md)
- [SQL MVP design and review decisions](sql/README.md)
