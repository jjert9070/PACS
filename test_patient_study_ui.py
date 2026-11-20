"""A's real app entry point with explicit fake B/C contracts; no cloud requests."""

from contextlib import ExitStack
from dataclasses import dataclass
from io import BytesIO
import os
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from PIL import ImageOps
from streamlit.testing.v1 import AppTest

from firebase_client import FirebaseRequestError, Session
from viewer import load_image, sample_image_bytes


@dataclass(frozen=True)
class Patient:
    issuer: str
    patient_id: str

    def __post_init__(self):
        if not self.issuer.strip() or not self.patient_id.strip():
            raise ValueError("missing identity")


@dataclass(frozen=True)
class Study:
    study_id: str
    patient: Patient


@dataclass(frozen=True)
class Series:
    study_id: str
    series_id: str
    modality: str = "CT"
    body_region: str = "HEAD"


@dataclass(frozen=True)
class Details:
    study_id: str
    acquired_on: str = ""
    linkage_status: str = "unconfirmed"
    receipt_status: str = "unknown"


class StudyDetailsConflict(ValueError):
    pass


@dataclass(frozen=True)
class Registration:
    success: bool
    stage: str
    error_code: str = ""
    original_key: str = ""
    retry_image_id: str = ""


def make_api():
    patients = [Patient("issuer-A", "same-ID"), Patient("issuer-B", "same-ID")]
    studies = [Study("1.2.3", patients[0]), Study("1.2.4", patients[0]), Study("1.2.5", patients[1])]
    series = [Series(s.study_id, s.study_id + ".1") for s in studies]
    source = load_image("first.png", sample_image_bytes())
    stream = BytesIO()
    ImageOps.invert(source.image).save(stream, format="PNG")
    other = load_image("second.png", stream.getvalue())
    api = NS(patients=patients, studies=studies, all_series=series, source=source, other=other)
    api.identity = NS(PatientIdentity=Patient, StudyIdentity=Study)
    api.store = NS(
        list_patients=Mock(return_value=(patients, "")),
        list_patient_studies=Mock(side_effect=lambda p, s, patient, token: ([v for v in studies if v.patient == patient], "")),
        save_patient=Mock(), save_study=Mock(),
    )
    api.series = NS(Series=Series, list_study_series=Mock(side_effect=lambda p, s, sid, token: ([v for v in series if v.study_id == sid], "")))
    api.images = NS(list_series_images=Mock(side_effect=lambda p, s, sid, ser, token: ([NS(study_id=sid, series_id=ser, image_id=sid + ".png")], "")))
    api.details = NS(StudyDetails=Details, StudyDetailsConflict=StudyDetailsConflict,
                     get_study_details=Mock(side_effect=lambda p, s, sid: NS(details=Details(sid), update_time="v1")),
                     save_study_details=Mock())
    api.service = NS(open_selected_original=Mock(side_effect=lambda p, s, root, patient, sid, ser, iid: NS(source=source if sid == "1.2.3" else other)),
                     register_sample_original=Mock(return_value=Registration(True, "linked")), RegistrationResult=Registration)
    instance = NS(metadata=NS(sop_uid="1.2.3.1.1", frame_count=1))
    api.dicom = NS(list_series_instances=Mock(return_value=([instance], "")))
    api.flow = NS(open_dicom_display=Mock(return_value=NS(source=source, object_frame_count=1,
                    displayable_frame_count=1, sop_uid=instance.metadata.sop_uid, imported=NS(study_date="2026-11-20"))),
                  register_dicom_file=Mock(), list_pending=Mock(return_value=[]), retry_pending=Mock(),
                  summarize=lambda results: {"completed": sum(r.completed for r in results)})
    api.recovery = NS(list_pending_links=Mock(return_value=[]), retry_pending_link=Mock())
    return api


class PatientStudyScreenTests(unittest.TestCase):
    def setUp(self):
        self.api = make_api()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("patient_study_ui.get_api", return_value=self.api))
        self.stack.enter_context(patch.dict(os.environ, {"PACS_FIREBASE_API_KEY": "test", "PACS_FIREBASE_PROJECT_ID": "p"}))
        self.stack.enter_context(patch("firebase_client.urlopen", side_effect=AssertionError("unexpected external request")))

    def app(self):
        app = AppTest.from_file("app.py", default_timeout=15)
        app.session_state["pacs_session"] = Session("owner", "test", "token", float("inf"))
        app.run()
        app.checkbox(key="patient_ui_enabled").check().run()
        self.assertFalse(app.exception)
        return app

    def selected(self, open_image=True, kind="sample"):
        app = self.app()
        for level in ("patients", "studies", "series"):
            app.button(key="patient_ui_" + level + "_choose_0").click().run()
            self.assertFalse(app.exception)
        if kind == "sample":
            app.button(key="patient_ui_kind_sample").click().run()
        else:
            app.button(key="patient_ui_kind_dicom").click().run()
        if open_image:
            app.button(key="patient_ui_objects_choose_0").click().run()
        self.assertFalse(app.exception)
        return app

    def test_selection_opens_source_and_shows_patient_study_context(self):
        app = self.selected()
        self.assertEqual(app.session_state["viewer_identity"], self.api.source.source_id)
        context = app.session_state["patient_ui_context"]
        self.assertEqual((context["issuer"], context["patient_id"], context["study_id"]), ("issuer-A", "same-ID", "1.2.3"))
        self.assertEqual((context["frame_index"], context["frame_count"]), (0, 1))
        self.assertEqual(context["acquired_on"], "미상")
        self.assertTrue(any("issuer-A" in t.value and "same-ID" in t.value for t in app.text))
        self.api.service.open_selected_original.assert_called_with("p", unittest.mock.ANY, unittest.mock.ANY,
                        self.api.patients[0], "1.2.3", "1.2.3.1", "1.2.3.png")

    def test_same_identifier_different_issuer_is_a_different_patient(self):
        app = self.selected()
        app.button(key="patient_ui_patients_choose_1").click().run()
        self.assertEqual(app.session_state["patient_ui_patient"], self.api.patients[1])
        self.assertNotIn("patient_ui_study", app.session_state.filtered_state)
        self.assertNotIn("patient_ui_source", app.session_state.filtered_state)
        self.assertIsNone(app.session_state.filtered_state.get("viewer_identity"))
        self.api.store.list_patient_studies.assert_called_with("p", unittest.mock.ANY, self.api.patients[1], "")

    def test_different_study_clears_source_result_roi_and_context_before_reopen(self):
        app = self.selected()
        app.button(key="viewer_processing_apply").click().run()
        next(b for b in app.button if b.label == "ROI 적용").click().run()
        app.checkbox(key="case_record_enabled").check().run()
        app.checkbox(key="case_record_discard").check().run()
        app.button(key="patient_ui_studies_choose_1").click().run()
        self.assertFalse(app.exception)
        state = app.session_state.filtered_state
        self.assertEqual(state["patient_ui_study"].study_id, "1.2.4")
        self.assertNotIn("patient_ui_series", state)
        self.assertNotIn("patient_ui_context", state)
        self.assertNotIn("viewer_processing_result", state)
        self.assertNotIn("viewer_roi_statistics", state)
        self.assertIsNone(state.get("viewer_identity"))
        app.button(key="patient_ui_series_choose_0").click().run()
        app.button(key="patient_ui_kind_sample").click().run()
        app.button(key="patient_ui_objects_choose_0").click().run()
        self.assertEqual(app.session_state["viewer_identity"], self.api.other.source_id)
        self.assertEqual(app.session_state["patient_ui_context"]["study_id"], "1.2.4")

    def test_unsaved_note_and_processing_block_patient_transition(self):
        for change in ("note", "processing", "details"):
            with self.subTest(change=change):
                app = self.selected()
                if change == "note":
                    app.checkbox(key="case_record_enabled").check().run()
                    app.text_area(key="case_record_note").set_value("keep my note").run()
                elif change == "processing":
                    app.button(key="viewer_processing_apply").click().run()
                else:
                    app.text_input(key="patient_ui_date").set_value("2026-11-20").run()
                app.button(key="patient_ui_patients_choose_1").click().run()
                self.assertEqual(app.session_state["patient_ui_patient"], self.api.patients[0])
                self.assertEqual(app.session_state["viewer_identity"], self.api.source.source_id)
                self.assertTrue(any("저장하지 않은 변경" in w.value for w in app.warning))
                app.button(key="patient_ui_allow_discard").click().run()
                app.button(key="patient_ui_patients_choose_1").click().run()
                self.assertFalse(app.exception)
                self.assertEqual(app.session_state["patient_ui_patient"], self.api.patients[1])
                self.assertNotIn("patient_ui_source", app.session_state.filtered_state)
                self.assertIsNone(app.session_state.filtered_state.get("viewer_identity"))

    def test_query_failure_is_not_an_empty_result_and_retry_recovers(self):
        self.api.store.list_patients.side_effect = FirebaseRequestError(503)
        app = self.app()
        self.assertTrue(app.error)
        self.assertFalse(any("환자 목록이 없습니다" in i.value for i in app.info))
        self.api.store.list_patients.side_effect = None
        app.button(key="patient_ui_patients_refresh").click().run()
        self.assertFalse(app.error)
        self.assertEqual(len([b for b in app.button if (b.key or "").startswith("patient_ui_patients_choose_")]), 2)
        self.api.store.list_patients.return_value = ([], "")
        app.button(key="patient_ui_patients_refresh").click().run()
        self.assertTrue(any("환자 목록이 없습니다" in i.value for i in app.info))
        self.assertFalse(app.error)

    def test_search_only_filters_loaded_rows_and_zero_is_distinct(self):
        app = self.app()
        app.text_input(key="patient_ui_patients_search").set_value("no-match").run()
        self.assertTrue(any("검색 결과가 없습니다" in i.value for i in app.info))
        self.assertTrue(any("전체 자료 검색이 아닙니다" in c.value for c in app.caption))
        self.assertFalse(app.error)
        self.api.store.list_patients.assert_called_once()

    def test_patient_next_page_and_child_tokens_are_scoped(self):
        self.api.store.list_patients.side_effect = [(self.api.patients[:1], "next-patient"), (self.api.patients[1:], "")]
        app = self.app()
        app.button(key="patient_ui_patients_next").click().run()
        self.api.store.list_patients.assert_called_with("p", unittest.mock.ANY, "next-patient")
        self.api.store.list_patient_studies.return_value = (self.api.studies[:2], "old-study-token")
        self.api.store.list_patient_studies.side_effect = None
        app.button(key="patient_ui_patients_choose_0").click().run()
        app.button(key="patient_ui_studies_choose_0").click().run()
        self.api.store.list_patient_studies.return_value = ([self.api.studies[2]], "")
        app.button(key="patient_ui_patients_choose_1").click().run()
        self.api.store.list_patient_studies.assert_called_with("p", unittest.mock.ANY, self.api.patients[1], "")
        self.assertNotIn("patient_ui_series_page", app.session_state.filtered_state)

    def test_no_images_and_open_failure_have_different_states(self):
        self.api.images.list_series_images.return_value = ([], "")
        self.api.images.list_series_images.side_effect = None
        app = self.selected(open_image=False)
        self.assertTrue(any("연결 영상 목록이 없습니다" in i.value for i in app.info))
        self.assertFalse(app.error)
        self.api.images.list_series_images.return_value = ([NS(image_id="image")], "")
        app.button(key="patient_ui_objects_refresh").click().run()
        self.api.service.open_selected_original.side_effect = FileNotFoundError("private filesystem path")
        app.button(key="patient_ui_objects_choose_0").click().run()
        self.assertTrue(any("original_unavailable" in e.value for e in app.error))
        self.assertFalse(any("private filesystem path" in e.value for e in app.error))
        self.assertIsNone(app.session_state.filtered_state.get("viewer_identity"))
        self.assertNotIn("patient_ui_context", app.session_state.filtered_state)

    def test_failed_reopen_removes_previous_successful_image(self):
        app = self.selected()
        self.api.service.open_selected_original.side_effect = FirebaseRequestError(403)
        app.button(key="patient_ui_objects_choose_0").click().run()
        self.assertIsNone(app.session_state["viewer_active_source"])
        self.assertTrue(any("permission_denied" in e.value for e in app.error))
        self.assertNotIn("patient_ui_source", app.session_state.filtered_state)
        self.assertNotIn("viewer_displayed_result", app.session_state.filtered_state)

    def test_context_reference_mismatch_is_rejected_by_default_viewer(self):
        app = self.selected()
        context = dict(app.session_state["patient_ui_context"], source_id="0" * 64)
        app.session_state["patient_ui_context"] = context
        app.run()
        self.assertTrue(any("원본 참조가 일치하지" in e.value for e in app.error))
        self.assertIsNone(app.session_state["viewer_identity"])

    def test_view_controls_and_reset_preserve_original_and_selected_context(self):
        app = self.selected()
        before = dict(app.session_state["patient_ui_context"])
        app.slider(key="viewer_zoom").set_value(2.0).run()
        app.slider(key="viewer_x").set_value(100).run()
        app.slider(key="viewer_y").set_value(0).run()
        app.button(key="viewer_rotate").click().run()
        app.toggle(key="viewer_flip_horizontal").set_value(True).run()
        app.toggle(key="viewer_flip_vertical").set_value(True).run()
        app.slider(key="viewer_brightness").set_value(150).run()
        app.slider(key="viewer_contrast").set_value(50).run()
        app.button(key="viewer_reset").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["viewer_active_source"].original_bytes, self.api.source.original_bytes)
        self.assertEqual(app.session_state["patient_ui_context"], before)
        self.assertEqual(app.slider(key="viewer_zoom").value, 1.0)
        self.assertEqual(app.slider(key="viewer_brightness").value, 100)
        self.assertEqual(app.slider(key="viewer_contrast").value, 100)

    def test_dicom_display_contract_and_object_reference_are_used(self):
        app = self.selected(kind="dicom")
        self.api.flow.open_dicom_display.assert_called_with("p", unittest.mock.ANY, unittest.mock.ANY,
                        self.api.studies[0], "1.2.3.1", "1.2.3.1.1")
        # Explicitly stored unknown dates stay unknown; a missing details record may use B's tag.
        self.assertEqual(app.session_state["patient_ui_context"]["acquired_on"], "미상")
        self.assertEqual(app.session_state["patient_ui_context"]["object_id"], "1.2.3.1.1")

    def test_creation_validates_identity_and_preserves_conflict_input(self):
        app = self.app()
        app.button(key="patient_ui_create_patient").click().run()
        self.assertTrue(app.error)
        self.api.store.save_patient.assert_not_called()
        app.text_input(key="patient_ui_new_issuer").set_value("research")
        app.text_input(key="patient_ui_new_patient_id").set_value("p-1")
        app.button(key="patient_ui_create_patient").click().run()
        self.api.store.save_patient.assert_called_once()
        app.button(key="patient_ui_patients_choose_0").click().run()
        self.api.store.save_study.side_effect = StudyDetailsConflict("conflict")
        app.text_input(key="patient_ui_new_study_id").set_value("study-conflict")
        app.button(key="patient_ui_create_study").click().run()
        self.assertTrue(app.error)
        self.assertEqual(app.text_input(key="patient_ui_new_study_id").value, "study-conflict")

    def test_details_uses_read_version_and_conflict_keeps_draft_for_comparison(self):
        app = self.selected()
        app.text_input(key="patient_ui_date").set_value("2026-11-19").run()
        self.api.details.save_study_details.side_effect = StudyDetailsConflict("changed")
        self.api.details.get_study_details.return_value = NS(details=Details("1.2.3", "2026-11-18"), update_time="v2")
        self.api.details.get_study_details.side_effect = None
        app.button(key="patient_ui_details_save").click().run()
        self.api.details.save_study_details.assert_called_with("p", unittest.mock.ANY,
                            Details("1.2.3", "2026-11-19"), "v1")
        self.assertEqual(app.text_input(key="patient_ui_date").value, "2026-11-19")
        self.assertTrue(any("현재 입력을 유지" in e.value for e in app.error))
        self.assertTrue(any("2026-11-18" in c.value for c in app.caption))
        self.assertEqual(app.session_state["patient_ui_context"]["acquired_on"], "2026-11-18")
        self.assertNotIn("수신 완료", app.selectbox(key="patient_ui_receipt").options)

    def test_permission_denial_and_expired_session_do_not_leak_previous_selection(self):
        app = self.selected()
        self.api.store.list_patients.side_effect = FirebaseRequestError(403)
        app.button(key="patient_ui_patients_refresh").click().run()
        self.assertTrue(any("403" in e.value for e in app.error))
        self.api.store.list_patients.side_effect = FirebaseRequestError(401)
        app.button(key="patient_ui_patients_refresh").click().run()
        self.assertNotIn("pacs_session", app.session_state.filtered_state)
        self.assertFalse(any(k.startswith(("patient_ui_", "viewer_", "case_")) for k in app.session_state.filtered_state))

    def test_logout_and_owner_switch_clear_patient_screen_cache(self):
        app = self.selected()
        next(b for b in app.button if b.label == "로그아웃").click().run()
        self.assertFalse(any(k.startswith("patient_ui_") for k in app.session_state.filtered_state))
        app = self.selected()
        app.session_state["pacs_session"] = Session("other", "other", "token", float("inf"))
        app.run()
        self.assertNotIn("patient_ui_patient", app.session_state.filtered_state)
        self.assertNotIn("patient_ui_source", app.session_state.filtered_state)

    def test_upload_registration_counts_partial_failure_and_retries_same_id(self):
        app = self.selected(open_image=False)
        result = NS(status="saved_not_linked", stage="after_save", message="원본 보관·연결 미확인",
                    completed=False, error_code="link_not_confirmed", retry_recorded=True)
        self.api.flow.register_dicom_file.return_value = result
        upload = NS(name="ct.dcm", getvalue=lambda: b"mock-dicom")
        with patch("streamlit.file_uploader", side_effect=lambda *a, **kw: [upload] if kw.get("key") == "patient_ui_register_files" else None):
            app.run()
            app.text_input(key="patient_ui_register_series").set_value("1.2.3.1")
            app.button(key="patient_ui_register").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any("연결 완료 0개" in c.value for c in app.caption))
        self.assertFalse(any("등록 완료" in s.value for s in app.success))
        pending = NS(sop_uid="1.2.3.1.1", selected=self.api.studies[0], state="request_failed")
        self.api.flow.list_pending.return_value = [pending]
        app.button(key="patient_ui_pending_reload").click().run()
        self.api.flow.retry_pending.return_value = NS(status="registered", stage="after_save", message="완료",
                                                       completed=True, error_code="")
        app.button(key="patient_ui_retry_0").click().run()
        self.api.flow.retry_pending.assert_called_once_with("p", unittest.mock.ANY, unittest.mock.ANY, "1.2.3.1.1")
        self.assertFalse(app.exception)

    def test_all_dicom_registration_statuses_keep_input_order_and_only_count_completed(self):
        app = self.selected(open_image=False)
        statuses = ("registered", "reused", "rejected", "conflict", "failed", "saved_not_linked")
        self.api.flow.register_dicom_file.side_effect = [NS(status=s, stage="after_save" if s in {"registered", "reused", "saved_not_linked"} else "before_save",
                    message=s, completed=s in {"registered", "reused"}, error_code="") for s in statuses]
        uploads = [NS(name=str(i) + ".dcm", getvalue=lambda: b"fake") for i in range(6)]
        with patch("streamlit.file_uploader", side_effect=lambda *a, **kw: uploads if kw.get("key") == "patient_ui_register_files" else None):
            app.run()
            app.text_input(key="patient_ui_register_series").set_value("1.2.3.1")
            app.button(key="patient_ui_register").click().run()
        self.assertFalse(app.exception)
        self.assertEqual([r.status for _, _, r in app.session_state["patient_ui_registration_results"]], list(statuses))
        self.assertTrue(any("파일 6개 중 연결 완료 2개" in c.value for c in app.caption))

    def test_png_registration_uses_C_result_and_pending_image_id(self):
        app = self.selected(open_image=False)
        self.api.service.register_sample_original.return_value = Registration(False, "link", "permission_denied", "internal-key", "same-image")
        upload = NS(name="sample.png", getvalue=lambda: self.api.source.original_bytes)
        with patch("streamlit.file_uploader", side_effect=lambda *a, **kw: [upload] if kw.get("key") == "patient_ui_register_files" else None):
            app.run()
            app.text_input(key="patient_ui_register_series").set_value("series-id")
            app.text_input(key="patient_ui_register_image_id").set_value("same-image")
            app.button(key="patient_ui_register").click().run()
        self.assertTrue(any("연결 완료 0개" in c.value for c in app.caption))
        self.assertTrue(any("전체 등록 완료가 아닙니다" in w.value for w in app.warning))
        self.api.flow.register_dicom_file.assert_not_called()
        pending = NS(image=NS(image_id="same-image", study_id="1.2.3"), state="retryable")
        self.api.recovery.list_pending_links.return_value = [pending]
        app.button(key="patient_ui_pending_reload").click().run()
        app.button(key="patient_ui_retry_0").click().run()
        self.api.recovery.retry_pending_link.assert_called_with(unittest.mock.ANY, "p", unittest.mock.ANY, "same-image")

    def test_missing_backend_is_an_explicit_error_with_existing_viewer_available(self):
        from patient_study_api import BackendUnavailable
        with patch("patient_study_ui.get_api", side_effect=BackendUnavailable("서비스 연결 실패")):
            app = self.app()
        self.assertTrue(any("서비스 연결 실패" in e.value for e in app.error))
        app.radio(key="viewer_mode").set_value("합성 예시").run()
        self.assertIsNotNone(app.session_state["viewer_active_source"])


if __name__ == "__main__":
    unittest.main()
