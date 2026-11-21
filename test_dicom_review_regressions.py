"""Regression cases for the B/C review: keep invalid inputs out of storage."""
from io import BytesIO
from fractions import Fraction
import math
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

import numpy as np
from pydicom import dcmread
from pydicom.filewriter import dcmwrite

from dicom_input import (DicomInvalidValueError, DicomMissingTagError, DicomReadError,
                         DicomUnsupportedError, display_values, read_dicom, storage_metadata)
from test_dicom_input import sample


def edited_dicom(change):
    ds = dcmread(BytesIO(sample("ct_phantom.dcm")))
    change(ds)
    stream = BytesIO()
    # Preserve deliberate header inconsistencies; enforce_file_format would repair them.
    dcmwrite(stream, ds, enforce_file_format=False)
    return stream.getvalue()


class ReaderReviewRegressions(unittest.TestCase):
    def test_storage_decoder_without_C_runtime_does_not_build_display(self):
        # C owns these two value types; this B-only check supplies just that boundary.
        patient_type, metadata_type = Mock(), Mock()
        modules = {"patient_study_identity": SimpleNamespace(PatientIdentity=patient_type),
                   "dicom_instance_store": SimpleNamespace(DicomMetadata=metadata_type)}
        data = sample("ct_phantom.dcm")
        expected = read_dicom("ct.dcm", data)
        with patch.dict("sys.modules", modules):
            with patch("dicom_input.display_values", side_effect=AssertionError("unnecessary display")):
                result = storage_metadata(data)
        self.assertIs(result, metadata_type.return_value)
        patient_type.assert_called_once_with(expected.patient_issuer, expected.patient_id)
        metadata_type.assert_called_once_with(patient_type.return_value, expected.study_uid,
                                              expected.series_uid, expected.sop_uid, expected.sop_class_uid,
                                              expected.frame_count, expected.modality, expected.body_region)

    def test_unsupported_voi_functions_are_not_treated_as_linear(self):
        for function in ("SIGMOID", "LINEAR_EXACT", "UNKNOWN"):
            with self.subTest(function=function):
                data = edited_dicom(lambda ds: setattr(ds, "VOILUTFunction", function))
                with self.assertRaises(DicomUnsupportedError):
                    read_dicom("voi.dcm", data)

    def test_multivalue_window_uses_the_first_pair(self):
        def change(ds):
            ds.WindowCenter, ds.WindowWidth = [40, 80], [400, 200]
        imported = read_dicom("windows.dcm", edited_dicom(change))
        self.assertEqual((imported.display.center, imported.display.width), (40, 400))
        self.assertEqual(imported.display_image.tobytes(), read_dicom("base.dcm", sample("ct_phantom.dcm")).display_image.tobytes())

    def test_file_meta_instance_uid_mismatch_is_rejected(self):
        data = edited_dicom(lambda ds: setattr(ds.file_meta, "MediaStorageSOPInstanceUID", "1.2.3.99"))
        ds = dcmread(BytesIO(data))
        self.assertNotEqual(ds.file_meta.MediaStorageSOPInstanceUID, ds.SOPInstanceUID)
        with self.assertRaises(DicomInvalidValueError):
            read_dicom("instance.dcm", data)

    def test_file_meta_class_uid_mismatch_is_rejected(self):
        data = edited_dicom(lambda ds: setattr(ds.file_meta, "MediaStorageSOPClassUID", "1.2.3.99"))
        ds = dcmread(BytesIO(data))
        self.assertNotEqual(ds.file_meta.MediaStorageSOPClassUID, ds.SOPClassUID)
        with self.assertRaises(DicomInvalidValueError):
            read_dicom("class.dcm", data)

    def test_missing_header_instance_uid_is_rejected(self):
        data = edited_dicom(lambda ds: delattr(ds.file_meta, "MediaStorageSOPInstanceUID"))
        self.assertNotIn("MediaStorageSOPInstanceUID", dcmread(BytesIO(data)).file_meta)
        with self.assertRaises(DicomMissingTagError):
            read_dicom("missing-id.dcm", data)

    def test_invalid_width_and_unpaired_window_are_rejected(self):
        for width in (0, 0.5, -1):
            with self.subTest(width=width):
                data = edited_dicom(lambda ds: setattr(ds, "WindowWidth", width))
                with self.assertRaises(DicomInvalidValueError):
                    read_dicom("width.dcm", data)
        for keyword in ("WindowCenter", "WindowWidth"):
            with self.subTest(missing=keyword):
                data = edited_dicom(lambda ds: delattr(ds, keyword))
                with self.assertRaises(DicomInvalidValueError):
                    read_dicom("unpaired.dcm", data)

    def test_width_one_is_a_threshold_without_division_by_zero(self):
        with np.errstate(all="raise"):
            pixels, mapping = display_values(np.array([[38.0, 39.5, 40.0, 41.0]]), 40, 1, False)
            inverted, _ = display_values(np.array([[38.0, 39.5, 40.0, 41.0]]), 40, 1, True)
        self.assertEqual(pixels.tolist(), [[0, 0, 255, 255]])
        self.assertEqual(inverted.tolist(), [[255, 255, 0, 0]])
        self.assertEqual((mapping.method, mapping.width), ("dicom_window", 1))

    def test_width_one_matches_exact_threshold_at_float_boundaries(self):
        largest = float.fromhex("0x1.fffffffffffffp+1023")
        centers = [0.0, 0.1, -0.1, 0.3, -0.3, 1.0, -1.0,
                   float(2**53), -float(2**53), float(2**100), -float(2**100),
                   largest, -largest, math.ulp(0.0), -math.ulp(0.0)]
        for center in centers:
            threshold = center - 0.5
            values = [0.0, center, threshold, math.nextafter(threshold, -math.inf),
                      math.nextafter(threshold, math.inf), largest, -largest]
            values = [v for v in values if math.isfinite(v)]
            exact = Fraction.from_float(center) - Fraction(1, 2)
            expected = [0 if Fraction.from_float(v) <= exact else 255 for v in values]
            with self.subTest(center=center), np.errstate(all="raise"):
                actual, _ = display_values(np.array([values]), center, 1, False)
                inverted, _ = display_values(np.array([values]), center, 1, True)
                self.assertEqual(actual.tolist(), [expected])
                self.assertEqual(inverted.tolist(), [[255 - v for v in expected]])

    def test_linear_window_at_large_centers_matches_exact_rational_mapping(self):
        for center in (float(2**53), -float(2**53)):
            for width in (2, 3, 400):
                values = [center + offset for offset in (-4, -2, 0, 2, 4)]
                expected = []
                for value in values:
                    scaled = ((Fraction.from_float(value) - Fraction.from_float(center) + Fraction(1, 2))
                              / (width - 1) + Fraction(1, 2))
                    expected.append(round(min(max(scaled, 0), 1) * 255))
                with self.subTest(center=center, width=width), np.errstate(all="raise"):
                    mapped, _ = display_values(np.array([values]), center, width, False)
                    self.assertEqual(mapped.tolist(), [expected])

    def test_large_center_dicom_does_not_turn_center_pixels_black(self):
        def change(ds):
            ds.RescaleSlope = 1
            ds.RescaleIntercept = "9007199254740992"
            ds.WindowCenter = "9007199254740992"
            ds.WindowWidth = 1
        data = edited_dicom(change)
        imported = read_dicom("large-center.dcm", data)
        self.assertEqual(imported.display_image.getextrema(), ((255, 255),) * 3)
        self.assertEqual(imported.original_bytes, data)

    def test_absent_window_keeps_min_max_mapping(self):
        with np.errstate(all="raise"):
            pixels, mapping = display_values(np.array([[0.0, 5.0, 10.0]]), None, None, False)
        self.assertEqual(pixels.tolist(), [[0, 128, 255]])
        self.assertEqual(mapping.method, "min_max")

    def test_nonfinite_rescale_result_is_rejected(self):
        def change(ds):
            ds.RescaleSlope = "1e308"
            del ds.WindowCenter
            del ds.WindowWidth
        with np.errstate(all="raise"):
            with self.assertRaises(DicomInvalidValueError):
                read_dicom("overflow.dcm", edited_dicom(change))

    def test_nonfinite_display_values_are_rejected(self):
        for value in (np.nan, np.inf, -np.inf):
            with self.subTest(value=value):
                with self.assertRaises(DicomInvalidValueError):
                    display_values(np.array([[value]]), None, None, False)

    def test_direct_display_rejects_nonfinite_or_unpaired_window(self):
        for center, width in ((np.nan, 1), (0, np.inf), (None, 2), (2, None)):
            with self.subTest(center=center, width=width):
                with self.assertRaises(DicomInvalidValueError):
                    display_values(np.array([[0.0, 1.0]]), center, width, False)

    def test_study_date_requires_a_real_calendar_day(self):
        for value, expected in (("20240229", "2024-02-29"), ("20230229", ""), ("20260231", ""), ("", "")):
            with self.subTest(value=value):
                imported = read_dicom("date.dcm", edited_dicom(lambda ds: setattr(ds, "StudyDate", value)))
                self.assertEqual(imported.study_date, expected)


try:
    from dicom_instance_store import DicomMetadata
    C_AVAILABLE = True
except ModuleNotFoundError:
    C_AVAILABLE = False


@unittest.skipUnless(C_AVAILABLE, "C storage modules are not present on this branch")
class MetadataReviewRegressions(unittest.TestCase):
    def test_metadata_inspection_does_not_build_a_display(self):
        with patch("dicom_input.display_values", side_effect=AssertionError("unnecessary display")):
            metadata = storage_metadata(sample("ct_phantom.dcm"))
        self.assertIsInstance(metadata, DicomMetadata)
        self.assertEqual((metadata.modality, metadata.body_region), ("CT", "HEAD"))

    def test_metadata_inspection_keeps_the_reader_validation(self):
        cases = [edited_dicom(lambda ds: setattr(ds, "VOILUTFunction", "SIGMOID")),
                 edited_dicom(lambda ds: setattr(ds.file_meta, "MediaStorageSOPInstanceUID", "1.2.3.99")),
                 edited_dicom(lambda ds: setattr(ds, "WindowWidth", 0)),
                 sample("truncated.dcm"), sample("missing_issuer.dcm")]
        for number, data in enumerate(cases):
            with self.subTest(case=number):
                with self.assertRaises(DicomReadError):
                    storage_metadata(data)

    def test_metadata_rejects_empty_present_window_settings(self):
        for field in ("WindowCenter", "WindowWidth"):
            with self.subTest(field=field):
                data = edited_dicom(lambda ds: setattr(ds, field, ""))
                with self.assertRaises(DicomInvalidValueError):
                    storage_metadata(data)



if __name__ == "__main__":
    unittest.main()
