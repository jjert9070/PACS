from copy import deepcopy
from dataclasses import replace
import unittest

from case_records import ProcessingRecord
from image_processing import gaussian_blur
from review_validation import prepare_review_draft
from roi import compare_roi
import test_review_sources as fixtures


class ReviewDraftTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ReviewSourceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.source, self.mapping = self.fixture.open(self.fixture.dicom_ref)
        self.roi = [20, 20, 100, 100]
        image = gaussian_blur(self.source.image, 2)
        stats = compare_roi(self.source.image, image, self.roi)
        stats["source_id"] = self.source.source_id
        processing = ProcessingRecord(self.source.source_id, "gaussian_blur", "2.0.0", {"radius": 2}, image,
                                      tuple(self.roi), deepcopy(stats), environment={"libraries": {"pillow": "old"}})
        self.draft = {"schema": "pacs_review_v1", "review_id": "a" * 32,
                      "source_ref": deepcopy(self.fixture.dicom_ref), "annotations": [],
                      "roi": self.roi[:], "roi_statistics": stats, "processing": processing,
                      "display_mapping": deepcopy(self.mapping)}

    def prepare(self, draft=None):
        return prepare_review_draft("p", self.fixture.session, self.fixture.root, self.draft if draft is None else draft)

    def test_actual_dicom_and_result_validate_without_writes(self):
        before = {p: p.read_bytes() for p in self.fixture.root.rglob("*") if p.is_file()}
        self.fixture.requests.clear()
        source, checked = self.prepare()
        self.assertEqual(source.original_bytes, self.source.original_bytes)
        self.assertEqual(checked["roi_statistics"], self.draft["roi_statistics"])
        self.assertEqual(before, {p: p.read_bytes() for p in self.fixture.root.rglob("*") if p.is_file()})
        self.assertFalse(any(write for _, write in self.fixture.requests))

    def test_result_and_all_mutable_metadata_detached(self):
        _, checked = self.prepare()
        pixels = checked["processing"].result.tobytes()
        self.draft["processing"].result.paste((0, 0, 0), (0, 0, 256, 256))
        self.draft["processing"].parameters["radius"] = 8
        self.draft["processing"].environment["libraries"]["pillow"] = "new"
        self.draft["roi_statistics"]["original"]["mean"] = 0
        self.draft["source_ref"]["patient_id"] = "changed"
        self.assertEqual(checked["processing"].result.tobytes(), pixels)
        self.assertEqual(checked["processing"].parameters, {"radius": 2})
        self.assertEqual(checked["processing"].environment["libraries"]["pillow"], "old")
        self.assertEqual(checked["source_ref"], self.fixture.dicom_ref)

    def test_roi_without_processing_and_processing_without_roi(self):
        roi_only = deepcopy(self.draft)
        roi_only["processing"] = None
        roi_only["roi_statistics"] = compare_roi(self.source.image, None, self.roi)
        roi_only["roi_statistics"]["source_id"] = self.source.source_id
        _, checked = self.prepare(roi_only)
        self.assertIsNone(checked["roi_statistics"]["result"])
        result_only = deepcopy(self.draft)
        result_only.update(roi=None, roi_statistics=None)
        result_only["processing"] = replace(result_only["processing"], roi=None, statistics={})
        _, checked = self.prepare(result_only)
        self.assertIsNone(checked["roi"])
        self.assertEqual(checked["processing"].statistics, {})

    def test_empty_png_draft_requires_null_mapping(self):
        draft = deepcopy(self.draft)
        draft.update(source_ref=self.fixture.png_ref, processing=None, roi=None, roi_statistics=None, display_mapping=None)
        _, checked = self.prepare(draft)
        self.assertIsNone(checked["display_mapping"])
        draft["display_mapping"] = self.mapping
        with self.assertRaises(ValueError):
            self.prepare(draft)

    def test_fabricated_or_changed_statistics_rejected_even_when_record_agrees(self):
        for name in ("original", "result"):
            for field, value in (("mean", 250), ("population_variance", 0), ("pixel_count", True)):
                with self.subTest(name=name, field=field):
                    draft = deepcopy(self.draft)
                    draft["roi_statistics"][name][field] = value
                    draft["processing"].statistics[name][field] = value
                    with self.assertRaises(ValueError):
                        self.prepare(draft)

    def test_fabricated_mapping_and_boolean_provenance_rejected(self):
        for key, value in (("source_id", "a" * 64), ("frame_count", True)):
            draft = deepcopy(self.draft)
            draft["display_mapping"][key] = value
            with self.assertRaises(ValueError):
                self.prepare(draft)

    def test_wrong_statistics_metadata_and_calculation_rejected(self):
        for change in ({"width": True}, {"bounds": [20.0, 20, 100, 100]},
                       {"source_id": "0" * 64}, {"calculation": {}}, {"result": None}):
            draft = deepcopy(self.draft)
            draft["roi_statistics"].update(change)
            with self.assertRaises(ValueError):
                self.prepare(draft)

    def test_no_roi_requires_null_statistics_and_matching_record(self):
        draft = deepcopy(self.draft)
        draft["roi"] = None
        with self.assertRaises(ValueError):
            self.prepare(draft)
        draft["roi_statistics"] = None
        with self.assertRaises(ValueError):
            self.prepare(draft)

    def test_future_missing_or_extra_fields_and_invalid_id_rejected(self):
        for change in ({"schema": "future"}, {"review_id": "../path"}, {"extra": None}, {"annotations": {}}):
            with self.assertRaises(ValueError):
                self.prepare(dict(self.draft, **change))
        missing = dict(self.draft)
        del missing["display_mapping"]
        with self.assertRaises(ValueError):
            self.prepare(missing)

    def test_annotations_not_silently_dropped_before_measurement_connection(self):
        with self.assertRaisesRegex(ValueError, "B 측정"):
            self.prepare(dict(self.draft, annotations=[{"annotation_id": "b" * 32}]))

    def test_nan_and_oversized_utf8_metadata_rejected(self):
        for value in (float("nan"), "가" * 21000):
            draft = deepcopy(self.draft)
            draft["processing"].parameters["extra"] = value
            with self.assertRaises(ValueError):
                self.prepare(draft)


if __name__ == "__main__":
    unittest.main()
