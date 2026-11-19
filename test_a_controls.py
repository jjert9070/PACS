from io import BytesIO
import unittest
from unittest.mock import patch
from PIL import Image
from streamlit.testing.v1 import AppTest
from viewer import ViewTransform, adjust_display_tone, viewport_box


class ViewTransformTests(unittest.TestCase):
    def test_crop_then_clockwise_rotation_and_display_axis_flips(self):
        # Source rows are [1, 2, 3, 4] and [5, 6, 7, 8].
        source = Image.frombytes("L", (4, 2), bytes(range(1, 9)))
        box = (1, 0, 4, 2)
        rotated = ViewTransform(box, turns=1).apply(source)
        self.assertEqual(rotated.size, (2, 3))
        self.assertEqual(rotated.tobytes(), bytes([6, 2, 7, 3, 8, 4]))
        self.assertEqual(ViewTransform(box, 1, True).apply(source).tobytes(),
                         bytes([2, 6, 3, 7, 4, 8]))
        self.assertEqual(ViewTransform(box, 1, False, True).apply(source).tobytes(),
                         bytes([8, 4, 7, 3, 6, 2]))
        self.assertEqual(ViewTransform(box, turns=4).apply(source).tobytes(),
                         source.crop(box).tobytes())
        self.assertEqual(source.tobytes(), bytes(range(1, 9)))

    def test_zoom_and_rotation_at_the_edges_of_narrow_images(self):
        for size in ((1, 1), (1, 17), (17, 1)):
            with self.subTest(size=size):
                source = Image.new("L", size, 73)
                box = viewport_box(size, 4, 100, 100)
                preview = ViewTransform(box, turns=1, flip_vertical=True).apply(source)
                self.assertEqual(preview.size, (box[3] - box[1], box[2] - box[0]))
                self.assertEqual(preview.getextrema(), (73, 73))

    def test_display_tone_uses_fixed_midpoint_and_preserves_alpha(self):
        image = Image.frombytes("RGBA", (2, 1), bytes([0, 64, 160, 31, 255, 64, 160, 209]))
        self.assertEqual(adjust_display_tone(image, 100, 100).tobytes(), image.tobytes())
        adjusted = adjust_display_tone(image, 125, 150)
        self.assertEqual(adjusted.tobytes(), bytes([0, 56, 236, 31, 255, 56, 236, 209]))
        self.assertEqual(image.getpixel((0, 0)), (0, 64, 160, 31))

class AViewScreenTests(unittest.TestCase):

    def viewer_app(self, script="from viewer import render_viewer\nrender_viewer()"):
        return AppTest.from_string(script, default_timeout=10).run()

    def test_rotation_flip_reset_and_source_change(self):
        app = self.viewer_app()
        app.radio(key="viewer_mode").set_value("합성 예시").run()
        for turns in (1, 2, 3, 0):
            app.button(key="viewer_rotate").click().run()
            self.assertEqual(app.session_state["viewer_turns"], turns)
        horizontal = "viewer_flip_horizontal"
        vertical = "viewer_flip_vertical"
        for key in (horizontal, vertical):
            app.toggle(key=key).set_value(True).run()
            self.assertTrue(app.toggle(key=key).value)
            app.toggle(key=key).set_value(False).run()
            self.assertFalse(app.toggle(key=key).value)
            app.toggle(key=key).set_value(True).run()
        app.button(key="viewer_rotate").click().run()
        self.assertTrue(any("회전 90° · 좌우 반전 켜짐 · 상하 반전 켜짐" in item.value
                            for item in app.caption))
        app.button(key="viewer_reset").click().run()
        self.assertEqual(app.session_state["viewer_turns"], 0)
        self.assertFalse(app.toggle(key=horizontal).value)
        self.assertFalse(app.toggle(key=vertical).value)
        self.assertTrue(any("회전 0° · 좌우 반전 꺼짐 · 상하 반전 꺼짐" in item.value
                            for item in app.caption))
        app.button(key="viewer_rotate").click().run()
        app.toggle(key=horizontal).set_value(True).run()
        app.radio(key="viewer_mode").set_value("파일 가져오기").run()
        app.radio(key="viewer_mode").set_value("합성 예시").run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["viewer_turns"], 0)
        self.assertFalse(app.toggle(key=horizontal).value)

    def test_display_tone_changes_both_previews_and_reset_preserves_other_state(self):
        pixels = bytes([64] * 3 + [160] * 3)
        stream = BytesIO()
        Image.frombytes("RGB", (2, 1), pixels).save(stream, format="PNG")
        source_bytes = stream.getvalue()
        script = '''from PIL import Image
import streamlit as st
from viewer import render_viewer

def processor(image):
    st.session_state['test_processor_input'] = image.tobytes()
    if 'test_result' not in st.session_state:
        st.session_state['test_result'] = Image.frombytes('L', (2, 1), bytes([64, 10]))
    return st.session_state['test_result']

source = render_viewer(processor)
if source:
    st.session_state['test_source'] = source
'''
        with patch("viewer.sample_image_bytes", return_value=source_bytes), patch("viewer.st.image") as display:
            app = self.viewer_app(script)
            self.assertEqual(len(app.slider), 0)
            app.radio(key="viewer_mode").set_value("합성 예시").run()
            app.session_state["viewer_roi_bounds"] = (0, 0, 1, 1)
            app.session_state["viewer_processing_choice"] = "example"
            app.session_state["case_note"] = "draft note"
            app.slider(key="viewer_brightness").set_value(125).run()
            display.reset_mock()
            app.slider(key="viewer_contrast").set_value(150).run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertEqual(display.call_count, 2)
            original_preview, result_preview = [call.args[0] for call in display.call_args_list]
            self.assertEqual(original_preview.tobytes(), bytes([56] * 3 + [236] * 3))
            self.assertEqual(result_preview.tobytes(), bytes([56, 0]))
            self.assertEqual(app.session_state["test_processor_input"], pixels)
            self.assertEqual(app.session_state["test_source"].image.tobytes(), pixels)
            self.assertEqual(app.session_state["test_source"].original_bytes, source_bytes)
            self.assertEqual(app.session_state["test_result"].tobytes(), bytes([64, 10]))

            app.button(key="viewer_reset").click().run()
            self.assertEqual(app.slider(key="viewer_brightness").value, 100)
            self.assertEqual(app.slider(key="viewer_contrast").value, 100)
            self.assertEqual(app.session_state["viewer_roi_bounds"], (0, 0, 1, 1))
            self.assertEqual(app.session_state["viewer_processing_choice"], "example")
            self.assertEqual(app.session_state["case_note"], "draft note")
