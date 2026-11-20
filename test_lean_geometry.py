"""Finite Pillow/ROI observations for GeometryRules.lean, not all-image proofs."""

from itertools import product
import unittest

from PIL import Image, ImageOps

from roi import compare_roi
from viewer import ViewTransform


def pattern(width, height):
    image = Image.new("RGB", (width, height))
    image.putdata([(x, y, y * width + x) for y in range(height) for x in range(width)])
    return image


def point_observations():
    for width, height in product(range(1, 6), range(1, 5)):
        source = pattern(width, height)
        for name, turns, flip_x, flip_y in (
            ("clockwise", 1, False, False),
            ("horizontal", 0, True, False),
            ("vertical", 0, False, True),
        ):
            result = ViewTransform((0, 0, width, height), turns, flip_x, flip_y).apply(source)
            positions = {result.getpixel((x, y)): (x, y)
                         for y in range(result.height) for x in range(result.width)}
            for x, y in product(range(width), range(height)):
                yield name, width, height, x, y, positions[source.getpixel((x, y))]


class LeanGeometryTests(unittest.TestCase):
    def test_actual_pixel_locations_match_integer_model(self):
        count = 0
        for name, width, height, x, y, observed in point_observations():
            expected = {"clockwise": (height - 1 - y, x),
                        "horizontal": (width - 1 - x, y),
                        "vertical": (x, height - 1 - y)}[name]
            self.assertEqual(observed, expected)
            count += 1
        self.assertEqual(count, 450)

    def test_crop_rotate_flip_compositions_preserve_source_pixels(self):
        count = 0
        for width, height in product(range(1, 6), range(1, 5)):
            source = pattern(width, height)
            original = source.tobytes()
            for left in range(width):
                for top in range(height):
                    for right in range(left + 1, width + 1):
                        for bottom in range(top + 1, height + 1):
                            crop_width, crop_height = right - left, bottom - top
                            for turns, flip_x, flip_y in product(range(4), (False, True), (False, True)):
                                result = ViewTransform((left, top, right, bottom), turns, flip_x, flip_y).apply(source)
                                expected_size = (crop_height, crop_width) if turns % 2 else (crop_width, crop_height)
                                self.assertEqual(result.size, expected_size)
                                for x, y in product(range(crop_width), range(crop_height)):
                                    px, py, rw, rh = x, y, crop_width, crop_height
                                    for _ in range(turns):
                                        px, py, rw, rh = rh - 1 - py, px, rh, rw
                                    if flip_x:
                                        px = rw - 1 - px
                                    if flip_y:
                                        py = rh - 1 - py
                                    self.assertEqual(result.getpixel((px, py)), source.getpixel((left + x, top + y)))
                                count += 1
            self.assertEqual(source.tobytes(), original)
        self.assertEqual(count, 11200)

    def test_roi_counts_and_result_size_boundary(self):
        count = 0
        for width, height in product(range(1, 6), range(1, 5)):
            source = pattern(width, height)
            result = ImageOps.invert(source)
            for left in range(width):
                for top in range(height):
                    for right in range(left + 1, width + 1):
                        for bottom in range(top + 1, height + 1):
                            roi = (left, top, right, bottom)
                            stats = compare_roi(source, result, roi)
                            expected = (right - left) * (bottom - top)
                            self.assertEqual(stats["original"]["pixel_count"], expected)
                            self.assertEqual(stats["result"]["pixel_count"], expected)
                            self.assertEqual(stats["bounds"], list(roi))
                            count += 1
            with self.assertRaises(ValueError):
                compare_roi(source, Image.new("RGB", (width + 1, height)), (0, 0, width, height))
        self.assertEqual(count, 700)


if __name__ == "__main__":
    unittest.main()
