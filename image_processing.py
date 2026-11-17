"""Original-size RGB processing functions; display transforms belong to the viewer."""

import cv2
import numpy as np
from PIL import Image

GAUSSIAN_ALGORITHM_ID = "gaussian_blur"
GAUSSIAN_ALGORITHM_VERSION = "2.0.0"
# Starter policy, not a requirement imposed by the assignment.
MAX_GAUSSIAN_RADIUS = 20.0

GAMMA_ALGORITHM_ID = "gamma_correction"
GAMMA_ALGORITHM_VERSION = "1.0.0"
# Team policy: outside [0.1, 10] almost every 8-bit level collapses to 0 or 255.
MIN_GAMMA = 0.1
MAX_GAMMA = 10.0


def gaussian_blur(image: Image.Image, radius: float = 1.0) -> Image.Image:
    """Return a new RGB image blurred with sigma=radius original-image pixels.

    OpenCV uses an odd kernel spanning +/-ceil(3*sigma) pixels and
    BORDER_REFLECT_101. Radius zero returns an independent copy.
    """
    if not isinstance(image, Image.Image):
        raise TypeError("image must be a PIL Image")
    if image.mode != "RGB" or min(image.size) < 1:
        raise ValueError("image must be a nonempty RGB image from load_image")
    scalar_types = (int, float, np.integer, np.floating)
    if isinstance(radius, (bool, np.bool_)) or not isinstance(radius, scalar_types):
        raise TypeError("radius must be a real number, not bool or text")
    try:
        sigma = float(radius)
    except (OverflowError, ValueError) as exc:
        raise ValueError("radius must be finite and between 0 and 20 pixels") from exc
    if not np.isfinite(sigma) or not 0 <= sigma <= MAX_GAUSSIAN_RADIUS:
        raise ValueError("radius must be finite and between 0 and 20 pixels")
    if sigma == 0:
        return image.copy()
    kernel_radius = int(np.ceil(3 * sigma))
    kernel_size = 2 * kernel_radius + 1

    # GaussianBlur processes channels independently: RGB needs no BGR swap.
    # This adapter is for the 8-bit viewer, not a DICOM pixel decoder.
    source_pixels = np.array(image, dtype=np.uint8, copy=True)
    result_pixels = cv2.GaussianBlur(
        source_pixels,
        (kernel_size, kernel_size),
        sigmaX=sigma,
        sigmaY=sigma,
        borderType=cv2.BORDER_REFLECT_101,
    )
    return Image.fromarray(result_pixels)


def gamma_lut(gamma: float) -> np.ndarray:
    """Return the 256-entry uint8 table out = rint(255 * (in / 255) ** gamma).

    gamma < 1 brightens mid-tones, gamma > 1 darkens them, gamma = 1 is the
    identity. 0 and 255 stay fixed for every allowed gamma. np.rint rounds
    exact halves to the even integer, matching the Gaussian experiments.
    """
    scalar_types = (int, float, np.integer, np.floating)
    if isinstance(gamma, (bool, np.bool_)) or not isinstance(gamma, scalar_types):
        raise TypeError("gamma must be a real number, not bool or text")
    try:
        value = float(gamma)
    except (OverflowError, ValueError) as exc:
        raise ValueError("gamma must be finite and between 0.1 and 10") from exc
    if not np.isfinite(value) or not MIN_GAMMA <= value <= MAX_GAMMA:
        raise ValueError("gamma must be finite and between 0.1 and 10")
    levels = np.arange(256, dtype=np.float64) / 255.0
    return np.clip(np.rint(255.0 * np.power(levels, value)), 0, 255).astype(np.uint8)


def gamma_correction(image: Image.Image, gamma: float = 1.0) -> Image.Image:
    """Return a new RGB image with the gamma lookup table applied per channel.

    R, G and B use the same table independently: gray stays gray, but the hue
    of saturated colors can shift slightly. gamma = 1 returns an independent
    copy. Only 8-bit display values are mapped; this is not a DICOM window.
    """
    if not isinstance(image, Image.Image):
        raise TypeError("image must be a PIL Image")
    if image.mode != "RGB" or min(image.size) < 1:
        raise ValueError("image must be a nonempty RGB image from load_image")
    table = gamma_lut(gamma)
    if float(gamma) == 1.0:
        return image.copy()
    source_pixels = np.array(image, dtype=np.uint8, copy=True)
    return Image.fromarray(table[source_pixels])
