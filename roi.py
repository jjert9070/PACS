"""P02 statistics on EXIF-corrected RGB originals, never display crops."""

import numpy as np
from PIL import Image, __version__ as pillow_version

COORDINATE_SYSTEM = "exif_transposed_original_pixels_v1"
BRIGHTNESS_METHOD = "pillow_L_v1"


def calculation_metadata() -> dict:
    """Record the actual calculation environment for a future saved record."""
    return {
        "numpy": np.__version__,
        "pillow": pillow_version,
        "brightness_method": BRIGHTNESS_METHOD,
        "variance_definition": "population",
        "ddof": 0,
        "calculation_dtype": "float64",
    }


def validate_roi(bounds, size) -> tuple[int, int, int, int]:
    if not isinstance(bounds, (tuple, list)) or len(bounds) != 4:
        raise ValueError("ROI는 left, top, right, bottom 좌표 4개가 필요합니다.")
    if any(isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)) for value in bounds):
        raise ValueError("ROI 좌표는 정수 픽셀이어야 합니다.")
    left, top, right, bottom = (int(value) for value in bounds)
    width, height = size
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise ValueError(f"ROI 범위: 0 ≤ left < right ≤ {width}, 0 ≤ top < bottom ≤ {height}. 끝 좌표는 제외합니다.")
    return left, top, right, bottom


def _validate_image(image):
    if not isinstance(image, Image.Image):
        raise TypeError("ROI 입력은 PIL 이미지여야 합니다.")
    if image.mode != "RGB" or min(image.size) < 1:
        raise ValueError("방향 보정된 비어 있지 않은 RGB 원본을 사용하세요.")


def brightness_statistics(image: Image.Image, bounds) -> dict:
    _validate_image(image)
    validated = validate_roi(bounds, image.size)
    # Both images use the same L conversion, with float64 population variance.
    brightness = np.asarray(image.crop(validated).convert("L"), dtype=np.float64)
    return {
        "pixel_count": int(brightness.size),
        "mean": float(np.mean(brightness)),
        "population_variance": float(np.var(brightness, ddof=0)),
    }


def compare_roi(original: Image.Image, result: Image.Image | None, bounds) -> dict:
    _validate_image(original)
    validated = validate_roi(bounds, original.size)
    if result is not None:
        _validate_image(result)
        if result.size != original.size:
            raise ValueError("결과는 원본과 같은 크기여야 합니다.")
    left, top, right, bottom = validated
    return {
        "bounds": list(validated),
        "width": right - left,
        "height": bottom - top,
        "coordinate_system": COORDINATE_SYSTEM,
        "brightness_method": BRIGHTNESS_METHOD,
        "calculation": calculation_metadata(),
        "original": brightness_statistics(original, validated),
        "result": None if result is None else brightness_statistics(result, validated),
    }
