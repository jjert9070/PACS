import json
from io import BytesIO
import unittest

import numpy as np
from PIL import Image

from roi import brightness_statistics, compare_roi, validate_roi
from viewer import load_image
from case_records import ProcessingRecord, _processing_json


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
        self.assertEqual(stats["calculation"]["ddof"], 0)
        self.assertEqual(stats["calculation"]["numpy"], np.__version__)
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

    def test_all_edges_and_result_pixels_are_preserved(self):
        pixels = np.arange(12, dtype=np.uint8).reshape(3, 4)
        original = Image.fromarray(pixels).convert("RGB")
        result = Image.fromarray(pixels + 30).convert("RGB")
        before = (original.tobytes(), result.tobytes())
        for bounds in ((0, 0, 4, 3), (0, 0, 4, 1), (0, 2, 4, 3),
                       (0, 0, 1, 3), (3, 0, 4, 3), (3, 2, 4, 3)):
            left, top, right, bottom = bounds
            expected = pixels[top:bottom, left:right].astype(np.float64)
            with self.subTest(bounds=bounds):
                stats = compare_roi(original, result, bounds)
                self.assertEqual(stats["original"]["pixel_count"], expected.size)
                self.assertAlmostEqual(stats["original"]["mean"], np.mean(expected))
                self.assertAlmostEqual(stats["original"]["population_variance"], np.var(expected))
                self.assertAlmostEqual(stats["result"]["mean"], np.mean(expected) + 30)
        self.assertEqual((original.tobytes(), result.tobytes()), before)

    def test_roi_coordinates_follow_exif_corrected_original(self):
        pixels = np.array([[10, 20, 30], [40, 50, 60]], dtype=np.uint8)
        expected_by_orientation = {
            2: np.fliplr(pixels), 3: np.rot90(pixels, 2), 4: np.flipud(pixels),
            5: pixels.T, 6: np.rot90(pixels, -1),
            7: np.rot90(pixels, 2).T, 8: np.rot90(pixels),
        }
        for orientation, expected in expected_by_orientation.items():
            with self.subTest(orientation=orientation):
                exif = Image.Exif()
                exif[274] = orientation
                stream = BytesIO()
                Image.fromarray(pixels).save(stream, format="PNG", exif=exif)
                source = load_image("oriented.png", stream.getvalue())
                np.testing.assert_array_equal(np.asarray(source.image)[:, :, 0], expected)
                width, height = source.image.size
                bounds = (width - 1, height - 1, width, height)
                stats = compare_roi(source.image, None, bounds)
                self.assertEqual(stats["original"]["mean"], float(expected[-1, -1]))
                self.assertEqual(stats["original"]["population_variance"], 0)
                self.assertEqual(source.original_bytes, stream.getvalue())

    def test_statistics_match_c_record_contract_and_reject_other_source(self):
        stream = BytesIO()
        self.checker().save(stream, format="PNG")
        source = load_image("checker.png", stream.getvalue())
        bounds = validate_roi((0, 0, 2, 2), source.image.size)
        result = source.image.copy()
        statistics = compare_roi(source.image, result, bounds)
        statistics["source_id"] = source.source_id
        record = ProcessingRecord(source.source_id, "gaussian_blur", "2.0.0",
                                  {"radius": 0.0}, result, bounds, statistics)
        metadata = json.loads(json.dumps(_processing_json(record, source), allow_nan=False))
        self.assertEqual(metadata["statistics"]["source_id"], source.source_id)
        self.assertEqual(metadata["roi"], [0, 0, 2, 2])
        self.assertEqual(metadata["statistics"]["calculation"]["variance_definition"], "population")
        record = ProcessingRecord("different-source", "gaussian_blur", "2.0.0",
                                  {"radius": 0.0}, result, bounds, statistics)
        with self.assertRaises(ValueError):
            _processing_json(record, source)


if __name__ == "__main__":
    unittest.main()
