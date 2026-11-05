/* Azure SQL Database / SQL Server T-SQL design. No DROP statements. */
IF SCHEMA_ID(N'Control') IS NULL EXEC(N'CREATE SCHEMA Control');
IF SCHEMA_ID(N'Bronze') IS NULL EXEC(N'CREATE SCHEMA Bronze');
IF SCHEMA_ID(N'Silver') IS NULL EXEC(N'CREATE SCHEMA Silver');
GO

IF OBJECT_ID(N'Control.IntegrationRun', N'U') IS NULL
BEGIN
    CREATE TABLE Control.IntegrationRun
    (
        RunId               UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_IntegrationRun_RunId DEFAULT NEWSEQUENTIALID(),
        SourceSystem        NVARCHAR(30) NOT NULL CONSTRAINT DF_IntegrationRun_Source DEFAULT N'UKG',
        ProcessingMode      NVARCHAR(20) NOT NULL,
        RequestedWindowStart DATETIMEOFFSET(3) NULL,
        RequestedWindowEnd   DATETIMEOFFSET(3) NULL,
        RunStartedAt        DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_IntegrationRun_Started DEFAULT TODATETIMEOFFSET(SYSUTCDATETIME(), '+00:00'),
        RunCompletedAt      DATETIMEOFFSET(3) NULL,
        RunStatus           NVARCHAR(20) NOT NULL CONSTRAINT DF_IntegrationRun_Status DEFAULT N'Running',
        ReadCount           INT NOT NULL CONSTRAINT DF_IntegrationRun_ReadCount DEFAULT (0),
        MatchedCount        INT NOT NULL CONSTRAINT DF_IntegrationRun_MatchedCount DEFAULT (0),
        WrittenCount        INT NOT NULL CONSTRAINT DF_IntegrationRun_WrittenCount DEFAULT (0),
        PendingCount        INT NOT NULL CONSTRAINT DF_IntegrationRun_PendingCount DEFAULT (0),
        ErrorCount          INT NOT NULL CONSTRAINT DF_IntegrationRun_ErrorCount DEFAULT (0),
        SafeErrorSummary    NVARCHAR(1000) NULL,
        CONSTRAINT PK_IntegrationRun PRIMARY KEY (RunId),
        CONSTRAINT CK_IntegrationRun_Mode CHECK (ProcessingMode IN (N'scheduled', N'expedite')),
        CONSTRAINT CK_IntegrationRun_Status CHECK (RunStatus IN (N'Running', N'Completed', N'Failed', N'Blocked')),
        CONSTRAINT CK_IntegrationRun_Window CHECK
            (RequestedWindowStart IS NULL OR RequestedWindowEnd IS NULL OR RequestedWindowStart <= RequestedWindowEnd),
        CONSTRAINT CK_IntegrationRun_Counts CHECK
            (ReadCount >= 0 AND MatchedCount >= 0 AND WrittenCount >= 0 AND PendingCount >= 0 AND ErrorCount >= 0)
    );
END;
GO

IF OBJECT_ID(N'Control.WebhookEventInbox', N'U') IS NULL
BEGIN
    CREATE TABLE Control.WebhookEventInbox
    (
        EventId              BIGINT NOT NULL,
        EventTime            DATETIMEOFFSET(3) NOT NULL,
        EventType            NVARCHAR(100) NOT NULL,
        Operation            NVARCHAR(50) NOT NULL,
        EeId                 NVARCHAR(50) NULL,
        CoId                 NVARCHAR(20) NULL,
        ReceivedAtUtc        DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_WebhookEventInbox_Received DEFAULT TODATETIMEOFFSET(SYSUTCDATETIME(), '+00:00'),
        ProcessingStatus     NVARCHAR(20) NOT NULL CONSTRAINT DF_WebhookEventInbox_Status DEFAULT N'Received',
        AttemptCount         INT NOT NULL CONSTRAINT DF_WebhookEventInbox_Attempts DEFAULT (0),
        LastAttemptAtUtc     DATETIMEOFFSET(3) NULL,
        ProcessedAtUtc       DATETIMEOFFSET(3) NULL,
        IntegrationRunId     UNIQUEIDENTIFIER NULL,
        SafeErrorCode        NVARCHAR(100) NULL,
        CONSTRAINT PK_WebhookEventInbox PRIMARY KEY (EventId),
        CONSTRAINT CK_WebhookEventInbox_Status CHECK
            (ProcessingStatus IN (N'Received', N'Pending', N'Processing', N'Processed', N'Unsupported', N'Failed')),
        CONSTRAINT CK_WebhookEventInbox_Attempts CHECK (AttemptCount >= 0),
        CONSTRAINT FK_WebhookEventInbox_Run FOREIGN KEY (IntegrationRunId)
            REFERENCES Control.IntegrationRun (RunId)
    );
END;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(N'Control.WebhookEventInbox') AND name = N'IX_WebhookEventInbox_Claim')
    CREATE INDEX IX_WebhookEventInbox_Claim
        ON Control.WebhookEventInbox (ProcessingStatus, EventTime)
        INCLUDE (EventType, Operation, EeId, CoId, ReceivedAtUtc, AttemptCount, LastAttemptAtUtc, IntegrationRunId);
GO

IF OBJECT_ID(N'Control.JdeFileDelivery', N'U') IS NULL
BEGIN
    CREATE TABLE Control.JdeFileDelivery
    (
        DeliveryId          UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_JdeFileDelivery_Id DEFAULT NEWSEQUENTIALID(),
        RunId               UNIQUEIDENTIFIER NOT NULL,
        DeliveryKey         NVARCHAR(200) NOT NULL,
        StagedFilePath      NVARCHAR(1024) NOT NULL,
        PickupFilePath      NVARCHAR(1024) NULL,
        ArchiveFilePath     NVARCHAR(1024) NULL,
        RecordCount         INT NOT NULL,
        DeliveryStatus      NVARCHAR(20) NOT NULL CONSTRAINT DF_JdeFileDelivery_Status DEFAULT N'Staged',
        CreatedAt           DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_JdeFileDelivery_Created DEFAULT TODATETIMEOFFSET(SYSUTCDATETIME(), '+00:00'),
        DeliveryStartedAt   DATETIMEOFFSET(3) NULL,
        DeliveredAt         DATETIMEOFFSET(3) NULL,
        ArchivedAt          DATETIMEOFFSET(3) NULL,
        SafeFailureSummary  NVARCHAR(1000) NULL,
        CONSTRAINT PK_JdeFileDelivery PRIMARY KEY (DeliveryId),
        CONSTRAINT UQ_JdeFileDelivery_DeliveryKey UNIQUE (DeliveryKey),
        CONSTRAINT UQ_JdeFileDelivery_StagedPath UNIQUE (StagedFilePath),
        CONSTRAINT FK_JdeFileDelivery_Run FOREIGN KEY (RunId) REFERENCES Control.IntegrationRun (RunId),
        CONSTRAINT CK_JdeFileDelivery_Count CHECK (RecordCount >= 0),
        CONSTRAINT CK_JdeFileDelivery_Status CHECK
            (DeliveryStatus IN (N'Staged', N'InProgress', N'Delivered', N'Failed', N'Archived'))
    );
END;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(N'Control.JdeFileDelivery') AND name = N'IX_JdeFileDelivery_Ready')
    CREATE INDEX IX_JdeFileDelivery_Ready
        ON Control.JdeFileDelivery (DeliveryStatus, CreatedAt)
        INCLUDE (RunId, DeliveryKey, StagedFilePath, RecordCount);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(N'Control.JdeFileDelivery') AND name = N'UX_JdeFileDelivery_PickupPath')
    CREATE UNIQUE INDEX UX_JdeFileDelivery_PickupPath
        ON Control.JdeFileDelivery (PickupFilePath)
        WHERE PickupFilePath IS NOT NULL;
GO
