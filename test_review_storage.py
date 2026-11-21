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

from case_records import ProcessingRecord
from dicom_input import read_dicom, to_viewer_source
from dicom_original_storage import save_dicom_original
from firebase_client import FirebaseRequestError, Session
from image_processing import gaussian_blur
from local_storage import LocalStorageError, _user_dir, save_original
from review_storage import load_processing_result, save_processing_result
from roi import compare_roi


class ReviewResultStorageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.session = Session("owner", "test", "token", time.monotonic() + 3600)
        data = (Path(__file__).parent / "samples" / "dicom" / "ct_phantom.dcm").read_bytes()
        self.source = to_viewer_source(read_dicom("ct.dcm", data))
        self.original_key = save_dicom_original(self.root, self.session.uid, data)
        self.result = gaussian_blur(self.source.image, 2)
        roi = (20, 20, 100, 100)
        statistics = compare_roi(self.source.image, self.result, roi)
        statistics["source_id"] = self.source.source_id
        self.record = ProcessingRecord(
            self.source.source_id, "gaussian_blur", "2.0.0", {"radius": 2}, self.result,
            roi, statistics, environment={"schema": "b_processing_environment_v1", "libraries": {"pillow": "old"}},
        )

    def save(self, record=None):
        return save_processing_result(self.root, self.session, self.source, record or self.record)

    def load(self, metadata):
        return load_processing_result(self.root, self.session, self.source, metadata)

    def test_roundtrip_preserves_original_pixels_settings_roi_and_environment(self):
        original = _user_dir(self.root, self.session.uid) / self.original_key
        before = original.read_bytes()
        metadata = self.save()
        restored = self.load(json.loads(json.dumps(metadata)))
        self.assertEqual(restored.result.tobytes(), self.record.result.tobytes())
        self.assertEqual(restored.parameters, self.record.parameters)
        self.assertEqual(restored.statistics, self.record.statistics)
        self.assertEqual(tuple(restored.roi), self.record.roi)
        self.assertEqual(restored.environment, self.record.environment)
        self.assertEqual(original.read_bytes(), before)
        self.assertEqual(sha256(before).hexdigest(), self.source.source_id)
        self.assertEqual(metadata["source_id"], self.source.source_id)
        self.assertNotEqual(metadata["result_key"][:-4], self.source.source_id)

    def test_no_roi_and_no_processing_are_distinct(self):
        record = replace(self.record, roi=None, statistics={})
        restored = self.load(self.save(record))
        self.assertIsNone(restored.roi)
        self.assertEqual(restored.statistics, {})
        self.assertEqual(restored.environment, self.record.environment)
        files = set(self.root.rglob("*"))
        self.assertIsNone(save_processing_result(self.root, self.session, self.source, None))
        self.assertIsNone(self.load(None))
        self.assertEqual(files, set(self.root.rglob("*")))

    def test_saved_metadata_and_pixels_detached_from_later_edits(self):
        metadata = self.save()
        expected = self.record.result.tobytes()
        self.record.parameters["radius"] = 9
        self.record.environment["libraries"]["pillow"] = "new"
        self.record.result.paste((0, 0, 0), (0, 0, 256, 256))
        restored = self.load(metadata)
        self.assertEqual(restored.parameters, {"radius": 2})
        self.assertEqual(restored.environment["libraries"]["pillow"], "old")
        self.assertEqual(restored.result.tobytes(), expected)

    def test_repeated_result_reuses_same_content_file(self):
        first = self.save()
        target = _user_dir(self.root / "results", self.session.uid) / first["result_key"]
        target_stat = target.stat()
        second = self.save()
        self.assertEqual(first, second)
        self.assertEqual(target.stat().st_mtime_ns, target_stat.st_mtime_ns)
        self.assertEqual(len(list((self.root / "results").rglob("*.png"))), 1)

    def test_invalid_metadata_or_result_rejected_before_file_writes(self):
        records = [replace(self.record, source_id="a" * 64),
                   replace(self.record, result=Image.new("RGB", (2, 2))),
                   replace(self.record, result=self.result.convert("L")),
                   replace(self.record, environment={"number": float("nan")}),
                   replace(self.record, parameters={"note": "가" * 21000}),
                   replace(self.record, parameters={"note": "\ud800"})]
        for record in records:
            with self.subTest(record=record.parameters):
                with patch("review_storage.save_original") as write:
                    with self.assertRaises(ValueError):
                        self.save(record)
                    write.assert_not_called()

    def test_invalid_record_fields_and_paths_rejected_before_result_read(self):
        metadata = self.save()
        bad = [dict(metadata, result_key="../other.png"), dict(metadata, result_key=None),
               dict(metadata, source_id="0" * 64), dict(metadata, coordinate_space="screen"),
               dict(metadata, schema="future")]
        missing = deepcopy(metadata)
        del missing["parameters"]
        bad.append(missing)
        for item in bad:
            with patch("review_storage.open_original") as read:
                with self.assertRaises(ValueError):
                    self.load(item)
                read.assert_not_called()

    def test_missing_tampered_and_other_owners_result_not_returned(self):
        metadata = self.save()
        target = _user_dir(self.root / "results", self.session.uid) / metadata["result_key"]
        data = target.read_bytes()
        target.write_bytes(b"changed")
        with self.assertRaises(LocalStorageError):
            self.load(metadata)
        target.unlink()
        with self.assertRaises(LocalStorageError):
            self.load(metadata)
        target.write_bytes(data)
        with self.assertRaises(LocalStorageError):
            load_processing_result(self.root, replace(self.session, uid="other"), self.source, metadata)

    def test_valid_hash_but_wrong_result_size_is_rejected(self):
        metadata = self.save()
        stream = BytesIO()
        Image.new("RGB", (3, 3)).save(stream, "PNG")
        metadata["result_key"] = save_original(self.root / "results", self.session.uid, "small.png", stream.getvalue())
        with self.assertRaises(ValueError):
            self.load(metadata)

    def test_legacy_environment_and_unknown_algorithm_read_without_execution(self):
        metadata = self.save()
        del metadata["environment"]
        metadata["algorithm_id"] = "legacy_algorithm"
        with patch("image_processing.gaussian_blur", side_effect=AssertionError("must not rerun")):
            restored = self.load(metadata)
        self.assertIsNone(restored.environment)
        self.assertEqual(restored.algorithm_id, "legacy_algorithm")
        self.assertEqual(restored.result.tobytes(), self.result.tobytes())

    def test_file_failure_does_not_report_success_or_remove_original(self):
        target = _user_dir(self.root, self.session.uid) / self.original_key
        with patch("review_storage.save_original", side_effect=LocalStorageError("failed")):
            with self.assertRaises(LocalStorageError):
                self.save()
        self.assertEqual(target.read_bytes(), self.source.original_bytes)

    def test_expired_session_cannot_read_or_write_even_absent_processing(self):
        expired = replace(self.session, expires_at=0)
        for call in (save_processing_result, load_processing_result):
            with self.assertRaises(FirebaseRequestError) as result:
                call(self.root, expired, self.source, None)
            self.assertEqual(result.exception.status, 401)


if __name__ == "__main__":
    unittest.main()
