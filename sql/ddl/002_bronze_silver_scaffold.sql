/* Azure SQL Database / SQL Server T-SQL design. No DROP statements. */
IF OBJECT_ID(N'Bronze.UkgEmployeeChangeRaw', N'U') IS NULL
BEGIN
    CREATE TABLE Bronze.UkgEmployeeChangeRaw
    (
        RawRecordId         BIGINT IDENTITY(1,1) NOT NULL,
        RunId               UNIQUEIDENTIFIER NOT NULL,
        IngestedAt          DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_UkgEmployeeChangeRaw_Ingested DEFAULT TODATETIMEOFFSET(SYSUTCDATETIME(), '+00:00'),
        EmployeeId          NVARCHAR(50) NULL,
        CompanyId           NVARCHAR(20) NULL,
        SourceRecordJson    NVARCHAR(MAX) NULL,
        CONSTRAINT PK_UkgEmployeeChangeRaw PRIMARY KEY (RawRecordId),
        CONSTRAINT FK_UkgEmployeeChangeRaw_Run FOREIGN KEY (RunId) REFERENCES Control.IntegrationRun (RunId),
        CONSTRAINT CK_UkgEmployeeChangeRaw_Json CHECK (SourceRecordJson IS NULL OR ISJSON(SourceRecordJson) = 1)
    );
END;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(N'Bronze.UkgEmployeeChangeRaw') AND name = N'IX_UkgEmployeeChangeRaw_Run')
    CREATE INDEX IX_UkgEmployeeChangeRaw_Run ON Bronze.UkgEmployeeChangeRaw (RunId, IngestedAt);
GO

/*
  Silver is an identity/audit scaffold, not a completed employee model.
  The confirmed employee matching identity is the pair (CompanyId, EmployeeId).
  This table contains only those identifiers and system/source metadata.
*/
IF OBJECT_ID(N'Silver.EmployeeCurrent', N'U') IS NULL
BEGIN
    CREATE TABLE Silver.EmployeeCurrent
    (
        EmployeeKey         UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_EmployeeCurrent_Key DEFAULT NEWSEQUENTIALID(),
        EmployeeId          NVARCHAR(50) NOT NULL,
        CompanyId           NVARCHAR(20) NOT NULL,
        SourceSystem        NVARCHAR(30) NOT NULL CONSTRAINT DF_EmployeeCurrent_Source DEFAULT N'UKG',
        FirstSeenAt         DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_EmployeeCurrent_FirstSeen DEFAULT TODATETIMEOFFSET(SYSUTCDATETIME(), '+00:00'),
        LastSeenAt          DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_EmployeeCurrent_LastSeen DEFAULT TODATETIMEOFFSET(SYSUTCDATETIME(), '+00:00'),
        LastSourceRunId     UNIQUEIDENTIFIER NULL,
        CONSTRAINT PK_EmployeeCurrent PRIMARY KEY (EmployeeKey),
        CONSTRAINT UQ_EmployeeCurrent_Company_Employee UNIQUE (CompanyId, EmployeeId),
        CONSTRAINT FK_EmployeeCurrent_Run FOREIGN KEY (LastSourceRunId) REFERENCES Control.IntegrationRun (RunId)
    );
END;
GO
