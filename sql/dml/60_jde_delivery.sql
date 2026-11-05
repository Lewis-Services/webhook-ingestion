/* Idempotently register one completed staged file. File bytes stay in storage. */
SET XACT_ABORT ON;
DECLARE @RunId UNIQUEIDENTIFIER = NULL; -- Bind completed run.
DECLARE @DeliveryKey NVARCHAR(200) = NULL; -- Stable approved run/file key.
DECLARE @StagedFilePath NVARCHAR(1024) = NULL; -- Returned by processing Function.
DECLARE @RecordCount INT = 0;
DECLARE @DeliveryId UNIQUEIDENTIFIER;

BEGIN TRY
    BEGIN TRANSACTION;
    SELECT @DeliveryId = DeliveryId
    FROM Control.JdeFileDelivery WITH (UPDLOCK, HOLDLOCK)
    WHERE DeliveryKey = @DeliveryKey;

    IF @DeliveryId IS NOT NULL AND EXISTS
       (SELECT 1 FROM Control.JdeFileDelivery WHERE DeliveryId = @DeliveryId AND StagedFilePath <> @StagedFilePath)
        THROW 51001, 'Delivery key is already associated with a different staged path.', 1;

    IF @DeliveryId IS NULL
    BEGIN
        SELECT @DeliveryId = DeliveryId
        FROM Control.JdeFileDelivery WITH (UPDLOCK, HOLDLOCK)
        WHERE StagedFilePath = @StagedFilePath;
        IF @DeliveryId IS NOT NULL
            THROW 51002, 'Staged path is already associated with a different delivery key.', 1;
    END;

    IF @DeliveryId IS NULL
    BEGIN
        SET @DeliveryId = NEWID();
        INSERT Control.JdeFileDelivery (DeliveryId, RunId, DeliveryKey, StagedFilePath, RecordCount)
        VALUES (@DeliveryId, @RunId, @DeliveryKey, @StagedFilePath, @RecordCount);
    END;
    COMMIT TRANSACTION;
    SELECT @DeliveryId AS DeliveryId;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO

/* ADF's later pipeline can read only staged file metadata, never employee rows. */
SELECT DeliveryId, RunId, DeliveryKey, StagedFilePath, RecordCount, CreatedAt
FROM Control.JdeFileDelivery
WHERE DeliveryStatus = N'Staged'
ORDER BY CreatedAt, DeliveryId;
GO

/* ADF claims a staged delivery before moving/copying the file. */
DECLARE @DeliveryIdToClaim UNIQUEIDENTIFIER = NULL;
UPDATE Control.JdeFileDelivery
SET DeliveryStatus = N'InProgress',
    DeliveryStartedAt = TODATETIMEOFFSET(SYSUTCDATETIME(), '+00:00'),
    SafeFailureSummary = NULL
WHERE DeliveryId = @DeliveryIdToClaim AND DeliveryStatus = N'Staged';
/* Require one affected row; otherwise another worker has claimed/completed it. */
GO

/* ADF marks the outcome only after transfer verification. Bind safe paths. */
DECLARE @DeliveredId UNIQUEIDENTIFIER = NULL;
DECLARE @Outcome NVARCHAR(20) = N'Delivered'; -- Delivered or Failed
DECLARE @PickupFilePath NVARCHAR(1024) = NULL;
DECLARE @ArchiveFilePath NVARCHAR(1024) = NULL;
DECLARE @SafeFailureSummary NVARCHAR(1000) = NULL;
UPDATE Control.JdeFileDelivery
SET DeliveryStatus = @Outcome,
    PickupFilePath = @PickupFilePath,
    ArchiveFilePath = @ArchiveFilePath,
    DeliveredAt = CASE WHEN @Outcome = N'Delivered' THEN TODATETIMEOFFSET(SYSUTCDATETIME(), '+00:00') ELSE NULL END,
    SafeFailureSummary = @SafeFailureSummary
WHERE DeliveryId = @DeliveredId AND DeliveryStatus = N'InProgress'
  AND @Outcome IN (N'Delivered', N'Failed');
GO

/* Archive transition after approved post-pickup archive verification. */
DECLARE @ArchivedId UNIQUEIDENTIFIER = NULL;
UPDATE Control.JdeFileDelivery
SET DeliveryStatus = N'Archived',
    ArchivedAt = TODATETIMEOFFSET(SYSUTCDATETIME(), '+00:00')
WHERE DeliveryId = @ArchivedId AND DeliveryStatus = N'Delivered';
GO
