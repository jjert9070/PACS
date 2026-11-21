from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from firebase_client import FirebaseRequestError
from review_pending import get_pending_review, remember_review
from review_retry import retry_review
from review_storage import save_processing_result
from test_review_commit_transport import FakeFirestore
import test_review_validation as fixtures


class ReviewRetryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ReviewDraftTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.env = self.fixture.fixture
        self.operation = "b" * 32
        self.snapshot = deepcopy(self.fixture.draft)
        self.snapshot["processing"] = save_processing_result(
            self.env.root, self.env.session, self.fixture.source, self.snapshot["processing"])
        remember_review(self.env.root, "p", self.env.session, self.snapshot, None, self.operation, 1)
        self.store = FakeFirestore()
        patcher = patch("review_commit_transport._request", side_effect=self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def retry(self):
        return retry_review(self.env.root, "p", self.env.session, self.operation)

    def test_retry_commits_frozen_snapshot_then_reuses_receipt(self):
        first, second = self.retry(), self.retry()
        self.assertEqual((first.status, second.status), ("saved", "reused"))
        self.assertEqual(first.snapshot, self.snapshot)
        self.assertEqual(self.store.serial, 1)
        self.assertEqual(get_pending_review(self.env.root, "p", self.env.session, self.operation).snapshot, self.snapshot)

    def test_missing_or_changed_result_is_not_committed(self):
        from local_storage import _user_dir
        target = _user_dir(self.env.root / "results", self.env.session.uid) / self.snapshot["processing"]["result_key"]
        target.write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            self.retry()
        self.assertEqual(self.store.serial, 0)
        self.assertEqual(get_pending_review(self.env.root, "p", self.env.session, self.operation).snapshot, self.snapshot)

    def test_failed_database_retains_request_and_same_operation_can_retry(self):
        self.store.error = 503
        with self.assertRaises(FirebaseRequestError):
            self.retry()
        self.assertEqual(get_pending_review(self.env.root, "p", self.env.session, self.operation).snapshot, self.snapshot)
        self.store.error = None
        self.assertEqual(self.retry().status, "saved")

    def test_missing_original_prevents_commit(self):
        from local_storage import _user_dir
        target = _user_dir(self.env.root, self.env.session.uid) / (self.fixture.source.source_id + ".dcm")
        target.unlink()
        with self.assertRaises(ValueError):
            self.retry()
        self.assertEqual(self.store.serial, 0)


if __name__ == "__main__":
    unittest.main()
