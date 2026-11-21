import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from firebase_client import FirebaseRequestError, Session
from review_pending import PendingReviewConflict, get_pending_review, remember_review


class PendingReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.session = Session("owner", "test", "token", time.monotonic() + 600)
        self.snapshot = {"schema": "pacs_review_v1", "review_id": "a" * 32,
                         "source_ref": {"source_id": "c" * 64}, "annotations": []}
        self.operation = "b" * 32

    def remember(self, snapshot=None, version=None, revision=1):
        remember_review(self.root, "project", self.session, self.snapshot if snapshot is None else snapshot,
                        version, self.operation, revision)

    def test_same_request_reused_and_other_request_rejected(self):
        self.remember()
        self.remember()
        with self.assertRaises(PendingReviewConflict):
            self.remember(dict(self.snapshot, annotations=["different"]))
        self.assertEqual(get_pending_review(self.root, "project", self.session, self.operation).snapshot, self.snapshot)

    def test_survives_new_process_and_keeps_original_request(self):
        self.remember()
        script = ("import json,sys,time; from pathlib import Path; from firebase_client import Session; "
                  "from review_pending import get_pending_review; "
                  "r=get_pending_review(Path(sys.argv[1]),'project',Session('owner','test','token',time.monotonic()+60),sys.argv[2]); "
                  "print(json.dumps([r.snapshot,r.expected_version,r.revision]))")
        result = subprocess.run([sys.executable, "-c", script, str(self.root), self.operation],
                                text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), [self.snapshot, None, 1])

    def test_separate_user_and_project_do_not_read(self):
        self.remember()
        other = Session("other", "test", "token", time.monotonic() + 60)
        for project, session in (("project", other), ("different", self.session)):
            with self.assertRaises(ValueError):
                get_pending_review(self.root, project, session, self.operation)

    def test_expired_session_is_rejected(self):
        self.remember()
        with self.assertRaises(FirebaseRequestError):
            get_pending_review(self.root, "project", Session("owner", "test", "token", 0), self.operation)


if __name__ == "__main__":
    unittest.main()
