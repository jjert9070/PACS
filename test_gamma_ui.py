import unittest
from unittest.mock import patch

from PIL import Image
from streamlit.testing.v1 import AppTest

from gaussian_ui import PREFIX
from image_processing import gamma_correction
from viewer import load_image, sample_image_bytes


SCRIPT = '''import streamlit as st
from viewer import render_viewer, clear_viewer
from gaussian_ui import process_gaussian, render_gaussian_controls
from roi_ui import render_roi_controls, render_roi_statistics
if st.button('테스트 로그아웃'):
    clear_viewer()
source = render_viewer(processor=process_gaussian, processing_controls=render_gaussian_controls,
                       roi_controls=render_roi_controls, roi_summary=render_roi_statistics)
if source:
    st.session_state['test_original'] = source.image.tobytes()
'''


class GammaWebTests(unittest.TestCase):
    def app(self):
        app = AppTest.from_string(SCRIPT, default_timeout=15).run()
        app.radio(key="viewer_mode").set_value("합성 예시").run()
        app.selectbox(key=PREFIX + "algorithm").set_value("감마 보정").run()
        return app

    def apply(self, app, gamma):
        app.number_input(key=PREFIX + "gamma").set_value(gamma).run()
        app.button(key=PREFIX + "apply").click().run()

    def test_gamma_settings_replace_gaussian_settings(self):
        app = self.app()
        keys = [widget.key for widget in app.number_input]
        self.assertIn(PREFIX + "gamma", keys)
        self.assertNotIn(PREFIX + "radius", keys)
        self.assertEqual(app.number_input(key=PREFIX + "gamma").value, 1.0)
        self.assertTrue(any("감마 보정 적용" in text.value for text in app.info))

    def test_apply_preserves_source_and_records_settings(self):
        app = self.app()
        self.apply(app, 2.0)
        source = load_image("sample.png", sample_image_bytes())
        self.assertEqual(app.session_state["test_original"], source.image.tobytes())
        expected = gamma_correction(source.image, 2.0)
        self.assertEqual(app.session_state[PREFIX + "result"].tobytes(), expected.tobytes())
        applied = app.session_state[PREFIX + "applied"]
        self.assertEqual(applied["algorithm_id"], "gamma_correction")
        self.assertEqual(applied["algorithm_version"], "1.0.0")
        self.assertEqual(applied["parameters"], {"gamma": 2.0})
        self.assertEqual(applied["source_id"], source.source_id)
        self.assertTrue(any("실제 적용 γ=2" in text.value for text in app.caption))
        self.assertFalse(app.error)
        self.assertFalse(app.exception)

    def test_gamma_one_matches_original(self):
        app = self.app()
        self.apply(app, 1.0)
        self.assertEqual(app.session_state[PREFIX + "result"].tobytes(), app.session_state["test_original"])

    def test_brighter_and_darker_change_roi_mean(self):
        app = self.app()
        next(button for button in app.button if button.label == "ROI 적용").click().run()
        self.apply(app, 0.5)
        stats = app.session_state["viewer_roi_statistics"]
        self.assertGreater(stats["result"]["mean"], stats["original"]["mean"])
        self.apply(app, 2.0)
        stats = app.session_state["viewer_roi_statistics"]
        self.assertLess(stats["result"]["mean"], stats["original"]["mean"])

    def test_draft_change_does_not_reprocess_or_relabel(self):
        app = self.app()
        with patch("gaussian_ui.gamma_correction", wraps=gamma_correction) as processing:
            self.apply(app, 2.0)
            app.number_input(key=PREFIX + "gamma").set_value(0.5).run()
            app.slider(key="viewer_zoom").set_value(2.0).run()
            self.assertEqual(processing.call_count, 1)
        self.assertEqual(app.session_state[PREFIX + "applied"]["parameters"], {"gamma": 2.0})
        self.assertTrue(any("설정이 변경" in text.value and "γ=2" in text.value for text in app.info))

    def test_switching_algorithm_discards_result(self):
        app = self.app()
        self.apply(app, 2.0)
        app.selectbox(key=PREFIX + "algorithm").set_value("가우시안").run()
        self.assertNotIn(PREFIX + "result", app.session_state)
        self.assertNotIn(PREFIX + "applied", app.session_state)
        app.selectbox(key=PREFIX + "algorithm").set_value("감마 보정").run()
        self.assertNotIn(PREFIX + "result", app.session_state)
        self.assertEqual(app.number_input(key=PREFIX + "gamma").value, 1.0)

    def test_failure_and_wrong_result_are_not_success(self):
        for patched in ({"side_effect": RuntimeError("test failure")},
                        {"return_value": Image.new("RGB", (1, 1))}):
            with self.subTest(patched=patched):
                app = self.app()
                self.apply(app, 2.0)
                with patch("gaussian_ui.gamma_correction", **patched):
                    self.apply(app, 0.5)
                self.assertTrue(app.error)
                self.assertNotIn(PREFIX + "result", app.session_state)
                self.assertEqual(app.session_state["test_original"],
                                 load_image("s.png", sample_image_bytes()).image.tobytes())
                self.apply(app, 0.5)
                self.assertFalse(app.error)

    def test_source_change_and_logout_clear_result(self):
        app = self.app()
        self.apply(app, 2.0)
        app.radio(key="viewer_mode").set_value("파일 가져오기").run()
        self.assertNotIn(PREFIX + "result", app.session_state)
        app.radio(key="viewer_mode").set_value("합성 예시").run()
        app.selectbox(key=PREFIX + "algorithm").set_value("감마 보정").run()
        self.apply(app, 2.0)
        next(button for button in app.button if button.label == "테스트 로그아웃").click().run()
        self.assertNotIn(PREFIX + "result", app.session_state)
        self.assertNotIn(PREFIX + "applied", app.session_state)


if __name__ == "__main__":
    unittest.main()
