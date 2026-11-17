import unittest

import numpy as np
from PIL import Image

from image_processing import threshold_segmentation
from roi import brightness_statistics


def gray_row(*levels):
    """1-pixel-high RGB image of gray pixels; gray (v, v, v) has Pillow L == v."""
    image = Image.new("RGB", (len(levels), 1))
    for x, level in enumerate(levels):
        image.putpixel((x, 0), (level, level, level))
    return image


def mask_values(image):
    return [image.getpixel((x, 0))[0] for x in range(image.width)]


class ThresholdBoundaryTests(unittest.TestCase):
    def test_b03_just_below_equal_and_just_above(self):
        for threshold in (1, 100, 128, 254):
            with self.subTest(threshold=threshold):
                source = gray_row(threshold - 1, threshold, threshold + 1)
                self.assertEqual(mask_values(threshold_segmentation(source, threshold)), [0, 255, 255])

    def test_extreme_thresholds(self):
        source = gray_row(0, 1, 254, 255)
        self.assertEqual(mask_values(threshold_segmentation(source, 0)), [255, 255, 255, 255])
        self.assertEqual(mask_values(threshold_segmentation(source, 255)), [0, 0, 0, 255])

    def test_color_pixels_use_pillow_l_brightness(self):
        source = Image.new("RGB", (1, 1), (255, 0, 0))
        level = source.convert("L").getpixel((0, 0))
        self.assertEqual(level, 76)  # 255 * 299/1000 = 76.2, Pillow integer luma
        self.assertEqual(mask_values(threshold_segmentation(source, 76)), [255])
        self.assertEqual(mask_values(threshold_segmentation(source, 77)), [0])


class ThresholdContractTests(unittest.TestCase):
    def test_b01_size_mode_binary_output_and_source_preserved(self):
        rng = np.random.default_rng(20261117)
        for size in ((1, 1), (1, 7), (17, 11)):
            with self.subTest(size=size):
                pixels = rng.integers(0, 256, size=(size[1], size[0], 3), dtype=np.uint8)
                source = Image.fromarray(pixels)
                before = source.tobytes()
                result = threshold_segmentation(source, 128)
                self.assertEqual(result.mode, "RGB")
                self.assertEqual(result.size, size)
                self.assertEqual(source.tobytes(), before)
                array = np.asarray(result)
                self.assertTrue(set(np.unique(array)) <= {0, 255})
                self.assertTrue(np.array_equal(array[..., 0], array[..., 1]))
                self.assertTrue(np.array_equal(array[..., 0], array[..., 2]))
                expected = np.where(np.asarray(source.convert("L")) >= 128, 255, 0)
                self.assertTrue(np.array_equal(array[..., 0], expected))

    def test_result_is_independent_of_source(self):
        source = gray_row(10, 200)
        result = threshold_segmentation(source, 100)
        result.putpixel((0, 0), (1, 2, 3))
        self.assertEqual(source.getpixel((0, 0)), (10, 10, 10))

    def test_roi_statistics_of_mask_follow_white_fraction(self):
        # Two dark and two bright pixels -> mask 0,0,255,255 -> P02 reference values.
        source = Image.new("RGB", (2, 2))
        for (x, y), level in zip(((0, 0), (1, 0), (0, 1), (1, 1)), (20, 60, 180, 240)):
            source.putpixel((x, y), (level, level, level))
        stats = brightness_statistics(threshold_segmentation(source, 128), (0, 0, 2, 2))
        self.assertEqual(stats["pixel_count"], 4)
        self.assertAlmostEqual(stats["mean"], 127.5)
        self.assertAlmostEqual(stats["population_variance"], 16256.25)

    def test_numpy_integer_thresholds(self):
        source = gray_row(99, 100, 101)
        for threshold in (np.int32(100), np.int64(100), np.uint8(100)):
            with self.subTest(threshold_type=type(threshold)):
                self.assertEqual(mask_values(threshold_segmentation(source, threshold)), [0, 255, 255])

    def test_invalid_thresholds_are_rejected(self):
        source = gray_row(1, 2)
        for threshold in (-1, 256, 10**400, np.int64(-1), np.int64(256)):
            with self.subTest(threshold=threshold):
                with self.assertRaises(ValueError):
                    threshold_segmentation(source, threshold)
        for threshold in (128.0, 127.5, np.float64(128), np.nan, True, False, np.bool_(True),
                          "128", None, [128], np.array(128), 1+2j):
            with self.subTest(threshold=threshold):
                with self.assertRaises(TypeError):
                    threshold_segmentation(source, threshold)

    def test_invalid_images_are_rejected(self):
        with self.assertRaises(TypeError):
            threshold_segmentation(None, 128)
        for mode in ("L", "RGBA", "I;16", "P"):
            with self.subTest(mode=mode):
                with self.assertRaises(ValueError):
                    threshold_segmentation(Image.new(mode, (3, 3)), 128)
        with self.assertRaises(ValueError):
            threshold_segmentation(Image.new("RGB", (0, 3)), 128)


if __name__ == "__main__":
    unittest.main()
