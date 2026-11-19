import csv
import json
from pathlib import Path
import tempfile
import unittest

from experiments.roi_experiment import REGIONS, run_experiment


class ROIExperimentTests(unittest.TestCase):
    def test_experiment_records_same_coordinates_and_noise_structure_tradeoff(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            rows = run_experiment(output)
            self.assertEqual(len(rows), 48)
            for row in rows:
                bounds = tuple(row[key] for key in ("left", "top", "right", "bottom"))
                self.assertEqual(bounds, REGIONS[row["region"]])
                self.assertEqual(row["pixel_count"], row["width"] * row["height"])
                if row["sigma_px"] == 0:
                    self.assertEqual(row["input_mean"], row["result_mean"])
                    self.assertEqual(row["input_population_variance"], row["result_population_variance"])
            def measured(region, noise, sigma):
                return next(row for row in rows if
                            (row["region"], row["noise_sigma"], row["sigma_px"]) == (region, noise, sigma))
            clean = measured("flat", 0, 0)
            self.assertEqual(clean["result_mean"], 80)
            self.assertEqual(clean["result_population_variance"], 0)
            self.assertLess(measured("flat", 20, 1)["rmse_l_to_clean"],
                            measured("flat", 20, 0)["rmse_l_to_clean"])
            self.assertGreater(measured("edge", 0, 4)["rmse_l_to_clean"], 0)
            with (output / "metrics.csv").open(encoding="utf-8", newline="") as file:
                self.assertEqual(len(list(csv.DictReader(file))), 48)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["roi_calculation"]["ddof"], 0)
            self.assertEqual(manifest["generator"], "numpy.random.Generator(PCG64)")
            self.assertIn("comparison.png", manifest["file_sha256"])


if __name__ == "__main__":
    unittest.main()
