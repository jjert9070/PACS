from copy import deepcopy
from dataclasses import replace
import json
import unittest

from case_processing import freeze_processing, processing_from_state
from case_records import ProcessingRecord, _processing_json, reopen_case_processing
import test_case_records as fixtures


class ProcessingEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CaseRecordTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.environment = {"schema": "b_processing_environment_v1", "libraries": {"numpy": "2.2.6", "pillow": "12.3.0", "opencv": "4.12.0"}}
        self.record = replace(self.fixture.processing_fixture(), environment=deepcopy(self.environment))

    def test_environment_roundtrip_with_and_without_roi(self):
        for roi in (True, False):
            with self.subTest(roi=roi):
                record = self.record if roi else replace(self.record, roi=None, statistics={})
                self.fixture.case_id = ("c" if roi else "d") * 32
                saved = self.fixture.save(processing=record)
                self.assertEqual(json.loads(saved.values["record_json"])["environment"], self.environment)
                _, restored = reopen_case_processing(self.fixture.root, self.fixture.session, saved)
                self.assertEqual(restored.environment, self.environment)
                self.assertEqual(restored.result.tobytes(), record.result.tobytes())

    def test_missing_or_null_legacy_environment_remains_readable(self):
        for explicit_null in (False, True):
            record = replace(self.record, environment=None)
            self.fixture.case_id = ("e" if explicit_null else "f") * 32
            saved = self.fixture.save(processing=record)
            metadata = json.loads(saved.values["record_json"])
            self.assertNotIn("environment", metadata)
            if explicit_null:
                metadata["environment"] = None
                saved = replace(saved, values={**saved.values, "record_json": json.dumps(metadata)})
            _, restored = reopen_case_processing(self.fixture.root, self.fixture.session, saved)
            self.assertIsNone(restored.environment)

    def test_freeze_detaches_nested_environment_for_retry(self):
        frozen = freeze_processing(self.fixture.source, self.record)
        self.record.environment["libraries"]["numpy"] = "changed"
        self.assertEqual(frozen.environment, self.environment)
        saved = self.fixture.save(processing=frozen)
        self.assertEqual(self.fixture.save(processing=frozen), saved)
        self.assertEqual(len(self.fixture.commits), 1)

    def test_applied_state_and_roi_clear_preserve_environment(self):
        state = {"viewer_processing_applied": {"source_id": self.record.source_id,
                 "algorithm_id": self.record.algorithm_id, "algorithm_version": self.record.algorithm_version,
                 "parameters": self.record.parameters, "environment": deepcopy(self.environment)},
                 "viewer_displayed_result": self.record.result, "viewer_processing_result": self.record.result}
        current = processing_from_state(self.fixture.source, state)
        state["viewer_processing_applied"]["environment"]["libraries"]["numpy"] = "later"
        self.assertEqual(current.environment, self.environment)
        cleared = processing_from_state(self.fixture.source, {"viewer_displayed_processing": self.record, "viewer_roi_cleared": True})
        self.assertIsNone(cleared.roi)
        self.assertEqual(cleared.environment, self.environment)

    def test_invalid_environment_is_rejected_before_any_write(self):
        for environment in ([], "value", {"numpy": float("nan")}, {"numpy": object()}):
            with self.subTest(environment=type(environment).__name__):
                with self.assertRaises(ValueError):
                    self.fixture.save(processing=replace(self.record, environment=environment))
        self.assertEqual(self.fixture.documents, {})
        self.assertFalse(list(self.fixture.root.rglob("*.png")))

    def test_old_positional_constructor_and_json_are_unchanged(self):
        old = ProcessingRecord(self.record.source_id, "test", "1", {}, self.record.result)
        self.assertIsNone(old.environment)
        self.assertNotIn("environment", _processing_json(old, self.fixture.source))
        with self.assertRaises(TypeError):
            ProcessingRecord(self.record.source_id, "test", "1", {}, self.record.result, None, {}, {})


if __name__ == "__main__":
    unittest.main()
