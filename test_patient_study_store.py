from copy import deepcopy
import time
import unittest
from unittest.mock import patch

from firebase_client import FirebaseRequestError, Session
from patient_study_identity import PatientIdentity, StudyIdentity, StudyPatientConflict
import patient_study_store as store


class PatientStudyStoreTests(unittest.TestCase):
    def setUp(self):
        self.session = Session("owner", "test", "token", time.monotonic() + 3600)
        self.patient = PatientIdentity("research-a", "patient-1")
        self.study = StudyIdentity("study-1", self.patient)
        self.documents = {}
        patcher = patch("patient_study_store._request", side_effect=self.request)
        patcher.start()
        self.addCleanup(patcher.stop)

    def request(self, path, session, payload=None):
        if payload is None:
            if path not in self.documents:
                raise FirebaseRequestError(404)
            return deepcopy(self.documents[path])
        for write in payload["writes"]:
            doc = write["update"]
            if doc["name"] in self.documents:
                raise FirebaseRequestError(409)
            self.documents[doc["name"]] = deepcopy(doc)
        return {}

    def test_save_read_and_repeat_are_idempotent(self):
        store.save_patient("p", self.session, self.patient)
        store.save_patient("p", self.session, self.patient)
        store.save_study("p", self.session, self.study)
        store.save_study("p", self.session, self.study)
        self.assertEqual(store.get_patient("p", self.session, self.patient), self.patient)
        self.assertEqual(store.get_study("p", self.session, "study-1"), self.study)
        self.assertEqual(len(self.documents), 2)

    def test_different_issuer_is_not_merged_and_conflicting_patient_is_rejected(self):
        other = PatientIdentity("research-b", "patient-1")
        store.save_patient("p", self.session, self.patient)
        store.save_patient("p", self.session, other)
        store.save_study("p", self.session, self.study)
        with self.assertRaises(StudyPatientConflict):
            store.save_study("p", self.session, StudyIdentity("study-1", other))
        self.assertEqual(store.get_study("p", self.session, "study-1"), self.study)

    def test_missing_patient_prevents_study_creation(self):
        with self.assertRaises(FirebaseRequestError) as caught:
            store.save_study("p", self.session, self.study)
        self.assertEqual(caught.exception.status, 404)
        self.assertFalse(self.documents)

    def test_different_user_cannot_read_another_users_record(self):
        store.save_patient("p", self.session, self.patient)
        other = Session("other", "test", "other-token", time.monotonic() + 3600)
        with self.assertRaises(FirebaseRequestError) as caught:
            store.get_patient("p", other, self.patient)
        self.assertEqual(caught.exception.status, 404)

    def test_permission_failure_is_not_reported_as_success(self):
        with patch("patient_study_store._request", side_effect=FirebaseRequestError(403)):
            with self.assertRaises(FirebaseRequestError) as caught:
                store.save_patient("p", self.session, self.patient)
        self.assertEqual(caught.exception.status, 403)

    def test_duplicate_403_is_checked_without_overwriting(self):
        store.save_patient("p", self.session, self.patient)
        other = PatientIdentity("research-b", "patient-1")
        store.save_patient("p", self.session, other)
        store.save_study("p", self.session, self.study)

        def deny_existing(path, session, payload=None):
            if payload and payload["writes"][0]["update"]["name"] in self.documents:
                raise FirebaseRequestError(403)
            return self.request(path, session, payload)

        with patch("patient_study_store._request", side_effect=deny_existing):
            store.save_patient("p", self.session, self.patient)
            store.save_study("p", self.session, self.study)
            with self.assertRaises(StudyPatientConflict):
                store.save_study("p", self.session, StudyIdentity("study-1", other))
        self.assertEqual(store.get_study("p", self.session, "study-1"), self.study)

    def test_denied_create_with_no_existing_record_preserves_403(self):
        def deny_create(path, session, payload=None):
            if payload:
                raise FirebaseRequestError(403)
            return self.request(path, session, payload)

        with patch("patient_study_store._request", side_effect=deny_create):
            with self.assertRaises(FirebaseRequestError) as caught:
                store.save_patient("p", self.session, self.patient)
        self.assertEqual(caught.exception.status, 403)

    def test_mismatched_or_forged_document_is_rejected(self):
        store.save_patient("p", self.session, self.patient)
        document = next(iter(self.documents.values()))
        document["fields"]["owner_uid"] = {"stringValue": "someone-else"}
        with self.assertRaises(FirebaseRequestError):
            store.get_patient("p", self.session, self.patient)

    def test_list_returns_only_valid_owner_documents_and_next_page(self):
        store.save_patient("p", self.session, self.patient)
        store.save_study("p", self.session, self.study)
        patients = [doc for name, doc in self.documents.items() if "/patients/" in name]
        studies = [doc for name, doc in self.documents.items() if "/studies/" in name]
        with patch("patient_study_store.request_json", return_value={"documents": patients, "nextPageToken": "next"}):
            self.assertEqual(store.list_patients("p", self.session), ([self.patient], "next"))
        with patch("patient_study_store.request_json", return_value={"documents": studies}):
            self.assertEqual(store.list_studies("p", self.session), ([self.study], ""))

    def test_expired_session_fails_before_request(self):
        expired = Session("owner", "test", "token", 0)
        with self.assertRaises(FirebaseRequestError) as caught:
            store.save_patient("p", expired, self.patient)
        self.assertEqual(caught.exception.status, 401)
        self.assertFalse(self.documents)

    def test_patient_scoped_study_query_uses_the_confirmed_patient(self):
        store.save_patient("p", self.session, self.patient)
        with patch("storage_query.query_page", return_value=([self.study], "next")) as query:
            self.assertEqual(store.list_patient_studies("p", self.session, self.patient), ([self.study], "next"))
        self.assertEqual(query.call_args.args[2:5], ("studies", "patient_key", store._patient_key(self.patient)))


if __name__ == "__main__":
    unittest.main()
