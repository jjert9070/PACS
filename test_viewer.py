from io import BytesIO
import struct
import unittest
from unittest.mock import patch
import zlib

from PIL import Image
from streamlit.testing.v1 import AppTest

from viewer import ViewerInputError, load_image, sample_image_bytes, viewport_box


def encoded_image(file_format="PNG", size=(24, 16), mode="RGB"):
    stream = BytesIO()
    Image.new(mode, size).save(stream, format=file_format)
    return stream.getvalue()


def color_png(bit_depth, color_type):
    """Encode actual RGB/RGBA samples with valid PNG chunks and CRCs.

    Pillow cannot encode 16-bit color PNG; changing only an 8-bit PNG header
    would instead produce a corrupt file and would not reproduce this bug.
    """
    channels = {2: 3, 6: 4}[color_type]

    def chunk(kind, payload):
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload)))

    header = struct.pack(">IIBBBBB", 1, 1, bit_depth, color_type, 0, 0, 0)
    sample = b"\x12\x34" if bit_depth == 16 else b"\x12"
    pixels = b"\x00" + sample * channels  # One scanline, filter type 0.
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


class ImageInputTests(unittest.TestCase):
    def test_rejects_real_16_bit_color_png_despite_pillow_mode(self):
        for color_type, mode in [(2, "RGB"), (6, "RGBA")]:
            with self.subTest(mode=mode):
                data = color_png(16, color_type)
                self.assertEqual(data[24], 16)
                with Image.open(BytesIO(data)) as opened:
                    self.assertEqual(opened.mode, mode)
                    opened.load()
                    # Confirms Pillow silently loses the low byte before validation.
                    self.assertEqual(opened.getpixel((0, 0)), (0x12,) * len(mode))
                with self.assertRaisesRegex(ViewerInputError, "16비트"):
                    load_image("color16.png", data)

    def test_accepts_8_bit_rgb_and_rgba_png(self):
        for color_type in (2, 6):
            with self.subTest(color_type=color_type):
                source = load_image("color8.png", color_png(8, color_type))
                self.assertEqual(source.image.getpixel((0, 0)), (0x12,) * 3)

    def test_high_bit_png_is_rejected_before_pillow_opens_it(self):
        with patch("viewer.Image.open") as open_image:
            with self.assertRaisesRegex(ViewerInputError, "16비트"):
                load_image("color16.png", color_png(16, 2))
        open_image.assert_not_called()

    def test_rejects_truncated_or_invalid_ihdr(self):
        data = color_png(8, 2)
        invalid_headers = (data[:24], data[:32],
                           data[:8] + b"\x00\x00\x00\x0c" + data[12:],
                           data[:12] + b"IDAT" + data[16:])
        for invalid in invalid_headers:
            with self.subTest(header=invalid[:16]), self.assertRaises(ViewerInputError):
                load_image("broken.png", invalid)

    def test_existing_low_bit_png_modes_still_open(self):
        for mode in ("1", "L", "P"):
            with self.subTest(mode=mode):
                source = load_image("lowbit.png", encoded_image(mode=mode))
                self.assertEqual(source.image.size, (24, 16))

    def test_png_and_jpeg_decode_and_source_identity(self):
        for name, fmt in [("a.PNG", "PNG"), ("a.jpeg", "JPEG")]:
            data = encoded_image(fmt)
            source = load_image(name, data)
            self.assertEqual(source.image.size, (24, 16))
            self.assertEqual(source.image.mode, "RGB")
            self.assertEqual(source.file_format, fmt)
            self.assertEqual(source.byte_count, len(data))
            self.assertEqual(source.source_id, load_image(name, data).source_id)

    def test_rejects_corrupt_unsupported_empty_and_disguised_files(self):
        for name, data in [("a.png", b"broken"), ("a.png", b""),
                           ("a.dcm", b"DICM"), ("a.jpg", encoded_image("PNG")),
                           ("a.png", encoded_image(mode="I;16"))]:
            with self.subTest(name=name), self.assertRaises(ViewerInputError):
                load_image(name, data)

    def test_limits_apply_before_full_decode(self):
        with patch("viewer.MAX_FILE_BYTES", 5), self.assertRaises(ViewerInputError):
            load_image("a.png", encoded_image())
        with patch("viewer.MAX_PIXELS", 10), self.assertRaises(ViewerInputError):
            load_image("a.png", encoded_image())

    def test_exif_rotation_is_applied(self):
        image = Image.new("RGB", (24, 16))
        exif = Image.Exif()
        exif[274] = 6
        stream = BytesIO()
        image.save(stream, format="JPEG", exif=exif)
        self.assertEqual(load_image("rotated.jpg", stream.getvalue()).image.size, (16, 24))

    def test_zoom_and_pan_bounds(self):
        self.assertEqual(viewport_box((640, 480), 1, 0, 100), (0, 0, 640, 480))
        self.assertEqual(viewport_box((640, 480), 2, 50, 50), (160, 120, 480, 360))
        self.assertEqual(viewport_box((640, 480), 4, 100, 100), (480, 360, 640, 480))
        with self.assertRaises(ValueError):
            viewport_box((640, 480), 0, 50, 50)


class ViewerScreenTests(unittest.TestCase):
    def viewer_app(self, script="from viewer import render_viewer\nrender_viewer()"):
        return AppTest.from_string(script, default_timeout=10).run()

    def test_sample_zoom_reset_and_clear_when_source_removed(self):
        app = self.viewer_app()
        self.assertFalse(app.exception)
        self.assertTrue(any("영상을 선택" in item.value for item in app.info))
        app.radio(key="viewer_mode").set_value("합성 예시").run()
        self.assertFalse(app.exception)
        self.assertFalse(app.warning)
        app.slider(key="viewer_zoom").set_value(2.0).run()
        self.assertTrue(any("x=[160, 480)" in item.value for item in app.caption))
        app.button[0].click().run()
        self.assertEqual(app.slider(key="viewer_zoom").value, 1.0)
        app.slider(key="viewer_zoom").set_value(3.0).run()
        app.radio(key="viewer_mode").set_value("파일 가져오기").run()
        self.assertIsNone(app.session_state["viewer_identity"])
        app.radio(key="viewer_mode").set_value("합성 예시").run()
        self.assertEqual(app.slider(key="viewer_zoom").value, 1.0)
        self.assertFalse(app.exception)

    def test_processor_receives_copy_and_wrong_size_is_not_displayed(self):
        original = load_image("sample.png", sample_image_bytes())
        app = self.viewer_app('''from viewer import render_viewer
def processor(image):
    image.paste((255, 0, 0), (0, 0, image.width, image.height))
    return image
source = render_viewer(processor)
if source:
    import streamlit as st
    st.session_state['test_pixel'] = source.image.getpixel((0, 0))
''')
        app.radio(key="viewer_mode").set_value("합성 예시").run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        self.assertEqual(app.session_state["test_pixel"], original.image.getpixel((0, 0)))
        app = self.viewer_app('''from PIL import Image
from viewer import render_viewer
render_viewer(lambda image: Image.new('RGB', (1, 1)))''')
        app.radio(key="viewer_mode").set_value("합성 예시").run()
        self.assertFalse(app.exception)
        self.assertTrue(any("원본은 유지" in item.value for item in app.error))


if __name__ == "__main__":
    unittest.main()
