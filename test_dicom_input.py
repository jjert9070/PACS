"""Issue #23: DICOM reading, original preservation, display mapping, errors and wiring."""

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image
from pydicom import dcmread
from pydicom.filewriter import dcmwrite
from streamlit.testing.v1 import AppTest

from dicom_input import (
    DicomCorruptError,
    DicomInvalidValueError,
    DicomMissingTagError,
    DicomReadError,
    DicomUnsupportedError,
    display_values,
    load_dicom_source,
    read_dicom,
    storage_metadata,
)
from experiments import dicom_samples
from viewer import MAX_FILE_BYTES

SAMPLES = Path(__file__).resolve().parent / "samples" / "dicom"


def sample(name: str) -> bytes:
    return (SAMPLES / name).read_bytes()


def rewrite(data: bytes, change) -> bytes:
    """Re-encode a sample after editing its dataset (used for one-off negative cases)."""
    ds = dcmread(BytesIO(data))
    change(ds)
    stream = BytesIO()
    dcmwrite(stream, ds, enforce_file_format=True)
    return stream.getvalue()


class SampleTests(unittest.TestCase):
    def test_committed_samples_match_manifest_and_generator(self):
        manifest = json.loads((SAMPLES / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["patient_data"], "none (synthetic)")
        with tempfile.TemporaryDirectory() as folder:
            regenerated = dicom_samples.write_samples(Path(folder))
            for name, entry in manifest["files"].items():
                with self.subTest(name=name):
                    data = sample(name)
                    self.assertEqual(sha256(data).hexdigest(), entry["sha256"])
                    self.assertEqual((Path(folder) / name).read_bytes(), data)
            self.assertEqual(regenerated["files"], manifest["files"])

    def test_every_sample_has_the_expected_outcome(self):
        manifest = json.loads((SAMPLES / "manifest.json").read_text(encoding="utf-8"))
        for name, entry in manifest["files"].items():
            with self.subTest(name=name):
                if entry["expected"] == "supported":
                    self.assertEqual(read_dicom(name, sample(name)).frame_count, 1)
                else:
                    with self.assertRaises(DicomReadError) as caught:
                        read_dicom(name, sample(name))
                    self.assertEqual(caught.exception.code, entry["expected"])


class ReadTests(unittest.TestCase):
    def test_ct_identifiers_stored_values_and_window(self):
        data = sample("ct_phantom.dcm")
        result = read_dicom("ct_phantom.dcm", data)
        self.assertEqual((result.modality, result.body_region, result.study_date), ("CT", "HEAD", "2026-11-19"))
        self.assertEqual((result.patient_issuer, result.patient_id), ("PACS-EDU-SYNTH", "SYN-0001"))
        self.assertEqual(result.study_uid, dicom_samples.uid("study-ct"))
        self.assertEqual(result.series_uid, dicom_samples.uid("series-ct"))
        self.assertEqual(result.sop_uid, dicom_samples.uid("sop-ct"))
        self.assertEqual(result.sop_class_uid, "1.2.840.10008.5.1.4.1.1.2")
        self.assertEqual((result.rows, result.columns, result.frame_count), (256, 256, 1))
        # Stored values keep their original dtype and numbers (HU = stored - 1024).
        self.assertEqual(result.stored_pixels.dtype, np.uint16)
        self.assertTrue(np.array_equal(result.stored_pixels, dicom_samples.ct_phantom_array()))
        self.assertFalse(result.stored_pixels.flags.writeable)
        self.assertEqual((result.rescale_slope, result.rescale_intercept), (1.0, -1024.0))
        # VOI LUT LINEAR, center 40 width 400: air 0, water ((0-39.5)/399+0.5)*255 = 102.25 -> 102, bone 255.
        self.assertEqual((result.display.method, result.display.center, result.display.width),
                         ("dicom_window", 40.0, 400.0))
        image = result.display_image
        self.assertEqual((image.mode, image.size), ("RGB", (256, 256)))
        self.assertEqual(image.getpixel((0, 0)), (0, 0, 0))
        self.assertEqual(image.getpixel((128, 128)), (102, 102, 102))
        self.assertEqual(image.getpixel((128, 160)), (255, 255, 255))

    def test_mr_non_square_orientation_and_min_max(self):
        result = read_dicom("mr.dcm", sample("mr_phantom.dcm"))
        self.assertEqual(result.transfer_syntax_uid, "1.2.840.10008.1.2")  # implicit VR
        self.assertEqual((result.rows, result.columns), (160, 192))
        self.assertEqual(result.display_image.size, (192, 160))           # PIL size is (width, height)
        self.assertEqual(result.display.method, "min_max")
        self.assertEqual(result.display_image.getpixel((0, 0)), (255, 255, 255))   # marker 4095 = max
        self.assertEqual(result.display_image.getpixel((191, 159))[0],
                         round((1700 - 200) / (4095 - 200) * 255))               # right-edge gradient

    def test_monochrome1_is_inverted_for_display(self):
        result = read_dicom("cr.dcm", sample("cr_monochrome1.dcm"))
        self.assertTrue(result.display.inverted)
        self.assertEqual(result.display_image.getpixel((0, 0)), (255, 255, 255))     # stored 0
        self.assertEqual(result.display_image.getpixel((127, 0)), (0, 0, 0))         # stored 254
        self.assertEqual((result.patient_id, result.modality), ("SYN-0002", "CR"))

    def test_original_bytes_and_hash_are_unchanged(self):
        for name in ("ct_phantom.dcm", "mr_phantom.dcm", "cr_monochrome1.dcm"):
            with self.subTest(name=name):
                data = sample(name)
                before = sha256(data).hexdigest()
                result = read_dicom(name, data)
                self.assertEqual(sha256(data).hexdigest(), before)
                self.assertEqual(result.source_id, before)
                self.assertIs(result.original_bytes, data)
                self.assertEqual(result.byte_count, len(data))
                self.assertEqual((SAMPLES / name).read_bytes(), data)

    def test_viewer_source_contract(self):
        data = sample("ct_phantom.dcm")
        source = load_dicom_source("ct_phantom.dcm", data)
        self.assertEqual((source.file_format, source.name, source.byte_count), ("DICOM", "ct_phantom.dcm", len(data)))
        self.assertEqual(source.source_id, sha256(data).hexdigest())
        self.assertIs(source.original_bytes, data)
        self.assertEqual((source.image.mode, source.image.size), ("RGB", (256, 256)))

    def test_provenance_records_mapping_without_patient_ids(self):
        record = read_dicom("ct.dcm", sample("ct_phantom.dcm")).provenance()
        json.dumps(record, allow_nan=False)
        self.assertEqual(record["display_mapping"]["method"], "dicom_window")
        self.assertEqual(record["pixel"]["bits_stored"], 12)
        text = json.dumps(record)
        self.assertNotIn("SYN-0001", text)
        self.assertNotIn("Synthetic", text)


class DisplayMappingTests(unittest.TestCase):
    def test_linear_window_boundaries(self):
        values = np.array([[-161.0, -160.0, 39.5, 239.0, 240.0]])
        mapped, mapping = display_values(values, 40, 400, False)
        # low edge c-0.5-(w-1)/2 = -160 -> 0; high edge 239 is the last scaled value.
        self.assertEqual(mapped.tolist(), [[0, 0, 128, 255, 255]])
        self.assertEqual(mapping.method, "dicom_window")

    def test_flat_image_without_window_is_black_not_error(self):
        mapped, mapping = display_values(np.full((2, 2), 7.0), None, None, False)
        self.assertEqual(mapped.tolist(), [[0, 0], [0, 0]])
        self.assertEqual((mapping.method, mapping.width), ("min_max", 0.0))

    def test_width_below_one_falls_back_to_min_max(self):
        _, mapping = display_values(np.array([[0.0, 10.0]]), 5, 0.5, False)
        self.assertEqual(mapping.method, "min_max")


class ErrorTests(unittest.TestCase):
    def assertRejected(self, data, error_type, *phrases):
        with self.assertRaises(error_type) as caught:
            read_dicom("x.dcm", data)
        message = str(caught.exception)
        for phrase in phrases:
            self.assertIn(phrase, message)
        self.assertNotIn("SYN-000", message)            # no patient identifiers in messages
        self.assertNotIn("Synthetic", message)
        return caught.exception

    def test_not_dicom_empty_and_truncated_are_corrupt(self):
        png = BytesIO()
        Image.new("RGB", (4, 4)).save(png, format="PNG")
        self.assertRejected(png.getvalue(), DicomCorruptError, "DICM")
        self.assertRejected(b"", DicomCorruptError)
        self.assertRejected(b"\0" * 128 + b"DICM" + b"\x02\x00", DicomCorruptError)
        self.assertRejected(sample("truncated.dcm"), DicomCorruptError, "픽셀 데이터")

    def test_unsupported_inputs_name_the_reason(self):
        self.assertRejected(sample("unsupported_jpeg_baseline.dcm"), DicomUnsupportedError, "1.2.840.10008.1.2.4.50")
        self.assertRejected(sample("unsupported_multiframe.dcm"), DicomUnsupportedError, "3개")
        self.assertRejected(sample("unsupported_rgb.dcm"), DicomUnsupportedError, "RGB")
        self.assertRejected(dicom_samples.presentation_state(), DicomUnsupportedError, "SOP Class")
        self.assertRejected(b"\0" * (MAX_FILE_BYTES + 1), DicomUnsupportedError, "20 MB")
        bits = rewrite(sample("ct_phantom.dcm"), lambda ds: setattr(ds, "BitsAllocated", 32))
        self.assertRejected(bits, DicomUnsupportedError, "32")

    def test_missing_required_information(self):
        self.assertRejected(sample("missing_issuer.dcm"), DicomMissingTagError, "IssuerOfPatientID")
        for keyword in ("PatientID", "StudyInstanceUID", "Rows", "PixelData"):
            with self.subTest(keyword=keyword):
                self.assertRejected(dicom_samples.without(keyword), DicomMissingTagError, keyword)

    def test_invalid_values(self):
        bad_uid = rewrite(sample("ct_phantom.dcm"), lambda ds: setattr(ds, "SeriesInstanceUID", "1.02.3"))
        self.assertRejected(bad_uid, DicomInvalidValueError, "SeriesInstanceUID")
        same = rewrite(sample("ct_phantom.dcm"), lambda ds: setattr(ds, "SeriesInstanceUID", ds.StudyInstanceUID))
        self.assertRejected(same, DicomInvalidValueError, "서로 달라야")
        slope = rewrite(sample("ct_phantom.dcm"), lambda ds: setattr(ds, "RescaleSlope", 0))
        self.assertRejected(slope, DicomInvalidValueError, "RescaleSlope")

    def test_type_error_for_non_bytes(self):
        with self.assertRaises(TypeError):
            read_dicom("x.dcm", bytearray(sample("ct_phantom.dcm")))


class StorageHandoffTests(unittest.TestCase):
    def test_metadata_for_c_decoder(self):
        try:
            from dicom_instance_store import DicomMetadata
        except ModuleNotFoundError:
            self.skipTest("C storage modules are on feature/C-patient-study-storage")
        metadata = storage_metadata(sample("ct_phantom.dcm"))
        self.assertIsInstance(metadata, DicomMetadata)
        self.assertEqual((metadata.patient.issuer, metadata.patient.patient_id), ("PACS-EDU-SYNTH", "SYN-0001"))
        self.assertEqual((metadata.frame_count, metadata.modality, metadata.body_region), (1, "CT", "HEAD"))
        with self.assertRaises(DicomReadError):
            storage_metadata(sample("unsupported_multiframe.dcm"))


SCRIPT = '''import streamlit as st
import viewer, dicom_input
from gaussian_ui import process_gaussian, render_gaussian_controls
from roi_ui import render_roi_controls, render_roi_statistics
original_load = viewer.load_image
# Test-only stand-in for the .dcm branch that A adds to the viewer (#24).
viewer.load_image = lambda name, data: (dicom_input.load_dicom_source(name, data)
                                        if name.lower().endswith(".dcm") else original_load(name, data))
try:
    viewer.render_viewer(processor=process_gaussian, processing_controls=render_gaussian_controls,
                         roi_controls=render_roi_controls, roi_summary=render_roi_statistics,
                         stored_source=st.session_state.get("test_stored_source"))
finally:
    viewer.load_image = original_load
'''


class ViewerWiringTests(unittest.TestCase):
    def open(self, name):
        app = AppTest.from_string(SCRIPT, default_timeout=20)
        source = load_dicom_source(name, sample(name))
        app.session_state["test_stored_source"] = source
        app.run()
        app.radio(key="viewer_mode").set_value("로컬 저장 영상").run()
        return app, source

    def test_processing_and_roi_reference_the_dicom_original(self):
        app, source = self.open("ct_phantom.dcm")
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        self.assertTrue(any("DICOM · 256 × 256 px" in item.value for item in app.caption))
        next(button for button in app.button if button.label == "ROI 적용").click().run()
        for label, key, value in (("가우시안", "radius", 2.0), ("감마 보정", "gamma", 0.5), ("임계값 분할", "threshold", 128)):
            with self.subTest(algorithm=label):
                app.selectbox(key="viewer_processing_algorithm").set_value(label).run()
                app.number_input(key="viewer_processing_" + key).set_value(value).run()
                app.button(key="viewer_processing_apply").click().run()
                self.assertFalse(app.error)
                applied = app.session_state["viewer_processing_applied"]
                self.assertEqual(applied["source_id"], source.source_id)
                self.assertEqual(app.session_state["viewer_processing_result"].size, (256, 256))
                statistics = app.session_state["viewer_roi_statistics"]
                self.assertEqual(statistics["source_id"], source.source_id)
                self.assertEqual(statistics["original"]["pixel_count"], 256 * 256)
        self.assertTrue(any("CT HU" in item.value for item in app.caption))
        self.assertEqual(sha256(source.original_bytes).hexdigest(), source.source_id)

    def test_unsupported_dicom_is_an_error_not_an_image(self):
        app = AppTest.from_string(SCRIPT, default_timeout=20)
        source = load_dicom_source("ct_phantom.dcm", sample("ct_phantom.dcm"))
        broken = type(source)(source.source_id, "multi.dcm", "DICOM", source.byte_count, source.image,
                              original_bytes=sample("unsupported_multiframe.dcm"))
        app.session_state["test_stored_source"] = broken
        app.run()
        with self.assertRaises(DicomUnsupportedError):
            load_dicom_source("multi.dcm", sample("unsupported_multiframe.dcm"))
        app.radio(key="viewer_mode").set_value("로컬 저장 영상").run()
        self.assertFalse(app.exception)
        self.assertTrue(any("단일 프레임만" in item.value for item in app.error))
        self.assertNotIn("viewer_processing_result", app.session_state)
        self.assertFalse(any("DICOM ·" in item.value for item in app.caption))

    def test_dicom_errors_use_the_viewer_input_error_path(self):
        from viewer import ViewerInputError
        with self.assertRaises(ViewerInputError):
            load_dicom_source("t.dcm", sample("truncated.dcm"))
        self.assertTrue(issubclass(DicomReadError, ValueError))


if __name__ == "__main__":
    unittest.main()
