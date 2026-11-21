"""Reproducible comparison experiments for gamma correction and threshold segmentation.

Inputs differ in brightness (normal / under / over exposure), noise and subject
(synthetic chart vs. the repository's Lenna). The synthetic chart has known
regions, so gamma's local contrast and threshold's segmentation accuracy can be
measured against ground truth. Numbers use Pillow L brightness, like the web ROI.

Run from the PACS root:
    uv run --locked python experiments/gamma_threshold_experiment.py
Outputs: docs/image-processing/results/gamma/ and docs/image-processing/results/threshold/
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, __version__ as pillow_version

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from experiments.gaussian_experiment import generate_noise_input, read_input, save_rgb_png  # noqa: E402
from image_processing import (  # noqa: E402
    GAMMA_ALGORITHM_ID, GAMMA_ALGORITHM_VERSION, THRESHOLD_ALGORITHM_ID, THRESHOLD_ALGORITHM_VERSION,
    gamma_correction, gamma_lut, threshold_segmentation,
)
from roi import calculation_metadata, compare_roi  # noqa: E402

SEED = 20261115
NOISE_SIGMA = 20
EXPOSURE_FACTOR = 0.45
GAMMAS = (0.5, 0.8, 1.0, 1.5, 2.2)
THRESHOLDS = (64, 100, 128, 160, 200)
ROOT = Path(__file__).resolve().parents[1]
LENNA = ROOT / "experiments" / "Lenna.png"
RESULTS = ROOT / "docs" / "image-processing" / "results"

# Chart layout (x = column, y = row; right/bottom exclusive like the web ROI).
RAMP_ROWS = (0, 32)                     # value = column index 0..255
BACKGROUND = 60
OBJECT = 180
DARK_PATCH, DARK_BG = (20, 44, 50, 74), (56, 44, 86, 74)          # 75 on 60: dark, low contrast
BRIGHT_BLOCK, BRIGHT_PATCH = (180, 40, 250, 90), (200, 50, 230, 80)  # 225 on 210: bright, low contrast
OBJECT_ROI, BODY_BG_ROI = (70, 120, 110, 160), (12, 200, 42, 240)  # inside the circle / plain background
ROIS = {"dark_patch": DARK_PATCH, "dark_background": DARK_BG,
        "bright_patch": BRIGHT_PATCH, "object": OBJECT_ROI, "background": BODY_BG_ROI}


def chart() -> np.ndarray:
    """256x256 grayscale chart (uint8)."""
    y, x = np.mgrid[0:256, 0:256]
    image = np.full((256, 256), BACKGROUND, dtype=np.uint8)
    image[RAMP_ROWS[0]:RAMP_ROWS[1], :] = x[RAMP_ROWS[0]:RAMP_ROWS[1], :]
    image[(x - 90) ** 2 + (y - 140) ** 2 <= 40 ** 2] = OBJECT             # circle
    image[180:230, 150:230] = OBJECT                                       # rectangle
    image[110:250, 40:42] = OBJECT                                         # 2 px thin line
    left, top, right, bottom = DARK_PATCH
    image[top:bottom, left:right] = 75
    left, top, right, bottom = BRIGHT_BLOCK
    image[top:bottom, left:right] = 210
    left, top, right, bottom = BRIGHT_PATCH
    image[top:bottom, left:right] = 225
    return image


def ground_truth() -> np.ndarray:
    """Bright structures of the clean chart body (rows below the ramp)."""
    return chart()[RAMP_ROWS[1]:, :] >= 128


def exposure(gray: np.ndarray, kind: str) -> np.ndarray:
    values = gray.astype(np.float64)
    if kind == "under":
        values = values * EXPOSURE_FACTOR
    elif kind == "over":
        values = 255.0 - (255.0 - values) * EXPOSURE_FACTOR
    return np.clip(np.rint(values), 0, 255).astype(np.uint8)


def to_rgb(gray: np.ndarray) -> Image.Image:
    return Image.fromarray(np.repeat(gray[:, :, None], 3, axis=2))


def inputs() -> dict[str, Image.Image]:
    base = chart()
    noisy = generate_noise_input(base, NOISE_SIGMA, seed=SEED)   # one noise field shared by R, G, B
    images = {
        "chart_normal": to_rgb(base),
        "chart_under": to_rgb(exposure(base, "under")),
        "chart_over": to_rgb(exposure(base, "over")),
        "chart_noise20": to_rgb(noisy),
    }
    if LENNA.exists():
        images["lenna_gray"] = Image.fromarray(read_input(LENNA, "grayscale"))
    return images


def brightness(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L"), dtype=np.float64)


def region_mean(values: np.ndarray, bounds) -> float:
    left, top, right, bottom = bounds
    return float(values[top:bottom, left:right].mean())


def distinct_levels(values: np.ndarray, columns=slice(0, 256)) -> int:
    """Distinct output codes across one ramp row: lost codes are merged input levels."""
    return int(np.unique(values[RAMP_ROWS[0], columns]).size)


def segmentation_scores(mask: np.ndarray, truth: np.ndarray) -> dict:
    predicted = mask.astype(bool)
    true_positive = np.logical_and(predicted, truth).sum()
    union = np.logical_or(predicted, truth).sum()
    negatives, positives = (~truth).sum(), truth.sum()
    return {
        "iou": float(true_positive / union) if union else 1.0,
        "false_positive_rate": float(np.logical_and(predicted, ~truth).sum() / negatives),
        "false_negative_rate": float(np.logical_and(~predicted, truth).sum() / positives),
    }


def otsu_threshold(image: Image.Image) -> int:
    """Reference only (not a team algorithm): OpenCV Otsu on the same L body region."""
    body = np.asarray(image.convert("L"))[RAMP_ROWS[1]:, :]
    value, _ = cv2.threshold(body, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return int(value)


def montage(rows: list[tuple[str, list[tuple[str, Image.Image]]]]) -> np.ndarray:
    header, size = 26, 256
    columns = max(len(items) for _, items in rows)
    sheet = np.full((len(rows) * (size + header), columns * size, 3), 30, dtype=np.uint8)
    for row_index, (row_label, items) in enumerate(rows):
        top = row_index * (size + header)
        for column_index, (label, image) in enumerate(items):
            left = column_index * size
            text = f"{row_label} | {label}" if column_index == 0 else label
            cv2.putText(sheet, text, (left + 4, top + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)
            sheet[top + header:top + header + size, left:left + size] = np.asarray(image.resize((size, size)))
    return sheet


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def roi_columns(original: Image.Image, result: Image.Image) -> dict:
    columns = {}
    for name in ("dark_patch", "bright_patch", "object"):
        stats = compare_roi(original, result, ROIS[name])
        columns[f"{name}_input_mean"] = stats["original"]["mean"]
        columns[f"{name}_result_mean"] = stats["result"]["mean"]
        columns[f"{name}_result_variance"] = stats["result"]["population_variance"]
    return columns


def run_gamma(images: dict[str, Image.Image], output: Path) -> list[dict]:
    output.mkdir(parents=True, exist_ok=True)
    rows, montage_rows = [], []
    for name, image in images.items():
        save_rgb_png(output / f"{name}.png", np.asarray(image))
        source = brightness(image)
        tiles = [("input", image)]
        for gamma in GAMMAS:
            result = gamma_correction(image, gamma)
            save_rgb_png(output / f"{name}-gamma-{gamma:g}.png", np.asarray(result))
            values = brightness(result)
            row = {
                "input": name, "gamma": gamma,
                "input_mean": float(source.mean()), "result_mean": float(values.mean()),
                "result_std": float(values.std()),
                "saturated_fraction": float(np.mean((values <= 0) | (values >= 255))),
            }
            if name.startswith("chart"):
                row.update({
                    "dark_contrast_input": region_mean(source, DARK_PATCH) - region_mean(source, DARK_BG),
                    "dark_contrast_result": region_mean(values, DARK_PATCH) - region_mean(values, DARK_BG),
                    "bright_contrast_input": region_mean(source, BRIGHT_PATCH) - region_mean(source, BRIGHT_BLOCK),
                    "bright_contrast_result": region_mean(values, BRIGHT_PATCH) - region_mean(values, BRIGHT_BLOCK),
                    "object_contrast_result": region_mean(values, OBJECT_ROI) - region_mean(values, BODY_BG_ROI),
                    "ramp_levels_input": distinct_levels(source),
                    "ramp_levels_result": distinct_levels(values),
                    "dark_ramp_levels_result": distinct_levels(values, slice(0, 64)),
                    "bright_ramp_levels_result": distinct_levels(values, slice(192, 256)),
                })
            row.update(roi_columns(image, result))
            rows.append(row)
            if gamma != 1.0:
                tiles.append((f"gamma={gamma:g}", result))
        montage_rows.append((name, tiles))
    write_csv(output / "metrics.csv", _complete(rows))
    save_rgb_png(output / "comparison.png", montage(montage_rows))
    return rows


def run_threshold(images: dict[str, Image.Image], output: Path) -> list[dict]:
    output.mkdir(parents=True, exist_ok=True)
    truth = ground_truth()
    rows, montage_rows = [], []
    for name, image in images.items():
        save_rgb_png(output / f"{name}.png", np.asarray(image))
        otsu = otsu_threshold(image)
        tiles = [("input", image)]
        for threshold, kind in [(value, "fixed") for value in THRESHOLDS] + [(otsu, "otsu_reference")]:
            result = threshold_segmentation(image, threshold)
            suffix = f"t-{threshold}" if kind == "fixed" else f"otsu-{threshold}"
            save_rgb_png(output / f"{name}-{suffix}.png", np.asarray(result))
            mask = np.asarray(result)[RAMP_ROWS[1]:, :, 0] == 255
            row = {"input": name, "threshold": threshold, "kind": kind,
                   "white_fraction_body": float(mask.mean())}
            if name.startswith("chart"):
                row.update(segmentation_scores(mask, truth))
            row.update(roi_columns(image, result))
            rows.append(row)
            tiles.append((f"t={threshold}" if kind == "fixed" else f"Otsu t={threshold}", result))
        montage_rows.append((name, tiles))
    write_csv(output / "metrics.csv", _complete(rows))
    save_rgb_png(output / "comparison.png", montage(montage_rows))
    return rows


def _complete(rows: list[dict]) -> list[dict]:
    """Same columns for every row; chart-only metrics are blank for Lenna."""
    keys = list(dict.fromkeys(key for row in rows for key in row))
    return [{key: row.get(key, "") for key in keys} for row in rows]


def manifest(output: Path, algorithm_id: str, version: str, parameters: dict) -> None:
    data = {
        "algorithm_id": algorithm_id, "algorithm_version": version, "parameters": parameters,
        "python": platform.python_version(), "numpy": np.__version__, "opencv": cv2.__version__,
        "pillow": pillow_version, "roi_calculation": calculation_metadata(),
        "seed": SEED, "generator": "numpy.random.Generator(PCG64)", "noise_sigma": NOISE_SIGMA,
        "noise_policy": "one L noise field copied to R, G, B; float64 addition, np.rint, clip 0..255",
        "exposure": {"under": f"x*{EXPOSURE_FACTOR}", "over": f"255-(255-x)*{EXPOSURE_FACTOR}", "rounding": "np.rint"},
        "chart": {"ramp_rows": RAMP_ROWS, "background": BACKGROUND, "object": OBJECT,
                  "dark_patch": [DARK_PATCH, 75], "bright_block": [BRIGHT_BLOCK, 210],
                  "bright_patch": [BRIGHT_PATCH, 225], "rois": ROIS},
        "lenna": "experiments/Lenna.png, INTER_AREA 256x256, Pillow L copied to RGB (same as Gaussian experiment)",
        "file_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in sorted(output.glob("*.png"))},
    }
    (output / "manifest.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                                          encoding="utf-8", newline="\n")


def _fmt(value, digits=1) -> str:
    return "-" if value in ("", None) else f"{float(value):.{digits}f}"


def gamma_summary(output: Path, rows: list[dict]) -> None:
    lines = [
        "# 감마 보정 비교 실험", "",
        "입력: 합성 차트(정상, 노출 부족 ×0.45, 노출 과다, 잡음 σ=20)와 Lenna 흑백. 값은 Pillow L 밝기 기준이다.",
        "대비는 패치 평균에서 주변 평균을 뺀 값이다. 밝기 단계 수는 0~255 밝기 띠 한 줄에서 서로 다른 출력값의 개수다.",
        "", "| 입력 | γ | 평균 밝기 (입력→결과) | 어두운 패치 대비 (입력→결과) | 밝은 패치 대비 (입력→결과) "
        "| 밝기 단계 수 (전체/어두운 0~63/밝은 192~255) |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        levels = ("-" if row.get("ramp_levels_result") in ("", None) else
                  f"{row['ramp_levels_result']} / {row['dark_ramp_levels_result']} / {row['bright_ramp_levels_result']}")
        lines.append(
            f"| {row['input']} | {row['gamma']:g} | {_fmt(row['input_mean'])} → {_fmt(row['result_mean'])} "
            f"| {_fmt(row.get('dark_contrast_input'))} → {_fmt(row.get('dark_contrast_result'))} "
            f"| {_fmt(row.get('bright_contrast_input'))} → {_fmt(row.get('bright_contrast_result'))} | {levels} |")
    lines += ["", "전체 열(ROI 평균·분산, 포화 비율, 물체 대비)은 `metrics.csv`, 결과 영상은 `comparison.png`에 있다.",
              "해석과 한계는 `docs/image-processing/gamma-web.md`의 비교 실험 절에 정리했다.",
              "교육용 합성 실험이며 진단 품질이나 보편적인 최적 γ를 뜻하지 않는다."]
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def threshold_summary(output: Path, rows: list[dict]) -> None:
    lines = [
        "# 임계값 분할 비교 실험", "",
        "정답 마스크: 깨끗한 정상 차트 본문(32행 아래)에서 L ≥ 128인 밝은 구조. 노출·잡음을 바꿔도 같은 위치를 정답으로 쓴다.",
        "IoU는 결과 흰색 영역과 정답의 교집합/합집합, FPR은 배경을 흰색으로 잘못 고른 비율, FNR은 구조를 놓친 비율이다.",
        "Otsu는 팀 알고리즘이 아니라 같은 영상에 맞는 임계값이 무엇인지 보여주는 참고값이다(OpenCV, 본문 L).",
        "", "| 입력 | t | 종류 | 흰색 비율 | IoU | FPR | FNR |", "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        kind = "고정" if row["kind"] == "fixed" else "Otsu 참고"
        lines.append(f"| {row['input']} | {row['threshold']} | {kind} | {_fmt(row['white_fraction_body'], 3)} "
                     f"| {_fmt(row.get('iou'), 3)} | {_fmt(row.get('false_positive_rate'), 3)} "
                     f"| {_fmt(row.get('false_negative_rate'), 3)} |")
    lines += ["", "ROI 평균·분산 열은 `metrics.csv`, 결과 영상은 `comparison.png`에 있다.",
              "해석과 한계는 `docs/image-processing/threshold-web.md`의 비교 실험 절에 정리했다.",
              "교육용 합성 실험이며 의료적 영역 분할 성능을 뜻하지 않는다."]
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def run(results: Path = RESULTS) -> tuple[list[dict], list[dict]]:
    images = inputs()
    gamma_rows = run_gamma(images, results / "gamma")
    threshold_rows = run_threshold(images, results / "threshold")
    gamma_summary(results / "gamma", gamma_rows)
    threshold_summary(results / "threshold", threshold_rows)
    manifest(results / "gamma", GAMMA_ALGORITHM_ID, GAMMA_ALGORITHM_VERSION,
             {"gamma": GAMMAS, "lut": {str(g): gamma_lut(g)[[0, 32, 64, 128, 192, 255]].tolist() for g in GAMMAS}})
    manifest(results / "threshold", THRESHOLD_ALGORITHM_ID, THRESHOLD_ALGORITHM_VERSION,
             {"threshold": THRESHOLDS, "otsu_reference": "cv2.THRESH_OTSU on body L; reference, not a team algorithm",
              "ground_truth": "clean chart body (rows 32..255) >= 128"})
    return gamma_rows, threshold_rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=RESULTS)
    gamma_rows, threshold_rows = run(parser.parse_args().output)
    print(f"gamma rows {len(gamma_rows)}, threshold rows {len(threshold_rows)}")


if __name__ == "__main__":
    main()
