"""Actual input and temporary persistence checks for InputPersistenceRules.lean."""

from copy import deepcopy
from io import BytesIO
from itertools import product
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

import dicom_input as reader
import dicom_storage_flow as flow
from case_records import CaseConflict, ProcessingRecord
from case_processing import validate_processing
from firebase_client import Session
from patient_study_identity import PatientIdentity, StudyIdentity
from viewer import MAX_FILE_BYTES, ViewerInputError, load_image, sample_image_bytes
import test_case_records as cases
import test_dicom_input as dicoms
import test_patient_study_integration as integration


class ReaderReached(RuntimeError):
    pass


def bit_observations():
    base = dicoms.sample("ct_phantom.dcm")
    for allocated, stored in product((0, 8, 12, 16, 32), (0, 1, 8, 9, 12, 16, 17)):
        def change(ds):
            ds.Rows, ds.Columns = 2, 3
            ds.BitsAllocated, ds.BitsStored = allocated, stored
            ds.HighBit, ds.PixelRepresentation = max(0, stored - 1), 0
            ds.PixelData = np.zeros((2, 3), dtype=np.uint8 if allocated == 8 else np.uint16).tobytes()
        data = dicoms.rewrite(base, change)
        try:
            reader.read_dicom("bits.dcm", data)
        except reader.DicomUnsupportedError:
            accepted = False
        else:
            accepted = True
        yield allocated, stored, accepted


class LeanBoundaryTests(unittest.TestCase):
    def test_file_size_gate_runs_before_decoder(self):
        dicom = dicoms.sample("ct_phantom.dcm")
        png = sample_image_bytes()
        for name, base, load, target, too_large in (
            ("scan.dcm", dicom, reader.read_dicom, "dicom_input.dcmread", reader.DicomUnsupportedError),
            ("image.png", png, load_image, "viewer.Image.open", ViewerInputError),
        ):
            for size in (MAX_FILE_BYTES - 1, MAX_FILE_BYTES, MAX_FILE_BYTES + 1):
                with self.subTest(name=name, size=size):
                    data = base.ljust(size, b"\0")
                    with patch(target, side_effect=ReaderReached) as decode:
                        if size <= MAX_FILE_BYTES:
                            with self.assertRaises(ReaderReached):
                                load(name, data)
                            decode.assert_called_once()
                        else:
                            with self.assertRaises(too_large):
                                load(name, data)
                            decode.assert_not_called()
            with patch(target) as decode:
                with self.assertRaises(ViewerInputError):
                    load(name, b"")
                decode.assert_not_called()

    def test_encoded_bits_and_frames_match_support_gate(self):
        for allocated, stored, observed in bit_observations():
            self.assertEqual(observed, allocated in (8, 16) and 1 <= stored <= allocated)
        base = dicoms.sample("ct_phantom.dcm")
        for frames in (0, 1, 2, 3, 7):
            data = dicoms.rewrite(base, lambda ds: setattr(ds, "NumberOfFrames", frames))
            if frames == 1:
                self.assertEqual(reader.read_dicom("frame.dcm", data).frame_count, 1)
            else:
                with self.assertRaises(reader.DicomUnsupportedError):
                    reader.read_dicom("frame.dcm", data)

    def test_all_short_registration_status_sequences_are_counted(self):
        statuses = (flow.REGISTERED, flow.REUSED, flow.REJECTED, flow.CONFLICT, flow.FAILED, flow.SAVED_NOT_LINKED)
        count = 0
        for length in range(5):
            for sequence in product(statuses, repeat=length):
                results = [flow.DicomFileResult(str(i), status, flow.AFTER_SAVE, "synthetic")
                           for i, status in enumerate(sequence)]
                summary = flow.summarize(results)
                self.assertEqual(summary["total"], length)
                self.assertEqual(summary["completed"], sum(s in statuses[:2] for s in sequence))
                self.assertEqual(sum(summary[s] for s in statuses), length)
                count += 1
        self.assertEqual(count, 1555)

    def test_actual_pending_retry_scope_and_reuse_do_not_overwrite(self):
        fixture = integration.PatientStudyIntegrationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        data = dicoms.sample("ct_phantom.dcm")
        imported = reader.read_dicom("ct.dcm", data)
        session = Session("owner", "synthetic", "token", float("inf"))
        patient = PatientIdentity(imported.patient_issuer, imported.patient_id)
        selected = StudyIdentity(imported.study_uid, patient)
        fixture.api.store.save_patient("p", session, patient)
        fixture.api.store.save_study("p", session, selected)
        fixture.cloud.deny_instances = True
        result = flow.register_dicom_file("p", session, fixture.root, selected, imported.series_uid, "ct.dcm", data)
        self.assertEqual(result.status, flow.SAVED_NOT_LINKED)
        self.assertEqual(len(flow.list_pending("p", session, fixture.root)), 1)
        self.assertEqual(flow.list_pending("other-project", session, fixture.root), [])
        other = Session("other", "synthetic", "token", float("inf"))
        self.assertEqual(flow.list_pending("p", other, fixture.root), [])
        fixture.cloud.deny_instances = False
        self.assertTrue(flow.retry_pending("p", session, fixture.root, imported.sop_uid).completed)
        self.assertEqual(flow.list_pending("p", session, fixture.root), [])
        documents = deepcopy(fixture.cloud.documents)
        for _ in range(5):
            repeated = flow.register_dicom_file("p", session, fixture.root, selected, imported.series_uid, "ct.dcm", data)
            self.assertEqual(repeated.status, flow.REUSED)
        changed = dicoms.rewrite(data, lambda ds: setattr(ds, "StudyDate", "20261120"))
        rejected = flow.register_dicom_file("p", session, fixture.root, selected, imported.series_uid, "changed.dcm", changed)
        self.assertEqual(rejected.status, flow.CONFLICT)
        self.assertEqual(fixture.cloud.documents, documents)
        self.assertEqual(len(list(fixture.root.rglob("*.dcm"))), 1)

    def test_actual_case_operation_retries_are_idempotent(self):
        fixture = cases.CaseRecordTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        first = fixture.save()
        for _ in range(5):
            self.assertEqual(fixture.save(), first)
        for note in ("다른 메모", "another payload", "new content"):
            with self.assertRaises(CaseConflict):
                fixture.save(note=note)
        self.assertEqual(len(fixture.commits), 1)
        self.assertEqual(len(fixture.documents), 2)
        self.assertEqual(fixture.save(), first)

    def test_processing_reference_size_and_mode_gate(self):
        sizes = ((1, 1), (2, 3), (3, 2))
        for source_size, result_size, mode, same_hash in product(sizes, sizes, ("RGB", "L", "RGBA"), (False, True)):
            stream = BytesIO()
            Image.new("RGB", source_size).save(stream, format="PNG")
            source = load_image("source.png", stream.getvalue())
            record = ProcessingRecord(source.source_id if same_hash else "0" * 64,
                                      "synthetic", "1", {}, Image.new(mode, result_size))
            if same_hash and source_size == result_size and mode == "RGB":
                self.assertIs(validate_processing(source, record), record)
            else:
                with self.assertRaises(ValueError):
                    validate_processing(source, record)


if __name__ == "__main__":
    unittest.main()
