from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image

from dicom_storage_flow import register_dicom_file
from firebase_client import FirebaseRequestError, Session
from local_storage import _user_dir
from patient_storage_service import register_sample_original
from patient_study_identity import PatientIdentity, StudyIdentity
from patient_study_store import _key, save_patient, save_study
from review_sources import ReviewSourceError, open_review_source
from roi import COORDINATE_SYSTEM
from series_store import Series
from test_dicom_storage_flow import CT, JsonFirestore, ct_identity
from viewer import load_image, sample_image_bytes


class ReviewSourceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.session = Session("owner", "test", "token", time.monotonic() + 3600)
        self.store = JsonFirestore(self.root / "firestore.json")
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.requests = []

        def request(path, session, payload=None):
            self.requests.append((path, payload is not None))
            return self.store(path, session, payload)

        for module in ("patient_study_store", "series_store", "study_image_store", "dicom_instance_store"):
            stack.enter_context(patch(module + "._request", side_effect=request))
        self.imported, self.selected = ct_identity()
        save_patient("p", self.session, self.selected.patient)
        save_study("p", self.session, self.selected)
        result = register_dicom_file(
            "p", self.session, self.root, self.selected, self.imported.series_uid, "ct.dcm", CT,
        )
        self.assertTrue(result.completed)
        self.dicom_ref = self.ref(
            self.imported.source_id, "DICOM", self.selected, self.imported.series_uid,
            self.imported.sop_uid, (self.imported.columns, self.imported.rows),
        )
        self.png = load_image("sample.png", sample_image_bytes())
        self.png_study = StudyIdentity("sample-study", PatientIdentity("sample-issuer", "sample-id"))
        result = register_sample_original(
            "p", self.session, self.root, self.png_study.patient, Series("sample-study", "sample-series"),
            "sample-image", self.png.name, self.png.original_bytes,
        )
        self.assertTrue(result.success)
        self.png_ref = self.ref(self.png.source_id, "PNG", self.png_study, "sample-series", "sample-image", self.png.image.size)
        self.requests.clear()

    def ref(self, source_id, file_format, selected, series, object_id, size):
        return {
            "schema": "pacs_source_ref_v1", "owner_uid": "owner", "project_id": "p",
            "patient_issuer": selected.patient.issuer, "patient_id": selected.patient.patient_id,
            "study_id": selected.study_id, "series_id": series, "object_id": object_id,
            "source_id": source_id, "file_format": file_format, "frame_index": 0,
            "width": size[0], "height": size[1], "coordinate_space": COORDINATE_SYSTEM,
        }

    def open(self, ref):
        return open_review_source("p", self.session, self.root, ref)

    def assert_code(self, ref, code):
        with self.assertRaises(ReviewSourceError) as result:
            self.open(ref)
        self.assertEqual(result.exception.code, code)

    def test_dicom_original_and_trusted_mapping_without_writes(self):
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        source, mapping = self.open(self.dicom_ref)
        self.assertEqual(source.original_bytes, CT)
        self.assertEqual(source.image.tobytes(), self.imported.display_image.tobytes())
        self.assertEqual(mapping, self.imported.provenance())
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
        self.assertTrue(self.requests)
        self.assertFalse(any(write for _, write in self.requests))

    def test_png_original_without_dicom_mapping(self):
        source, mapping = self.open(self.png_ref)
        self.assertEqual(source.original_bytes, self.png.original_bytes)
        self.assertEqual(source.image.tobytes(), self.png.image.tobytes())
        self.assertIsNone(mapping)

    def test_jpeg_original_uses_existing_registration(self):
        stream = BytesIO()
        Image.new("RGB", (12, 9), "red").save(stream, "JPEG")
        data = stream.getvalue()
        result = register_sample_original(
            "p", self.session, self.root, self.png_study.patient, Series("sample-study", "sample-series"),
            "jpeg-image", "test.jpg", data,
        )
        self.assertTrue(result.success)
        ref = self.ref(sha256(data).hexdigest(), "JPEG", self.png_study, "sample-series", "jpeg-image", (12, 9))
        source, mapping = self.open(ref)
        self.assertEqual(source.original_bytes, data)
        self.assertIsNone(mapping)

    def test_other_owner_or_project_rejected_before_reads(self):
        for key in ("owner_uid", "project_id"):
            with self.subTest(key=key):
                self.assert_code(dict(self.dicom_ref, **{key: "other"}), "source_mismatch")
        self.assertEqual(self.requests, [])

    def test_expired_session_rejected_before_reads(self):
        with self.assertRaises(FirebaseRequestError) as result:
            open_review_source("p", replace(self.session, expires_at=0), self.root, self.dicom_ref)
        self.assertEqual(result.exception.status, 401)
        self.assertEqual(self.requests, [])

    def test_schema_missing_extra_path_and_bad_field_types(self):
        bad_refs = [None, {}, dict(self.png_ref, schema="future"), dict(self.png_ref, path="/tmp/other"),
                    dict(self.png_ref, width=True), dict(self.png_ref, height=0),
                    dict(self.png_ref, source_id="../source"), dict(self.png_ref, patient_id=4),
                    dict(self.png_ref, study_id=" sample-study"),
                    dict(self.png_ref, coordinate_space="screen_pixels")]
        missing = deepcopy(self.png_ref)
        del missing["object_id"]
        bad_refs.append(missing)
        for ref in bad_refs:
            with self.subTest(ref=ref):
                self.assert_code(ref, "invalid_input")
        self.assertEqual(self.requests, [])

    def test_nonzero_boolean_and_fractional_frame_rejected(self):
        for frame in (1, -1, True, 0.0):
            self.assert_code(dict(self.dicom_ref, frame_index=frame), "unsupported_frame")
        self.assertEqual(self.requests, [])

    def test_changed_hash_size_format_and_patient_rejected(self):
        for base in (self.png_ref, self.dicom_ref):
            for change in ({"source_id": "0" * 64}, {"width": base["width"] + 1},
                           {"patient_id": "another-patient"}):
                with self.subTest(base=base["file_format"], change=change):
                    self.assert_code(dict(base, **change), "source_mismatch")
        self.assert_code(dict(self.png_ref, file_format="JPEG"), "source_mismatch")

    def test_existing_other_series_rejected(self):
        from series_store import save_series
        save_series("p", self.session, Series(self.png_study.study_id, "other-series"))
        self.assert_code(dict(self.png_ref, series_id="other-series"), "source_mismatch")
        self.assert_code(dict(self.dicom_ref, series_id="2.25.9000"), "source_mismatch")

    def test_persisted_relationship_changes_are_rechecked(self):
        for ref in (self.png_ref, self.dicom_ref):
            path = "projects/p/databases/(default)/documents/users/owner/series/" + _key(ref["series_id"])
            documents = self.store._load()
            documents[path]["fields"]["study_id"] = {"stringValue": "other-study"}
            documents[path]["fields"]["study_key"] = {"stringValue": _key("other-study")}
            self.store.path.write_text(json.dumps(documents), encoding="utf-8")
            self.assert_code(ref, "source_mismatch")

    def test_missing_or_tampered_originals_rejected(self):
        for ref, suffix in ((self.png_ref, ".png"), (self.dicom_ref, ".dcm")):
            target = _user_dir(self.root, self.session.uid) / (ref["source_id"] + suffix)
            data = target.read_bytes()
            target.write_bytes(b"tampered")
            self.assert_code(ref, "original_unavailable")
            target.unlink()
            self.assert_code(ref, "original_unavailable")
            target.write_bytes(data)

    def test_permission_and_transport_errors_are_not_source_conflicts(self):
        for status in (403, 503):
            with patch("patient_study_store._request", side_effect=FirebaseRequestError(status)):
                with self.assertRaises(FirebaseRequestError) as result:
                    self.open(self.png_ref)
                self.assertEqual(result.exception.status, status)
            with patch("dicom_instance_store._request", side_effect=FirebaseRequestError(status)):
                self.assert_code(self.dicom_ref, "permission_denied" if status == 403 else "remote_failure")


if __name__ == "__main__":
    unittest.main()
