"""P03 processing-record compatibility (pure functions; no Streamlit, Firebase or UI).

The screen wiring tests that used gaussian_ui.py are handed to A with the UI patch
(docs/handoff/b-p03-gaussian-ui.patch) because gaussian_ui.py is A's file in round 2.
"""

import copy
import json
import unittest

from processing_compat import (
    ENVIRONMENT_SCHEMA,
    check_compatibility,
    library_versions,
    processing_environment,
)


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
        stored = json.loads(json.dumps(processing_environment("gaussian_blur"), allow_nan=False))
        self.assertEqual(stored["schema"], "b_processing_environment_v1")
        with self.assertRaises(ValueError):
            processing_environment("unknown")

    def test_environment_does_not_depend_on_roi(self):
        # The environment belongs to the applied result; no ROI argument exists to lose it.
        first = processing_environment("gamma_correction")
        second = processing_environment("gamma_correction")
        self.assertEqual(first, second)
        self.assertIsNot(first, second)
        self.assertIsNot(first["libraries"], second["libraries"])


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
            ("gaussian_blur", "2.0.0", {"radius": float("inf")}),
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
                self.assertFalse(check.can_restore_settings or check.can_rerun)
                self.assertEqual(len(check.reasons), 1)

    def test_missing_environment_keeps_old_records_readable(self):
        for stored in (None, {}, {"schema": ENVIRONMENT_SCHEMA},
                       {"schema": ENVIRONMENT_SCHEMA, "libraries": {}}):
            with self.subTest(stored=stored):
                check = check_compatibility("gaussian_blur", "2.0.0", {"radius": 2.0}, stored)
                self.assertEqual(check.status, "supported")
                self.assertTrue(check.can_restore_settings and check.can_rerun)
                self.assertFalse(check.same_conditions)
                self.assertEqual(check.environment_status, "missing")
                self.assertIn("환경 정보 없음", check.environment_notes[0])

    def test_unknown_or_absent_schema_never_claims_same_conditions(self):
        libraries = environment()["libraries"]
        for stored in ({"libraries": dict(libraries)},
                       {"schema": "other_environment_v9", "libraries": dict(libraries)},
                       {"schema": None, "libraries": dict(libraries)},
                       "numpy 2", [], 3):
            with self.subTest(stored=stored):
                check = check_compatibility("gaussian_blur", "2.0.0", {"radius": 2.0}, stored)
                self.assertEqual(check.status, "supported")
                self.assertTrue(check.can_restore_settings and check.can_rerun)
                self.assertFalse(check.same_conditions)
                self.assertEqual(check.environment_status, "not_checked")
                self.assertIn("확인하지 않았습니다", check.environment_notes[0])

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

    def test_status_fields_keep_separate_meanings(self):
        # Supported settings with a changed environment: may re-run, but not under the same conditions.
        check = check_compatibility("gamma_correction", "1.0.0", {"gamma": 2.0}, environment(numpy="0.0.1"))
        self.assertEqual((check.status, check.environment_status), ("supported", "changed"))
        self.assertEqual((check.can_restore_settings, check.can_rerun, check.same_conditions), (True, True, False))

    def test_inputs_are_not_modified(self):
        parameters = {"radius": 2.0}
        stored = environment(opencv="4.11.0")
        before = (copy.deepcopy(parameters), copy.deepcopy(stored))
        check_compatibility("gaussian_blur", "2.0.0", parameters, stored)
        check_compatibility("gaussian_blur", "1.0.0", parameters, stored)
        self.assertEqual((parameters, stored), before)


if __name__ == "__main__":
    unittest.main()
