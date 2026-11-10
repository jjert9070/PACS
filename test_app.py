
import os
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


class PACSAppTests(unittest.TestCase):

    def test_missing_firebase_settings(self):
        # Firebase 설정이 없는 경우
        with patch.dict(os.environ, {
            "PACS_FIREBASE_API_KEY": "",
            "PACS_FIREBASE_PROJECT_ID": "",
        }):
            app = AppTest.from_file(
                "app.py", default_timeout=10
            ).run()

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(
            app.title[0].value, "교육용 PACS"
        )

    def test_login_screen(self):
        # 실제 Firebase에 접속하지 않고 화면 검사
        with patch.dict(os.environ, {
            "PACS_FIREBASE_API_KEY": "test-key",
            "PACS_FIREBASE_PROJECT_ID": "test-project",
        }):
            app = AppTest.from_file(
                "app.py", default_timeout=10
            ).run()

        self.assertEqual(len(app.exception), 0)
        self.assertTrue(
            any(
                item.value == "로그인"
                for item in app.subheader
            )
        )


if __name__ == "__main__":
    unittest.main()
