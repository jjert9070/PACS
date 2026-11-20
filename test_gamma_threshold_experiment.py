"""Verify the gamma/threshold experiment's definitions rather than snapshotting outputs."""

import csv
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from experiments import gamma_threshold_experiment as experiment
from image_processing import gamma_correction


class ChartTests(unittest.TestCase):
    def test_chart_layout_and_ground_truth(self):
        chart = experiment.chart()
        self.assertEqual(chart.shape, (256, 256))
        self.assertEqual(chart[0].tolist(), list(range(256)))           # ramp row = column index
        self.assertEqual(chart[60, 30], 75)                             # dark patch
        self.assertEqual(chart[60, 60], experiment.BACKGROUND)
        self.assertEqual(chart[60, 210], 225)                           # bright patch
        self.assertEqual(chart[45, 185], 210)                           # bright block
        self.assertEqual(chart[140, 90], experiment.OBJECT)             # circle centre
        truth = experiment.ground_truth()
        self.assertEqual(truth.shape, (224, 256))
        self.assertTrue(truth[140 - 32, 90])
        self.assertFalse(truth[60 - 32, 30])                            # 75 < 128 is not a structure

    def test_exposure_is_monotonic_and_bounded(self):
        ramp = np.arange(256, dtype=np.uint8)[None, :]
        under, over = experiment.exposure(ramp, "under"), experiment.exposure(ramp, "over")
        self.assertEqual((under[0, 0], under[0, 255]), (0, 115))
        self.assertEqual((over[0, 0], over[0, 255]), (140, 255))
        self.assertTrue(np.all(np.diff(under[0].astype(int)) >= 0))
        self.assertTrue(np.all(np.diff(over[0].astype(int)) >= 0))

    def test_inputs_are_grayscale_rgb_and_noise_is_seeded(self):
        first, second = experiment.inputs(), experiment.inputs()
        for name, image in first.items():
            with self.subTest(name=name):
                pixels = np.asarray(image)
                self.assertEqual((image.mode, image.size), ("RGB", (256, 256)))
                self.assertTrue(np.array_equal(pixels[..., 0], pixels[..., 1]))
                self.assertEqual(image.tobytes(), second[name].tobytes())


class MetricTests(unittest.TestCase):
    def test_segmentation_scores(self):
        truth = np.array([[True, True, False, False]])
        scores = experiment.segmentation_scores(np.array([[True, False, True, False]]), truth)
        self.assertAlmostEqual(scores["iou"], 1 / 3)
        self.assertAlmostEqual(scores["false_positive_rate"], 0.5)
        self.assertAlmostEqual(scores["false_negative_rate"], 0.5)
        self.assertEqual(experiment.segmentation_scores(truth, truth)["iou"], 1.0)

    def test_distinct_levels_counts_merged_codes(self):
        ramp = experiment.brightness(experiment.to_rgb(experiment.chart()))
        self.assertEqual(experiment.distinct_levels(ramp), 256)
        darkened = experiment.brightness(gamma_correction(experiment.to_rgb(experiment.chart()), 2.2))
        self.assertLess(experiment.distinct_levels(darkened, slice(0, 64)), 64)   # dark codes merge

    def test_otsu_reference_adapts_to_exposure(self):
        images = experiment.inputs()
        normal = experiment.otsu_threshold(images["chart_normal"])
        under = experiment.otsu_threshold(images["chart_under"])
        over = experiment.otsu_threshold(images["chart_over"])
        self.assertTrue(under < normal < over)


class RunTests(unittest.TestCase):
    def test_run_writes_reproducible_results(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            gamma_rows, threshold_rows = experiment.run(Path(first))
            experiment.run(Path(second))
            names = len(experiment.inputs())
            self.assertEqual(len(gamma_rows), names * len(experiment.GAMMAS))
            self.assertEqual(len(threshold_rows), names * (len(experiment.THRESHOLDS) + 1))
            for folder in ("gamma", "threshold"):
                with self.subTest(folder=folder):
                    one, two = Path(first) / folder, Path(second) / folder
                    for name in ("metrics.csv", "summary.md", "comparison.png"):
                        self.assertEqual((one / name).read_bytes(), (two / name).read_bytes())
                    manifest = json.loads((one / "manifest.json").read_text(encoding="utf-8"))
                    self.assertIn("comparison.png", manifest["file_sha256"])
            gamma = list(csv.DictReader((Path(first) / "gamma" / "metrics.csv").read_text(encoding="utf-8").splitlines()))
            identity = [row for row in gamma if row["gamma"] == "1.0"]
            for row in identity:
                self.assertAlmostEqual(float(row["input_mean"]), float(row["result_mean"]))
            threshold = list(csv.DictReader((Path(first) / "threshold" / "metrics.csv").read_text(encoding="utf-8").splitlines()))
            normal_128 = next(r for r in threshold if r["input"] == "chart_normal" and r["threshold"] == "128")
            self.assertEqual(float(normal_128["iou"]), 1.0)


if __name__ == "__main__":
    unittest.main()
