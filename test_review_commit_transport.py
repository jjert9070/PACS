from copy import deepcopy
import time
import unittest
from unittest.mock import patch

from firebase_client import FirebaseRequestError, Session
from review_commit_transport import commit_review


class FakeFirestore:
    def __init__(self):
        self.docs = {}
        self.serial = 0
        self.error = None
        self.after_commit_error = None
        self.read_error = None

    def __call__(self, path, session, payload=None):
        if payload is None:
            if self.read_error is not None:
                raise FirebaseRequestError(self.read_error)
            if path not in self.docs:
                raise FirebaseRequestError(404)
            return deepcopy(self.docs[path])
        if self.error is not None:
            raise FirebaseRequestError(self.error)
        for write in payload["writes"]:
            old = self.docs.get(write["update"]["name"])
            condition = write["currentDocument"]
            if (condition.get("exists") is False and old is not None
                    or "updateTime" in condition and (old is None or old["updateTime"] != condition["updateTime"])):
                raise FirebaseRequestError(412)
        self.serial += 1
        token = f"2026-11-21T00:00:{self.serial:02d}Z"
        for write in payload["writes"]:
            doc = deepcopy(write["update"])
            doc["updateTime"] = token
            doc["fields"]["saved_at"] = {"timestampValue": token}
            self.docs[doc["name"]] = doc
        if self.after_commit_error:
            raise FirebaseRequestError(self.after_commit_error)
        return {}


class CommitTransportTests(unittest.TestCase):
    def setUp(self):
        self.session = Session("owner", "test", "token", time.monotonic() + 600)
        self.snapshot = {"schema": "pacs_review_v1", "review_id": "a" * 32,
                         "annotations": [], "source_ref": {"source_id": "c" * 64}}
        self.firestore = FakeFirestore()
        patcher = patch("review_commit_transport._request", side_effect=self.firestore)
        patcher.start()
        self.addCleanup(patcher.stop)

    def commit(self, snapshot=None, version=None, op="b" * 32, revision=1):
        return commit_review("p", self.session, snapshot or self.snapshot, version, op, revision)

    def test_save_repeat_and_old_retry_return_original_snapshot(self):
        first = self.commit()
        changed = dict(self.snapshot, annotations=["new"])
        second = self.commit(changed, first.version_token, "d" * 32, 2)
        retry = self.commit()
        self.assertEqual((first.status, second.status, retry.status), ("saved", "saved", "reused"))
        self.assertEqual(retry.snapshot, self.snapshot)
        self.assertEqual(retry.version_token, first.version_token)
        self.assertEqual(self.firestore.serial, 2)

    def test_same_operation_different_payload_is_conflict(self):
        self.commit()
        result = self.commit(dict(self.snapshot, annotations=["changed"]))
        self.assertEqual((result.status, result.code), ("conflict", "operation_payload_mismatch"))
        self.assertEqual(self.firestore.serial, 1)

    def test_two_sessions_conflict_without_overwriting(self):
        first = self.commit()
        self.commit(dict(self.snapshot, annotations=["first"]), first.version_token, "d" * 32, 2)
        result = self.commit(dict(self.snapshot, annotations=["second"]), first.version_token, "e" * 32, 2)
        self.assertEqual((result.status, result.code), ("conflict", "stale_version"))
        self.assertEqual(self.firestore.serial, 2)

    def test_lost_response_receipt_confirms_success(self):
        self.firestore.after_commit_error = 409
        result = self.commit()
        self.assertEqual((result.status, result.code), ("reused", "ok"))
        self.assertEqual(result.snapshot, self.snapshot)

    def test_errors_without_evidence_remain_errors(self):
        for status in (400, 403, 409, 412):
            self.firestore.error = status
            with self.subTest(status=status), self.assertRaises(FirebaseRequestError) as error:
                self.commit()
            self.assertEqual(error.exception.status, status)
        self.firestore.error = 403
        self.firestore.read_error = 503
        with self.assertRaises(FirebaseRequestError) as error:
            self.commit()
        self.assertEqual(error.exception.status, 503)

    def test_existing_unchanged_head_does_not_turn_permission_error_into_conflict(self):
        first = self.commit()
        self.firestore.error = 403
        with self.assertRaises(FirebaseRequestError) as error:
            self.commit(version=first.version_token, op="d" * 32, revision=2)
        self.assertEqual(error.exception.status, 403)

    def test_corrupted_receipt_never_proves_success(self):
        self.commit()
        version = next(key for key in self.firestore.docs if "/versions/" in key)
        self.firestore.docs[version]["fields"]["owner_uid"] = {"stringValue": "other"}
        with self.assertRaises(FirebaseRequestError):
            self.commit()

    def test_different_revision_cannot_reuse_receipt(self):
        first = self.commit()
        self.commit(version=first.version_token, op="d" * 32, revision=2)
        result = self.commit(version=first.version_token, op="d" * 32, revision=3)
        self.assertEqual((result.status, result.code), ("conflict", "operation_payload_mismatch"))


if __name__ == "__main__":
    unittest.main()
