"""#38 measurement and calibration (pure; no Streamlit, Firebase or UI)."""

import copy
import json
import math
import unittest

import numpy as np
from pydicom.uid import ComputedRadiographyImageStorage, SecondaryCaptureImageStorage

import dicom_input
import test_dicom_input as samples
from measurement_tools import (
    CALIBRATION_METHOD_VERSION,
    MeasurementInputError,
    calibration_from_metadata,
    calibration_matches,
    measure_annotation,
    measurement_matches,
    synthetic_calibration,
)

HASH = "a" * 64


def ref(width=192, height=160, **changes):
    value = {
        "schema": "pacs_source_ref_v1", "owner_uid": "user-1", "project_id": "project-1",
        "patient_issuer": "PACS-EDU-SYNTH", "patient_id": "SYN-0001", "study_id": "2.25.1",
        "series_id": "2.25.2", "object_id": "2.25.3", "source_id": HASH, "file_format": "DICOM",
        "frame_index": 0, "width": width, "height": height,
        "coordinate_space": "exif_transposed_original_pixels_v1",
    }
    value.update(changes)
    return value


def line(p1, p2):
    return {"kind": "line", "points": [list(p1), list(p2)]}


def rect(*bounds):
    return {"kind": "rectangle", "bounds": list(bounds)}


def dicom(change=None, name="ct_phantom.dcm"):
    """A B-read DICOM and its SourceRef (display RGB size = columns x rows)."""
    data = samples.sample(name) if change is None else samples.rewrite(samples.sample(name), change)
    imported = dicom_input.read_dicom(name, data)
    source = ref(imported.columns, imported.rows, source_id=imported.source_id)
    return imported, source


def with_spacing(values, keyword="PixelSpacing"):
    def change(ds):
        setattr(ds, keyword, values)
    return change


class ContractExampleTests(unittest.TestCase):
    """The fixed examples in Issue #38 section 4."""

    def test_line_3_4_5_and_anisotropic_spacing(self):
        result = measure_annotation(ref(), line((10, 20), (13, 24)), synthetic_calibration(ref(), 0.5, 0.8))
        self.assertEqual((result["pixel_value"], result["pixel_unit"]), (5.0, "px"))
        self.assertTrue(math.isclose(result["physical_value"], 3.124099870362662, rel_tol=1e-9, abs_tol=1e-9))
        self.assertEqual((result["physical_unit"], result["calibration_status"]), ("mm", "verified"))
        self.assertEqual(result["method_version"], "measurement_v1")

    def test_rectangle_area_and_physical_area(self):
        result = measure_annotation(ref(), rect(10, 20, 30, 50), synthetic_calibration(ref(), 0.5, 0.8))
        self.assertEqual((result["pixel_value"], result["pixel_unit"]), (600, "px2"))
        self.assertTrue(math.isclose(result["physical_value"], 240.0, rel_tol=1e-9, abs_tol=1e-9))
        self.assertEqual(result["physical_unit"], "mm2")

    def test_one_pixel_rectangle_without_calibration(self):
        result = measure_annotation(ref(192, 160), rect(191, 159, 192, 160))
        self.assertEqual(result["pixel_value"], 1)
        self.assertIsNone(result["physical_value"])
        self.assertIsNone(result["physical_unit"])
        self.assertEqual((result["calibration_status"], result["calibration_reason"]), ("unverified", "no_calibration"))

    def test_same_point_twice_is_invalid(self):
        with self.assertRaises(MeasurementInputError) as caught:
            measure_annotation(ref(), line((5, 5), (5, 5)), synthetic_calibration(ref(), 0.5, 0.8))
        self.assertEqual(caught.exception.code, "invalid_geometry")

    def test_results_are_json_ready(self):
        calibration = synthetic_calibration(ref(), 0.5, 0.8)
        for result in (calibration, measure_annotation(ref(), line((0, 0), (3, 4)), calibration),
                       measure_annotation(ref(), rect(0, 0, 2, 2))):
            self.assertEqual(json.loads(json.dumps(result, allow_nan=False)), result)


class LineTests(unittest.TestCase):  # B14
    def test_swapping_points_keeps_length(self):
        calibration = synthetic_calibration(ref(), 0.5, 0.8)
        forward = measure_annotation(ref(), line((10, 20), (13, 24)), calibration)
        backward = measure_annotation(ref(), line((13, 24), (10, 20)), calibration)
        self.assertEqual(forward, backward)

    def test_horizontal_and_vertical_lines(self):
        self.assertEqual(measure_annotation(ref(), line((0, 7), (191, 7)))["pixel_value"], 191.0)
        self.assertEqual(measure_annotation(ref(), line((3, 0), (3, 159)))["pixel_value"], 159.0)


class RectangleTests(unittest.TestCase):  # B15, B05
    def test_full_image_and_one_pixel(self):
        self.assertEqual(measure_annotation(ref(192, 160), rect(0, 0, 192, 160))["pixel_value"], 192 * 160)
        self.assertEqual(measure_annotation(ref(192, 160), rect(0, 0, 1, 1))["pixel_value"], 1)

    def test_empty_reversed_and_outside_rectangles_are_rejected(self):
        for bounds in ((10, 10, 10, 20), (10, 10, 20, 10), (20, 20, 10, 30), (0, 0, 193, 10), (-1, 0, 5, 5),
                       (0, 0, 5)):
            with self.subTest(bounds=bounds), self.assertRaises(MeasurementInputError) as caught:
                measure_annotation(ref(192, 160), {"kind": "rectangle", "bounds": list(bounds)})
            self.assertEqual(caught.exception.code, "invalid_geometry")


class CoordinateValidationTests(unittest.TestCase):  # B03, B04, B16
    def test_bad_coordinates_are_rejected_not_converted(self):
        bad_points = (
            ((-1, 0), (5, 5)), ((0, 0), (192, 5)), ((0, 0), (5, 160)),       # negative and outside
            ((0.0, 0), (5, 5)), ((0, 0), (5.5, 5)),                          # floats are not truncated
            ((True, 0), (5, 5)), ((np.bool_(True), 0), (5, 5)),              # booleans
            ((float("nan"), 0), (5, 5)), ((0, float("inf")), (5, 5)),        # non-finite
            (("1", 0), (5, 5)),
        )
        for p1, p2 in bad_points:
            with self.subTest(points=(p1, p2)), self.assertRaises(MeasurementInputError) as caught:
                measure_annotation(ref(), line(p1, p2))
            self.assertEqual(caught.exception.code, "invalid_geometry")
        for bounds in ((0.0, 0, 5, 5), (False, 0, 5, 5), (0, 0, 5, float("nan"))):
            with self.subTest(bounds=bounds), self.assertRaises(MeasurementInputError):
                measure_annotation(ref(), {"kind": "rectangle", "bounds": list(bounds)})

    def test_numpy_integers_are_accepted_as_integers(self):
        result = measure_annotation(ref(), line((np.int64(10), np.int32(20)), (13, 24)))
        self.assertEqual(result["pixel_value"], 5.0)

    def test_geometry_shape_is_strict(self):
        for geometry in (
            None, [], {"points": [[0, 0], [1, 1]]}, {"kind": "circle", "points": [[0, 0], [1, 1]]},
            {"kind": "line", "points": [[0, 0], [1, 1]], "bounds": [0, 0, 1, 1]},
            {"kind": "rectangle", "bounds": [0, 0, 1, 1], "points": [[0, 0], [1, 1]]},
            {"kind": "line", "points": [[0, 0]]}, {"kind": "line", "points": [[0, 0], [1, 1], [2, 2]]},
            {"kind": "line", "points": [[0, 0, 0], [1, 1]]},
            {"kind": "line", "points": [[0, 0], [1, 1]], "view_revision": 3},  # view state is not geometry
        ):
            with self.subTest(geometry=geometry), self.assertRaises(MeasurementInputError) as caught:
                measure_annotation(ref(), geometry)
            self.assertEqual(caught.exception.code, "invalid_geometry")


class SourceRefTests(unittest.TestCase):  # B03, B17
    def test_malformed_or_mismatched_reference(self):
        for change in ({"schema": "pacs_source_ref_v0"}, {"source_id": "A" * 64}, {"source_id": "a" * 63},
                       {"file_format": "TIFF"}, {"width": 0}, {"height": True}, {"width": 1.5},
                       {"coordinate_space": "display_pixels"}, {"patient_id": ""}, {"object_id": None},
                       {"frame_index": True}, {"frame_index": 0.0}):
            with self.subTest(change=change), self.assertRaises(MeasurementInputError) as caught:
                measure_annotation(ref(**change), line((0, 0), (1, 1)))
            self.assertEqual(caught.exception.code, "source_mismatch")
        source = ref()
        source["extra"] = 1
        with self.assertRaises(MeasurementInputError):
            measure_annotation(source, line((0, 0), (1, 1)))

    def test_other_frames_are_unsupported(self):
        for frame in (1, -1, 7):
            with self.subTest(frame=frame), self.assertRaises(MeasurementInputError) as caught:
                measure_annotation(ref(frame_index=frame), line((0, 0), (1, 1)))
            self.assertEqual(caught.exception.code, "unsupported_frame")

    def test_calibration_from_another_original_is_rejected(self):
        other = synthetic_calibration(ref(source_id="b" * 64), 0.5, 0.8)
        with self.assertRaises(MeasurementInputError) as caught:
            measure_annotation(ref(), line((0, 0), (3, 4)), other)
        self.assertEqual(caught.exception.code, "source_mismatch")

    def test_coordinates_are_checked_against_this_reference_size(self):
        with self.assertRaises(MeasurementInputError) as caught:
            measure_annotation(ref(100, 50), line((0, 0), (120, 10)))
        self.assertEqual(caught.exception.code, "invalid_geometry")


class SpacingAxisTests(unittest.TestCase):  # B18, B08
    def test_row_spacing_is_vertical_and_column_spacing_is_horizontal(self):
        calibration = synthetic_calibration(ref(), 0.5, 0.8)  # row (vertical) 0.5, column (horizontal) 0.8
        horizontal = measure_annotation(ref(), line((0, 0), (10, 0)), calibration)
        vertical = measure_annotation(ref(), line((0, 0), (0, 10)), calibration)
        self.assertTrue(math.isclose(horizontal["physical_value"], 8.0, rel_tol=1e-9))
        self.assertTrue(math.isclose(vertical["physical_value"], 5.0, rel_tol=1e-9))

    def test_dicom_pixel_spacing_order_is_row_then_column(self):
        imported, source = dicom(with_spacing([0.5, 0.8]))
        calibration = calibration_from_metadata(source, imported.pixel_geometry)
        self.assertEqual((calibration["row_spacing_mm"], calibration["column_spacing_mm"]), (0.5, 0.8))
        horizontal = measure_annotation(source, line((0, 0), (10, 0)), calibration)
        self.assertTrue(math.isclose(horizontal["physical_value"], 8.0, rel_tol=1e-9))


class CalibrationTests(unittest.TestCase):  # B07, B09, B10, B19
    def test_ct_and_mr_image_plane_spacing_is_verified(self):
        for name in ("ct_phantom.dcm", "mr_phantom.dcm"):
            with self.subTest(name=name):
                imported, source = dicom(with_spacing([0.5, 0.8]), name)
                calibration = calibration_from_metadata(source, imported.pixel_geometry)
                self.assertEqual(calibration["status"], "verified")
                self.assertEqual(calibration["evidence"]["kind"], "dicom_supported_geometry")
                self.assertEqual(calibration["evidence"]["attributes"]["pixel_spacing_raw"], ["0.5", "0.8"])
                self.assertEqual(calibration["method_version"], CALIBRATION_METHOD_VERSION)
                result = measure_annotation(source, line((10, 20), (13, 24)), calibration)
                self.assertTrue(math.isclose(result["physical_value"], 3.124099870362662, rel_tol=1e-9))

    def test_missing_zero_negative_and_malformed_spacing_give_no_millimetres(self):
        cases = {
            "pixel_spacing_absent": None,
            "pixel_spacing_invalid": ([0, 0.8], [0.5, 0], [-0.5, 0.8], [0.5], [0.5, 0.8, 1.0],
                                      ["nan", "0.8"], ["inf", "0.8"]),
        }
        imported, source = dicom()
        self.assertEqual(calibration_from_metadata(source, imported.pixel_geometry)["reason"], "pixel_spacing_absent")
        for values in cases["pixel_spacing_invalid"]:
            with self.subTest(values=values):
                imported, source = dicom(with_spacing(values))
                calibration = calibration_from_metadata(source, imported.pixel_geometry)
                self.assertEqual((calibration["status"], calibration["reason"]),
                                 ("unverified", "pixel_spacing_invalid"))
                self.assertIsNone(calibration["row_spacing_mm"])
                result = measure_annotation(source, rect(0, 0, 10, 10), calibration)
                self.assertEqual(result["pixel_value"], 100)
                self.assertIsNone(result["physical_value"])

    def test_projection_radiograph_is_not_verified_even_with_spacing(self):
        for keyword in ("PixelSpacing", "ImagerPixelSpacing"):
            with self.subTest(keyword=keyword):
                imported, source = dicom(with_spacing([0.2, 0.2], keyword), "cr_monochrome1.dcm")
                self.assertEqual(imported.sop_class_uid, str(ComputedRadiographyImageStorage))
                calibration = calibration_from_metadata(source, imported.pixel_geometry)
                self.assertEqual((calibration["status"], calibration["reason"]),
                                 ("unverified", "projection_radiograph_spacing_not_verified"))

    def test_secondary_capture_and_raster_images_are_unsupported(self):
        def secondary(ds):
            ds.SOPClassUID = SecondaryCaptureImageStorage
            ds.file_meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
            ds.PixelSpacing = [0.5, 0.5]
        imported, source = dicom(secondary)
        self.assertEqual(calibration_from_metadata(source, imported.pixel_geometry)["status"], "unsupported")
        for file_format in ("PNG", "JPEG"):
            with self.subTest(file_format=file_format):
                calibration = calibration_from_metadata(ref(file_format=file_format), None)
                self.assertEqual((calibration["status"], calibration["reason"]),
                                 ("unsupported", "raster_has_no_physical_geometry"))
                self.assertIsNone(measure_annotation(ref(file_format=file_format), line((0, 0), (3, 4)),
                                                     calibration)["physical_value"])

    def test_no_metadata_is_unverified_not_an_error(self):
        calibration = calibration_from_metadata(ref(), None)
        self.assertEqual((calibration["status"], calibration["reason"]), ("unverified", "no_calibration_metadata"))

    def test_metadata_of_another_original_or_size_is_rejected(self):
        imported, source = dicom(with_spacing([0.5, 0.8]))
        for change, code in (({"source_id": "b" * 64}, "source_mismatch"),
                             ({"width": source["width"] + 1}, "source_mismatch"),
                             ({"file_format": "PNG"}, None)):
            with self.subTest(change=change):
                if code is None:  # a raster reference never uses DICOM metadata
                    self.assertEqual(calibration_from_metadata({**source, **change}, imported.pixel_geometry)["status"],
                                     "unsupported")
                    continue
                with self.assertRaises(MeasurementInputError) as caught:
                    calibration_from_metadata({**source, **change}, imported.pixel_geometry)
                self.assertEqual(caught.exception.code, code)
        multi = dict(imported.pixel_geometry, frame_count=2)
        with self.assertRaises(MeasurementInputError) as caught:
            calibration_from_metadata(source, multi)
        self.assertEqual(caught.exception.code, "unsupported_frame")
        with self.assertRaises(MeasurementInputError):
            calibration_from_metadata(source, {"pixel_spacing": {"values_mm": [0.5, 0.8]}})  # not B's metadata

    def test_forged_calibrations_are_rejected(self):
        good = synthetic_calibration(ref(), 0.5, 0.8)
        forged = (
            dict(good, evidence={"kind": "none", "attributes": {}}),           # verified without evidence
            dict(good, row_spacing_mm=None),                                   # verified without spacing
            dict(good, row_spacing_mm=0.0), dict(good, column_spacing_mm=-0.8),
            dict(good, row_spacing_mm=float("nan")), dict(good, column_spacing_mm=float("inf")),
            dict(good, row_spacing_mm=True), dict(good, status="trusted"),
            dict(good, method_version="calibration_v0"), {k: v for k, v in good.items() if k != "reason"},
        )
        for calibration in forged:
            with self.subTest(calibration=calibration), self.assertRaises(MeasurementInputError) as caught:
                measure_annotation(ref(), line((0, 0), (3, 4)), calibration)
            self.assertEqual(caught.exception.code, "source_mismatch")
        with self.assertRaises(MeasurementInputError):
            synthetic_calibration(ref(), 0, 0.8)


class VerificationHelperTests(unittest.TestCase):  # B13 (for C)
    def test_calibration_matches_only_what_b_derives_from_the_original(self):
        imported, source = dicom(with_spacing([0.5, 0.8]))
        real = calibration_from_metadata(source, imported.pixel_geometry)
        self.assertTrue(calibration_matches(source, real, imported.pixel_geometry))
        # A synthetic fixture is never accepted as the calibration of a real original.
        self.assertFalse(calibration_matches(source, synthetic_calibration(source, 0.5, 0.8), imported.pixel_geometry))
        self.assertFalse(calibration_matches(source, dict(real, row_spacing_mm=0.6), imported.pixel_geometry))

    def test_measurement_matches_with_tolerance_only(self):
        calibration = synthetic_calibration(ref(), 0.5, 0.8)
        geometry = line((10, 20), (13, 24))
        stored = measure_annotation(ref(), geometry, calibration)
        self.assertTrue(measurement_matches(ref(), geometry, calibration, stored))
        nudged = dict(stored, physical_value=stored["physical_value"] * (1 + 1e-12))
        self.assertTrue(measurement_matches(ref(), geometry, calibration, nudged))
        for wrong in (dict(stored, physical_value=3.12), dict(stored, pixel_value=0),
                      dict(stored, physical_unit="cm"), dict(stored, physical_value=None),
                      dict(stored, pixel_value="5"), {k: v for k, v in stored.items() if k != "method_version"}):
            with self.subTest(wrong=wrong):
                self.assertFalse(measurement_matches(ref(), geometry, calibration, wrong))
        self.assertFalse(measurement_matches(ref(), line((0, 0), (0, 0)), calibration, stored))


class PurityTests(unittest.TestCase):  # B20, B22
    def test_same_original_coordinates_give_the_same_result(self):
        calibration = synthetic_calibration(ref(), 0.5, 0.8)
        results = [measure_annotation(ref(), line((10, 20), (13, 24)), calibration) for _ in range(3)]
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])

    def test_inputs_metadata_and_images_are_not_modified(self):
        imported, source = dicom(with_spacing([0.5, 0.8]))
        geometry = line((10, 20), (13, 24))
        metadata = imported.pixel_geometry
        before = copy.deepcopy((source, geometry, metadata))
        image_bytes, pixels = imported.display_image.tobytes(), imported.stored_pixels.copy()
        calibration = calibration_from_metadata(source, metadata)
        calibration_before = copy.deepcopy(calibration)
        measure_annotation(source, geometry, calibration)
        measure_annotation(source, rect(0, 0, 5, 5), calibration)
        calibration_matches(source, calibration, metadata)
        self.assertEqual((source, geometry, metadata), before)
        self.assertEqual(calibration, calibration_before)
        self.assertEqual(imported.display_image.tobytes(), image_bytes)
        np.testing.assert_array_equal(imported.stored_pixels, pixels)

    def test_dicom_reader_contract_is_unchanged(self):
        imported, _ = dicom()
        # The new field is optional and last; existing provenance keys are unchanged.
        self.assertEqual(imported.pixel_geometry["schema"], "b_dicom_pixel_geometry_v1")
        self.assertNotIn("pixel_geometry", imported.provenance())
        self.assertNotIn("SYN-0001", json.dumps(imported.pixel_geometry))


if __name__ == "__main__":
    unittest.main()
