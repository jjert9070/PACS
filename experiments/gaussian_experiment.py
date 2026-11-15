"""Reproducible 8-bit PNG experiments, separate from display and DICOM rules."""

import argparse
import csv
import hashlib
import json
import math
import platform
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageFilter, __version__ as pillow_version

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from image_processing import (  # noqa: E402
    GAUSSIAN_ALGORITHM_ID,
    GAUSSIAN_ALGORITHM_VERSION,
    gaussian_blur,
)

RADII = (0, 1, 2, 4)
NOISE_LEVELS = (0, 5, 20, 40)
SEED = 20261115
IMAGE_SIZE = (256, 256)
RESULTS_ROOT = Path(__file__).resolve().parents[1] / "docs/image-processing/results/gaussian-opencv"


def save_rgb_png(path, pixels):
    """OpenCV codecs use BGR; Path handles Windows paths containing Korean."""
    bgr_pixels = cv2.cvtColor(pixels, cv2.COLOR_RGB2BGR)
    success, encoded = cv2.imencode(".png", bgr_pixels)
    if not success:
        raise OSError(f"Could not encode PNG: {path}")
    path.write_bytes(encoded.tobytes())


def read_input(path, color_mode):
    encoded = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    bgr_pixels = cv2.imdecode(encoded, cv2.IMREAD_COLOR | cv2.IMREAD_ANYDEPTH)
    if bgr_pixels is None:
        raise ValueError(f"Could not read input image: {path}")
    if bgr_pixels.dtype != np.uint8:
        raise ValueError("This experiment only supports 8-bit input; high-bit data must not be downcast")
    resized_bgr = cv2.resize(bgr_pixels, IMAGE_SIZE, interpolation=cv2.INTER_AREA)
    rgb_pixels = cv2.cvtColor(resized_bgr, cv2.COLOR_BGR2RGB)
    if color_mode == "grayscale":
        # Convert the clean reference too, so noise isn't confused with lost color.
        brightness = np.asarray(Image.fromarray(rgb_pixels).convert("L"))
        rgb_pixels = np.repeat(brightness[:, :, None], 3, axis=2)
    return rgb_pixels


def synthetic_reference():
    pixels = np.full((256, 256, 3), 80, dtype=np.uint8)
    pixels[:, 128:] = 176
    pixels[40:216, 60:62] = 224
    return pixels


def generate_noise_input(clean_pixels, noise_sigma, seed=SEED):
    # Reset per condition so sigma changes scale the same sampled noise pattern.
    generator = np.random.Generator(np.random.PCG64(seed))
    clean_float = clean_pixels.astype(np.float64)
    noise = generator.normal(0.0, noise_sigma, size=clean_float.shape)
    noisy_float = clean_float + noise
    # This rounding/clipping policy applies only to the 8-bit PNG experiment.
    rounded_pixels = np.rint(noisy_float)
    bounded_pixels = np.clip(rounded_pixels, 0.0, 255.0)
    return bounded_pixels.astype(np.uint8)


def calculate_metrics(result_pixels, clean_pixels, synthetic=False):
    result_float = result_pixels.astype(np.float64)
    clean_float = clean_pixels.astype(np.float64)
    pixel_difference = result_float - clean_float
    squared_difference = np.square(pixel_difference)
    mean_squared_error = np.mean(squared_difference)
    measured = {"rmse": float(np.sqrt(mean_squared_error))}
    if synthetic:
        brightness = np.asarray(Image.fromarray(result_pixels).convert("L"))
        brightness_float = brightness.astype(np.float64)
        flat_region = brightness_float[64:192, 16:48]
        step_difference = brightness_float[64:192, 128] - brightness_float[64:192, 127]
        line_brightness = np.mean(brightness_float[64:192, 60:62], axis=1)
        background_brightness = brightness_float[64:192, 52]
        line_difference = line_brightness - background_brightness
        measured["flat_variance"] = float(np.var(flat_region, ddof=0))
        measured["step_contrast"] = float(np.mean(step_difference))
        measured["thin_line_contrast"] = float(np.mean(line_difference))
    return measured


def compare_pillow_backend(source_pixels, opencv_pixels, radius):
    """Migration evidence uses identical input to isolate backend differences."""
    source_image = Image.fromarray(source_pixels)
    pillow_image = source_image.filter(ImageFilter.GaussianBlur(radius))
    pillow_float = np.asarray(pillow_image).astype(np.float64)
    difference = opencv_pixels.astype(np.float64) - pillow_float
    absolute_difference = np.abs(difference)
    measured = {}
    measured["pillow_difference_mae"] = float(np.mean(absolute_difference))
    measured["pillow_difference_max"] = float(np.max(absolute_difference))
    return measured


def create_comparison_image(comparison_rows, output_path):
    header_height = 30
    row_height = IMAGE_SIZE[1] + header_height
    sheet_shape = (len(comparison_rows) * row_height, 6 * IMAGE_SIZE[0], 3)
    sheet = np.full(sheet_shape, 30, dtype=np.uint8)
    for row_index, labeled_images in enumerate(comparison_rows):
        for column_index, (label, pixels) in enumerate(labeled_images):
            left = column_index * IMAGE_SIZE[0]
            top = row_index * row_height
            cv2.putText(sheet, label, (left + 5, top + 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)
            sheet[top + header_height:top + row_height, left:left + IMAGE_SIZE[0]] = pixels
    save_rgb_png(output_path, sheet)


def save_metrics_csv(output, rows):
    with (output / "metrics.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def save_summary(output, rows, synthetic):
    metric_names = ["rmse"]
    if synthetic:
        metric_names.extend(["flat_variance", "step_contrast", "thin_line_contrast"])
    metric_names.extend(["pillow_difference_mae", "pillow_difference_max"])
    columns = ["noise_sigma", "radius_px"] + metric_names
    lines = ["# OpenCV Gaussian measured results", ""]
    lines.append("Synthetic-only structure metrics." if synthetic else "External image: RGB RMSE only; no synthetic structure metrics.")
    lines.extend(["", "| " + " | ".join(columns) + " |"])
    lines.append("|" + "---:|" * len(columns))
    for row in rows:
        values = [f"{row[column]:.3f}" for column in columns]
        lines.append("| " + " | ".join(values) + " |")
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def save_experiment_info(output, sources, input_path, color_mode, synthetic):
    info = {}
    info["algorithm_id"] = GAUSSIAN_ALGORITHM_ID
    info["algorithm_version"] = GAUSSIAN_ALGORITHM_VERSION
    info["python"] = platform.python_version()
    info["numpy"] = np.__version__
    info["opencv"] = cv2.__version__
    info["pillow"] = pillow_version
    info["random_generator"] = "numpy.random.Generator(PCG64)"
    info["seed"] = SEED
    info["noise_sigma"] = NOISE_LEVELS
    info["noise_channels"] = "independent RGB" if color_mode == "color" else "one L sample replicated to RGB"
    info["rounding_clipping"] = "float64 -> np.rint (ties to even) -> clip 0..255 -> uint8; PNG experiment only"
    info["radius_px"] = RADII
    info["kernel_sizes"] = [1 if radius == 0 else 2 * math.ceil(3 * radius) + 1 for radius in RADII]
    info["sigma_xy"] = "radius in original pixels; radius=0 independent copy"
    info["border"] = "BORDER_REFLECT_101"
    info["size"] = IMAGE_SIZE
    info["experiment_type"] = "synthetic" if synthetic else "external"
    info["color_mode"] = color_mode
    info["metric_space"] = "RGB RMSE; synthetic structure metrics use Pillow L"
    info["input_file"] = input_path.name if input_path else None
    info["input_sha256"] = hashlib.sha256(input_path.read_bytes()).hexdigest() if input_path else None
    info["resize"] = "256x256 INTER_AREA; aspect ratio may change for external input"
    info["sources"] = sources
    info["output_sha256"] = {}
    for path in sorted(output.glob("*.png")):
        info["output_sha256"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (output / "manifest.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")


def run(output, input_path=None, color_mode="color"):
    output.mkdir(parents=True, exist_ok=True)
    synthetic = input_path is None

    # 1. Read the reference through OpenCV, including generated PNG fixtures.
    if synthetic:
        generated_input = output / "synthetic-input.png"
        save_rgb_png(generated_input, synthetic_reference())
        clean_pixels = read_input(generated_input, "grayscale")
        color_mode = "grayscale"
    else:
        clean_pixels = read_input(input_path, color_mode)
    save_rgb_png(output / "clean.png", clean_pixels)

    rows = []
    sources = []
    comparison_rows = []
    for noise_sigma in NOISE_LEVELS:
        # 2. Generate noise in floating point before 8-bit rounding and clipping.
        if color_mode == "grayscale":
            noisy_gray = generate_noise_input(clean_pixels[:, :, :1], noise_sigma)
            source_pixels = np.repeat(noisy_gray, 3, axis=2)
        else:
            source_pixels = generate_noise_input(clean_pixels, noise_sigma)
        source_path = output / f"noise-{noise_sigma}.png"
        save_rgb_png(source_path, source_pixels)
        source_id = hashlib.sha256(source_path.read_bytes()).hexdigest()
        sources.append({"noise_sigma": noise_sigma, "source_id": source_id})
        labeled_images = [("Reference", clean_pixels), (f"Noise={noise_sigma}", source_pixels)]
        source_image = Image.fromarray(source_pixels)
        original_bytes = source_image.tobytes()
        for radius in RADII:
            # 3. Process the same full source independently for every radius.
            result_image = gaussian_blur(source_image, radius)
            result_pixels = np.asarray(result_image)
            assert source_image.tobytes() == original_bytes, "source was modified"
            result_filename = f"noise-{noise_sigma}-radius-{radius}.png"
            save_rgb_png(output / result_filename, result_pixels)

            # 4. Calculate quality and migration metrics on signed float arrays.
            row = {"noise_sigma": noise_sigma, "radius_px": radius}
            row.update(calculate_metrics(result_pixels, clean_pixels, synthetic))
            row.update(compare_pillow_backend(source_pixels, result_pixels, radius))
            row["source_id"] = source_id
            row["result_file"] = result_filename
            rows.append(row)
            labeled_images.append((f"Sigma={radius}px", result_pixels))
        comparison_rows.append(labeled_images)

    # 5. Compose the comparison; 6. Save metrics and reproducibility information.
    create_comparison_image(comparison_rows, output / "comparison.png")
    save_metrics_csv(output, rows)
    save_summary(output, rows, synthetic)
    save_experiment_info(output, sources, input_path, color_mode, synthetic)
    print((output / "summary.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, help="Optional external 8-bit PNG/JPEG, e.g. a team-provided Lenna")
    parser.add_argument("--color-mode", choices=("color", "grayscale"), default="color")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    experiment_folder = "external-" + args.color_mode if args.input else "synthetic"
    output_folder = args.output if args.output else RESULTS_ROOT / experiment_folder
    run(output_folder, args.input, args.color_mode)
