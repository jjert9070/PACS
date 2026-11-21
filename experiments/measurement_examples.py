"""Write docs/handoff/b-measurement-examples.json: fixed inputs and outputs of B's #38 functions.

A and C use these examples in their own tests before the real connection exists.
Run: uv run --locked python experiments/measurement_examples.py
test_measurement_examples.py checks that the committed file matches this code.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import dicom_input  # noqa: E402
from measurement_tools import (  # noqa: E402
    MeasurementInputError,
    calibration_from_metadata,
    measure_annotation,
    synthetic_calibration,
)
from processing_compat import ENVIRONMENT_SCHEMA, check_compatibility  # noqa: E402

OUTPUT = ROOT / "docs" / "handoff" / "b-measurement-examples.json"
# Fixed library versions so the file does not depend on the machine that writes it.
ENVIRONMENT = {"schema": ENVIRONMENT_SCHEMA,
               "libraries": {"numpy": "2.2.6", "pillow": "12.3.0", "opencv": "4.12.0"}}


def source_ref(source_id, width, height, file_format="DICOM"):
    return {
        "schema": "pacs_source_ref_v1", "owner_uid": "<authenticated uid>", "project_id": "<project id>",
        "patient_issuer": "PACS-EDU-SYNTH", "patient_id": "SYN-0001", "study_id": "2.25.1",
        "series_id": "2.25.2", "object_id": "2.25.3", "source_id": source_id, "file_format": file_format,
        "frame_index": 0, "width": width, "height": height,
        "coordinate_space": "exif_transposed_original_pixels_v1",
    }


def error(source, geometry, calibration=None):
    try:
        measure_annotation(source, geometry, calibration)
    except MeasurementInputError as caught:
        return {"code": caught.code, "message": str(caught)}
    raise AssertionError("expected MeasurementInputError")


def compatibility(*args):
    check = check_compatibility(*args)
    return {"status": check.status, "environment_status": check.environment_status,
            "can_restore_settings": check.can_restore_settings, "can_rerun": check.can_rerun,
            "same_conditions": check.same_conditions}


def build() -> dict:
    synthetic = source_ref("a" * 64, 192, 160)
    calibration = synthetic_calibration(synthetic, 0.5, 0.8)
    png = source_ref("c" * 64, 640, 480, "PNG")

    def ct_with_spacing(ds):
        ds.PixelSpacing = [0.5, 0.8]
    import test_dicom_input as samples
    data = samples.rewrite(samples.sample("ct_phantom.dcm"), ct_with_spacing)
    imported = dicom_input.read_dicom("ct_phantom.dcm", data)
    ct = source_ref(imported.source_id, imported.columns, imported.rows)
    ct_calibration = calibration_from_metadata(ct, imported.pixel_geometry)
    ct_line = {"kind": "line", "points": [[10, 20], [13, 24]]}

    return {
        "note": "Synthetic data only. owner_uid and project_id are placeholders that C fills from the session.",
        "source_ref": synthetic,
        "measure_annotation": [
            {"case": "line 3-4-5, synthetic verified spacing (row 0.5 mm, column 0.8 mm)",
             "geometry": {"kind": "line", "points": [[10, 20], [13, 24]]}, "calibration": calibration,
             "result": measure_annotation(synthetic, {"kind": "line", "points": [[10, 20], [13, 24]]}, calibration)},
            {"case": "rectangle, same spacing",
             "geometry": {"kind": "rectangle", "bounds": [10, 20, 30, 50]}, "calibration": calibration,
             "result": measure_annotation(synthetic, {"kind": "rectangle", "bounds": [10, 20, 30, 50]}, calibration)},
            {"case": "one-pixel rectangle at the corner, no calibration (physical null)",
             "geometry": {"kind": "rectangle", "bounds": [191, 159, 192, 160]}, "calibration": None,
             "result": measure_annotation(synthetic, {"kind": "rectangle", "bounds": [191, 159, 192, 160]})},
            {"case": "PNG: pixel units only", "source_ref": png,
             "geometry": {"kind": "line", "points": [[0, 0], [30, 40]]},
             "calibration": calibration_from_metadata(png, None),
             "result": measure_annotation(png, {"kind": "line", "points": [[0, 0], [30, 40]]},
                                          calibration_from_metadata(png, None))},
            {"case": "synthetic CT with PixelSpacing [0.5, 0.8] read by B (verified)", "source_ref": ct,
             "metadata": imported.pixel_geometry, "geometry": ct_line, "calibration": ct_calibration,
             "result": measure_annotation(ct, ct_line, ct_calibration)},
        ],
        "errors": [
            {"case": "same point twice", **error(synthetic, {"kind": "line", "points": [[5, 5], [5, 5]]})},
            {"case": "float coordinate", **error(synthetic, {"kind": "line", "points": [[0.0, 0], [3, 4]]})},
            {"case": "calibration of another original",
             **error(synthetic, {"kind": "line", "points": [[0, 0], [3, 4]]},
                     synthetic_calibration(source_ref("b" * 64, 192, 160), 0.5, 0.8))},
            {"case": "second frame", **error(dict(synthetic, frame_index=1),
                                             {"kind": "line", "points": [[0, 0], [3, 4]]})},
        ],
        "processing_compat": {
            "environment_json": ENVIRONMENT,
            "results": [
                {"case": "supported, same environment",
                 "input": ["gaussian_blur", "2.0.0", {"radius": 2.0}, ENVIRONMENT],
                 "result": compatibility("gaussian_blur", "2.0.0", {"radius": 2.0}, ENVIRONMENT, )},
                {"case": "supported, record without environment (old record)",
                 "input": ["gaussian_blur", "2.0.0", {"radius": 2.0}, None],
                 "result": compatibility("gaussian_blur", "2.0.0", {"radius": 2.0}, None)},
                {"case": "unsupported version is not replaced by the current one",
                 "input": ["gaussian_blur", "1.0.0", {"radius": 2.0}, ENVIRONMENT],
                 "result": compatibility("gaussian_blur", "1.0.0", {"radius": 2.0}, ENVIRONMENT)},
                {"case": "environment without schema",
                 "input": ["gamma_correction", "1.0.0", {"gamma": 0.5}, {"libraries": ENVIRONMENT["libraries"]}],
                 "result": compatibility("gamma_correction", "1.0.0", {"gamma": 0.5},
                                         {"libraries": ENVIRONMENT["libraries"]})},
            ],
        },
    }


def render() -> str:
    return json.dumps(build(), ensure_ascii=False, indent=2, allow_nan=False) + "\n"


if __name__ == "__main__":
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
