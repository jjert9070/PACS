"""Original-size RGB processing functions; display transforms belong to the viewer."""

import math
from numbers import Real

from PIL import Image, ImageFilter

GAUSSIAN_ALGORITHM_ID = "gaussian_blur"
GAUSSIAN_ALGORITHM_VERSION = "1.0.0"
# Starter policy, not a requirement imposed by the assignment.
MAX_GAUSSIAN_RADIUS = 20.0


def gaussian_blur(image: Image.Image, radius: float = 1.0) -> Image.Image:
    """Return a new RGB image blurred with sigma=radius original-image pixels.

    Pillow approximates the Gaussian with extended box filters. Radius zero
    returns an independent copy. Invalid inputs fail without changing the source.
    """
    if not isinstance(image, Image.Image):
        raise TypeError("image must be a PIL Image")
    if image.mode != "RGB" or min(image.size) < 1:
        raise ValueError("image must be a nonempty RGB image from load_image")
    if isinstance(radius, bool) or not isinstance(radius, Real):
        raise TypeError("radius must be a real number, not bool or text")
    try:
        sigma = float(radius)
    except (OverflowError, ValueError) as exc:
        raise ValueError("radius must be finite and between 0 and 20 pixels") from exc
    if not math.isfinite(sigma) or not 0 <= sigma <= MAX_GAUSSIAN_RADIUS:
        raise ValueError("radius must be finite and between 0 and 20 pixels")
    if sigma == 0:
        return image.copy()
    return image.filter(ImageFilter.GaussianBlur(sigma))
