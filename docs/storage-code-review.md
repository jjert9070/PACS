# Storage code audit and change review

What this repo does: This is an educational PACS application. It opens sample images and keeps private case records, originals and processing history. The new C services add patient, study, series and DICOM storage contracts.

Load assumed: One development host, a few signed-in users and concurrent browser sessions. This review does not certify a hospital deployment or shared storage across servers.

What this change does: It repairs error handling and input validation in the new storage services. It also serializes retry-database upgrades and replaces nested status expressions with explicit branches.

## Must fix: fixed in this change

1. **Concurrent database upgrades could fail** (`study_link_recovery.py`, `_database`).
   - **What this is:** Each session opens the local retry database and upgrades an older table if necessary.
   - **Problem:** Two sessions could both see the old table and attempt to add the same column. One operation would fail instead of preserving the retry request.
   - **Fix:** Start one immediate SQLite transaction before inspecting the schema. Use the same transaction for the operation and remove the separate nested transaction starts.
   - **If we skip it:** One session can lose the ability to record a pending link during an upgrade. Original files are not deleted, but the retry workflow fails.

2. **A second failure could hide the original request error** (`study_link_recovery.py`, `retry_pending_link`; `dicom_registration.py`, `retry_dicom_registration`).
   - **What this is:** A failed link request updates its local failure record.
   - **Problem:** If the remote request fails and the disk write also fails, the disk error replaces the original permission or network error.
   - **Fix:** Keep the original error as the raised exception and chain the record-write error as its cause. Leave the pending intent intact.
   - **If we skip it:** The caller receives the wrong failure category and cannot explain whether the remote request was denied or unavailable.

3. **Wrong file input types could crash registration** (`viewer.py`, `load_image`).
   - **What this is:** The UI-facing registration service validates a sample image before writing identity records.
   - **Problem:** A missing filename or non-byte input could raise an uncaught TypeError instead of returning an input error.
   - **Fix:** Validate the filename and byte types in the shared image loader. The service can then return its normal invalid-input result before any write.
   - **If we skip it:** An incorrectly assembled A-to-C request can stop registration rather than display a recoverable input error.

4. **A class UID could be reused as an object UID** (`dicom_instance_store.py`, `DicomMetadata`; `firestore.rules`).
   - **What this is:** DICOM metadata identifies the study, series, instance and its class.
   - **Problem:** The validator checked only the three object identifiers. It accepted a class identifier that was identical to one of those objects.
   - **Fix:** Require distinct class and object identifiers in the model and the rule file. Reject unexpected Firestore field types while decoding an instance.
   - **If we skip it:** An invalid importer result can create an ambiguous stored reference. This is syntax and relationship validation, not proof of worldwide UID uniqueness.

5. **Malformed query responses could look like no results** (`storage_query.py`, `query_page`).
   - **What this is:** Server queries return documents or response metadata indicating an empty result.
   - **Problem:** An unrecognized row such as an empty object was silently ignored. The caller could display an empty list even though the response was invalid.
   - **Fix:** Validate metadata rows, document shapes and user-scoped document names before decoding results.
   - **If we skip it:** A broken response can be mistaken for the patient having no examinations.

## Readability

Status and error classification now uses explicit branches. Query payloads and DICOM fields are laid out by field. Retry transaction ownership is in one shared place. No packages were added, and the public function signatures remain compatible.

## Scope and verification

Read: README, dependencies, CI configuration, app entry point, authentication client, viewer input boundary, case writes and restoration, ROI contract, new identity and storage services, retry journals, query cursors, rule additions and related tests.

Regression cases cover concurrent legacy-schema upgrades, record-write failure after a remote failure, invalid file types, class/object UID collisions, malformed Firestore field types and malformed query responses.

Initial review result: 226 unittest cases passed in the existing Python 3.12 test environment. The final git diff --check also passed. The first run exposed a new test fixture that did not close its SQLite connection on Windows; the fixture now closes the connection, and the full suite passed on rerun.

Verdict: The concrete findings above are repaired. A deployment decision still requires the integration checks below.

Not checked: Live Firebase rules, query indexes and permissions; B's real DICOM parser and supported file corpus; A's new patient screens; multi-host storage; line-by-line audit of offline experiment scripts; medical correctness or clinical suitability. DICOM tests use synthetic bytes and a stub parser.


Later command testing reproduced and fixed a Windows path-spelling race. The final Git Bash suite passed 228 tests. See backend-command-checks.md for native Linux backend and process tests and their limits.

## Temporary-file cleanup fix

The final audit's remaining cleanup issue is fixed. Both original writers now call one cleanup helper. A failed unlink emits only a fixed warning code and errno; it does not replace a successful publish or the original storage exception. A valid original is never removed by cleanup. A temporary file may remain when the operating system refuses deletion.

Four regression tests cover successful publish followed by cleanup failure and simultaneous publish and cleanup failure for PNG and raw DICOM storage. The same fault-injection probe now preserves success in the first two cases and returns LocalStorageError for the publish failure.

After this fix, the Git Bash suite passed 232 tests with zero external connection attempts. The native Ubuntu backend subset passed 50 tests, with only the viewer boundary stubbed. Git diff --check passed. Real Firebase and actual B DICOM decoding remain unverified.