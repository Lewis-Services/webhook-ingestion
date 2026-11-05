/* Start a scheduled or expedite run and capture its generated RunId. */
SET XACT_ABORT ON;
DECLARE @RunId UNIQUEIDENTIFIER;
DECLARE @Mode NVARCHAR(20) = N'scheduled'; -- or N'expedite'
DECLARE @WindowStart DATETIMEOFFSET(3) = NULL; -- Set only from approved UKG window semantics.
DECLARE @WindowEnd DATETIMEOFFSET(3) = NULL;

INSERT Control.IntegrationRun (ProcessingMode, RequestedWindowStart, RequestedWindowEnd)
OUTPUT inserted.RunId
VALUES (@Mode, @WindowStart, @WindowEnd);
/* Caller retains the returned RunId and passes it to all work in this run. */
GO

/* Complete a run after all pages, validation, and staging operations succeed. */
DECLARE @CompleteRunId UNIQUEIDENTIFIER = NULL; -- Bind RunId from the start operation.
DECLARE @ReadCount INT = 0;
DECLARE @MatchedCount INT = 0;
DECLARE @WrittenCount INT = 0;
DECLARE @PendingCount INT = 0;
UPDATE Control.IntegrationRun
SET RunStatus = N'Completed',
    RunCompletedAt = TODATETIMEOFFSET(SYSUTCDATETIME(), '+00:00'),
    ReadCount = @ReadCount,
    MatchedCount = @MatchedCount,
    WrittenCount = @WrittenCount,
    PendingCount = @PendingCount,
    ErrorCount = 0,
    SafeErrorSummary = NULL
WHERE RunId = @CompleteRunId AND RunStatus = N'Running';
/* Require exactly one affected row in the caller. */
GO

/* Failure/blocked completion: bind a sanitized, non-PII summary. */
DECLARE @FailedRunId UNIQUEIDENTIFIER = NULL; -- Bind RunId.
DECLARE @FailureStatus NVARCHAR(20) = N'Failed'; -- or N'Blocked'
DECLARE @SafeSummary NVARCHAR(1000) = N'approved_api_contract_not_configured';
DECLARE @ErrorCount INT = 1;
UPDATE Control.IntegrationRun
SET RunStatus = @FailureStatus,
    RunCompletedAt = TODATETIMEOFFSET(SYSUTCDATETIME(), '+00:00'),
    ErrorCount = @ErrorCount,
    SafeErrorSummary = @SafeSummary
WHERE RunId = @FailedRunId AND RunStatus = N'Running'
  AND @FailureStatus IN (N'Failed', N'Blocked');
GO
