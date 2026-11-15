import unittest

import numpy as np
from PIL import Image

from image_processing import gaussian_blur


class GaussianTests(unittest.TestCase):
    def test_zero_is_independent_identity(self):
        source = Image.new("RGB", (9, 5), (10, 20, 30))
        result = gaussian_blur(source, 0)
        self.assertIsNot(result, source)
        self.assertEqual(result.tobytes(), source.tobytes())
        result.putpixel((0, 0), (0, 0, 0))
        self.assertEqual(source.getpixel((0, 0)), (10, 20, 30))

    def test_constant_color_and_size_preserved_including_small_images(self):
        for size in ((1, 1), (1, 7), (17, 11)):
            for radius in (0.25, 1, 20):
                with self.subTest(size=size, radius=radius):
                    source = Image.new("RGB", size, (12, 93, 211))
                    result = gaussian_blur(source, radius)
                    self.assertEqual(result.mode, "RGB")
                    self.assertEqual(result.size, size)
                    self.assertEqual(result.tobytes(), source.tobytes())

    def test_impulse_spreads_symmetrically_without_mutating_source(self):
        source = Image.new("RGB", (21, 21))
        source.putpixel((10, 10), (255, 0, 0))
        before = source.tobytes()
        result = gaussian_blur(source, 2)
        self.assertEqual(source.tobytes(), before)
        self.assertTrue(0 < result.getpixel((10, 10))[0] < 255)
        for distance in (1, 2, 3):
            left = result.getpixel((10-distance, 10))
            self.assertGreater(left[0], 0)
            self.assertEqual(left, result.getpixel((10+distance, 10)))
            self.assertEqual(left, result.getpixel((10, 10-distance)))
            self.assertEqual(left[1:], (0, 0))

    def test_step_edge_is_smoothed_and_repeatable(self):
        source = Image.new("RGB", (40, 20), (40, 40, 40))
        source.paste((200, 200, 200), (20, 0, 40, 20))
        result = gaussian_blur(source, 2)
        self.assertTrue(40 < result.getpixel((19, 10))[0] < result.getpixel((20, 10))[0] < 200)
        self.assertEqual(result.tobytes(), gaussian_blur(source, 2).tobytes())

    def test_known_kernel_and_reflect101_border(self):
        # At sigma=1, the normalized 7-tap kernel has center weight ~0.39905.
        # Reflect101 maps -1 to +1: a bright corner contributes once, not twice.
        source = Image.new("RGB", (9, 9))
        source.putpixel((0, 0), (255, 0, 0))
        result = gaussian_blur(source, 1)
        self.assertAlmostEqual(result.getpixel((0, 0))[0], 41, delta=1)
        self.assertAlmostEqual(result.getpixel((1, 0))[0], 25, delta=1)
        self.assertEqual(result.getpixel((0, 0))[1:], (0, 0))

    def test_numpy_scalar_and_result_independence(self):
        source = Image.new("RGB", (7, 7), (12, 93, 211))
        for radius in (1, 1.5, np.int32(1), np.int64(2), np.float32(1.5), np.float64(1.5)):
            with self.subTest(radius=radius, scalar_type=type(radius)):
                result = gaussian_blur(source, radius)
                self.assertEqual(result.mode, "RGB")
                self.assertEqual(result.size, source.size)
                result.putpixel((0, 0), (0, 0, 0))
                self.assertEqual(source.getpixel((0, 0)), (12, 93, 211))

    def test_invalid_parameters_are_rejected(self):
        source = Image.new("RGB", (3, 3))
        for radius in (-1, 20.01, np.nan, np.inf, -np.inf, np.float32(np.nan), 10**400):
            with self.subTest(radius=radius):
                with self.assertRaises(ValueError):
                    gaussian_blur(source, radius)
        for radius in (True, False, np.bool_(True), np.bool_(False), "2", None, [2], np.array(2), np.array([2]), 1+2j):
            with self.subTest(radius=radius):
                with self.assertRaises(TypeError):
                    gaussian_blur(source, radius)

    def test_invalid_images_are_rejected(self):
        with self.assertRaises(TypeError):
            gaussian_blur(None)
        for mode in ("L", "RGBA", "I;16", "P"):
            with self.subTest(mode=mode):
                with self.assertRaises(ValueError):
                    gaussian_blur(Image.new(mode, (3, 3)))
        with self.assertRaises(ValueError):
            gaussian_blur(Image.new("RGB", (0, 3)))

    def test_viewer_callback_uses_full_source_despite_display_zoom(self):
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_string('''import streamlit as st
from viewer import render_viewer
from image_processing import gaussian_blur
def processor(image):
    before = image.tobytes()
    result = gaussian_blur(image, radius=2)
    st.session_state['processing_sizes'] = (image.size, result.size)
    st.session_state['source_preserved'] = image.tobytes() == before
    return result
render_viewer(processor=processor)
''', default_timeout=10).run()
        app.radio(key="viewer_mode").set_value("합성 예시").run()
        app.slider(key="viewer_zoom").set_value(2.0).run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        self.assertEqual(app.session_state["processing_sizes"], ((640, 480), (640, 480)))
        self.assertTrue(app.session_state["source_preserved"])


if __name__ == "__main__":
    unittest.main()
