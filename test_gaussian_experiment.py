"""Verify arithmetic and color contracts rather than snapshotting whole outputs."""

import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from experiments.gaussian_experiment import (
    calculate_metrics,
    generate_noise_input,
    read_input,
    save_rgb_png,
    synthetic_reference,
)


class GaussianExperimentTests(unittest.TestCase):
    def test_rmse_uses_signed_floating_difference(self):
        black = np.zeros((2, 2, 3), dtype=np.uint8)
        white = np.full_like(black, 255)
        self.assertEqual(calculate_metrics(black, white)["rmse"], 255)
        self.assertEqual(calculate_metrics(white, black)["rmse"], 255)
        self.assertNotIn("flat_variance", calculate_metrics(black, white))

    def test_synthetic_reference_metrics(self):
        reference = synthetic_reference()
        measured = calculate_metrics(reference, reference, synthetic=True)
        self.assertEqual(measured["rmse"], 0)
        self.assertEqual(measured["flat_variance"], 0)
        self.assertEqual(measured["step_contrast"], 96)
        self.assertEqual(measured["thin_line_contrast"], 144)

    def test_seed_zero_noise_and_clipping(self):
        pixels = np.full((8, 8, 3), 127, dtype=np.uint8)
        self.assertTrue(np.array_equal(generate_noise_input(pixels, 0), pixels))
        first = generate_noise_input(pixels, 1000)
        second = generate_noise_input(pixels, 1000)
        self.assertTrue(np.array_equal(first, second))
        self.assertEqual(first.dtype, np.uint8)
        self.assertIn(0, first)
        self.assertIn(255, first)
        self.assertTrue(np.all(pixels == 127))

    def test_color_roundtrip_and_grayscale_reference(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "컬러.png"
            pixels = np.full((256, 256, 3), (240, 30, 10), dtype=np.uint8)
            save_rgb_png(path, pixels)
            color = read_input(path, "color")
            self.assertTrue(np.array_equal(color, pixels))
            self.assertTrue(np.array_equal(generate_noise_input(color, 0), color))
            gray = read_input(path, "grayscale")
            self.assertTrue(np.array_equal(gray[:, :, 0], gray[:, :, 2]))
            self.assertFalse(np.array_equal(gray, pixels))

    def test_high_bit_input_is_not_silently_downcast(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "16bit.png"
            success, encoded = cv2.imencode(".png", np.full((8, 8, 3), 65535, dtype=np.uint16))
            self.assertTrue(success)
            path.write_bytes(encoded.tobytes())
            with self.assertRaises(ValueError):
                read_input(path, "color")


if __name__ == "__main__":
    unittest.main()
