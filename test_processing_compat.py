import json
import unittest

from PIL import Image
from streamlit.testing.v1 import AppTest

from case_records import ProcessingRecord
from gaussian_ui import PREFIX
from processing_compat import (
    ENVIRONMENT_SCHEMA,
    check_compatibility,
    library_versions,
    processing_environment,
)
from viewer import load_image, sample_image_bytes


def environment(**overrides):
    libraries = {name: version for name, version in library_versions().items()}
    libraries.update(overrides)
    return {"schema": ENVIRONMENT_SCHEMA, "libraries": libraries}


class EnvironmentTests(unittest.TestCase):
    def test_lists_only_libraries_each_algorithm_uses(self):
        current = library_versions()
        self.assertEqual(processing_environment("gaussian_blur")["libraries"],
                         {"numpy": current["numpy"], "pillow": current["pillow"], "opencv": current["opencv"]})
        for algorithm_id in ("gamma_correction", "threshold_segmentation"):
            with self.subTest(algorithm_id=algorithm_id):
                self.assertEqual(set(processing_environment(algorithm_id)["libraries"]), {"numpy", "pillow"})

    def test_is_json_serialisable_and_rejects_unknown_algorithm(self):
        json.dumps(processing_environment("gaussian_blur"), allow_nan=False)
        with self.assertRaises(ValueError):
            processing_environment("unknown")


class CompatibilityTests(unittest.TestCase):
    def test_current_records_are_supported_with_same_environment(self):
        for algorithm_id, version, parameters in (
            ("gaussian_blur", "2.0.0", {"radius": 2.0}),
            ("gaussian_blur", "2.0.0", {"radius": 0}),
            ("gamma_correction", "1.0.0", {"gamma": 0.5}),
            ("threshold_segmentation", "1.0.0", {"threshold": 128}),
        ):
            with self.subTest(algorithm_id=algorithm_id, parameters=parameters):
                check = check_compatibility(algorithm_id, version, parameters,
                                            processing_environment(algorithm_id))
                self.assertEqual(check.status, "supported")
                self.assertTrue(check.can_restore_settings and check.can_rerun and check.same_conditions)
                self.assertEqual((check.reasons, check.environment_status), ((), "same"))

    def test_unsupported_version_is_never_mapped_to_current(self):
        # 1.0.0 was the earlier Pillow-based Gaussian: different computation.
        check = check_compatibility("gaussian_blur", "1.0.0", {"radius": 2.0}, environment())
        self.assertEqual(check.status, "unsupported_version")
        self.assertFalse(check.can_restore_settings or check.can_rerun or check.same_conditions)
        self.assertIn("1.0.0", check.reasons[0])
        self.assertIn("2.0.0", check.reasons[0])
        for version in ("", None, 2, "2.0"):
            with self.subTest(version=version):
                self.assertEqual(check_compatibility("gaussian_blur", version, {"radius": 2.0}).status,
                                 "unsupported_version")

    def test_unknown_algorithm(self):
        for algorithm_id in ("median_blur", "", None):
            with self.subTest(algorithm_id=algorithm_id):
                check = check_compatibility(algorithm_id, "1.0.0", {})
                self.assertEqual(check.status, "unknown_algorithm")
                self.assertEqual(check.environment_status, "not_checked")
                self.assertFalse(check.can_restore_settings)

    def test_invalid_stored_parameters(self):
        cases = (
            ("gaussian_blur", "2.0.0", {"radius": 20.5}),
            ("gaussian_blur", "2.0.0", {"radius": -1}),
            ("gaussian_blur", "2.0.0", {"radius": 10 ** 400}),
            ("gaussian_blur", "2.0.0", {"radius": float("nan")}),
            ("gaussian_blur", "2.0.0", {"radius": True}),
            ("gaussian_blur", "2.0.0", {"radius": "2"}),
            ("gaussian_blur", "2.0.0", {}),
            ("gaussian_blur", "2.0.0", {"radius": 2.0, "gamma": 1.0}),
            ("gaussian_blur", "2.0.0", None),
            ("gamma_correction", "1.0.0", {"gamma": 0.05}),
            ("gamma_correction", "1.0.0", {"radius": 1.0}),
            ("threshold_segmentation", "1.0.0", {"threshold": 128.0}),
            ("threshold_segmentation", "1.0.0", {"threshold": 256}),
            ("threshold_segmentation", "1.0.0", {"threshold": False}),
        )
        for algorithm_id, version, parameters in cases:
            with self.subTest(algorithm_id=algorithm_id, parameters=parameters):
                check = check_compatibility(algorithm_id, version, parameters, environment())
                self.assertEqual(check.status, "invalid_parameters")
                self.assertFalse(check.can_restore_settings)
                self.assertEqual(len(check.reasons), 1)

    def test_missing_environment_keeps_old_records_readable(self):
        for stored in (None, {}, {"schema": ENVIRONMENT_SCHEMA}, {"libraries": {}}, "numpy 2", []):
            with self.subTest(stored=stored):
                check = check_compatibility("gaussian_blur", "2.0.0", {"radius": 2.0}, stored)
                self.assertEqual(check.status, "supported")
                self.assertTrue(check.can_restore_settings)
                self.assertFalse(check.same_conditions)
                self.assertEqual(check.environment_status, "missing")
                self.assertIn("환경 정보 없음", check.environment_notes[0])

    def test_changed_environment_names_each_difference(self):
        check = check_compatibility("gaussian_blur", "2.0.0", {"radius": 2.0},
                                    environment(opencv="4.11.0", numpy="1.26.4"))
        self.assertEqual(check.environment_status, "changed")
        self.assertTrue(check.can_rerun)
        self.assertFalse(check.same_conditions)
        self.assertIn("재현 조건이 달라졌습니다", check.environment_notes[0])
        text = "\n".join(check.environment_notes)
        self.assertIn("opencv: 저장 당시 4.11.0", text)
        self.assertIn("numpy: 저장 당시 1.26.4", text)
        self.assertNotIn("pillow:", text)

    def test_missing_library_entry_and_unused_library_difference(self):
        stored = environment()
        del stored["libraries"]["opencv"]
        check = check_compatibility("gaussian_blur", "2.0.0", {"radius": 2.0}, stored)
        self.assertEqual(check.environment_status, "changed")
        self.assertIn("opencv: 저장 당시 버전 기록 없음", "\n".join(check.environment_notes))
        # Gamma does not use OpenCV, so an OpenCV difference does not change its conditions.
        check = check_compatibility("gamma_correction", "1.0.0", {"gamma": 2.0}, environment(opencv="0.0.1"))
        self.assertEqual(check.environment_status, "same")


SCRIPT = '''import streamlit as st
from viewer import render_viewer
from gaussian_ui import process_gaussian, render_gaussian_controls
render_viewer(processor=process_gaussian, processing_controls=render_gaussian_controls,
              stored_source=st.session_state.get('test_stored_source'))
'''


class ProcessingCompatibilityWebTests(unittest.TestCase):
    def stored_app(self):
        source = load_image("stored.png", sample_image_bytes())
        app = AppTest.from_string(SCRIPT, default_timeout=15)
        app.session_state["test_stored_source"] = source
        app.run()
        app.radio(key="viewer_mode").set_value("로컬 저장 영상").run()
        return app, source

    def restore(self, app, source, algorithm_id, version, parameters, environment=None, via_record=False):
        restored = {"source_id": source.source_id, "algorithm_id": algorithm_id, "parameters": parameters}
        if via_record:
            # C's current restore dict has no version; B reads it from C's stored record.
            app.session_state["viewer_stored_processing"] = ProcessingRecord(
                source.source_id, algorithm_id, version, parameters, Image.new("RGB", source.image.size))
        else:
            restored["algorithm_version"] = version
            if environment is not None:
                restored["environment"] = environment
        app.session_state["viewer_restore_controls"] = restored
        app.run()

    def test_applied_result_keeps_environment_without_roi(self):
        app = AppTest.from_string(SCRIPT, default_timeout=15).run()
        app.radio(key="viewer_mode").set_value("합성 예시").run()
        for label, key, value, algorithm_id in (
            ("가우시안", "radius", 2.0, "gaussian_blur"),
            ("감마 보정", "gamma", 0.5, "gamma_correction"),
            ("임계값 분할", "threshold", 128, "threshold_segmentation"),
        ):
            with self.subTest(algorithm=label):
                app.selectbox(key=PREFIX + "algorithm").set_value(label).run()
                app.number_input(key=PREFIX + key).set_value(value).run()
                app.button(key=PREFIX + "apply").click().run()
                applied = app.session_state[PREFIX + "applied"]
                self.assertNotIn("viewer_roi_bounds", app.session_state)
                self.assertEqual(applied["environment"], processing_environment(algorithm_id))

    def test_unsupported_version_is_not_restored_and_reason_is_shown(self):
        for via_record in (False, True):
            with self.subTest(via_record=via_record):
                app, source = self.stored_app()
                self.restore(app, source, "gaussian_blur", "1.0.0", {"radius": 3.0}, via_record=via_record)
                warning = " ".join(item.value for item in app.warning)
                self.assertIn("새 설정을 선택하세요", warning)
                self.assertIn("1.0.0", warning)
                self.assertIn("2.0.0", warning)
                self.assertEqual(app.number_input(key=PREFIX + "radius").value, 1.0)
                self.assertNotIn(PREFIX + "result", app.session_state)
                self.assertNotIn(PREFIX + "applied", app.session_state)

    def test_supported_record_restores_controls_without_running(self):
        app, source = self.stored_app()
        self.restore(app, source, "threshold_segmentation", "1.0.0", {"threshold": 200},
                     environment=processing_environment("threshold_segmentation"))
        self.assertEqual(app.selectbox(key=PREFIX + "algorithm").value, "임계값 분할")
        self.assertEqual(app.number_input(key=PREFIX + "threshold").value, 200)
        self.assertNotIn(PREFIX + "result", app.session_state)
        self.assertFalse(app.warning)

    def test_old_record_without_environment_is_labelled(self):
        app, source = self.stored_app()
        self.restore(app, source, "gamma_correction", "1.0.0", {"gamma": 0.5})
        self.assertEqual(app.number_input(key=PREFIX + "gamma").value, 0.5)
        self.assertTrue(any("환경 정보 없음" in item.value for item in app.caption))

    def test_changed_environment_is_explained_and_cleared_by_new_apply(self):
        app, source = self.stored_app()
        self.restore(app, source, "gaussian_blur", "2.0.0", {"radius": 2.0},
                     environment=environment(opencv="4.11.0"))
        self.assertEqual(app.number_input(key=PREFIX + "radius").value, 2.0)
        notice = " ".join(item.value for item in app.info)
        self.assertIn("재현 조건이 달라졌습니다", notice)
        self.assertIn("opencv: 저장 당시 4.11.0", notice)
        app.button(key=PREFIX + "apply").click().run()
        self.assertNotIn(PREFIX + "stored_check", app.session_state)
        self.assertFalse(any("재현 조건이 달라졌습니다" in item.value for item in app.info))
        self.assertIn("environment", app.session_state[PREFIX + "applied"])


if __name__ == "__main__":
    unittest.main()
