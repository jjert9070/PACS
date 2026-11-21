"""Compare missing-receipt case recovery with the Lean version/error rules."""

from copy import deepcopy
from itertools import product
import unittest
from unittest.mock import patch

import case_records as store
from firebase_client import FirebaseRequestError
import test_case_records as cases
from test_lean_recovery import READ_ERRORS, WRITE_STATUSES


def case_recovery_observations():
    fixture = cases.CaseRecordTests()
    fixture.setUp()
    try:
        base = fixture.save()
        case_path = fixture.collection + "/" + fixture.case_id
        old_document = deepcopy(fixture.documents[case_path])
        fixture.save(operation_id="c" * 32, note="first writer", base=base)
        new_document = deepcopy(fixture.documents[case_path])
        reads = [("found", "v1", False), ("found", "v2", False)]
        reads += [("failed", status, False) for status in READ_ERRORS]
        for status, expected, read in product(WRITE_STATUSES, (None, "v1"), reads):
            kind, value, _ = read
            fixture.documents[case_path] = deepcopy(old_document if value == "v1" else new_document)
            documents_before = deepcopy(fixture.documents)
            commits_before = len(fixture.commits)
            writes = []

            def request(path, session, payload=None):
                if payload is not None:
                    writes.append(deepcopy(payload))
                    raise FirebaseRequestError(status)
                if path == case_path and kind == "failed":
                    raise FirebaseRequestError(value)
                return fixture.request(path, session)

            with patch.object(store, "_request", side_effect=request):
                try:
                    fixture.save(operation_id="d" * 32, base=base if expected else None)
                except store.CaseConflict:
                    observed = ("conflict", None)
                except FirebaseRequestError as error:
                    observed = ("requestError", error.status)
                else:
                    observed = ("accepted", None)
            assert len(writes) == 1
            assert fixture.documents == documents_before
            assert len(fixture.commits) == commits_before
            yield status, expected, read, observed
    finally:
        fixture.doCleanups()


class LeanCaseRecoveryTests(unittest.TestCase):
    def test_missing_receipt_matches_version_and_read_error_model(self):
        count = 0
        with self.assertLogs("case_records", level="WARNING"):
            for status, expected, read, actual in case_recovery_observations():
                with self.subTest(status=status, expected=expected, read=read):
                    kind, value, _ = read
                    if status not in (400, 403, 409, 412):
                        wanted = ("requestError", status)
                    elif kind == "failed":
                        wanted = ("requestError", status if value == 404 else value)
                    elif expected is None or value != expected:
                        wanted = ("conflict", None)
                    else:
                        wanted = ("requestError", status)
                    self.assertEqual(actual, wanted)
                    count += 1
        self.assertEqual(count, 216)

    def test_confirmed_operation_is_recovered_without_duplicate_or_changed_payload(self):
        fixture = cases.CaseRecordTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        base = fixture.save()
        latest = fixture.save(operation_id="c" * 32, base=base, note="later edit")
        before = deepcopy(fixture.documents)
        for status in (400, 403, 409, 412):
            def reject_write(path, session, payload=None):
                if payload is not None:
                    raise FirebaseRequestError(status)
                return fixture.request(path, session)

            with self.subTest(status=status), patch.object(store, "_request", side_effect=reject_write):
                with self.assertLogs("case_records", level="WARNING"):
                    self.assertEqual(fixture.save(), latest)
                    with self.assertRaises(store.CaseConflict):
                        fixture.save(title="different payload")
            self.assertEqual(fixture.documents, before)
            self.assertEqual(len(fixture.commits), 2)


if __name__ == "__main__":
    unittest.main()
