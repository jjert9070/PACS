"""Actual A app + B decoder + C storage; only Firestore/auth are simulated.

Run on a B/C integration checkout with A's files applied. A-only skips explicitly.
"""

from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace as NS
from urllib.parse import unquote

from streamlit.testing.v1 import AppTest

from firebase_client import FirebaseRequestError, Session
from patient_study_api import BackendUnavailable, get_api

try:
    import dicom_input
    from patient_study_identity import PatientIdentity
    AVAILABLE = True
except ModuleNotFoundError:
    AVAILABLE = False


class ScreenFirestore:
    """Versioned Firestore document/query boundary, not a substitute B/C backend."""

    def __init__(self):
        self.documents = {}
        self.version = 0
        self.deny_instances = False
        self.fail_queries = False

    def request(self, path, session, payload=None):
        if payload is None:
            if path not in self.documents:
                raise FirebaseRequestError(404)
            return deepcopy(self.documents[path])
        for write in payload["writes"]:
            doc = write["update"]
            if self.deny_instances and "/dicom_instances/" in doc["name"]:
                raise FirebaseRequestError(503)
            old = self.documents.get(doc["name"])
            condition = write["currentDocument"]
            if condition.get("exists") is False and old is not None:
                raise FirebaseRequestError(409)
            if "updateTime" in condition and (old is None or old["updateTime"] != condition["updateTime"]):
                raise FirebaseRequestError(412)
        for write in payload["writes"]:
            doc = deepcopy(write["update"])
            self.version += 1
            doc["updateTime"] = str(self.version)
            self.documents[doc["name"]] = doc
        return {}

    def http(self, request, array_response=False):
        if self.fail_queries:
            raise FirebaseRequestError(503)
        path = unquote(request.full_url.split("/v1/", 1)[1])
        if request.data:
            query = json.loads(request.data)["structuredQuery"]
            collection = path.removesuffix(":runQuery") + "/" + query["from"][0]["collectionId"]
            field = query["where"]["fieldFilter"]
            docs = [v for k, v in sorted(self.documents.items()) if k.rsplit("/", 1)[0] == collection
                    and v["fields"].get(field["field"]["fieldPath"]) == field["value"]]
            after = query.get("startAt", {}).get("values", [{}])[0].get("referenceValue", "")
            docs = [d for d in docs if d["name"] > after][:query["limit"]]
            return [{"document": deepcopy(d)} for d in docs] or [{"readTime": "synthetic"}]
        collection = path.split("?", 1)[0]
        return {"documents": [deepcopy(v) for k, v in sorted(self.documents.items()) if k.rsplit("/", 1)[0] == collection]}


@unittest.skipUnless(AVAILABLE, "Actual B/C modules are required; run on intergrationBC + A changes")
class PatientStudyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.root = Path(self.temp) / "originals"
        self.cloud = ScreenFirestore()
        self.api = get_api()
        for module in ("patient_study_store", "series_store", "study_details_store", "study_image_store", "dicom_instance_store"):
            self.stack.enter_context(patch(module + "._request", side_effect=self.cloud.request))
        self.stack.enter_context(patch("storage_query.request_json", side_effect=self.cloud.http))
        self.stack.enter_context(patch("patient_study_store.request_json", side_effect=self.cloud.http))
        self.stack.enter_context(patch("firebase_client.request_json", side_effect=self.cloud.http))
        self.stack.enter_context(patch("firebase_client.urlopen", side_effect=AssertionError("external network forbidden")))
        self.stack.enter_context(patch.dict(os.environ, {"PACS_FIREBASE_API_KEY": "test", "PACS_FIREBASE_PROJECT_ID": "p",
                                                       "PACS_LOCAL_STORAGE_DIR": str(self.root)}))

    def app(self):
        app = AppTest.from_file("app.py", default_timeout=20)
        app.session_state["pacs_session"] = Session("owner", "synthetic", "token", float("inf"))
        app.run()
        app.checkbox(key="patient_ui_enabled").check().run()
        self.assertFalse(app.exception)
        return app

    def button(self, app, prefix, text):
        button = next(b for b in app.button if (b.key or "").startswith(prefix) and text in b.label)
        button.click().run()
        self.assertFalse(app.exception)
        return app

    def create(self, app, name="ct_phantom.dcm"):
        data = (Path("samples/dicom") / name).read_bytes()
        imported = dicom_input.read_dicom(name, data)
        app.text_input(key="patient_ui_new_issuer").set_value(imported.patient_issuer)
        app.text_input(key="patient_ui_new_patient_id").set_value(imported.patient_id)
        app.button(key="patient_ui_create_patient").click().run()
        self.button(app, "patient_ui_patients_choose_", imported.patient_id)
        app.text_input(key="patient_ui_new_study_id").set_value(imported.study_uid)
        app.button(key="patient_ui_create_study").click().run()
        self.button(app, "patient_ui_studies_choose_", imported.study_uid)
        return app, imported, data

    def register(self, app, imported, files):
        uploads = [NS(name=name, getvalue=lambda data=data: data) for name, data in files]
        with patch("streamlit.file_uploader", side_effect=lambda *a, **kw: uploads if kw.get("key") == "patient_ui_register_files" else None):
            app.run()
            app.text_input(key="patient_ui_register_series").set_value(imported.series_uid)
            app.button(key="patient_ui_register").click().run()
        self.assertFalse(app.exception)
        app.run()

    def open(self, app, imported):
        self.button(app, "patient_ui_series_choose_", imported.series_uid)
        app.button(key="patient_ui_kind_dicom").click().run()
        self.button(app, "patient_ui_objects_choose_", imported.sop_uid)

    def test_actual_screen_create_register_list_open_reopen_process_and_roi(self):
        app, imported, data = self.create(self.app())
        self.register(app, imported, [("ct.dcm", data)])
        self.assertTrue(any("연결 완료 1개" in c.value for c in app.caption))
        self.open(app, imported)
        self.assertFalse(app.error)
        source = app.session_state["viewer_active_source"]
        self.assertEqual(source.original_bytes, data)
        self.assertEqual(source.source_id, sha256(data).hexdigest())
        self.assertEqual(source.image.getpixel((128, 128)), imported.display_image.getpixel((128, 128)))
        self.assertEqual(app.session_state["patient_ui_context"]["study_id"], imported.study_uid)
        app.button(key="viewer_processing_apply").click().run()
        next(b for b in app.button if b.label == "ROI 적용").click().run()
        self.assertEqual(app.session_state["viewer_processing_applied"]["source_id"], source.source_id)
        self.assertEqual(app.session_state["viewer_roi_statistics"]["source_id"], source.source_id)
        app.slider(key="viewer_zoom").set_value(2.0).run()
        app.slider(key="viewer_x").set_value(100).run()
        app.button(key="viewer_rotate").click().run()
        app.toggle(key="viewer_flip_horizontal").set_value(True).run()
        app.slider(key="viewer_brightness").set_value(150).run()
        statistics = app.session_state["viewer_roi_statistics"]
        app.button(key="viewer_reset").click().run()
        self.assertEqual(app.session_state["viewer_roi_statistics"], statistics)
        app.checkbox(key="case_record_enabled").check().run()
        self.assertTrue(app.button(key="case_record_save").disabled)
        # Reopen from fresh app state through the actual C list and B decode paths.
        fresh = self.app()
        self.button(fresh, "patient_ui_patients_choose_", imported.patient_id)
        self.button(fresh, "patient_ui_studies_choose_", imported.study_uid)
        self.open(fresh, imported)
        self.assertEqual(fresh.session_state["viewer_active_source"].original_bytes, data)
        self.assertIsNone(fresh.session_state["viewer_displayed_result"])

    def test_connection_failure_is_incomplete_and_new_app_retries_same_stored_sop(self):
        app, imported, data = self.create(self.app())
        self.cloud.deny_instances = True
        self.register(app, imported, [("ct.dcm", data)])
        self.assertTrue(any("연결 완료 0개" in c.value for c in app.caption))
        self.assertEqual(app.session_state["patient_ui_registration_results"][0][2].status, "saved_not_linked")
        self.assertTrue(list(self.root.rglob("*.dcm")))
        fresh = self.app()
        fresh.button(key="patient_ui_pending_reload").click().run()
        pending = fresh.session_state["patient_ui_pending"]
        self.assertEqual(pending[0][1].sop_uid, imported.sop_uid)
        self.cloud.deny_instances = False
        fresh.button(key="patient_ui_retry_0").click().run()
        result = fresh.session_state["patient_ui_retry_result"][2]
        self.assertTrue(result.completed)
        self.assertEqual(result.sop_uid, imported.sop_uid)
        fresh.button(key="patient_ui_pending_reload").click().run()
        self.assertEqual(fresh.session_state["patient_ui_pending"], [])
        self.button(fresh, "patient_ui_patients_choose_", imported.patient_id)
        self.button(fresh, "patient_ui_studies_choose_", imported.study_uid)
        self.open(fresh, imported)
        self.assertEqual(fresh.session_state["viewer_active_source"].original_bytes, data)

    def test_actual_unsupported_and_corrupt_files_keep_per_file_order_and_count(self):
        app, imported, data = self.create(self.app())
        names = ["ct_phantom.dcm", "unsupported_multiframe.dcm", "truncated.dcm", "missing_issuer.dcm"]
        files = [(name, (Path("samples/dicom") / name).read_bytes()) for name in names]
        self.register(app, imported, files)
        results = [result for _, _, result in app.session_state["patient_ui_registration_results"]]
        self.assertEqual([r.file_name for r in results], names)
        self.assertEqual([r.status for r in results], ["registered", "rejected", "rejected", "rejected"])
        self.assertEqual([r.error_code for r in results[1:]], ["unsupported", "corrupt", "missing_tag"])
        self.assertTrue(any("파일 4개 중 연결 완료 1개" in c.value for c in app.caption))

    def test_actual_other_study_and_same_name_patient_do_not_mix_sources(self):
        app, ct, ct_data = self.create(self.app())
        self.register(app, ct, [("ct.dcm", ct_data)])
        self.open(app, ct)
        # MR shares the synthetic name/patient but belongs to a different study.
        app, mr, mr_data = self.create(app, "mr_phantom.dcm")
        self.assertNotIn("patient_ui_source", app.session_state.filtered_state)
        self.register(app, mr, [("mr.dcm", mr_data)])
        self.open(app, mr)
        self.assertEqual(app.session_state["viewer_identity"], sha256(mr_data).hexdigest())
        self.assertEqual(app.session_state["patient_ui_context"]["study_id"], mr.study_uid)
        self.assertIsNone(app.session_state["viewer_displayed_result"])
        # CR has the same PatientName in B's fixtures, but a different PatientID.
        app, cr, cr_data = self.create(app, "cr_monochrome1.dcm")
        self.assertNotEqual(cr.patient_id, ct.patient_id)
        self.register(app, cr, [("cr.dcm", cr_data)])
        self.open(app, cr)
        self.assertEqual(app.session_state["patient_ui_context"]["patient_id"], cr.patient_id)
        self.assertEqual(app.session_state["viewer_active_source"].original_bytes, cr_data)

    def test_default_upload_and_stored_paths_use_B_without_test_loader_replacement(self):
        app = self.app()
        data = Path("samples/dicom/ct_phantom.dcm").read_bytes()
        upload = NS(name="ct.dcm", getvalue=lambda: data)
        with patch("streamlit.file_uploader", side_effect=lambda *a, **kw: upload if kw.get("key") == "viewer_upload" else []):
            app.run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["viewer_active_source"].file_format, "DICOM")
        original = app.session_state["viewer_active_source"]
        app.session_state["viewer_stored_source"] = original
        app.session_state["viewer_stored_owner"] = "owner"
        app.radio(key="viewer_mode").set_value("로컬 저장 영상").run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["viewer_active_source"].original_bytes, data)
        broken = Path("samples/dicom/unsupported_multiframe.dcm").read_bytes()
        app.session_state["viewer_stored_source"] = type(original)(sha256(broken).hexdigest(), "multi.dcm", "DICOM",
                                     len(broken), original.image, original_bytes=broken)
        app.run()
        self.assertTrue(app.error)
        self.assertIsNone(app.session_state["viewer_active_source"])

    def test_actual_png_registration_reopen_and_missing_original_error(self):
        from viewer import sample_image_bytes
        app, imported, _ = self.create(self.app())
        data = sample_image_bytes()
        app.text_input(key="patient_ui_register_image_id").set_value("png-original").run()
        self.register(app, NS(series_uid="PNG-SERIES"), [("sample.png", data)])
        self.assertTrue(any("연결 완료 1개" in c.value for c in app.caption))
        self.button(app, "patient_ui_series_choose_", "PNG-SERIES")
        app.button(key="patient_ui_kind_sample").click().run()
        self.button(app, "patient_ui_objects_choose_", "png-original")
        self.assertEqual(app.session_state["viewer_active_source"].original_bytes, data)
        self.assertEqual(app.session_state["patient_ui_context"]["study_id"], imported.study_uid)
        self.assertFalse(app.error)
        # Remove only this test's temporary original and use the same screen action.
        next(self.root.rglob("*.png")).unlink()
        self.button(app, "patient_ui_objects_choose_", "png-original")
        self.assertTrue(any("original_unavailable" in e.value for e in app.error))
        self.assertIsNone(app.session_state["viewer_active_source"])
        self.assertNotIn("patient_ui_context", app.session_state.filtered_state)


if __name__ == "__main__":
    unittest.main()
