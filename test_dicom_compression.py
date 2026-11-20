"""Synthetic compressed DICOM only; no external patient data or network downloads."""

from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import numpy as np
from pydicom import dcmread
from pydicom.encaps import encapsulate, generate_frames
from pydicom.filewriter import dcmwrite
from pydicom.uid import JPEG2000Lossless, JPEG2000, RLELossless

import dicom_input as reader
import test_dicom_input as samples
import test_patient_study_integration as integration

SYNTAXES = (JPEG2000Lossless, JPEG2000, RLELossless)


def compressed_sample(syntax, name="ct_phantom.dcm", signed=False):
    ds = dcmread(BytesIO(samples.sample(name)))
    expected = ds.pixel_array.copy()
    if signed:
        expected = (expected.astype(np.int16) - 1024).astype(np.int16)
        ds.PixelRepresentation = 1
        ds.PixelData = expected.tobytes()
    options = {"j2k_cr": [8]} if syntax == JPEG2000 else {}
    ds.compress(syntax, encoding_plugin="pydicom" if syntax == RLELossless else "pylibjpeg",
                generate_instance_uid=False, **options)
    stream = BytesIO()
    dcmwrite(stream, ds, enforce_file_format=True)
    return stream.getvalue(), expected


class CompressedDicomTests(unittest.TestCase):
    def test_lossless_pixels_and_original_bytes_are_preserved(self):
        for syntax in (JPEG2000Lossless, RLELossless):
            for name in ("ct_phantom.dcm", "mr_phantom.dcm", "cr_monochrome1.dcm"):
                with self.subTest(syntax=str(syntax), name=name):
                    data, expected = compressed_sample(syntax, name)
                    imported = reader.read_dicom("compressed.dcm", data)
                    np.testing.assert_array_equal(imported.stored_pixels, expected)
                    self.assertFalse(imported.stored_pixels.flags.writeable)
                    self.assertIs(imported.original_bytes, data)
                    self.assertEqual(imported.source_id, sha256(data).hexdigest())
                    self.assertEqual(imported.transfer_syntax_uid, str(syntax))
                    native = reader.read_dicom(name, samples.sample(name))
                    self.assertEqual(imported.display_image.tobytes(), native.display_image.tobytes())

    def test_signed_lossless_pixels_are_preserved(self):
        for syntax in (JPEG2000Lossless, RLELossless):
            data, expected = compressed_sample(syntax, signed=True)
            imported = reader.read_dicom("signed.dcm", data)
            np.testing.assert_array_equal(imported.stored_pixels, expected)
            self.assertEqual(imported.pixel_representation, 1)

    def test_lossy_jpeg2000_decodes_without_claiming_identical_pixels(self):
        data, original = compressed_sample(JPEG2000)
        imported = reader.read_dicom("lossy.dcm", data)
        self.assertEqual(imported.stored_pixels.shape, original.shape)
        self.assertEqual(imported.display_image.mode, "RGB")
        self.assertFalse(np.array_equal(imported.stored_pixels, original))
        self.assertEqual(imported.original_bytes, data)

    def test_jpeg2000_header_mismatch_is_rejected_before_pixel_allocation(self):
        data, _ = compressed_sample(JPEG2000Lossless)
        for field, value in (("Rows", 128), ("Columns", 128), ("BitsStored", 16), ("PixelRepresentation", 1)):
            with self.subTest(field=field):
                changed = samples.rewrite(data, lambda ds: setattr(ds, field, value))
                with patch("dicom_input.pixel_array") as decode:
                    with self.assertRaises(reader.DicomInvalidValueError):
                        reader.read_dicom("mismatch.dcm", changed)
                    decode.assert_not_called()

    def test_missing_plugin_is_reported_as_unsupported(self):
        data, _ = compressed_sample(JPEG2000Lossless)
        with patch("dicom_input.get_decoder", return_value=NS(available_plugins=())):
            with self.assertRaises(reader.DicomUnsupportedError):
                reader.read_dicom("codec-missing.dcm", data)

    def test_corrupt_compressed_payload_has_no_display_result(self):
        for syntax in SYNTAXES:
            data, _ = compressed_sample(syntax)
            broken = samples.rewrite(data, lambda ds: setattr(ds, "PixelData", encapsulate([b"broken-frame"])))
            with self.assertRaises(reader.DicomCorruptError):
                reader.read_dicom("broken.dcm", broken)

    def test_extra_encoded_frame_is_rejected_before_decoding(self):
        data, _ = compressed_sample(JPEG2000Lossless)
        def add_frame(ds):
            frame = next(generate_frames(ds.PixelData, number_of_frames=1))
            ds.PixelData = encapsulate([frame, frame])
            ds.NumberOfFrames = 1
        broken = samples.rewrite(data, add_frame)
        with patch("dicom_input.pixel_array") as decode:
            with self.assertRaises(reader.DicomUnsupportedError):
                reader.read_dicom("extra-frame.dcm", broken)
            decode.assert_not_called()

    def test_missing_issuer_and_multiframe_remain_rejected(self):
        data, _ = compressed_sample(JPEG2000Lossless)
        missing = samples.rewrite(data, lambda ds: ds.__delattr__("IssuerOfPatientID"))
        with self.assertRaises(reader.DicomMissingTagError):
            reader.read_dicom("no-issuer.dcm", missing)
        multi = samples.rewrite(data, lambda ds: setattr(ds, "NumberOfFrames", 2))
        with self.assertRaises(reader.DicomUnsupportedError):
            reader.read_dicom("multi.dcm", multi)

    def test_actual_app_registers_reopens_and_retries_compressed_originals(self):
        for syntax in SYNTAXES:
            with self.subTest(syntax=str(syntax)):
                fixture = integration.PatientStudyIntegrationTests()
                fixture.setUp()
                try:
                    app, imported, _ = fixture.create(fixture.app())
                    data, _ = compressed_sample(syntax)
                    fixture.cloud.deny_instances = True
                    fixture.register(app, imported, [("compressed.dcm", data)])
                    result = app.session_state["patient_ui_registration_results"][0][2]
                    self.assertEqual(result.status, "saved_not_linked")
                    fixture.cloud.deny_instances = False
                    app.button(key="patient_ui_pending_reload").click().run()
                    app.button(key="patient_ui_retry_0").click().run()
                    self.assertTrue(app.session_state["patient_ui_retry_result"][2].completed)
                    fresh = fixture.app()
                    fixture.button(fresh, "patient_ui_patients_choose_", imported.patient_id)
                    fixture.button(fresh, "patient_ui_studies_choose_", imported.study_uid)
                    fixture.open(fresh, imported)
                    source = fresh.session_state["viewer_active_source"]
                    self.assertEqual(source.original_bytes, data)
                    self.assertEqual(source.source_id, sha256(data).hexdigest())
                    fresh.button(key="viewer_processing_apply").click().run()
                    next(b for b in fresh.button if b.label == "ROI 적용").click().run()
                    self.assertFalse(fresh.exception)
                    self.assertEqual(fresh.session_state["viewer_roi_statistics"]["source_id"], source.source_id)
                finally:
                    fixture.doCleanups()


if __name__ == "__main__":
    unittest.main()
