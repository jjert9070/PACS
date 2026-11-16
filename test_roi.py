import json
import unittest

import numpy as np
from PIL import Image

from roi import brightness_statistics, compare_roi, validate_roi


class ROIStatisticsTests(unittest.TestCase):
    def checker(self):
        pixels = np.array([[0, 255], [255, 0]], dtype=np.uint8)
        return Image.fromarray(pixels).convert("RGB")

    def test_entire_two_by_two_population_statistics(self):
        original = self.checker()
        before = original.tobytes()
        stats = compare_roi(original, original.copy(), (0, 0, 2, 2))
        expected = {"pixel_count": 4, "mean": 127.5, "population_variance": 16256.25}
        self.assertEqual(stats["original"], expected)
        self.assertEqual(stats["result"], expected)
        self.assertEqual(stats["width"], 2)
        self.assertEqual(stats["height"], 2)
        self.assertEqual(original.tobytes(), before)
        json.dumps(stats, allow_nan=False)

    def test_single_pixel_exclusive_right_bottom_and_missing_result(self):
        stats = compare_roi(self.checker(), None, (1, 0, 2, 1))
        self.assertEqual(stats["original"], {"pixel_count": 1, "mean": 255.0, "population_variance": 0.0})
        self.assertIsNone(stats["result"])

    def test_invalid_empty_reversed_outside_and_noninteger_coordinates(self):
        invalid = [None, (), (0, 0, 1), (0, 0, 0, 2), (1, 0, 0, 1),
                   (-1, 0, 1, 1), (0, 0, 3, 2), (0, 0, 2, 3), (0, 2, 2, 2),
                   (0.0, 0, 2, 2), (False, 0, 2, 2), (np.bool_(False), 0, 2, 2),
                   ("0", 0, 2, 2), (np.nan, 0, 2, 2)]
        for bounds in invalid:
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                validate_roi(bounds, (2, 2))
        self.assertEqual(validate_roi((np.int64(0), 0, 2, 2), (2, 2)), (0, 0, 2, 2))

    def test_same_l_conversion_for_color_original_and_result(self):
        original = Image.new("RGB", (1, 1), (255, 0, 0))
        result = Image.new("RGB", (1, 1), (0, 255, 0))
        stats = compare_roi(original, result, (0, 0, 1, 1))
        self.assertEqual(stats["original"]["mean"], 76)
        self.assertEqual(stats["result"]["mean"], 150)
        self.assertEqual(stats["brightness_method"], "pillow_L_v1")

    def test_image_contract_rejects_mismatched_results(self):
        for result in (Image.new("RGB", (1, 1)), Image.new("L", (2, 2))):
            with self.subTest(result=result), self.assertRaises(ValueError):
                compare_roi(self.checker(), result, (0, 0, 2, 2))
        with self.assertRaises(TypeError):
            brightness_statistics(None, (0, 0, 1, 1))
        with self.assertRaises(ValueError):
            brightness_statistics(Image.new("RGB", (0, 1)), (0, 0, 1, 1))


if __name__ == "__main__":
    unittest.main()
