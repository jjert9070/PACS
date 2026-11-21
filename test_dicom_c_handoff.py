"""B decoder through C's real registration flow (runs once C's storage modules are present).

Firestore is mocked as in C's own tests; the DICOM bytes and the decoder are real.
"""

from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

try:
    from dicom_instance_store import DicomInputError, DicomInstanceConflict, get_dicom_instance
    from dicom_original_storage import read_dicom_original
    from dicom_registration import list_pending_dicom, register_dicom_original
    from firebase_client import FirebaseRequestError, Session
    from patient_study_identity import PatientIdentity, StudyIdentity
    from series_store import Series
    C_AVAILABLE = True
except ModuleNotFoundError:
    C_AVAILABLE = False

from dicom_input import read_dicom, storage_metadata

SAMPLES = Path(__file__).resolve().parent / "samples" / "dicom"


@unittest.skipUnless(C_AVAILABLE, "C storage modules are on feature/C-patient-study-storage")
class DicomHandoffTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.session = Session("owner", "test", "token", time.monotonic() + 3600)
        self.data = (SAMPLES / "ct_phantom.dcm").read_bytes()
        imported = read_dicom("ct_phantom.dcm", self.data)
        self.patient = PatientIdentity(imported.patient_issuer, imported.patient_id)
        self.selected = StudyIdentity(imported.study_uid, self.patient)
        self.series_uid = imported.series_uid
        self.sop_uid = imported.sop_uid
        self.documents = {}
        stack = ExitStack()
        self.addCleanup(stack.close)
        for module in ("patient_study_store", "dicom_instance_store"):
            stack.enter_context(patch(module + "._request", side_effect=self.request))
        stack.enter_context(patch("dicom_registration.get_study", return_value=self.selected))
        stack.enter_context(patch("dicom_instance_store.get_study", return_value=self.selected))
        stack.enter_context(patch("dicom_registration.save_series"))
        stack.enter_context(patch("dicom_instance_store.get_series",
                                  return_value=Series(imported.study_uid, imported.series_uid, "CT", "HEAD")))

    def request(self, path, session, payload=None):
        if payload is None:
            if path not in self.documents:
                raise FirebaseRequestError(404)
            return deepcopy(self.documents[path])
        for write in payload["writes"]:
            doc = write["update"]
            if doc["name"] in self.documents:
                raise FirebaseRequestError(403)
            self.documents[doc["name"]] = deepcopy(doc)
        return {}

    def register(self, data, selected=None, series_uid=None):
        return register_dicom_original("p", self.session, self.root, selected or self.selected,
                                       series_uid or self.series_uid, data, storage_metadata)

    def test_real_sample_registers_with_unchanged_original(self):
        instance = self.register(self.data)
        self.assertEqual(instance.metadata.sop_uid, self.sop_uid)
        self.assertEqual((instance.metadata.modality, instance.metadata.frame_count), ("CT", 1))
        self.assertEqual(read_dicom_original(self.root, "owner", instance.original_key), self.data)
        self.assertEqual(instance.original_key[:-4], read_dicom("", self.data).source_id)
        self.assertEqual(get_dicom_instance("p", self.session, self.sop_uid), instance)
        self.assertEqual(self.register(self.data), instance)          # same UID + content reused
        self.assertFalse(list_pending_dicom(self.root, "p", self.session))

    def test_unsupported_or_corrupt_dicom_is_never_stored(self):
        for name in ("unsupported_multiframe.dcm", "unsupported_jpeg_baseline.dcm",
                     "unsupported_rgb.dcm", "missing_issuer.dcm", "truncated.dcm"):
            with self.subTest(name=name):
                with self.assertRaises(DicomInputError):
                    self.register((SAMPLES / name).read_bytes())
        self.assertFalse(list(self.root.rglob("*.dcm")))

    def test_selection_must_match_the_file(self):
        other = StudyIdentity(self.selected.study_id, PatientIdentity("PACS-EDU-SYNTH", "SYN-0002"))
        with self.assertRaises(DicomInstanceConflict):
            self.register(self.data, selected=other)
        self.assertFalse(list(self.root.rglob("*.dcm")))


if __name__ == "__main__":
    unittest.main()
