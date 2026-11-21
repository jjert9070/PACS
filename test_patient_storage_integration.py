"""Local integration of identity, series, original storage, and existing viewer processing."""

from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from firebase_client import FirebaseRequestError, Session
from image_processing import gaussian_blur
from patient_study_identity import PatientIdentity, StudyIdentity
from patient_study_store import save_patient, save_study
from series_store import Series, save_series
from study_details_store import StudyDetails, get_study_details, save_study_details
from study_image_store import save_and_link_original, open_study_original
from viewer import ViewTransform, sample_image_bytes


class PatientStorageIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.session = Session("owner", "test", "token", time.monotonic() + 3600)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.documents = {}
        self.version = 0
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for module in ("patient_study_store", "series_store", "study_details_store", "study_image_store"):
            self.stack.enter_context(patch(module + "._request", side_effect=self.request))

    def request(self, path, session, payload=None):
        if payload is None:
            if path not in self.documents:
                raise FirebaseRequestError(404)
            return deepcopy(self.documents[path])
        for write in payload["writes"]:
            doc = deepcopy(write["update"])
            old = self.documents.get(doc["name"])
            condition = write["currentDocument"]
            if (condition.get("exists") is False and old is not None) or (
                "updateTime" in condition and (old is None or old["updateTime"] != condition["updateTime"])
            ):
                raise FirebaseRequestError(412)
            self.version += 1
            doc["updateTime"] = str(self.version)
            self.documents[doc["name"]] = doc
        return {}

    def test_save_reopen_and_use_existing_viewer_and_processing(self):
        patient = PatientIdentity("research", "patient-1")
        save_patient("p", self.session, patient)
        save_study("p", self.session, StudyIdentity("study-1", patient))
        save_study_details("p", self.session, StudyDetails("study-1", "2026-11-19", "confirmed", "partial"))
        save_series("p", self.session, Series("study-1", "series-1"))
        data = sample_image_bytes()
        image = save_and_link_original("p", self.session, self.root, "study-1", "series-1", "image-1", "sample.png", data)
        reopened = open_study_original("p", self.session, self.root, "study-1", image.image_id)
        self.assertEqual(reopened.original_bytes, data)
        self.assertEqual(get_study_details("p", self.session, "study-1").details.receipt_status, "partial")
        processed = gaussian_blur(reopened.image, 2.0)
        transform = ViewTransform((0, 0, 320, 240), turns=1)
        self.assertEqual(transform.apply(reopened.image).size, transform.apply(processed).size)
        self.assertEqual(reopened.original_bytes, data)


if __name__ == "__main__":
    unittest.main()
