/* TEMPLATE ONLY — NOT EXECUTABLE until the approved Employee Changes response
   schema and retention policy are known. The matching contract confirms only
   employeeId and companyId as endpoint identifiers. Do not invent other fields.
   Keep SourceRecordJson NULL unless raw endpoint-record retention is approved.
*/
DECLARE @RunId UNIQUEIDENTIFIER = NULL; -- Bind active run.

/* Replace the source alias with the approved endpoint page mapping. */
INSERT Bronze.UkgEmployeeChangeRaw
    (RunId, EmployeeId, CompanyId, SourceRecordJson)
SELECT
    @RunId,
    src.employeeId,
    src.companyId,
    NULL
FROM ConfirmedEmployeeChangesSource AS src; -- TEMPLATE PLACEHOLDER; no such object exists.
