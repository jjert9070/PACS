import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
from streamlit.testing.v1 import AppTest

from gaussian_ui import PREFIX
from image_processing import threshold_segmentation
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


def sample_source():
    return load_image("sample.png", sample_image_bytes())


class ThresholdWebTests(unittest.TestCase):
    def app(self):
        app = AppTest.from_string(SCRIPT, default_timeout=15).run()
        app.radio(key="viewer_mode").set_value("합성 예시").run()
        app.selectbox(key=PREFIX + "algorithm").set_value("임계값 분할").run()
        return app

    def apply(self, app, threshold):
        app.number_input(key=PREFIX + "threshold").set_value(threshold).run()
        app.button(key=PREFIX + "apply").click().run()

    def test_threshold_settings_replace_gaussian_settings(self):
        app = self.app()
        keys = [widget.key for widget in app.number_input]
        self.assertIn(PREFIX + "threshold", keys)
        self.assertNotIn(PREFIX + "radius", keys)
        self.assertEqual(app.number_input(key=PREFIX + "threshold").value, 128)
        self.assertTrue(any("임계값 분할 적용" in text.value for text in app.info))

    def test_apply_preserves_source_and_records_settings(self):
        app = self.app()
        self.apply(app, 200)
        source = sample_source()
        self.assertEqual(app.session_state["test_original"], source.image.tobytes())
        expected = threshold_segmentation(source.image, 200)
        self.assertEqual(app.session_state[PREFIX + "result"].tobytes(), expected.tobytes())
        applied = app.session_state[PREFIX + "applied"]
        self.assertEqual(applied["algorithm_id"], "threshold_segmentation")
        self.assertEqual(applied["algorithm_version"], "1.0.0")
        self.assertEqual(applied["parameters"], {"threshold": 200})
        self.assertIsInstance(applied["parameters"]["threshold"], int)
        self.assertEqual(applied["source_id"], source.source_id)
        self.assertTrue(any("실제 적용 임계값=200" in text.value for text in app.caption))
        self.assertFalse(app.error)
        self.assertFalse(app.exception)

    def test_roi_mean_of_result_is_white_fraction(self):
        app = self.app()
        next(button for button in app.button if button.label == "ROI 적용").click().run()
        luminance = np.asarray(sample_source().image.convert("L"))
        for threshold in (0, 128, 236):
            with self.subTest(threshold=threshold):
                self.apply(app, threshold)
                stats = app.session_state["viewer_roi_statistics"]
                white_fraction = float(np.mean(luminance >= threshold))
                self.assertAlmostEqual(stats["result"]["mean"], 255 * white_fraction, places=6)
        self.assertEqual(app.session_state["viewer_roi_statistics"]["result"]["mean"],
                         255 * float(np.mean(luminance >= 236)))

    def test_draft_change_does_not_reprocess_or_relabel(self):
        app = self.app()
        with patch("gaussian_ui.threshold_segmentation", wraps=threshold_segmentation) as processing:
            self.apply(app, 200)
            app.number_input(key=PREFIX + "threshold").set_value(50).run()
            app.slider(key="viewer_zoom").set_value(2.0).run()
            self.assertEqual(processing.call_count, 1)
        self.assertEqual(app.session_state[PREFIX + "applied"]["parameters"], {"threshold": 200})
        self.assertTrue(any("설정이 변경" in text.value and "임계값=200" in text.value for text in app.info))

    def test_switching_algorithm_discards_result(self):
        app = self.app()
        self.apply(app, 200)
        app.selectbox(key=PREFIX + "algorithm").set_value("가우시안").run()
        self.assertNotIn(PREFIX + "result", app.session_state)
        self.assertNotIn(PREFIX + "applied", app.session_state)
        app.selectbox(key=PREFIX + "algorithm").set_value("임계값 분할").run()
        self.assertNotIn(PREFIX + "result", app.session_state)
        self.assertEqual(app.number_input(key=PREFIX + "threshold").value, 128)

    def test_failure_and_wrong_result_are_not_success(self):
        for patched in ({"side_effect": RuntimeError("test failure")},
                        {"return_value": Image.new("RGB", (1, 1))}):
            with self.subTest(patched=patched):
                app = self.app()
                self.apply(app, 200)
                with patch("gaussian_ui.threshold_segmentation", **patched):
                    self.apply(app, 100)
                self.assertTrue(app.error)
                self.assertNotIn(PREFIX + "result", app.session_state)
                self.assertEqual(app.session_state["test_original"], sample_source().image.tobytes())
                self.apply(app, 100)
                self.assertFalse(app.error)

    def test_source_change_and_logout_clear_result(self):
        app = self.app()
        self.apply(app, 200)
        app.radio(key="viewer_mode").set_value("파일 가져오기").run()
        self.assertNotIn(PREFIX + "result", app.session_state)
        app.radio(key="viewer_mode").set_value("합성 예시").run()
        app.selectbox(key=PREFIX + "algorithm").set_value("임계값 분할").run()
        self.apply(app, 200)
        next(button for button in app.button if button.label == "테스트 로그아웃").click().run()
        self.assertNotIn(PREFIX + "result", app.session_state)
        self.assertNotIn(PREFIX + "applied", app.session_state)


if __name__ == "__main__":
    unittest.main()
