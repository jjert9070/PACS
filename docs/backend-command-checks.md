# Backend command checks

What this repo does: This educational PACS stores private originals and patient, study and series references. Expected load is a development host with a few concurrent browser sessions. The new A screens and real B DICOM importer are not available for this test.

What this change does: Storage no longer depends on comparing two Windows spellings of the same path. It publishes complete files with the same no-overwrite hard-link operation on Windows and Linux.

## Must fix: fixed

1. **Valid concurrent uploads could be rejected as unsafe paths** (`local_storage.py`, `dicom_original_storage.py`, `study_link_recovery.py`).
   - **What this is:** The original and retry stores validate their storage paths before writing.
   - **Problem:** A real four-process test reproduced a Windows race where one resolved path had an extended-path prefix and another equivalent path did not. Containment comparison rejected a safe DICOM file.
   - **Fix:** Keep the resolved configured root and fixed hashed child names. Reject symbolic links and junctions explicitly, rather than comparing unstable spellings. Use a hard link to publish the complete temporary file without replacing an existing original on either OS.
   - **If we skip it:** Concurrent registrations intermittently fail during storage startup. This failure does not mean that the original escaped its directory.

## Results

| Check | Result | Scope |
|---|---|---|
| RTK | 0.50.0 used for commands | Actual invocation, not just version lookup |
| Lock consistency | Passed | uv lock --check --offline |
| Installed dependencies | Passed, 37 packages | Project Python environment, no package changes |
| Project versions | Python 3.12.16, Streamlit 1.55.0, Pillow 12.3.0, NumPy 2.2.6, OpenCV 4.12.0 | Native Windows |
| Windows encoding | Filesystem UTF-8, locale cp65001 | Python's separate UTF-8-mode flag is 0; the Windows locale is UTF-8 |
| Syntax parsing | 56 Python files passed | Root modules, tests and experiment scripts |
| Clean module imports | 25 application modules passed | Native project environment |
| Server startup | Health 200 with body "ok", root 200 with HTML | Loopback-only, random free port, no real Firebase settings loaded |
| Server cleanup | Passed | Only the child started by the harness was stopped |
| Full suite in Git Bash | 228 tests passed | Project environment, external sockets and DNS blocked; zero external attempts |
| Windows process stress | Six full rounds passed | Four workers, 120 writes and reads per PNG/raw fixture per round |
| Concurrent conflicting retry intents | Passed | One saved intent and eleven explicit conflicts per round |
| Concurrent legacy SQLite upgrade | Passed | Twelve successful requests, one resulting intent per round |
| Abrupt process exit | Passed | Committed intent survived immediate process exit and could be listed from another process |
| Linux backend suite | 48 tests passed | Ubuntu 24.04, Python 3.12.3, native /tmp storage |
| Linux process stress | Passed | Four workers, raw-byte writes, retry conflicts, schema upgrade and abrupt-exit recovery |
| Linux symbolic-link escape | Rejected | No file created in the outside directory |
| Git diff check | Passed | No whitespace errors |

The stress fixtures reuse a small number of controlled byte sequences. The raw-byte fixture is not a valid DICOM file. These checks test preservation, collisions and concurrency, not DICOM image support or performance under hospital workloads.

The Ubuntu installation has no Streamlit, Pillow, NumPy or OpenCV packages. The Linux runner replaces only the viewer import boundary and runs actual backend, SQLite and filesystem code. It does not certify the full app on Linux. No Linux packages were installed by this test.

The first process test failed before the path fix. After the fix, the unmodified storage functions passed six Windows rounds and one Linux round. The older full suite passed 226 tests; the final 228 includes the new path and link-guard regressions. Counts are overlapping checks, not additive coverage totals.

## Repeating the checks

The repository suite can be run from Git Bash with uv run --no-sync python -m unittest discover -q after the locked environment is prepared. The temporary suite runner additionally blocks external network access. The command, server and process harnesses and their logs are stored outside the repository under the Codex workspace's work/c-command-tests directory.

For Linux deployment, use Python 3.12 or later and install the locked project dependencies. Configure PACS_LOCAL_STORAGE_DIR as a persistent directory owned by the service account. The same-filesystem hard-link publish and fsync operations must be supported; the implementation fails instead of falling back to an overwrite. A shared multi-host or network-filesystem setup needs separate testing.

Verdict: The available backend and command checks passed after the reproduced path bug was fixed. This is not a UI or hospital deployment approval.

Not checked: Live Firebase rules, query indexes and account permissions; a real B DICOM parser and supported sample corpus; new A/B screen integration; the full Linux dependency stack; JCloud persistence and multi-host operation. Java and Firebase CLI were not available, so Firestore emulator tests were not run.

## Temporary-file cleanup fix

The final audit's remaining cleanup issue is fixed. Both original writers now call one cleanup helper. A failed unlink emits only a fixed warning code and errno; it does not replace a successful publish or the original storage exception. A valid original is never removed by cleanup. A temporary file may remain when the operating system refuses deletion.

Four regression tests cover successful publish followed by cleanup failure and simultaneous publish and cleanup failure for PNG and raw DICOM storage. The same fault-injection probe now preserves success in the first two cases and returns LocalStorageError for the publish failure.

After this fix, the Git Bash suite passed 232 tests with zero external connection attempts. The native Ubuntu backend subset passed 50 tests, with only the viewer boundary stubbed. Git diff --check passed. Real Firebase and actual B DICOM decoding remain unverified.