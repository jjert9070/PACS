"""A04/A05/A06 with B controls and C persistence; cloud requests use C's fixture."""

from dataclasses import replace
from io import BytesIO
import unittest
from unittest.mock import patch

from PIL import Image, ImageOps
from streamlit.testing.v1 import AppTest

import test_bc_integration as bc_tests
from test_gaussian_ui import column_items
from firebase_client import FirebaseRequestError
from case_records_ui import _switch


class AIntegrationTests(unittest.TestCase):
    setUp = bc_tests.BCIntegrationTests.setUp
    app = bc_tests.BCIntegrationTests.app
    set_roi = bc_tests.BCIntegrationTests.set_roi
    processed_app = bc_tests.BCIntegrationTests.processed_app
    save = bc_tests.BCIntegrationTests.save
    open = bc_tests.BCIntegrationTests.open
    def test_a04_actual_b_statistics_and_c_record_unchanged_by_all_view_controls(self):
        app = self.processed_app()
        before = app.session_state["viewer_roi_statistics"]
        result = app.session_state["viewer_displayed_result"].tobytes()
        for key, value in (("viewer_brightness", 150), ("viewer_contrast", 50),
                           ("viewer_zoom", 2.0), ("viewer_x", 25), ("viewer_y", 75)):
            app.slider(key=key).set_value(value).run()
        app.button(key="viewer_rotate").click().run()
        app.toggle(key="viewer_flip_horizontal").set_value(True).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["viewer_roi_statistics"], before)
        self.assertEqual(app.session_state["viewer_displayed_result"].tobytes(), result)
        app.button(key="viewer_reset").click().run()
        self.assertEqual(app.session_state["viewer_roi_statistics"], before)
        record = self.save(app)
        reopened = self.open(self.app(), record)
        self.assertEqual(reopened.session_state["viewer_roi_statistics"], before)

    def test_a05_b_roi_rotates_with_both_images_and_offscreen_selection_survives(self):
        app = self.processed_app(parameter="radius", value=0.0)
        self.set_roi(app, (0, 0, 8, 6))
        captures = []
        with patch("viewer.st.image", side_effect=lambda image, **kw: captures.append(image.copy())):
            app.button(key="viewer_rotate").click().run()
        self.assertEqual(len(captures), 2)
        self.assertEqual(captures[0].tobytes(), captures[1].tobytes())
        self.assertEqual(captures[0].getpixel((479, 0)), (255, 210, 0))
        app.run()
        self.assertEqual(column_items(app, "**처리 결과**")[0], "imgs")
        app.slider(key="viewer_zoom").set_value(4.0).run()
        app.slider(key="viewer_x").set_value(100).run()
        app.slider(key="viewer_y").set_value(100).run()
        self.assertTrue(any("현재 보기에 표시되지 않음" in item.value for item in app.info))
        self.assertEqual(app.session_state["viewer_roi_bounds"], (0, 0, 8, 6))

    def test_c_unsaved_note_blocks_source_removal_until_explicit_discard(self):
        app = self.processed_app()
        app.checkbox(key="case_record_enabled").check().run()
        app.text_area(key="case_record_note").set_value("keep my draft").run()
        source_id = app.session_state["viewer_identity"]
        app.radio(key="viewer_mode").set_value("파일 가져오기").run()
        self.assertEqual(app.session_state["viewer_identity"], source_id)
        self.assertTrue(any("저장하지 않은 변경" in item.value for item in app.warning))
        self.assertEqual(app.text_area(key="case_record_note").value, "keep my draft")
        app.checkbox(key="case_record_discard").check().run()
        self.assertIsNone(app.session_state["viewer_identity"])
        self.assertNotIn("viewer_roi_statistics", app.session_state.filtered_state)
        self.assertNotIn("viewer_processing_result", app.session_state.filtered_state)

    def test_a06_logout_clears_real_processing_roi_case_and_view_state(self):
        app = self.processed_app()
        app.slider(key="viewer_brightness").set_value(150).run()
        app.button(key="viewer_rotate").click().run()
        next(b for b in app.button if b.label == "로그아웃").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(any(k.startswith(("viewer_", "case_")) for k in app.session_state.filtered_state))

    def test_a06_clean_case_switch_keeps_form_source_result_and_roi_together(self):
        first = self.save(self.processed_app())
        stream = BytesIO()
        ImageOps.invert(self.fixture.source.image).save(stream, format="PNG")
        with patch("viewer.sample_image_bytes", return_value=stream.getvalue()):
            second_app = self.processed_app(value=3.0)
            self.set_roi(second_app, (10, 12, 30, 28))
            second_app.checkbox(key="case_record_enabled").check().run()
            second_app.text_area(key="case_record_note").set_value("second source")
            second = self.save(second_app)
        second_source, second_processing = bc_tests.reopen_case_processing(
            self.fixture.root, self.fixture.session, second,
        )
        original_only = self.fixture.save(source=second_source)
        self.assertNotEqual(first.values["source_id"], second_source.source_id)

        for target in (second, original_only):
            with self.subTest(processed=target is second):
                app = self.open(self.app(), first)
                app = self.open(app, target)
                for _ in range(2):
                    self.assertFalse(app.exception)
                    self.assertFalse(app.error)
                    self.assertEqual(app.session_state["case_record_loaded"].case_id, target.case_id)
                    self.assertEqual(app.text_area(key="case_record_note").value, target.values["note"])
                    self.assertEqual(app.session_state["viewer_identity"], second_source.source_id)
                    self.assertEqual(app.session_state["viewer_active_source"].original_bytes,
                                     second_source.original_bytes)
                    current = app.session_state["case_record_processing_current"]
                    self.assertEqual(current, app.session_state["case_record_processing_initial"])
                    self.assertFalse(any("저장하지 않은 변경" in item.value or "뷰어 원본이 다릅니다" in item.value
                                         for item in app.warning))
                    state = app.session_state.filtered_state
                    self.assertNotIn("viewer_processing_applied", state)
                    if target is second:
                        self.assertEqual(app.session_state["viewer_displayed_processing"], second_processing)
                        self.assertEqual(app.session_state["viewer_displayed_result"].tobytes(),
                                         second_processing.result.tobytes())
                        self.assertEqual(app.session_state["viewer_roi_bounds"], tuple(second_processing.roi))
                        self.assertEqual(app.session_state["viewer_roi_statistics"], second_processing.statistics)
                        self.assertEqual(app.number_input(key="viewer_processing_radius").value, 3.0)
                    else:
                        self.assertIsNone(app.session_state["viewer_displayed_result"])
                        self.assertNotIn("viewer_displayed_processing", state)
                        self.assertNotIn("viewer_roi_bounds", state)
                        self.assertNotIn("viewer_roi_statistics", state)
                    app.run()
                # A subsequent transition must also work without discarding anything.
                self.open(app, first)
                self.assertEqual(app.session_state["viewer_identity"], first.values["source_id"])

    def test_a06_new_case_after_processed_saved_case_clears_previous_image(self):
        record = self.save(self.processed_app())
        app = self.open(self.app(), record)
        app.slider(key="viewer_brightness").set_value(150).run()
        app.button(key="viewer_rotate").click().run()
        app.button(key="case_record_new").click().run()
        for _ in range(2):
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertIsNone(app.session_state["case_record_loaded"])
            self.assertEqual(app.text_input(key="case_record_title").value, "")
            self.assertEqual(app.text_area(key="case_record_note").value, "")
            self.assertEqual(app.text_area(key="case_record_provenance").value, "")
            self.assertIsNone(app.session_state["viewer_identity"])
            self.assertIsNone(app.session_state["viewer_active_source"])
            self.assertIsNone(app.session_state["case_record_processing_initial"])
            self.assertIsNone(app.session_state["case_record_processing_current"])
            state = app.session_state.filtered_state
            self.assertFalse(any(k.startswith(("viewer_stored_", "viewer_current_", "viewer_displayed_",
                                              "viewer_processing_", "viewer_roi_", "viewer_restore_"))
                                 for k in state))
            self.assertFalse(any("저장하지 않은 변경" in item.value for item in app.warning))
            self.assertTrue(app.button(key="case_record_save").disabled)
            app.run()

    def test_invalid_record_blocks_wrong_source_result_before_display(self):
        app = self.processed_app()
        record = app.session_state["viewer_displayed_processing"]
        app.session_state["viewer_current_processing"] = replace(record, source_id="0" * 64)
        app.run()
        self.assertTrue(app.error)
        self.assertIsNone(app.session_state["viewer_displayed_result"])
        self.assertIsNotNone(app.session_state["viewer_active_source"])

    def test_stored_source_is_redecoded_and_forged_metadata_is_rejected(self):
        source = self.fixture.source
        script = '''import streamlit as st
from viewer import render_viewer
value = render_viewer(stored_source=st.session_state['source'])
st.session_state['seen'] = value
'''
        app = AppTest.from_string(script)
        app.session_state["source"] = replace(source, image=Image.new("RGB", (1, 1)))
        app.run()
        app.radio(key="viewer_mode").set_value("로컬 저장 영상").run()
        self.assertEqual(app.session_state["seen"].image.size, source.image.size)
        app.session_state["source"] = replace(source, original_bytes=b"broken")
        app.run()
        self.assertIsNone(app.session_state["seen"])
        self.assertTrue(app.error)
        app.session_state["source"] = replace(source, source_id="0" * 64)
        app.run()
        self.assertIsNone(app.session_state["seen"])

    def test_c_case_reopen_distinguishes_denied_missing_and_file_loss(self):
        import streamlit as st
        for error, expected in ((FirebaseRequestError(403), "접근이 거절"),
                                (FirebaseRequestError(404), "문서를 찾을 수"),
                                (ValueError("원본 파일 누락"), "원본 파일 누락")):
            with self.subTest(error=expected), patch("case_records_ui._dirty", return_value=False), \
                    patch("case_records_ui.get_case", side_effect=error):
                _switch("p", self.fixture.session, "0" * 32)
                self.assertIn(expected, st.session_state["case_record_error"])
        st.session_state.pop("case_record_error", None)


if __name__ == "__main__":
    unittest.main()
