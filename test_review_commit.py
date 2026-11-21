from copy import deepcopy
import time
import unittest

from firebase_client import FirebaseRequestError, Session
from review_commit import review_commit


class ReviewCommitTests(unittest.TestCase):
    def setUp(self):
        self.session = Session("owner", "test", "token", time.monotonic() + 60)
        self.snapshot = {"schema": "pacs_review_v1", "review_id": "a" * 32,
                         "source_ref": {"source_id": "c" * 64}, "annotations": [], "roi": None}
        self.op = "b" * 32

    def test_create_has_atomic_preconditions_and_detached_fields(self):
        payload = review_commit("p", self.session, self.snapshot, None, self.op, 1)
        head, version = payload["writes"]
        self.assertEqual(head["currentDocument"], {"exists": False})
        self.assertEqual(version["currentDocument"], {"exists": False})
        self.assertEqual(version["update"]["name"], head["update"]["name"] + "/versions/" + self.op)
        self.assertEqual(head["update"]["fields"], version["update"]["fields"])
        self.snapshot["annotations"].append("changed")
        self.assertNotIn("changed", head["update"]["fields"]["snapshot_json"]["stringValue"])

    def test_revision_uses_opaque_update_time_and_payload_identity(self):
        one = review_commit("p", self.session, self.snapshot, "2026-11-21T00:00:00Z", self.op, 2)
        two = review_commit("p", self.session, deepcopy(self.snapshot), "2026-11-21T00:00:00Z", self.op, 2)
        self.assertEqual(one, two)
        self.assertEqual(one["writes"][0]["currentDocument"], {"updateTime": "2026-11-21T00:00:00Z"})
        changed = review_commit("p", self.session, self.snapshot, "other", self.op, 2)
        self.assertNotEqual(one["writes"][1]["update"]["fields"]["request_hash"],
                            changed["writes"][1]["update"]["fields"]["request_hash"])

    def test_rejects_invalid_versions_ids_and_oversized_payload(self):
        for expected, revision, operation in ((None, 2, self.op), ("token", 1, self.op),
                                               ("", 2, self.op), (None, True, self.op), (None, 1, "../bad")):
            with self.subTest(expected=expected, revision=revision, operation=operation):
                with self.assertRaises(ValueError):
                    review_commit("p", self.session, self.snapshot, expected, operation, revision)
        for value in (float("nan"), "가" * 21000):
            with self.assertRaises(ValueError):
                review_commit("p", self.session, dict(self.snapshot, extra=value), None, self.op, 1)

    def test_expired_session_rejected(self):
        with self.assertRaises(FirebaseRequestError):
            review_commit("p", Session("owner", "test", "token", 0), self.snapshot, None, self.op, 1)


if __name__ == "__main__":
    unittest.main()
