from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from streamlit.testing.v1 import AppTest

from local_storage import LocalStorageError, list_originals, open_original, save_original
from viewer import ViewerInputError, load_image, sample_image_bytes


class LocalStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.data = sample_image_bytes()

    def test_save_and_reopen_original_bytes_after_new_session(self):
        key = save_original(self.root, "user-a", "example.png", self.data)
        reopened = open_original(Path(str(self.root)), "user-a", key)
        self.assertEqual(reopened.original_bytes, self.data)
        self.assertEqual(reopened.source_id, sha256(self.data).hexdigest())
        self.assertEqual(reopened.image.size, (640, 480))
        self.assertEqual(list_originals(self.root, "user-a"), [key])

    def test_duplicate_save_does_not_replace_original(self):
        key = save_original(self.root, "user-a", "example.png", self.data)
        stored = next(self.root.rglob(key))
        original_inode = stored.stat().st_ino
        self.assertEqual(save_original(self.root, "user-a", "other.png", self.data), key)
        self.assertEqual(stored.stat().st_ino, original_inode)
        self.assertEqual(list(stored.parent.iterdir()), [stored])

    def test_corruption_blocks_open_and_is_not_overwritten(self):
        key = save_original(self.root, "user-a", "example.png", self.data)
        stored = next(self.root.rglob(key))
        stored.write_bytes(b"changed")
        with self.assertRaisesRegex(LocalStorageError, "해시"):
            open_original(self.root, "user-a", key)
        with self.assertRaises(LocalStorageError):
            save_original(self.root, "user-a", "example.png", self.data)
        self.assertEqual(stored.read_bytes(), b"changed")

    def test_users_cannot_open_each_others_keys(self):
        key = save_original(self.root, "user-a", "example.png", self.data)
        self.assertEqual(list_originals(self.root, "user-b"), [])
        with self.assertRaises(LocalStorageError):
            open_original(self.root, "user-b", key)

    def test_traversal_and_invalid_input_do_not_write(self):
        for key in ("../image.png", "a.png", "a" * 64 + ".exe"):
            with self.assertRaises(LocalStorageError):
                open_original(self.root, "user-a", key)
        with self.assertRaises(ViewerInputError):
            save_original(self.root, "user-a", "bad.png", b"broken")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_failed_publish_leaves_no_partial_original(self):
        operation = "local_storage.os.rename" if os.name == "nt" else "local_storage.os.link"
        with patch(operation, side_effect=OSError("disk failure")):
            with self.assertRaises(LocalStorageError):
                save_original(self.root, "user-a", "example.png", self.data)
        self.assertEqual(list_originals(self.root, "user-a"), [])
        self.assertFalse(any(path.is_file() for path in self.root.rglob("*")))

    def test_jpeg_keeps_original_encoding_and_orientation(self):
        image = Image.new("RGB", (24, 16))
        exif = Image.Exif()
        exif[274] = 6
        encoded = BytesIO()
        image.save(encoded, format="JPEG", exif=exif)
        data = encoded.getvalue()
        key = save_original(self.root, "user-a", "photo.jpeg", data)
        reopened = open_original(self.root, "user-a", key)
        self.assertTrue(key.endswith(".jpg"))
        self.assertEqual(reopened.original_bytes, data)
        self.assertEqual(reopened.image.size, (16, 24))


class LocalStorageScreenTests(unittest.TestCase):
    def test_save_list_and_reopen_through_viewer(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"PACS_LOCAL_STORAGE_DIR": directory}
        ):
            script = """import streamlit as st
from viewer import render_viewer
from local_storage_ui import render_local_storage
from firebase_client import Session
source = render_viewer(stored_source=st.session_state.get('viewer_stored_source'))
render_local_storage(Session('user-a','e','t',float('inf')), source)
"""
            app = AppTest.from_string(script).run()
            app.radio(key="viewer_mode").set_value("합성 예시").run()
            app.checkbox(key="viewer_local_enabled").check().run()
            next(b for b in app.button if b.label == "현재 원본을 로컬에 저장").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("이 PC에 원본" in x.value for x in app.success))
            app.button(key="viewer_local_reload").click().run()
            next(b for b in app.button if b.label == "선택 원본을 뷰어로 다시 열기").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.radio(key="viewer_mode").value, "로컬 저장 영상")
            self.assertEqual(app.session_state["viewer_stored_source"].original_bytes, sample_image_bytes())
            # A fresh app session has no cached pixels and still reads the disk file.
            fresh = AppTest.from_string(script).run()
            fresh.checkbox(key="viewer_local_enabled").check().run()
            fresh.button(key="viewer_local_reload").click().run()
            next(b for b in fresh.button if b.label == "선택 원본을 뷰어로 다시 열기").click().run()
            self.assertFalse(fresh.exception)
            self.assertEqual(fresh.session_state["viewer_stored_source"].image.size, (640, 480))

    def test_original_bytes_are_not_in_source_repr(self):
        data = sample_image_bytes()
        source = load_image("example.png", data)
        self.assertEqual(source.original_bytes, data)
        self.assertNotIn(repr(data), repr(source))


if __name__ == "__main__":
    unittest.main()
