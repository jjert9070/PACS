"""Original-size RGB processing functions; display transforms belong to the viewer."""

import cv2
import numpy as np
from PIL import Image

GAUSSIAN_ALGORITHM_ID = "gaussian_blur"
GAUSSIAN_ALGORITHM_VERSION = "2.0.0"
# Starter policy, not a requirement imposed by the assignment.
MAX_GAUSSIAN_RADIUS = 20.0

THRESHOLD_ALGORITHM_ID = "threshold_segmentation"
THRESHOLD_ALGORITHM_VERSION = "1.0.0"
# Pillow L brightness is an 8-bit integer, so thresholds are integers 0..255.
MIN_THRESHOLD = 0
MAX_THRESHOLD = 255


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


def threshold_segmentation(image: Image.Image, threshold: int = 128) -> Image.Image:
    """Return a black/white RGB mask: L >= threshold is white (255), else black (0).

    L is Pillow's "L" conversion (ITU-R 601-2 luma), the same brightness the
    ROI statistics use, so a mask ROI mean of 255 * p means p of the pixels
    passed. A pixel exactly equal to the threshold is white; threshold 0 makes
    every pixel white. Only integers 0..255 are accepted: float, bool and text
    are type errors rather than being rounded silently.
    """
    if not isinstance(image, Image.Image):
        raise TypeError("image must be a PIL Image")
    if image.mode != "RGB" or min(image.size) < 1:
        raise ValueError("image must be a nonempty RGB image from load_image")
    if isinstance(threshold, (bool, np.bool_)) or not isinstance(threshold, (int, np.integer)):
        raise TypeError("threshold must be an integer, not bool, float or text")
    level = int(threshold)
    if not MIN_THRESHOLD <= level <= MAX_THRESHOLD:
        raise ValueError("threshold must be an integer between 0 and 255")
    brightness = np.asarray(image.convert("L"), dtype=np.uint8)
    mask = np.where(brightness >= level, 255, 0).astype(np.uint8)
    # Repeat the single mask channel so the viewer and ROI keep the RGB contract.
    return Image.fromarray(np.stack([mask, mask, mask], axis=-1))
