/* TEMPLATE ONLY — NOT EXECUTABLE until the Employee Changes response schema
   and required validated employee attributes are approved. The confirmed
   identity/matching key is the pair (companyId, employeeId). No other response
   columns are assumed here.
*/
DECLARE @RunId UNIQUEIDENTIFIER = NULL; -- Bind active run.

/*
  Provide a validated source set with one row per (companyId, employeeId).
  Then update existing current rows and insert missing rows in one transaction.
  Add only employee attributes documented by the approved endpoint/mapping.
*/
BEGIN TRANSACTION;
-- UPDATE ec WITH (UPDLOCK, HOLDLOCK) ... FROM Silver.EmployeeCurrent ec JOIN <validated source> src
--   ON ec.CompanyId = src.companyId AND ec.EmployeeId = src.employeeId;
-- INSERT Silver.EmployeeCurrent (EmployeeId, CompanyId, LastSourceRunId, ...approved columns...)
-- SELECT src.employeeId, src.companyId, @RunId, ... FROM <validated source> src
-- WHERE NOT EXISTS (SELECT 1 FROM Silver.EmployeeCurrent ec WITH (UPDLOCK, HOLDLOCK)
--   WHERE ec.CompanyId = src.companyId AND ec.EmployeeId = src.employeeId);
COMMIT TRANSACTION;
