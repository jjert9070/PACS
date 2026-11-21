from copy import deepcopy
import unittest

from review_annotations import validate_annotations


class AnnotationTests(unittest.TestCase):
    def setUp(self):
        self.source = {"source_id": "a" * 64, "width": 10, "height": 8, "frame_index": 0}
        self.metadata = {"spacing": None}
        self.calls = []
        self.calibration = {"status": "unverified", "source_id": "a" * 64}
        self.measurement = {"pixel_value": 5, "pixel_unit": "px", "physical_value": None}
        self.item = {"schema": "pacs_annotation_v1", "annotation_id": "b" * 32,
                     "source_ref": deepcopy(self.source), "kind": "line", "points": [[0, 0], [3, 4]],
                     "label": "example", "calibration": deepcopy(self.calibration),
                     "measurement": deepcopy(self.measurement)}

    def calibration_fn(self, ref, metadata):
        self.calls.append((ref, metadata))
        return deepcopy(self.calibration)

    def measure_fn(self, ref, geometry, calibration):
        self.calls.append((ref, geometry, calibration))
        return deepcopy(self.measurement)

    def validate(self, annotations):
        return validate_annotations(self.source, annotations, self.calibration_fn, self.measure_fn, self.metadata)

    def test_original_metadata_and_geometry_used_for_recalculation(self):
        result = self.validate([self.item])
        self.assertEqual(result, [self.item])
        self.assertEqual(self.calls[0], (self.source, self.metadata))
        self.assertEqual(self.calls[1][1], {"kind": "line", "points": [[0, 0], [3, 4]]})
        self.item["points"][0][0] = 2
        self.assertEqual(result[0]["points"][0][0], 0)

    def test_mismatched_measurement_calibration_source_and_frame_rejected(self):
        for field, value in (("measurement", {"pixel_value": 0}), ("calibration", {"status": "verified"}),
                             ("source_ref", dict(self.source, frame_index=1))):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate([dict(self.item, **{field: value})])

    def test_invalid_geometry_duplicate_and_extra_field_rejected(self):
        for points in ([[0, 0], [0, 0]], [[0, 0], [10, 0]], [[True, 0], [1, 1]], [[0, 0], [1.0, 1]]):
            with self.subTest(points=points), self.assertRaises(ValueError):
                self.validate([dict(self.item, points=points)])
        with self.assertRaises(ValueError):
            self.validate([self.item, self.item])
        with self.assertRaises(ValueError):
            self.validate([dict(self.item, bounds=[0, 0, 1, 1])])

    def test_rectangle_end_exclusive_and_missing_adapter(self):
        rectangle = dict(self.item, kind="rectangle", bounds=[9, 7, 10, 8])
        del rectangle["points"]
        self.assertEqual(self.validate([rectangle]), [rectangle])
        with self.assertRaises(ValueError):
            validate_annotations(self.source, [rectangle], None, self.measure_fn, self.metadata)


if __name__ == "__main__":
    unittest.main()
