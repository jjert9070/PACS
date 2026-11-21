"""The committed handoff examples for A and C match what B's code returns (#38 B13)."""

import json
import unittest

from experiments import measurement_examples as examples


class HandoffExampleTests(unittest.TestCase):
    def test_committed_file_matches_the_code(self):
        committed = examples.OUTPUT.read_text(encoding="utf-8").replace("\r\n", "\n")
        self.assertEqual(committed, examples.render(),
                         "Run: uv run --locked python experiments/measurement_examples.py")

    def test_examples_cover_success_null_unit_and_every_error_code(self):
        data = json.loads(examples.render())
        statuses = {item["result"]["calibration_status"] for item in data["measure_annotation"]}
        self.assertEqual(statuses, {"verified", "unverified", "unsupported"})
        self.assertTrue(any(item["result"]["physical_value"] is None for item in data["measure_annotation"]))
        self.assertEqual({item["code"] for item in data["errors"]},
                         {"invalid_geometry", "source_mismatch", "unsupported_frame"})
        self.assertNotIn("NaN", examples.render())


if __name__ == "__main__":
    unittest.main()
