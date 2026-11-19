from pathlib import Path
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import tempfile
import time
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from firebase_client import FirebaseRequestError, Session
from local_storage import LocalStorageError, _user_dir, save_original
from study_image_store import OriginalSavedLinkError, StudyImage, StudyImageConflict, save_and_link_original
from study_link_recovery import list_pending_links, remember_link, retry_pending_link
from viewer import sample_image_bytes


class LinkRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = Session("owner", "test", "token", time.monotonic() + 3600)
        self.key = save_original(self.root, "owner", "sample.png", sample_image_bytes())
        self.image = StudyImage("study", "series", "image", self.key)

    def test_intent_survives_restart_and_is_scoped_to_project_and_user(self):
        remember_link(self.root, "p", self.session, self.image)
        self.assertEqual(list_pending_links(Path(str(self.root)), "p", self.session)[0].image, self.image)
        self.assertEqual(list_pending_links(self.root, "other-project", self.session), [])
        other = Session("other", "test", "token", time.monotonic() + 3600)
        self.assertEqual(list_pending_links(self.root, "p", other), [])

    def test_different_intent_cannot_replace_pending_original(self):
        remember_link(self.root, "p", self.session, self.image)
        with self.assertRaises(StudyImageConflict):
            remember_link(self.root, "p", self.session, StudyImage("other", "series", "image", self.key))
        self.assertEqual(list_pending_links(self.root, "p", self.session)[0].image, self.image)

    def test_concurrent_different_requests_keep_one_intent_and_report_conflict(self):
        other = StudyImage("other", "series", "image", self.key)
        def remember(image):
            try:
                remember_link(self.root, "p", self.session, image)
                return "saved"
            except StudyImageConflict:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(remember, [self.image, other]))
        self.assertCountEqual(results, ["saved", "conflict"])
        self.assertEqual(len(list_pending_links(self.root, "p", self.session)), 1)

    def test_failure_retained_and_success_removes_intent(self):
        remember_link(self.root, "p", self.session, self.image)
        with patch("study_link_recovery.link_original", side_effect=FirebaseRequestError(503)):
            with self.assertRaises(FirebaseRequestError):
                retry_pending_link(self.root, "p", self.session, "image")
        self.assertEqual(list_pending_links(self.root, "p", self.session)[0].state, "retryable")
        self.assertEqual(list_pending_links(self.root, "p", self.session)[0].error_code, "request_failed")
        with patch("study_link_recovery.link_original"):
            retry_pending_link(self.root, "p", self.session, "image")
        self.assertEqual(list_pending_links(self.root, "p", self.session), [])

    def test_save_and_link_records_partial_failure(self):
        with patch("study_image_store.get_study"), patch("study_image_store.get_series", return_value=SimpleNamespace(study_id="study")):
            with patch("study_link_recovery.link_original", side_effect=FirebaseRequestError(503)):
                with self.assertRaises(OriginalSavedLinkError):
                    save_and_link_original(self.root.name, self.session, self.root, "study", "series", "image", "sample.png", sample_image_bytes())
        self.assertEqual(list_pending_links(self.root, self.root.name, self.session)[0].image, self.image)

    def test_legacy_schema_upgrades_under_concurrent_writers(self):
        path = _user_dir(self.root, self.session.uid) / "pending-links.sqlite3"
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE pending_links (project_id TEXT, image_id TEXT, "
                       "study_id TEXT, series_id TEXT, original_key TEXT, state TEXT, "
                       "PRIMARY KEY (project_id, image_id))")
        with ThreadPoolExecutor(max_workers=2) as workers:
            list(workers.map(lambda _: remember_link(self.root, "p", self.session, self.image), range(2)))
        self.assertEqual(len(list_pending_links(self.root, "p", self.session)), 1)

    def test_failure_to_record_error_does_not_replace_original_error(self):
        remember_link(self.root, "p", self.session, self.image)
        pending = list_pending_links(self.root, "p", self.session)
        with patch("study_link_recovery.list_pending_links", return_value=pending):
            with patch("study_link_recovery.link_original", side_effect=FirebaseRequestError(503)):
                with patch("study_link_recovery._database", side_effect=LocalStorageError("disk full")):
                    with self.assertRaises(FirebaseRequestError) as caught:
                        retry_pending_link(self.root, "p", self.session, "image")
        self.assertEqual(caught.exception.status, 503)
        self.assertIsInstance(caught.exception.__cause__, LocalStorageError)


if __name__ == "__main__":
    unittest.main()
