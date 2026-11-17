"""Compare fixed original-pixel ROIs across reproducible Gaussian conditions."""

import argparse
import csv
import hashlib
import json
import platform
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from experiments.gaussian_experiment import (  # noqa: E402
    generate_noise_input, save_rgb_png, synthetic_reference,
)
from image_processing import (  # noqa: E402
    GAUSSIAN_ALGORITHM_ID, GAUSSIAN_ALGORITHM_VERSION, gaussian_blur,
)
from roi import COORDINATE_SYSTEM, calculation_metadata, compare_roi  # noqa: E402

SEED = 20261115
NOISE_LEVELS = (0, 5, 20, 40)
RADII = (0, 1, 2, 4)
REGIONS = {
    "flat": (16, 64, 48, 192),
    "edge": (112, 64, 144, 192),
    "thin_line": (52, 64, 70, 192),
}
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "docs/image-processing/results/roi-gaussian"


def roi_rmse(reference, result, bounds):
    # Use the same Pillow L brightness as the web ROI, not RGB-channel RMSE.
    reference_l = np.asarray(reference.crop(bounds).convert("L"), dtype=np.float64)
    result_l = np.asarray(result.crop(bounds).convert("L"), dtype=np.float64)
    pixel_difference = result_l - reference_l
    return float(np.sqrt(np.mean(np.square(pixel_difference))))


def comparison_image(reference, noise_input, results):
    rows = []
    columns = [("Clean", reference), ("Noise=20", noise_input)]
    columns.extend((f"Sigma={radius}", results[radius]) for radius in RADII)
    for region_name, bounds in REGIONS.items():
        tiles = []
        for label, image in columns:
            crop = np.asarray(image.crop(bounds))
            # Nearest-neighbor display enlargement keeps narrow structures visible.
            enlarged = cv2.resize(crop, (120, 256), interpolation=cv2.INTER_NEAREST)
            tile = np.full((300, 120, 3), 28, dtype=np.uint8)
            tile[44:, :, :] = enlarged
            cv2.putText(tile, region_name, (4, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (240, 240, 240), 1)
            cv2.putText(tile, label, (4, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (240, 240, 240), 1)
            tiles.append(tile)
        rows.append(cv2.hconcat(tiles))
    return cv2.vconcat(rows)


def run_experiment(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    reference_pixels = synthetic_reference()
    reference = Image.fromarray(reference_pixels)
    save_rgb_png(output / "reference.png", reference_pixels)
    rows = []
    input_records = []
    comparison_input = None
    comparison_results = {}

    for noise_sigma in NOISE_LEVELS:
        noisy_pixels = generate_noise_input(reference_pixels, noise_sigma, seed=SEED)
        noise_input = Image.fromarray(noisy_pixels)
        input_path = output / f"noise-{noise_sigma}.png"
        save_rgb_png(input_path, noisy_pixels)
        source_id = hashlib.sha256(input_path.read_bytes()).hexdigest()
        input_records.append({"noise_sigma": noise_sigma, "source_id": source_id, "file": input_path.name})
        for radius in RADII:
            result = gaussian_blur(noise_input, radius)
            save_rgb_png(output / f"noise-{noise_sigma}-sigma-{radius}.png", np.asarray(result))
            for region_name, bounds in REGIONS.items():
                statistics = compare_roi(noise_input, result, bounds)
                reference_stats = compare_roi(reference, None, bounds)["original"]
                left, top, right, bottom = bounds
                row = {
                    "region": region_name, "noise_sigma": noise_sigma, "sigma_px": radius,
                    "source_id": source_id, "left": left, "top": top,
                    "right": right, "bottom": bottom,
                    "width": right - left, "height": bottom - top,
                    "pixel_count": statistics["original"]["pixel_count"],
                    "reference_mean": reference_stats["mean"],
                    "reference_population_variance": reference_stats["population_variance"],
                    "input_mean": statistics["original"]["mean"],
                    "input_population_variance": statistics["original"]["population_variance"],
                    "result_mean": statistics["result"]["mean"],
                    "result_population_variance": statistics["result"]["population_variance"],
                    "rmse_l_to_clean": roi_rmse(reference, result, bounds),
                }
                rows.append(row)
            if noise_sigma == 20:
                comparison_input = noise_input
                comparison_results[radius] = result

    with (output / "metrics.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    save_rgb_png(output / "comparison.png", comparison_image(reference, comparison_input, comparison_results))
    manifest = {
        "algorithm_id": GAUSSIAN_ALGORITHM_ID,
        "algorithm_version": GAUSSIAN_ALGORITHM_VERSION,
        "python": platform.python_version(), "opencv": cv2.__version__,
        "roi_calculation": calculation_metadata(),
        "coordinate_system": COORDINATE_SYSTEM,
        "image_size": list(reference.size), "regions": REGIONS,
        "seed": SEED, "generator": "numpy.random.Generator(PCG64)",
        "noise_sigma": NOISE_LEVELS, "sigma_px": RADII,
        "kernel_size": {str(sigma): 2 * int(np.ceil(3 * sigma)) + 1 for sigma in RADII},
        "border": "BORDER_REFLECT_101; sigma=0 independent copy, no filtering",
        "noise_policy": "RGB independent normal noise; float64 addition, np.rint, clip 0..255, uint8 (8-bit experiment only)",
        "brightness": "Pillow L for reference, input and result; population variance ddof=0",
        "inputs": input_records,
        "reference_sha256": hashlib.sha256((output / "reference.png").read_bytes()).hexdigest(),
        "result_encoding": "RGB PNG; OpenCV encoder receives BGR",
    }
    manifest["file_sha256"] = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output.glob("*.png"))
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Fixed ROI comparison: Gaussian", "",
        "Generated synthetic 256x256 RGB image; no patient or external image data.",
        "Coordinates are original pixels; right/bottom exclusive. All conditions use the same regions.",
        "Brightness uses Pillow L; population variance uses NumPy float64 and ddof=0.", "",
        "| Region | Bounds | Size | Pixels |", "|---|---|---|---|",
    ]
    for name, (left, top, right, bottom) in REGIONS.items():
        width, height = right - left, bottom - top
        lines.append(f"| {name} | {(left, top, right, bottom)} | {width}x{height} | {width * height} |")
    lines.extend([
        "", "## Noise sigma=20 (full conditions are in metrics.csv)", "",
        "| Region | Filter sigma | Input mean | Input variance | Result mean | Result variance | L RMSE to clean |",
        "|---|---|---|---|---|---|---|",
    ])
    for row in rows:
        if row["noise_sigma"] == 20:
            values = (row[key] for key in ("input_mean", "input_population_variance", "result_mean",
                                          "result_population_variance", "rmse_l_to_clean"))
            measured = " | ".join(f"{value:.6f}" for value in values)
            lines.append(f"| {row['region']} | {row['sigma_px']} | {measured} |")
    lines.extend([
        "", "## Interpretation and limits", "",
        "Flat-region variance describes noise fluctuations. Edge/line-region variance also includes genuine structure.",
        "A lower variance alone does not show better quality: stronger smoothing may remove edges and thin lines.",
        "RMSE compares the same L region with the clean synthetic reference, not with the noisy input.",
        "RGB noise is converted to L before statistics; noise sigma is not the resulting L standard deviation.",
        "Sigma=0 preserves every input pixel, so input and result statistics are equal.",
        "The montage enlarges ROI crops for visibility; calculation uses the original crops without resizing.",
        "The montage shows RGB crops; numeric metrics consistently use Pillow L brightness.",
        "These synthetic results are not Lenna quality metrics, diagnostic evidence or a universal best sigma.",
        "Web rotation/flip and Firebase persistence are separate integration checks, not demonstrated by this experiment.",
    ])
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    run_experiment(arguments.output)
    print(arguments.output / "summary.md")
