"""Real B controls and algorithms through C storage, with mock cloud and temp files."""

from contextlib import ExitStack
from dataclasses import replace
import json
import os
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from case_records import get_case, reopen_case_processing
from firebase_client import FirebaseRequestError
import test_case_records as fixtures


class BCIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CaseRecordTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {
            "PACS_FIREBASE_API_KEY": "test", "PACS_FIREBASE_PROJECT_ID": "p",
        }))
        self.stack.enter_context(patch("case_records_ui.storage_root", return_value=self.fixture.root))

    def app(self):
        app = AppTest.from_file("app.py", default_timeout=15)
        app.session_state["pacs_session"] = self.fixture.session
        app.run()
        return app

    def set_roi(self, app, bounds):
        for label, value in zip(
            ("ROI left", "ROI top", "ROI right (제외)", "ROI bottom (제외)"), bounds,
        ):
            next(item for item in app.number_input if item.label == label).set_value(value)
        next(button for button in app.button if button.label == "ROI 적용").click().run()
        self.assertFalse(app.exception)

    def processed_app(self, algorithm="가우시안", parameter="radius", value=2.0):
        app = self.app()
        app.radio(key="viewer_mode").set_value("합성 예시").run()
        app.selectbox(key="viewer_processing_algorithm").set_value(algorithm).run()
        app.number_input(key="viewer_processing_" + parameter).set_value(value).run()
        app.button(key="viewer_processing_apply").click().run()
        self.set_roi(app, (210, 135, 225, 145))
        return app

    def save(self, app):
        if not app.checkbox(key="case_record_enabled").value:
            app.checkbox(key="case_record_enabled").check().run()
        app.text_input(key="case_record_title").set_value("BC integration")
        app.text_area(key="case_record_provenance").set_value("직접 만든 합성 영상")
        app.button(key="case_record_save").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        return get_case("p", self.fixture.session, app.session_state["case_record_saved"])

    def open(self, app, record):
        if not app.checkbox(key="case_record_enabled").value:
            app.checkbox(key="case_record_enabled").check().run()
        with patch("case_records_ui.list_cases", return_value=([record], "")):
            app.button(key="case_record_reload").click().run()
        app.button(key="case_record_open_" + record.case_id).click().run()
        self.assertFalse(app.exception)
        return app

    def test_all_algorithms_save_and_restore_without_reexecution(self):
        for algorithm, parameter, value, algorithm_id in (
            ("가우시안", "radius", 2.0, "gaussian_blur"),
            ("감마 보정", "gamma", 0.5, "gamma_correction"),
            ("임계값 분할", "threshold", 128, "threshold_segmentation"),
        ):
            with self.subTest(algorithm=algorithm):
                app = self.processed_app(algorithm, parameter, value)
                expected = app.session_state["viewer_displayed_processing"]
                original_bytes = self.fixture.source.original_bytes
                # Draft input must never be stored as the setting of an older result.
                app.number_input(key="viewer_processing_" + parameter).set_value(value + 1).run()
                record = self.save(app)
                metadata = json.loads(record.values["record_json"])
                self.assertEqual(metadata["algorithm_id"], algorithm_id)
                self.assertEqual(metadata["parameters"], {parameter: value})
                self.assertEqual(metadata["statistics"], expected.statistics)
                source, stored = reopen_case_processing(self.fixture.root, self.fixture.session, record)
                self.assertEqual(source.original_bytes, original_bytes)
                self.assertEqual(stored.result.tobytes(), expected.result.tobytes())
                with patch("gaussian_ui._run", side_effect=AssertionError("must not reprocess")) as run:
                    fresh = self.open(self.app(), record)
                    fresh.slider(key="viewer_zoom").set_value(2.0).run()
                    run.assert_not_called()
                self.assertEqual(fresh.selectbox(key="viewer_processing_algorithm").value, algorithm)
                self.assertEqual(fresh.number_input(key="viewer_processing_" + parameter).value, value)
                self.assertEqual(fresh.session_state["viewer_roi_bounds"], expected.roi)
                for label, bound in zip(
                    ("ROI left", "ROI top", "ROI right (제외)", "ROI bottom (제외)"), expected.roi,
                ):
                    self.assertEqual(next(item for item in fresh.number_input if item.label == label).value, bound)
                self.assertEqual(fresh.session_state["viewer_displayed_processing"].statistics, stored.statistics)
                fresh.text_area(key="case_record_note").set_value("메모만 수정")
                edited = self.save(fresh)
                self.assertEqual(edited.values["record_json"], record.values["record_json"])

    def test_reapply_after_restore_saves_new_version_and_keeps_old_snapshot(self):
        record = self.save(self.processed_app())
        old_source, old_processing = reopen_case_processing(self.fixture.root, self.fixture.session, record)
        fresh = self.open(self.app(), record)
        fresh.number_input(key="viewer_processing_radius").set_value(3.0).run()
        fresh.button(key="viewer_processing_apply").click().run()
        self.assertFalse(fresh.exception)
        self.assertNotIn("viewer_processing_request", fresh.session_state.filtered_state)
        self.assertEqual(fresh.session_state["viewer_displayed_processing"].parameters, {"radius": 3.0})
        updated = self.save(fresh)
        self.assertEqual(updated.values["version"], 2)
        self.assertEqual(json.loads(updated.values["record_json"])["parameters"], {"radius": 3.0})
        _, old_again = reopen_case_processing(self.fixture.root, self.fixture.session, record)
        self.assertEqual(old_again.result.tobytes(), old_processing.result.tobytes())
        self.assertEqual(old_again.parameters, {"radius": 2.0})
        self.assertNotEqual(old_again.result.tobytes(), fresh.session_state["viewer_processing_result"].tobytes())
        self.assertEqual(old_source.original_bytes, self.fixture.source.original_bytes)
        self.assertEqual(len(self.fixture.documents), 3)

    def test_restored_controls_have_no_duplicate_defaults_and_details_start_closed(self):
        for algorithm, parameter, value in (
            ("가우시안", "radius", 2.0),
            ("감마 보정", "gamma", 0.5),
            ("임계값 분할", "threshold", 128),
        ):
            with self.subTest(algorithm=algorithm):
                record = self.save(self.processed_app(algorithm, parameter, value))
                # Streamlit normally emits this warning only once per process.
                with patch("streamlit.elements.lib.policies._shown_default_value_warning", False):
                    fresh = self.open(self.app(), record)
                self.assertFalse(any("Session State API" in warning.value for warning in fresh.warning))
                details = next(item for item in fresh.expander if item.label == "처리 기록 상세")
                self.assertFalse(details.proto.expanded)
                self.assertEqual(len(details.get("json")), 1)
                self.assertEqual(fresh.number_input(key="viewer_processing_" + parameter).value, value)
                fresh.run()
                self.assertEqual(fresh.selectbox(key="viewer_processing_algorithm").value, algorithm)
                self.assertEqual(fresh.number_input(key="viewer_processing_" + parameter).value, value)
                self.assertEqual(fresh.session_state["viewer_roi_bounds"], (210, 135, 225, 145))

    def test_restored_roi_can_be_changed_and_removed(self):
        record = self.save(self.processed_app())
        fresh = self.open(self.app(), record)
        bounds = (0, 0, 10, 8)
        self.set_roi(fresh, bounds)
        changed = fresh.session_state["viewer_displayed_processing"]
        self.assertEqual(changed.roi, bounds)
        self.assertEqual(changed.statistics["bounds"], list(bounds))
        self.assertEqual(changed.statistics["result"]["pixel_count"], 80)
        edited = self.save(fresh)
        self.assertEqual(json.loads(edited.values["record_json"])["roi"], list(bounds))
        fresh = self.open(self.app(), edited)
        fresh.button(key="viewer_roi_clear").click().run()
        cleared = fresh.session_state["viewer_displayed_processing"]
        self.assertIsNone(cleared.roi)
        self.assertEqual(cleared.statistics, {})
        edited = self.save(fresh)
        self.assertIsNone(json.loads(edited.values["record_json"])["roi"])

    def test_processing_failure_after_restore_blocks_save_and_old_result(self):
        record = self.save(self.processed_app())
        fresh = self.open(self.app(), record)
        with patch("gaussian_ui._run", side_effect=ValueError("test failure")):
            fresh.button(key="viewer_processing_apply").click().run()
        self.assertFalse(fresh.exception)
        self.assertTrue(fresh.error)
        self.assertTrue(fresh.button(key="case_record_save").disabled)
        self.assertIsNone(fresh.session_state["viewer_displayed_result"])
        self.assertNotIn("viewer_displayed_processing", fresh.session_state.filtered_state)
        self.assertEqual(len(self.fixture.commits), 1)
        fresh.button(key="viewer_processing_apply").click().run()
        self.assertFalse(fresh.error)
        self.assertFalse(fresh.button(key="case_record_save").disabled)

    def test_invalid_roi_blocks_saving_saved_roi_as_current(self):
        record = self.save(self.processed_app())
        fresh = self.open(self.app(), record)
        self.set_roi(fresh, (30, 0, 10, 8))
        self.assertTrue(fresh.button(key="case_record_save").disabled)
        self.assertTrue(fresh.error)
        fresh.button(key="viewer_roi_clear").click().run()
        self.assertFalse(fresh.error)
        self.assertFalse(fresh.button(key="case_record_save").disabled)

    def test_out_of_range_saved_parameter_is_not_inserted_into_control(self):
        app = self.processed_app()
        processing = app.session_state["viewer_displayed_processing"]
        record = self.fixture.save(processing=replace(processing, parameters={"radius": 10 ** 400}))
        fresh = self.open(self.app(), record)
        self.assertTrue(any("새 설정을 선택하세요" in item.value for item in fresh.warning))
        self.assertEqual(fresh.number_input(key="viewer_processing_radius").value, 1.0)
        self.assertEqual(fresh.session_state["viewer_displayed_processing"].parameters, {"radius": 10 ** 400})

    def test_unsaved_processing_and_roi_changes_require_discard_before_switch(self):
        record = self.save(self.processed_app())
        for change in ("processing", "roi", "clear_roi"):
            with self.subTest(change=change):
                app = self.open(self.app(), record)
                if change == "processing":
                    app.number_input(key="viewer_processing_radius").set_value(3.0).run()
                    app.button(key="viewer_processing_apply").click().run()
                elif change == "roi":
                    self.set_roi(app, (0, 0, 10, 8))
                else:
                    app.button(key="viewer_roi_clear").click().run()
                current = app.session_state["viewer_displayed_processing"]
                app.button(key="case_record_new").click().run()
                self.assertEqual(app.session_state["case_record_loaded"].case_id, record.case_id)
                self.assertEqual(app.session_state["viewer_displayed_processing"], current)
                self.assertTrue(any("저장하지 않은 내용" in item.value for item in app.warning))
                app.checkbox(key="case_record_discard").check().run()
                app.button(key="case_record_new").click().run()
                self.assertIsNone(app.session_state["case_record_loaded"])
                self.assertEqual(app.text_input(key="case_record_title").value, "")
                self.assertEqual(app.text_area(key="case_record_note").value, "")
                self.assertEqual(app.text_area(key="case_record_provenance").value, "")
                self.assertNotIn("viewer_displayed_processing", app.session_state.filtered_state)

    def test_retry_uses_first_processing_snapshot_and_protects_later_edits(self):
        from case_records import save_case
        app = self.processed_app()
        app.checkbox(key="case_record_enabled").check().run()
        app.text_input(key="case_record_title").set_value("BC retry")
        app.text_area(key="case_record_provenance").set_value("직접 만든 합성 영상")
        calls = []
        def save(*args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise FirebaseRequestError(503)
            return save_case(*args, **kwargs)
        with patch("case_records_ui.save_case", side_effect=save):
            app.button(key="case_record_save").click().run()
            self.assertTrue(app.error)
            app.number_input(key="viewer_processing_radius").set_value(4.0).run()
            app.button(key="viewer_processing_apply").click().run()
            self.set_roi(app, (0, 0, 10, 8))
            app.button(key="case_record_retry").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        self.assertEqual(calls[1]["operation_id"], calls[0]["operation_id"])
        self.assertIs(calls[1]["processing"], calls[0]["processing"])
        record = get_case("p", self.fixture.session, app.session_state["case_record_saved"])
        metadata = json.loads(record.values["record_json"])
        self.assertEqual(metadata["parameters"], {"radius": 2.0})
        self.assertEqual(metadata["roi"], [210, 135, 225, 145])
        self.assertEqual(app.session_state["viewer_displayed_processing"].parameters, {"radius": 4.0})
        app.button(key="case_record_new").click().run()
        self.assertTrue(any("저장하지 않은 내용" in item.value for item in app.warning))
        self.assertEqual(app.session_state["viewer_displayed_processing"].parameters, {"radius": 4.0})

    def test_two_sessions_keep_first_processing_and_preserve_rejected_input(self):
        record = self.save(self.processed_app())
        first = self.open(self.app(), record)
        second = self.open(self.app(), record)
        first.number_input(key="viewer_processing_radius").set_value(3.0).run()
        first.button(key="viewer_processing_apply").click().run()
        updated = self.save(first)
        second.number_input(key="viewer_processing_radius").set_value(4.0).run()
        second.button(key="viewer_processing_apply").click().run()
        with patch("case_records._request", side_effect=self.fixture.reject_commit_with_403):
            with self.assertLogs("case_records", level="WARNING") as log:
                second.button(key="case_record_save").click().run()
        self.assertFalse(second.exception)
        self.assertTrue(any("다른 창에서 먼저 저장" in item.value for item in second.error))
        self.assertIn("reason=stale_case_version", "\n".join(log.output))
        self.assertEqual(second.session_state["viewer_displayed_processing"].parameters, {"radius": 4.0})
        self.assertEqual(second.session_state["case_record_pending"]["processing"].parameters, {"radius": 4.0})
        self.assertEqual(get_case("p", self.fixture.session, record.case_id), updated)
        self.assertEqual(json.loads(updated.values["record_json"])["parameters"], {"radius": 3.0})
        self.assertEqual(len(self.fixture.documents), 3)

    def test_switch_same_source_cases_clears_applied_cache_and_logout_clears_all(self):
        first = self.save(self.processed_app())
        source, processing = reopen_case_processing(self.fixture.root, self.fixture.session, first)
        second = self.fixture.save(processing=processing)
        app = self.open(self.app(), first)
        app.number_input(key="viewer_processing_radius").set_value(4.0).run()
        app.button(key="viewer_processing_apply").click().run()
        # Both cases have identical source bytes; source identity alone cannot reset caches.
        app.checkbox(key="case_record_discard").check().run()
        app = self.open(app, second)
        self.assertEqual(app.session_state["viewer_stored_source"].source_id, source.source_id)
        self.assertNotIn("viewer_processing_applied", app.session_state.filtered_state)
        self.assertEqual(app.number_input(key="viewer_processing_radius").value, 2.0)
        self.assertEqual(app.session_state["viewer_displayed_processing"].parameters, {"radius": 2.0})
        next(button for button in app.button if button.label == "로그아웃").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(any(key.startswith(("viewer_", "case_record_")) for key in app.session_state.filtered_state))

    def test_switch_same_source_cases_updates_existing_browser_controls(self):
        for algorithm, parameter, first_value, second_value in (
            ("가우시안", "radius", 3.0, 1.0),
            ("감마 보정", "gamma", 0.5, 2.0),
            ("임계값 분할", "threshold", 64, 192),
        ):
            with self.subTest(algorithm=algorithm):
                first_app = self.processed_app(algorithm, parameter, first_value)
                self.set_roi(first_app, (0, 0, 640, 480))
                first = self.save(first_app)
                second_app = self.processed_app(algorithm, parameter, second_value)
                self.set_roi(second_app, (20, 20, 100, 100))
                second = self.save(second_app)
                app = self.open(self.app(), second)
                for record, value, bounds in (
                    (first, first_value, (0, 0, 640, 480)),
                    (second, second_value, (20, 20, 100, 100)),
                    (first, first_value, (0, 0, 640, 480)),
                ):
                    with patch("streamlit.elements.lib.policies._shown_default_value_warning", False):
                        app = self.open(app, record)
                    control = app.number_input(key="viewer_processing_" + parameter)
                    self.assertEqual(control.value, value)
                    # A keyed browser widget needs an explicit update, not a changed default.
                    self.assertTrue(control.proto.set_value)
                    self.assertEqual(control.proto.value, value)
                    self.assertTrue(app.selectbox(key="viewer_processing_algorithm").proto.set_value)
                    for label, bound in zip(
                        ("ROI left", "ROI top", "ROI right (제외)", "ROI bottom (제외)"), bounds,
                    ):
                        roi_control = next(item for item in app.number_input if item.label == label)
                        self.assertTrue(roi_control.proto.set_value)
                        self.assertEqual(roi_control.proto.value, bound)
                    self.assertFalse(any("Session State API" in item.value for item in app.warning))
                    self.assertEqual(app.session_state["viewer_displayed_processing"].parameters, {parameter: value})
                    self.assertEqual(tuple(app.session_state["viewer_roi_bounds"]), bounds)

    def test_switch_to_case_without_roi_or_processing_resets_browser_inputs(self):
        selected = self.save(self.processed_app("감마 보정", "gamma", 2.0))
        cleared_app = self.processed_app(value=3.0)
        cleared_app.button(key="viewer_roi_clear").click().run()
        cleared = self.save(cleared_app)
        original_only = self.fixture.save()
        app = self.open(self.app(), selected)
        for record, radius in ((cleared, 3.0), (original_only, 1.0), (cleared, 3.0)):
            app = self.open(app, record)
            self.assertEqual(app.selectbox(key="viewer_processing_algorithm").value, "가우시안")
            self.assertTrue(app.selectbox(key="viewer_processing_algorithm").proto.set_value)
            self.assertEqual(app.number_input(key="viewer_processing_radius").value, radius)
            self.assertTrue(app.number_input(key="viewer_processing_radius").proto.set_value)
            for label, bound in zip(
                ("ROI left", "ROI top", "ROI right (제외)", "ROI bottom (제외)"), (0, 0, 640, 480),
            ):
                control = next(item for item in app.number_input if item.label == label)
                self.assertTrue(control.proto.set_value)
                self.assertEqual(control.proto.value, bound)
            self.assertNotIn("viewer_roi_bounds", app.session_state.filtered_state)


if __name__ == "__main__":
    unittest.main()
