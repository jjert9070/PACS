import unittest
from unittest.mock import patch

from PIL import Image
from streamlit.testing.v1 import AppTest

from gaussian_ui import PREFIX
from image_processing import gaussian_blur
from viewer import load_image, sample_image_bytes


SCRIPT = '''import streamlit as st
from viewer import render_viewer, clear_viewer
from gaussian_ui import process_gaussian, render_gaussian_controls
if st.button('테스트 로그아웃'):
    clear_viewer()
source = render_viewer(processor=process_gaussian, processing_controls=render_gaussian_controls,
                       stored_source=st.session_state.get('test_stored_source'))
if source:
    st.session_state['test_original'] = source.image.tobytes()
'''


class GaussianWebTests(unittest.TestCase):
    def app(self):
        app = AppTest.from_string(SCRIPT, default_timeout=15).run()
        app.radio(key="viewer_mode").set_value("합성 예시").run()
        return app

    def apply(self, app, radius):
        app.number_input(key=PREFIX + "radius").set_value(radius).run()
        app.button(key=PREFIX + "apply").click().run()

    def test_apply_preserves_source_and_shows_actual_metadata(self):
        app = self.app()
        self.assertNotIn(PREFIX + "result", app.session_state)
        self.apply(app, 2.0)
        source = load_image("sample.png", sample_image_bytes())
        self.assertEqual(app.session_state["test_original"], source.image.tobytes())
        expected = gaussian_blur(source.image, 2)
        self.assertEqual(app.session_state[PREFIX + "result"].tobytes(), expected.tobytes())
        self.assertTrue(any("실제 적용 σ=2 px" in text.value for text in app.caption))
        self.assertFalse(app.error)
        self.assertFalse(app.exception)

    def test_zero_and_upper_bound(self):
        app = self.app()
        self.apply(app, 0.0)
        self.assertEqual(app.session_state[PREFIX + "result"].tobytes(), app.session_state["test_original"])
        self.apply(app, 20.0)
        self.assertEqual(app.session_state[PREFIX + "applied"]["radius"], 20)
        self.assertFalse(app.error)

    def test_algorithm_selection_hides_settings_and_discards_result(self):
        app = self.app()
        self.apply(app, 2.0)
        app.selectbox(key=PREFIX + "algorithm").set_value("선택 안 함").run()
        self.assertEqual(len(app.number_input), 0)
        self.assertNotIn(PREFIX + "result", app.session_state)
        self.assertTrue(any("처리 알고리즘을 선택" in text.value for text in app.info))
        app.selectbox(key=PREFIX + "algorithm").set_value("가우시안").run()
        self.assertEqual(app.number_input(key=PREFIX + "radius").value, 1)
        self.assertNotIn(PREFIX + "result", app.session_state)

    def test_draft_and_view_changes_do_not_reprocess_or_relabel(self):
        app = self.app()
        with patch("gaussian_ui.gaussian_blur", wraps=gaussian_blur) as processing:
            self.apply(app, 2.0)
            app.number_input(key=PREFIX + "radius").set_value(3.0).run()
            app.slider(key="viewer_zoom").set_value(2.0).run()
            app.slider(key="viewer_x").set_value(75).run()
            app.slider(key="viewer_y").set_value(25).run()
            next(button for button in app.button if button.label == "보기 초기화").click().run()
            self.assertEqual(processing.call_count, 1)
        self.assertEqual(app.slider(key="viewer_zoom").value, 1)
        self.assertEqual(app.session_state[PREFIX + "applied"]["radius"], 2)
        self.assertTrue(any("설정이 변경" in text.value for text in app.info))

    def test_failure_discards_old_result_and_can_retry(self):
        app = self.app()
        self.apply(app, 2.0)
        with patch("gaussian_ui.gaussian_blur", side_effect=RuntimeError("test failure")):
            self.apply(app, 3.0)
        self.assertTrue(app.error)
        self.assertNotIn(PREFIX + "result", app.session_state)
        self.assertNotIn(PREFIX + "applied", app.session_state)
        app.slider(key="viewer_zoom").set_value(2.0).run()
        self.assertTrue(app.error)
        self.apply(app, 1.0)
        self.assertFalse(app.error)

    def test_wrong_result_contract_is_failure(self):
        for result in (Image.new("RGB", (1, 1)), Image.new("L", (640, 480)), None):
            with self.subTest(result=result):
                app = self.app()
                self.apply(app, 2.0)
                with patch("gaussian_ui.gaussian_blur", return_value=result):
                    self.apply(app, 1.0)
                self.assertTrue(app.error)
                self.assertNotIn(PREFIX + "result", app.session_state)

    def test_source_removal_and_logout_clear_processing(self):
        app = self.app()
        self.apply(app, 2.0)
        app.radio(key="viewer_mode").set_value("파일 가져오기").run()
        self.assertNotIn(PREFIX + "result", app.session_state)
        app.radio(key="viewer_mode").set_value("합성 예시").run()
        self.assertEqual(app.number_input(key=PREFIX + "radius").value, 1)
        self.apply(app, 2.0)
        next(button for button in app.button if button.label == "테스트 로그아웃").click().run()
        self.assertNotIn(PREFIX + "result", app.session_state)

    def test_same_filename_different_bytes_clears_result(self):
        from io import BytesIO

        app = self.app()
        first = load_image("same.png", sample_image_bytes())
        app.session_state["test_stored_source"] = first
        app.radio(key="viewer_mode").set_value("로컬 저장 영상").run()
        self.apply(app, 2.0)
        stream = BytesIO()
        Image.new("RGB", (640, 480), (20, 50, 90)).save(stream, format="PNG")
        second = load_image("same.png", stream.getvalue())
        app.session_state["test_stored_source"] = second
        app.run()
        self.assertNotEqual(first.source_id, second.source_id)
        self.assertNotIn(PREFIX + "result", app.session_state)
        self.assertEqual(app.session_state["test_original"], second.image.tobytes())


STORED_SCRIPT = '''import streamlit as st
from viewer import render_viewer
from gaussian_ui import process_gaussian, render_gaussian_controls
render_viewer(processor=process_gaussian, processing_controls=render_gaussian_controls,
              stored_source=st.session_state['test_stored_source'],
              stored_result=st.session_state['test_stored_result'])
'''
STORED_NOTICE = "이 케이스에 저장된 처리 결과입니다"


def column_items(app, title):
    """Child kinds of the viewer column headed by `title`, in screen order."""
    for column in app.columns:
        children = list(column.children.values())
        if children and getattr(children[0], "value", None) == title:
            kinds = []
            for child in children[1:]:
                nested = [getattr(item, "type", "") for item in getattr(child, "children", {}).values()]
                kinds.append("imgs" if child.type == "imgs" or "imgs" in nested else child.type)
            return kinds
    raise AssertionError(f"column {title!r} not found")


class GaussianReviewFollowUpTests(unittest.TestCase):
    """PR #6 review: logged failure cause and stored-result states."""

    def stored_app(self):
        source = load_image("stored.png", sample_image_bytes())
        # A visibly different saved result so it cannot be confused with a new one.
        stored_result = Image.new("RGB", source.image.size, (255, 0, 255))
        app = AppTest.from_string(STORED_SCRIPT, default_timeout=15)
        app.session_state["test_stored_source"] = source
        app.session_state["test_stored_result"] = stored_result
        app.run()
        app.radio(key="viewer_mode").set_value("로컬 저장 영상").run()
        return app, source, stored_result

    def apply(self, app, radius):
        app.number_input(key=PREFIX + "radius").set_value(radius).run()
        app.button(key=PREFIX + "apply").click().run()

    def test_failure_cause_is_logged_and_screen_stays_generic(self):
        app = AppTest.from_string(SCRIPT, default_timeout=15).run()
        app.radio(key="viewer_mode").set_value("합성 예시").run()
        with self.assertLogs("gaussian_ui", level="ERROR") as logs:
            with patch("gaussian_ui.gaussian_blur", side_effect=RuntimeError("test failure")):
                self.apply(app, 3.0)
        record = logs.records[0]
        self.assertIn("Gaussian processing failed", record.getMessage())
        self.assertIn("radius=3.0", record.getMessage())
        self.assertIsNotNone(record.exc_info)
        self.assertIsInstance(record.exc_info[1], RuntimeError)
        self.assertEqual(str(record.exc_info[1]), "test failure")
        self.assertTrue(app.error)
        self.assertNotIn("test failure", " ".join(error.value for error in app.error))
        self.assertNotIn(PREFIX + "result", app.session_state)

    def test_stored_result_before_apply_is_labelled_as_stored(self):
        app, _, _ = self.stored_app()
        self.assertTrue(any(STORED_NOTICE in text.value for text in app.caption))
        self.assertFalse(any("실제 적용" in text.value for text in app.caption))
        self.assertTrue(any("가우시안 적용을 누르세요" in text.value for text in app.info))
        self.assertEqual(column_items(app, "**처리 결과**")[0], "imgs")
        self.assertNotIn(PREFIX + "result", app.session_state)
        self.assertFalse(app.error)

    def test_successful_apply_replaces_stored_result_with_new_settings(self):
        app, source, stored_result = self.stored_app()
        self.apply(app, 2.0)
        result = app.session_state[PREFIX + "result"]
        self.assertEqual(result.tobytes(), gaussian_blur(source.image, 2).tobytes())
        self.assertNotEqual(result.tobytes(), stored_result.tobytes())
        self.assertFalse(any(STORED_NOTICE in text.value for text in app.caption))
        self.assertTrue(any("실제 적용 σ=2 px" in text.value for text in app.caption))
        self.assertEqual(app.session_state[PREFIX + "applied"]["source_id"], source.source_id)
        self.assertFalse(app.error)

    def test_failed_apply_does_not_present_stored_result_as_success(self):
        app, source, _ = self.stored_app()
        with patch("gaussian_ui.gaussian_blur", side_effect=RuntimeError("test failure")):
            self.apply(app, 2.0)
        self.assertTrue(app.error)
        self.assertFalse(any(STORED_NOTICE in text.value for text in app.caption))
        self.assertFalse(any("실제 적용" in text.value for text in app.caption))
        self.assertNotIn("imgs", column_items(app, "**처리 결과**"))
        self.assertEqual(column_items(app, "**원본 영상**")[0], "imgs")
        self.assertNotIn(PREFIX + "result", app.session_state)
        self.assertEqual(app.session_state["test_stored_source"].image.tobytes(), source.image.tobytes())


if __name__ == "__main__":
    unittest.main()
