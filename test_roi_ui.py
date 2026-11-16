import unittest
from unittest.mock import patch

from PIL import Image
from streamlit.testing.v1 import AppTest


SCRIPT = '''import streamlit as st
from viewer import render_viewer, clear_viewer
from gaussian_ui import process_gaussian, render_gaussian_controls
from roi_ui import render_roi_controls, render_roi_statistics
if st.button('테스트 로그아웃'):
    clear_viewer()
source = render_viewer(processor=process_gaussian, processing_controls=render_gaussian_controls,
                       roi_controls=render_roi_controls, roi_summary=render_roi_statistics)
if source:
    st.session_state['test_source_bytes'] = source.image.tobytes()
'''


class ROIWebTests(unittest.TestCase):
    def app(self):
        app = AppTest.from_string(SCRIPT, default_timeout=15).run()
        app.radio(key="viewer_mode").set_value("합성 예시").run()
        return app

    def apply_roi(self, app, bounds):
        for label, value in zip(("ROI left", "ROI top", "ROI right (제외)", "ROI bottom (제외)"), bounds):
            next(item for item in app.number_input if item.label == label).set_value(value)
        next(button for button in app.button if button.label == "ROI 적용").click().run()

    def test_unselected_then_original_only_and_processed_statistics(self):
        app = self.app()
        self.assertNotIn("viewer_roi_statistics", app.session_state)
        self.apply_roi(app, (0, 0, 640, 480))
        stats = app.session_state["viewer_roi_statistics"]
        self.assertEqual(stats["original"]["pixel_count"], 640 * 480)
        self.assertIsNone(stats["result"])
        app.number_input(key="viewer_processing_radius").set_value(0.0).run()
        app.button(key="viewer_processing_apply").click().run()
        stats = app.session_state["viewer_roi_statistics"]
        self.assertEqual(stats["original"], stats["result"])
        self.assertFalse(app.error)

    def test_statistics_and_original_survive_view_transforms(self):
        app = self.app()
        before = app.session_state["test_source_bytes"]
        self.apply_roi(app, (100, 80, 400, 300))
        expected = app.session_state["viewer_roi_statistics"]
        app.slider(key="viewer_zoom").set_value(2.0).run()
        app.slider(key="viewer_x").set_value(75).run()
        app.slider(key="viewer_y").set_value(25).run()
        self.assertEqual(app.session_state["viewer_roi_statistics"], expected)
        self.assertEqual(app.session_state["test_source_bytes"], before)
        next(button for button in app.button if button.label == "보기 초기화").click().run()
        self.assertEqual(app.session_state["viewer_roi_statistics"], expected)

    def test_invalid_roi_replaces_previous_statistics_with_error(self):
        app = self.app()
        self.apply_roi(app, (0, 0, 1, 1))
        self.apply_roi(app, (0, 0, 0, 1))
        self.assertTrue(app.error)
        self.assertNotIn("viewer_roi_statistics", app.session_state)
        self.assertNotIn("viewer_roi_bounds", app.session_state)

    def test_processing_failure_removes_result_statistics_only(self):
        app = self.app()
        self.apply_roi(app, (0, 0, 4, 4))
        app.button(key="viewer_processing_apply").click().run()
        self.assertIsNotNone(app.session_state["viewer_roi_statistics"]["result"])
        with patch("gaussian_ui.gaussian_blur", side_effect=ValueError("failure")):
            app.button(key="viewer_processing_apply").click().run()
        self.assertIsNone(app.session_state["viewer_roi_statistics"]["result"])
        self.assertEqual(app.session_state["viewer_roi_statistics"]["original"]["pixel_count"], 16)

    def test_same_name_different_content_discards_roi(self):
        script = '''import streamlit as st
from io import BytesIO
from PIL import Image
from viewer import load_image, render_viewer
from roi_ui import render_roi_controls, render_roi_statistics
value = st.selectbox('fixture', [10, 200])
stream = BytesIO()
Image.new('RGB', (4, 3), (value, value, value)).save(stream, format='PNG')
source = load_image('same.png', stream.getvalue())
render_viewer(stored_source=source, roi_controls=render_roi_controls,
              roi_summary=render_roi_statistics)
'''
        app = AppTest.from_string(script, default_timeout=15).run()
        app.radio(key="viewer_mode").set_value("로컬 저장 영상").run()
        self.apply_roi(app, (1, 1, 4, 3))
        statistics = app.session_state["viewer_roi_statistics"]
        self.assertEqual(statistics["original"]["mean"], 10)
        previous_id = statistics["source_id"]
        app.selectbox[0].set_value(200).run()
        self.assertFalse(app.exception)
        self.assertNotEqual(app.session_state["viewer_identity"][1], previous_id)
        self.assertNotIn("viewer_roi_bounds", app.session_state)
        self.assertNotIn("viewer_roi_statistics", app.session_state)
        self.apply_roi(app, (0, 0, 4, 3))
        self.assertEqual(app.session_state["viewer_roi_statistics"]["original"]["mean"], 200)

    def test_wrong_result_size_cannot_keep_previous_roi_result_statistics(self):
        app = self.app()
        self.apply_roi(app, (0, 0, 4, 4))
        app.button(key="viewer_processing_apply").click().run()
        with patch("gaussian_ui.gaussian_blur", return_value=Image.new("RGB", (1, 1))):
            app.button(key="viewer_processing_apply").click().run()
        self.assertTrue(app.error)
        self.assertFalse(app.exception)
        stats = app.session_state["viewer_roi_statistics"]
        self.assertEqual(stats["original"]["pixel_count"], 16)
        self.assertIsNone(stats["result"])

    def test_source_change_clear_and_logout(self):
        app = self.app()
        self.apply_roi(app, (0, 0, 1, 1))
        app.radio(key="viewer_mode").set_value("파일 가져오기").run()
        self.assertNotIn("viewer_roi_statistics", app.session_state)
        self.assertNotIn("viewer_roi_bounds", app.session_state)
        app.radio(key="viewer_mode").set_value("합성 예시").run()
        self.apply_roi(app, (0, 0, 1, 1))
        app.button(key="viewer_roi_clear").click().run()
        self.assertNotIn("viewer_roi_statistics", app.session_state)
        self.apply_roi(app, (0, 0, 1, 1))
        next(button for button in app.button if button.label == "테스트 로그아웃").click().run()
        self.assertNotIn("viewer_roi_statistics", app.session_state)


if __name__ == "__main__":
    unittest.main()
