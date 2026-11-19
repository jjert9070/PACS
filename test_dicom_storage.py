"""Storage fixtures use synthetic bytes and a stub importer, not real DICOM decoding."""

from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from dicom_instance_store import (DicomInstanceConflict, DicomInputError, DicomMetadata,
                                  claim_dicom_uids, get_dicom_instance, open_dicom_original)
from dicom_original_storage import read_dicom_original, save_dicom_original
from dicom_registration import (DicomOriginalSavedError, list_pending_dicom,
                                register_dicom_original, retry_dicom_registration)
from firebase_client import FirebaseRequestError, Session
from local_storage import LocalStorageError
from patient_study_identity import PatientIdentity, StudyIdentity
from series_store import Series


class DicomStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = Session("owner", "test", "token", time.monotonic() + 3600)
        self.metadata = DicomMetadata(PatientIdentity("research", "patient"),
                                      "1.2.3.1", "1.2.3.2", "1.2.3.3", "1.2.840.10008.5.1.4.1.1.2", 1, "CT", "HEAD")
        self.selected = StudyIdentity(self.metadata.study_uid, self.metadata.patient)
        self.data = b"synthetic raw fixture, not a valid DICOM file"
        self.documents = {}
        self.deny_instance = False
        stack = ExitStack()
        self.addCleanup(stack.close)
        for module in ("patient_study_store", "dicom_instance_store"):
            stack.enter_context(patch(module + "._request", side_effect=self.request))
        stack.enter_context(patch("dicom_registration.get_study", return_value=self.selected))
        stack.enter_context(patch("dicom_instance_store.get_study", return_value=self.selected))
        stack.enter_context(patch("dicom_registration.save_series"))
        stack.enter_context(patch("dicom_instance_store.get_series", return_value=Series(self.metadata.study_uid, self.metadata.series_uid, "CT", "HEAD")))

    def request(self, path, session, payload=None):
        if payload is None:
            if path not in self.documents:
                raise FirebaseRequestError(404)
            return deepcopy(self.documents[path])
        for write in payload["writes"]:
            doc = write["update"]
            if self.deny_instance and "/dicom_instances/" in doc["name"]:
                raise FirebaseRequestError(503)
            if doc["name"] in self.documents:
                raise FirebaseRequestError(403)
            self.documents[doc["name"]] = deepcopy(doc)
        return {}

    def register(self, data=None):
        return register_dicom_original("p", self.session, self.root, self.selected,
                                       self.metadata.series_uid, self.data if data is None else data,
                                       lambda original: self.metadata)

    def test_original_bytes_preserved_and_same_uid_reused(self):
        first = self.register()
        self.assertEqual(self.register(), first)
        self.assertEqual(read_dicom_original(self.root, "owner", first.original_key), self.data)
        self.assertEqual(get_dicom_instance("p", self.session, self.metadata.sop_uid), first)
        self.assertEqual(len(list(self.root.rglob("*.dcm"))), 1)
        self.assertFalse(list_pending_dicom(self.root, "p", self.session))

    def test_same_uid_different_content_is_not_saved_over_old_original(self):
        first = self.register()
        with self.assertRaises(DicomInstanceConflict):
            self.register(b"another synthetic raw fixture")
        self.assertEqual(read_dicom_original(self.root, "owner", first.original_key), self.data)
        self.assertEqual(len(list(self.root.rglob("*.dcm"))), 1)

    def test_partial_failure_survives_restart_and_retries_same_reference(self):
        self.deny_instance = True
        with self.assertRaises(DicomOriginalSavedError) as caught:
            self.register()
        self.assertTrue(caught.exception.retry_recorded)
        pending = list_pending_dicom(Path(str(self.root)), "p", self.session)
        self.assertEqual(pending[0].state, "request_failed")
        self.deny_instance = False
        retry_dicom_registration(self.root, "p", self.session, self.metadata.sop_uid, lambda data: self.metadata)
        self.assertFalse(list_pending_dicom(self.root, "p", self.session))

    def test_uid_cannot_switch_between_study_and_instance_roles(self):
        self.register()
        other = DicomMetadata(self.metadata.patient, self.metadata.sop_uid, "1.2.3.8", "1.2.3.9", self.metadata.sop_class_uid, 1)
        with self.assertRaises(DicomInstanceConflict):
            claim_dicom_uids("p", self.session, other)

    def test_wrong_patient_or_invalid_importer_does_not_write_original(self):
        other = StudyIdentity(self.metadata.study_uid, PatientIdentity("other", "patient"))
        with self.assertRaises(DicomInstanceConflict):
            register_dicom_original("p", self.session, self.root, other, self.metadata.series_uid, self.data, lambda data: self.metadata)
        with self.assertRaises(DicomInputError):
            register_dicom_original("p", self.session, self.root, self.selected, self.metadata.series_uid, self.data, lambda data: {})
        self.assertFalse(list(self.root.rglob("*.dcm")))

    def test_corruption_and_other_user_cannot_read_original(self):
        key = save_dicom_original(self.root, "owner", self.data)
        with self.assertRaises(LocalStorageError):
            read_dicom_original(self.root, "other", key)
        next(self.root.rglob(key)).write_bytes(b"corrupt")
        with self.assertRaises(LocalStorageError):
            read_dicom_original(self.root, "owner", key)
        with self.assertRaises(LocalStorageError):
            save_dicom_original(self.root, "owner", self.data)

    def test_cleanup_failure_preserves_published_dicom_original(self):
        with self.assertLogs("local_storage", level="WARNING") as logs:
            with patch.object(Path, "unlink", side_effect=PermissionError(13, "private cleanup path")):
                key = save_dicom_original(self.root, "owner", self.data)
        self.assertEqual(read_dicom_original(self.root, "owner", key), self.data)
        self.assertIn("original_temp_cleanup_failed errno=13", logs.output[0])
        self.assertNotIn("private cleanup path", logs.output[0])
        self.assertNotIn(str(self.root), logs.output[0])

    def test_cleanup_failure_does_not_mask_dicom_publish_error(self):
        with self.assertLogs("local_storage", level="WARNING"):
            with patch("dicom_original_storage.os.link", side_effect=OSError("publish failed")):
                with patch.object(Path, "unlink", side_effect=PermissionError(13, "cleanup failed")):
                    with self.assertRaises(LocalStorageError) as caught:
                        save_dicom_original(self.root, "owner", self.data)
        self.assertIn("저장", str(caught.exception))
        self.assertNotIn("cleanup", str(caught.exception))
        self.assertFalse(list(self.root.rglob("*.dcm")))

    def test_selected_series_is_checked_before_open(self):
        self.register()
        with self.assertRaises(DicomInstanceConflict):
            open_dicom_original("p", self.session, self.root, self.selected, "1.2.3.9", self.metadata.sop_uid, lambda data: self.metadata)

    def test_invalid_uid_and_unconfirmed_frame_count_rejected(self):
        for uid in ("1.02.3", "1..3", "name", "1.2.3 "):
            with self.assertRaises(DicomInputError):
                DicomMetadata(self.metadata.patient, uid, "1.2.4", "1.2.5", "1.2.6", 1)
        with self.assertRaises(DicomInputError):
            DicomMetadata(self.metadata.patient, "1.2.3", "1.2.4", "1.2.5", "1.2.6", True)

    def test_class_uid_cannot_identify_the_same_instance(self):
        with self.assertRaises(DicomInputError):
            DicomMetadata(self.metadata.patient, "1.2.3", "1.2.4", "1.2.5", "1.2.5", 1)

    def test_failure_journal_error_does_not_hide_remote_error(self):
        self.deny_instance = True
        with self.assertRaises(DicomOriginalSavedError):
            self.register()
        with patch("dicom_registration._finish", side_effect=LocalStorageError("disk full")):
            with self.assertRaises(FirebaseRequestError) as caught:
                retry_dicom_registration(self.root, "p", self.session, self.metadata.sop_uid, lambda data: self.metadata)
        self.assertEqual(caught.exception.status, 503)
        self.assertIsInstance(caught.exception.__cause__, LocalStorageError)

    def test_wrong_firestore_field_type_is_rejected(self):
        self.register()
        doc = next(doc for name, doc in self.documents.items() if "/dicom_instances/" in name)
        doc["fields"]["frame_count"] = {"stringValue": 1}
        with self.assertRaises(FirebaseRequestError):
            get_dicom_instance("p", self.session, self.metadata.sop_uid)


if __name__ == "__main__":
    unittest.main()
