"""A04/A05/A06 with B controls and C persistence; cloud requests use C's fixture."""

from dataclasses import replace
import unittest
from unittest.mock import patch

from PIL import Image
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
