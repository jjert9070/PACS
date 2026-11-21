from copy import deepcopy
import time
import unittest
from unittest.mock import patch

from firebase_client import FirebaseRequestError, Session
from study_details_store import StudyDetails, StudyDetailsConflict
import study_details_store as store


class StudyDetailsStoreTests(unittest.TestCase):
    def setUp(self):
        self.session = Session("owner", "test", "token", time.monotonic() + 3600)
        self.details = StudyDetails("study-1", "2026-11-19", "confirmed", "receiving")
        self.documents = {}
        self.counter = 0
        self.precondition_status = 412
        for target, action in (("study_details_store.get_study", lambda *args: object()),
                               ("study_details_store._request", self.request)):
            active = patch(target, side_effect=action)
            active.start()
            self.addCleanup(active.stop)

    def request(self, path, session, payload=None):
        if payload is None:
            if path not in self.documents:
                raise FirebaseRequestError(404)
            return deepcopy(self.documents[path])
        write = payload["writes"][0]
        doc = write["update"]
        current = self.documents.get(doc["name"])
        condition = write["currentDocument"]
        if (condition.get("exists") is False and current is not None) or (
            "updateTime" in condition and (current is None or current["updateTime"] != condition["updateTime"])
        ):
            raise FirebaseRequestError(self.precondition_status)
        self.counter += 1
        doc = deepcopy(doc)
        doc["updateTime"] = str(self.counter)
        self.documents[doc["name"]] = doc
        return {}

    def test_save_get_and_independent_statuses(self):
        store.save_study_details("p", self.session, self.details)
        saved = store.get_study_details("p", self.session, "study-1")
        self.assertEqual(saved.details, self.details)
        self.assertNotEqual(saved.details.linkage_status, saved.details.receipt_status)
        store.save_study_details("p", self.session, self.details)
        self.assertEqual(len(self.documents), 1)

    def test_stale_update_rejected_without_overwrite(self):
        for status in (400, 403, 409, 412):
            with self.subTest(status=status):
                self.documents.clear()
                self.precondition_status = status
                store.save_study_details("p", self.session, self.details)
                base = store.get_study_details("p", self.session, "study-1")
                updated = StudyDetails("study-1", "2026-11-19", "confirmed", "partial")
                store.save_study_details("p", self.session, updated, base.update_time)
                with self.assertRaises(StudyDetailsConflict):
                    store.save_study_details("p", self.session, self.details, base.update_time)
                self.assertEqual(store.get_study_details("p", self.session, "study-1").details, updated)

    def test_http_400_with_unchanged_version_remains_request_error(self):
        store.save_study_details("p", self.session, self.details)
        base = store.get_study_details("p", self.session, "study-1")

        def reject_write(path, session, payload=None):
            if payload is not None:
                raise FirebaseRequestError(400)
            return self.request(path, session)

        with patch("study_details_store._request", side_effect=reject_write):
            with self.assertRaises(FirebaseRequestError) as caught:
                store.save_study_details("p", self.session, self.details, base.update_time)
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(store.get_study_details("p", self.session, "study-1"), base)

    def test_http_400_followup_read_failure_is_not_conflict(self):
        for read_status, expected in ((403, 403), (404, 400)):
            with self.subTest(read_status=read_status):
                def fail_request(path, session, payload=None):
                    raise FirebaseRequestError(400 if payload is not None else read_status)

                with patch("study_details_store._request", side_effect=fail_request):
                    with self.assertRaises(FirebaseRequestError) as caught:
                        store.save_study_details("p", self.session, self.details, "old")
                self.assertEqual(caught.exception.status, expected)

    def test_invalid_date_and_unverified_completion_are_rejected(self):
        for invalid in ("2026-02-30", "20261119", "2026-1-1"):
            with self.assertRaises(ValueError):
                StudyDetails("study-1", acquired_on=invalid)
        with self.assertRaises(ValueError):
            StudyDetails("study-1", receipt_status="complete")

    def test_missing_study_and_permission_failure_are_not_success(self):
        with patch("study_details_store.get_study", side_effect=FirebaseRequestError(404)):
            with self.assertRaises(FirebaseRequestError):
                store.save_study_details("p", self.session, self.details)
        with patch("study_details_store._request", side_effect=FirebaseRequestError(403)):
            with self.assertRaises(FirebaseRequestError) as caught:
                store.save_study_details("p", self.session, self.details)
        self.assertEqual(caught.exception.status, 403)

    def test_tampered_owner_or_study_is_rejected(self):
        store.save_study_details("p", self.session, self.details)
        next(iter(self.documents.values()))["fields"]["study_key"] = {"stringValue": "b" * 64}
        with self.assertRaises(FirebaseRequestError):
            store.get_study_details("p", self.session, "study-1")


if __name__ == "__main__":
    unittest.main()
