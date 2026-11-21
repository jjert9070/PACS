"""B registration / retry / reopen flow over C's real storage functions (needs C's modules).

Firestore is a JSON-file fake shared across processes so a real restart can be
simulated; DICOM bytes, B's decoder and C's storage code are real.
"""

from contextlib import ExitStack, contextmanager
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

try:
    import dicom_registration  # noqa: F401
    from firebase_client import FirebaseRequestError, Session
    from local_storage import LocalStorageError
    from patient_study_identity import PatientIdentity, StudyIdentity
    from series_store import Series
    C_AVAILABLE = True
except ModuleNotFoundError:
    C_AVAILABLE = False

from pydicom import dcmread
from pydicom.filewriter import dcmwrite

from dicom_input import read_dicom
from dicom_storage_flow import (AFTER_SAVE, BEFORE_SAVE, CONFLICT, FAILED, REGISTERED, REJECTED, REUSED,
                                SAVED_NOT_LINKED, DicomOpenError, list_pending, open_dicom_display,
                                register_dicom_file, register_dicom_files, retry_pending, summarize)
from experiments import dicom_samples

SAMPLES = Path(__file__).resolve().parent / "samples" / "dicom"
CT = (SAMPLES / "ct_phantom.dcm").read_bytes()


class JsonFirestore:
    """Minimal document store with C's _request signature, persisted to one JSON file."""

    def __init__(self, path: Path, deny_instances: bool = False):
        self.path, self.deny_instances = path, deny_instances

    def _load(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def __call__(self, path, session, payload=None):
        documents = self._load()
        if payload is None:
            if path not in documents:
                raise FirebaseRequestError(404)
            return deepcopy(documents[path])
        for write in payload["writes"]:
            doc = write["update"]
            if self.deny_instances and "/dicom_instances/" in doc["name"]:
                raise FirebaseRequestError(503)
            if doc["name"] in documents:
                raise FirebaseRequestError(403)
            documents[doc["name"]] = deepcopy(doc)
        self.path.write_text(json.dumps(documents), encoding="utf-8")
        return {}


def ct_identity():
    imported = read_dicom("ct", CT)
    selected = StudyIdentity(imported.study_uid, PatientIdentity(imported.patient_issuer, imported.patient_id))
    return imported, selected


@contextmanager
def c_environment(store: JsonFirestore):
    """The same collaborator patches C's own DICOM tests use, around the real code."""
    imported, selected = ct_identity()
    with ExitStack() as stack:
        for module in ("patient_study_store", "dicom_instance_store"):
            stack.enter_context(patch(module + "._request", side_effect=store))
        stack.enter_context(patch("dicom_registration.get_study", return_value=selected))
        stack.enter_context(patch("dicom_instance_store.get_study", return_value=selected))
        stack.enter_context(patch("dicom_registration.save_series"))
        stack.enter_context(patch("dicom_instance_store.get_series",
                                  return_value=Series(imported.study_uid, imported.series_uid, "CT", "HEAD")))
        yield imported, selected


def session():
    return Session("owner", "test", "token", time.monotonic() + 3600)


def same_sop_other_content() -> bytes:
    ds = dcmread(BytesIO(CT))
    ds.ImageComments = "same SOP Instance UID, different content"
    stream = BytesIO()
    dcmwrite(stream, ds, enforce_file_format=True)
    return stream.getvalue()


def restart_step(root: str, step: str) -> None:
    """Run in a fresh Python process (see RestartTests)."""
    root_path = Path(root)
    store = JsonFirestore(root_path / "firestore.json", deny_instances=(step == "register"))
    with c_environment(store) as (imported, selected):
        if step == "register":
            result = register_dicom_file("p", session(), root_path, selected, imported.series_uid, "ct.dcm", CT)
            print(json.dumps({"status": result.status, "stage": result.stage,
                              "retry_recorded": result.retry_recorded, "original_key": result.original_key}))
        else:
            pending = list_pending("p", session(), root_path)
            retried = retry_pending("p", session(), root_path, imported.sop_uid)
            display = open_dicom_display("p", session(), root_path, selected, imported.series_uid, imported.sop_uid)
            print(json.dumps({"pending": [p.sop_uid for p in pending], "retry": retried.status,
                              "pending_after": len(list_pending("p", session(), root_path)),
                              "source_id": display.source.source_id,
                              "center_pixel": display.source.image.getpixel((128, 128)),
                              "pixels_match": bool((display.imported.stored_pixels
                                                    == dicom_samples.ct_phantom_array()).all())}))


@unittest.skipUnless(C_AVAILABLE, "C storage modules are on feature/C-patient-study-storage")
class RegistrationFlowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = JsonFirestore(self.root / "firestore.json")
        context = c_environment(self.store)
        self.imported, self.selected = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def register(self, name, data):
        return register_dicom_file("p", session(), self.root, self.selected, self.imported.series_uid, name, data)

    def dcm_files(self):
        return sorted(path.name for path in self.root.rglob("*.dcm"))

    def test_multiple_files_report_each_outcome_in_order(self):
        files = [("ct.dcm", CT), ("ct-again.dcm", CT),
                 ("mr.dcm", (SAMPLES / "mr_phantom.dcm").read_bytes()),
                 ("multi.dcm", (SAMPLES / "unsupported_multiframe.dcm").read_bytes()),
                 ("cut.dcm", (SAMPLES / "truncated.dcm").read_bytes()),
                 ("big.dcm", b"\0" * (20 * 1024 * 1024 + 1))]
        results = register_dicom_files("p", session(), self.root, self.selected, self.imported.series_uid, files)
        self.assertEqual([r.file_name for r in results], [name for name, _ in files])
        self.assertEqual([r.status for r in results], [REGISTERED, REUSED, CONFLICT, REJECTED, REJECTED, REJECTED])
        self.assertEqual([r.error_code for r in results[3:]], ["unsupported", "corrupt", "unsupported"])
        self.assertTrue(all(r.stage == BEFORE_SAVE for r in results[2:]))
        self.assertEqual(results[0].instance, results[1].instance)          # DicomInstance reused
        self.assertEqual(results[0].instance.metadata.sop_uid, self.imported.sop_uid)
        self.assertEqual(results[0].original_key, self.imported.source_id + ".dcm")
        counts = summarize(results)
        self.assertEqual((counts["completed"], counts[REJECTED], counts[CONFLICT], counts["total"]), (2, 3, 1, 6))
        self.assertEqual(self.dcm_files(), [self.imported.source_id + ".dcm"])

    def test_same_sop_uid_with_other_content_is_held_without_overwrite(self):
        first = self.register("ct.dcm", CT)
        other = same_sop_other_content()
        self.assertEqual(read_dicom("x", other).sop_uid, self.imported.sop_uid)
        result = self.register("other.dcm", other)
        self.assertEqual((result.status, result.stage), (CONFLICT, BEFORE_SAVE))
        self.assertIn("바뀌지 않았습니다", result.message)
        self.assertEqual(self.dcm_files(), [first.original_key])
        self.assertEqual(next(self.root.rglob(first.original_key)).read_bytes(), CT)

    def test_saved_original_with_unconfirmed_link_then_retry(self):
        self.store.deny_instances = True
        result = self.register("ct.dcm", CT)
        self.assertEqual((result.status, result.stage, result.error_code),
                         (SAVED_NOT_LINKED, AFTER_SAVE, "link_not_confirmed"))
        self.assertFalse(result.completed)
        self.assertTrue(result.retry_recorded)
        self.assertEqual(result.original_key, self.imported.source_id + ".dcm")
        self.assertIn("미완료 목록", result.message)
        self.assertEqual(summarize([result])["completed"], 0)
        self.assertEqual([p.sop_uid for p in list_pending("p", session(), self.root)], [self.imported.sop_uid])
        still_denied = retry_pending("p", session(), self.root, self.imported.sop_uid)
        self.assertEqual((still_denied.status, still_denied.stage), (FAILED, AFTER_SAVE))
        self.store.deny_instances = False
        retried = retry_pending("p", session(), self.root, self.imported.sop_uid)
        self.assertEqual(retried.status, REGISTERED)
        self.assertEqual(retried.original_key, result.original_key)        # same original, same SOP UID
        self.assertFalse(list_pending("p", session(), self.root))

    def test_saved_original_without_retry_record_has_a_different_message(self):
        self.store.deny_instances = True
        with patch("dicom_registration._remember", side_effect=LocalStorageError("record failed")):
            result = self.register("ct.dcm", CT)
        self.assertEqual((result.status, result.retry_recorded), (SAVED_NOT_LINKED, False))
        self.assertIn("같은 파일로 다시 등록", result.message)
        self.assertEqual(self.dcm_files(), [result.original_key])

    def test_failures_before_saving_store_nothing(self):
        with patch("dicom_storage_flow.read_dicom", wraps=read_dicom):
            with patch("dicom_instance_store.get_dicom_instance", side_effect=FirebaseRequestError(403)):
                denied = self.register("ct.dcm", CT)
        self.assertEqual((denied.status, denied.stage, denied.error_code), (FAILED, BEFORE_SAVE, "permission_denied"))
        with patch("dicom_registration.get_study", side_effect=FirebaseRequestError(503)):
            failed = self.register("ct.dcm", CT)
        self.assertEqual((failed.status, failed.stage, failed.error_code), (FAILED, BEFORE_SAVE, "request_failed"))
        other_patient = StudyIdentity(self.selected.study_id, PatientIdentity("PACS-EDU-SYNTH", "SYN-0002"))
        mismatch = register_dicom_file("p", session(), self.root, other_patient, self.imported.series_uid, "ct.dcm", CT)
        self.assertEqual((mismatch.status, mismatch.stage), (CONFLICT, BEFORE_SAVE))
        self.assertEqual(self.dcm_files(), [])

    def test_reopen_decodes_the_stored_original(self):
        registered = self.register("ct.dcm", CT)
        display = open_dicom_display("p", session(), self.root, self.selected, self.imported.series_uid,
                                     self.imported.sop_uid)
        self.assertEqual(display.sop_uid, self.imported.sop_uid)
        self.assertEqual(display.source.source_id, registered.original_key[:-4])
        self.assertEqual(display.source.original_bytes, CT)
        self.assertEqual((display.object_frame_count, display.displayable_frame_count), (1, 1))
        self.assertEqual(display.source.image.getpixel((128, 128)), (102, 102, 102))
        self.assertTrue((display.imported.stored_pixels == dicom_samples.ct_phantom_array()).all())

    def test_reopen_refuses_missing_corrupt_or_mismatched_originals(self):
        registered = self.register("ct.dcm", CT)
        path = next(self.root.rglob(registered.original_key))
        open_args = ("p", session(), self.root, self.selected, self.imported.series_uid, self.imported.sop_uid)
        path.write_bytes(CT[:-10] + b"0123456789")          # same size, different content
        with self.assertRaises(DicomOpenError) as corrupt:
            open_dicom_display(*open_args)
        self.assertEqual(corrupt.exception.code, "original_unavailable")
        path.unlink()
        with self.assertRaises(DicomOpenError) as missing:
            open_dicom_display(*open_args)
        self.assertEqual(missing.exception.code, "original_unavailable")
        path.write_bytes(CT)
        other_patient = StudyIdentity(self.selected.study_id, PatientIdentity("PACS-EDU-SYNTH", "SYN-0002"))
        with self.assertRaises(DicomOpenError) as mismatch:
            open_dicom_display("p", session(), self.root, other_patient, self.imported.series_uid, self.imported.sop_uid)
        self.assertEqual(mismatch.exception.code, "link_mismatch")
        with self.assertRaises(DicomOpenError) as unknown:
            open_dicom_display("p", session(), self.root, self.selected, self.imported.series_uid, "1.2.3.4.5")
        self.assertEqual(unknown.exception.code, "not_found")
        self.assertEqual(open_dicom_display(*open_args).source.original_bytes, CT)


@unittest.skipUnless(C_AVAILABLE, "C storage modules are on feature/C-patient-study-storage")
class RestartTests(unittest.TestCase):
    def run_step(self, root, step):
        code = f"import test_dicom_storage_flow as t; t.restart_step({str(root)!r}, {step!r})"
        completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                   cwd=Path(__file__).resolve().parent, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                                   timeout=120)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout.strip().splitlines()[-1])

    def test_pending_link_survives_restart_then_reopens_and_decodes(self):
        with tempfile.TemporaryDirectory() as folder:
            first = self.run_step(folder, "register")            # process 1: original kept, link denied
            self.assertEqual((first["status"], first["stage"], first["retry_recorded"]),
                             (SAVED_NOT_LINKED, AFTER_SAVE, True))
            second = self.run_step(folder, "resume")             # process 2: fresh interpreter
            imported, _ = ct_identity()
            self.assertEqual(second["pending"], [imported.sop_uid])
            self.assertEqual((second["retry"], second["pending_after"]), (REGISTERED, 0))
            self.assertEqual(second["source_id"] + ".dcm", first["original_key"])
            self.assertEqual(second["center_pixel"], [102, 102, 102])
            self.assertTrue(second["pixels_match"])


if __name__ == "__main__":
    unittest.main()
