from pathlib import Path
import time
import unittest
from unittest.mock import patch

from firebase_client import FirebaseRequestError, Session
from patient_study_identity import PatientIdentity, StudyIdentity
from series_store import Series
from study_image_store import OriginalSavedLinkError, StudyImage, StudyImageConflict
from patient_storage_service import open_selected_original, register_sample_original
from viewer import sample_image_bytes


class PatientStorageServiceTests(unittest.TestCase):
    def setUp(self):
        self.session = Session("owner", "test", "token", time.monotonic() + 3600)
        self.patient = PatientIdentity("research", "patient")
        self.series = Series("study", "series")
        self.image = StudyImage("study", "series", "image", "a" * 64 + ".png")

    def test_invalid_file_does_not_create_patient(self):
        with patch("patient_storage_service.save_patient") as save:
            result = register_sample_original("p", self.session, Path("unused"), self.patient, self.series, "image", "bad.png", b"broken")
        self.assertFalse(result.success)
        self.assertEqual(result.stage, "input")
        save.assert_not_called()

    def test_invalid_file_types_return_input_error_without_writes(self):
        for name, data in ((None, b"data"), ("sample.png", None), ("sample.png", "text")):
            with self.subTest(name=name, data=data):
                with patch("patient_storage_service.save_patient") as save:
                    result = register_sample_original("p", self.session, Path("unused"), self.patient, self.series, "image", name, data)
                self.assertFalse(result.success)
                self.assertEqual((result.stage, result.error_code), ("input", "invalid_input"))
                save.assert_not_called()

    def test_partial_failure_returns_original_and_retry_information(self):
        error = OriginalSavedLinkError(self.image.original_key, True)
        error.__cause__ = FirebaseRequestError(503)
        with patch("patient_storage_service.save_patient"), patch("patient_storage_service.save_study"), patch("patient_storage_service.save_series"):
            with patch("patient_storage_service.save_and_link_original", side_effect=error):
                result = register_sample_original("p", self.session, Path("unused"), self.patient, self.series, "image", "sample.png", sample_image_bytes())
        self.assertFalse(result.success)
        self.assertEqual((result.stage, result.error_code, result.retry_image_id), ("link", "remote_failure", "image"))
        self.assertEqual(result.original_key, self.image.original_key)

    def test_another_patients_selection_cannot_open_original(self):
        with patch("patient_storage_service.get_study", return_value=StudyIdentity("study", self.patient)):
            with patch("patient_storage_service.get_series", return_value=self.series), patch("patient_storage_service.get_image", return_value=self.image):
                with patch("patient_storage_service.open_original") as open_file:
                    with self.assertRaises(StudyImageConflict):
                        open_selected_original("p", self.session, Path("unused"), PatientIdentity("other", "patient"), "study", "series", "image")
        open_file.assert_not_called()


if __name__ == "__main__":
    unittest.main()
