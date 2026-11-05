/*
  Atomically claim new-hire inbox rows. Run row must already exist and be
  Running. Rows in Unsupported status are deliberately excluded from claiming.
  Validate READPAST behavior against the target database's isolation settings.
*/
SET XACT_ABORT ON;
DECLARE @RunId UNIQUEIDENTIFIER = NULL; -- Bind active IntegrationRun.RunId.
DECLARE @BatchSize INT = 100;
DECLARE @NewHireEventType NVARCHAR(100) = N'PersonHiredEvent'; -- Confirm event catalog.

BEGIN TRY
    BEGIN TRANSACTION;

    IF NOT EXISTS
       (SELECT 1 FROM Control.IntegrationRun WITH (UPDLOCK, HOLDLOCK)
        WHERE RunId = @RunId AND RunStatus = N'Running')
        THROW 51000, 'Run is missing or not Running.', 1;

    ;WITH Claimable AS
    (
        SELECT TOP (@BatchSize) EventId
        FROM Control.WebhookEventInbox WITH (UPDLOCK, READPAST, ROWLOCK, READCOMMITTEDLOCK)
        WHERE ProcessingStatus IN (N'Received', N'Pending', N'Failed')
          AND EventType = @NewHireEventType
        ORDER BY EventTime, EventId
    )
    UPDATE e
    SET ProcessingStatus = N'Processing',
        AttemptCount = AttemptCount + 1,
        LastAttemptAtUtc = TODATETIMEOFFSET(SYSUTCDATETIME(), '+00:00'),
        IntegrationRunId = @RunId,
        SafeErrorCode = NULL
    OUTPUT inserted.EventId, inserted.EventTime, inserted.EventType,
           inserted.Operation, inserted.EeId, inserted.CoId,
           inserted.AttemptCount
    FROM Control.WebhookEventInbox AS e
    INNER JOIN Claimable AS c ON c.EventId = e.EventId;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO

/* Explicit status transition after the worker handles each claimed event. */
DECLARE @EventIdToFinish BIGINT = NULL;
DECLARE @RunIdForEvent UNIQUEIDENTIFIER = NULL;
DECLARE @NextStatus NVARCHAR(20) = N'Processed'; -- Processed, Pending, Unsupported, Failed
DECLARE @SafeErrorCode NVARCHAR(100) = NULL;
UPDATE Control.WebhookEventInbox
SET ProcessingStatus = @NextStatus,
    ProcessedAtUtc = CASE WHEN @NextStatus = N'Processed' THEN TODATETIMEOFFSET(SYSUTCDATETIME(), '+00:00') ELSE NULL END,
    SafeErrorCode = @SafeErrorCode
WHERE EventId = @EventIdToFinish AND IntegrationRunId = @RunIdForEvent
  AND ProcessingStatus = N'Processing'
  AND @NextStatus IN (N'Processed', N'Pending', N'Unsupported', N'Failed');
GO
