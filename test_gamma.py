import unittest

import numpy as np
from PIL import Image

from image_processing import gamma_correction, gamma_lut


class GammaLutTests(unittest.TestCase):
    def test_identity_and_fixed_endpoints(self):
        self.assertEqual(gamma_lut(1).tolist(), list(range(256)))
        for gamma in (0.1, 0.5, 2, 10):
            with self.subTest(gamma=gamma):
                table = gamma_lut(gamma)
                self.assertEqual((table[0], table[255]), (0, 255))

    def test_known_values_and_direction(self):
        # 255 * (128/255)^2 = 64.25 -> 64; 255 * (128/255)^0.5 = 180.66 -> 181.
        self.assertEqual(gamma_lut(2)[128], 64)
        self.assertEqual(gamma_lut(2)[64], 16)
        self.assertEqual(gamma_lut(0.5)[128], 181)
        identity = np.arange(256)
        self.assertTrue(np.all(gamma_lut(0.5) >= identity))  # gamma < 1 brightens
        self.assertTrue(np.all(gamma_lut(2) <= identity))  # gamma > 1 darkens

    def test_table_is_monotonic(self):
        for gamma in (0.1, 0.45, 1.8, 10):
            with self.subTest(gamma=gamma):
                self.assertTrue(np.all(np.diff(gamma_lut(gamma).astype(int)) >= 0))


class GammaCorrectionTests(unittest.TestCase):
    def test_gamma_one_is_independent_identity(self):
        source = Image.new("RGB", (9, 5), (10, 20, 30))
        result = gamma_correction(source, 1)
        self.assertIsNot(result, source)
        self.assertEqual(result.tobytes(), source.tobytes())
        result.putpixel((0, 0), (0, 0, 0))
        self.assertEqual(source.getpixel((0, 0)), (10, 20, 30))

    def test_size_mode_and_source_preserved(self):
        for size in ((1, 1), (1, 7), (17, 11)):
            for gamma in (0.1, 0.5, 2.2, 10):
                with self.subTest(size=size, gamma=gamma):
                    source = Image.new("RGB", size, (12, 93, 211))
                    before = source.tobytes()
                    result = gamma_correction(source, gamma)
                    self.assertEqual(result.mode, "RGB")
                    self.assertEqual(result.size, size)
                    self.assertEqual(source.tobytes(), before)

    def test_channels_use_same_table_independently(self):
        source = Image.new("RGB", (2, 1))
        source.putpixel((0, 0), (128, 0, 255))
        source.putpixel((1, 0), (64, 64, 64))
        result = gamma_correction(source, 2)
        self.assertEqual(result.getpixel((0, 0)), (64, 0, 255))
        self.assertEqual(result.getpixel((1, 0)), (16, 16, 16))  # gray stays gray

    def test_numpy_scalars_and_repeatability(self):
        source = Image.new("RGB", (4, 4), (100, 150, 200))
        expected = gamma_correction(source, 1.5).tobytes()
        for gamma in (np.float32(1.5), np.float64(1.5)):
            with self.subTest(gamma_type=type(gamma)):
                self.assertEqual(gamma_correction(source, gamma).tobytes(), expected)
        self.assertEqual(gamma_correction(source, np.int64(2)).tobytes(),
                         gamma_correction(source, 2).tobytes())

    def test_invalid_parameters_are_rejected(self):
        source = Image.new("RGB", (3, 3))
        for gamma in (0, -1, 0.09, 10.01, np.nan, np.inf, -np.inf, 10**400):
            with self.subTest(gamma=gamma):
                with self.assertRaises(ValueError):
                    gamma_correction(source, gamma)
        for gamma in (True, False, np.bool_(True), "2", None, [2], np.array(2), 1+2j):
            with self.subTest(gamma=gamma):
                with self.assertRaises(TypeError):
                    gamma_correction(source, gamma)

    def test_invalid_images_are_rejected(self):
        with self.assertRaises(TypeError):
            gamma_correction(None, 2)
        for mode in ("L", "RGBA", "I;16", "P"):
            with self.subTest(mode=mode):
                with self.assertRaises(ValueError):
                    gamma_correction(Image.new(mode, (3, 3)), 2)
        with self.assertRaises(ValueError):
            gamma_correction(Image.new("RGB", (0, 3)), 2)


if __name__ == "__main__":
    unittest.main()
