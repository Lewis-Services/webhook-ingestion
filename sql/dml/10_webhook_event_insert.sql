/*
  Idempotent insert for the confirmed event schema. EventId is the unique key
  for this single-tenant integration. Existing rows are not updated on retry.
  PersonId, IntegrationRecordId, Tenant, and the complete request body are
  intentionally omitted.
*/
SET XACT_ABORT ON;

DECLARE @EventId BIGINT = 6214358703;
DECLARE @EventTime DATETIMEOFFSET(3) = '2026-09-22T12:44:13.267+00:00';
DECLARE @EventType NVARCHAR(100) = N'PersonHiredEvent';
DECLARE @Operation NVARCHAR(50) = N'Update';
DECLARE @EeId NVARCHAR(50) = N'GK205G000030';
DECLARE @CoId NVARCHAR(20) = N'KTDSQ';
DECLARE @Inserted BIT = 0;

BEGIN TRY
    BEGIN TRANSACTION;

    IF NOT EXISTS
    (
        SELECT 1
        FROM Control.WebhookEventInbox WITH (UPDLOCK, HOLDLOCK)
        WHERE EventId = @EventId
    )
    BEGIN
        INSERT Control.WebhookEventInbox
            (EventId, EventTime, EventType, Operation, EeId, CoId)
        VALUES
            (@EventId, @EventTime, @EventType, @Operation, @EeId, @CoId);
        SET @Inserted = 1;
    END;

    COMMIT TRANSACTION;
    SELECT @Inserted AS Inserted, @EventId AS EventId;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
