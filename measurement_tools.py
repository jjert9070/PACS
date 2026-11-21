"""Line and rectangle measurement on original pixel coordinates (#38, common contract R2.1).

Pure calculation: no Streamlit, Firebase, UI or storage imports. Lengths and areas use the
original pixel grid and, only when a calibration is verified, the row and column spacing.
Brightness statistics stay in roi.py and are a different calculation.

Physical units are given only for documented conditions (see docs/image-processing/measurement.md):
a missing or unverified calibration keeps the pixel value and returns null physical fields.
"""

import math
import re

import numpy as np

from roi import COORDINATE_SYSTEM, validate_roi

SOURCE_REF_SCHEMA = "pacs_source_ref_v1"
MEASUREMENT_METHOD_VERSION = "measurement_v1"
CALIBRATION_METHOD_VERSION = "calibration_v1"
CALIBRATION_STATUSES = ("verified", "unverified", "unsupported")
EVIDENCE_KINDS = ("none", "synthetic_fixture", "dicom_supported_geometry")
FILE_FORMATS = ("DICOM", "PNG", "JPEG")
SUPPORTED_FRAME_INDEX = 0

# SOP Classes whose Pixel Spacing is the in-plane spacing of the patient image plane (PS3.3 C.7.6.2).
CT_IMAGE_STORAGE = "1.2.840.10008.5.1.4.1.1.2"
MR_IMAGE_STORAGE = "1.2.840.10008.5.1.4.1.1.4"
VERIFIED_SOP_CLASSES = {CT_IMAGE_STORAGE: "CT", MR_IMAGE_STORAGE: "MR"}
# Projection radiography: spacing is at the detector or estimated; magnification is not known here.
PROJECTION_SOP_CLASSES = {
    "1.2.840.10008.5.1.4.1.1.1": "CR",
    "1.2.840.10008.5.1.4.1.1.1.1": "DX",
}

SOURCE_REF_FIELDS = (
    "schema", "owner_uid", "project_id", "patient_issuer", "patient_id", "study_id", "series_id",
    "object_id", "source_id", "file_format", "frame_index", "width", "height", "coordinate_space",
)
CALIBRATION_FIELDS = (
    "status", "reason", "row_spacing_mm", "column_spacing_mm", "source_id", "evidence", "method_version",
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
TOLERANCE = 1e-9


class MeasurementInputError(ValueError):
    """Rejected measurement input. `code` is one of the contract codes; the message has no patient data."""

    CODES = ("invalid_geometry", "source_mismatch", "unsupported_frame")

    def __init__(self, code: str, message: str):
        if code not in self.CODES:
            raise AssertionError(f"unknown measurement error code {code!r}")
        super().__init__(message)
        self.code = code


def _is_int(value) -> bool:
    return isinstance(value, (int, np.integer)) and not isinstance(value, (bool, np.bool_))


def _is_positive_finite(value) -> bool:
    return (isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, (bool, np.bool_))
            and math.isfinite(float(value)) and float(value) > 0)


def validate_source_ref(source_ref) -> dict:
    """Check the shape of a SourceRef. C decides that it belongs to the signed-in user (not done here)."""
    if not isinstance(source_ref, dict) or set(source_ref) != set(SOURCE_REF_FIELDS):
        raise MeasurementInputError("source_mismatch", "원본 참조의 필드가 계약(pacs_source_ref_v1)과 다릅니다.")
    if source_ref["schema"] != SOURCE_REF_SCHEMA:
        raise MeasurementInputError("source_mismatch", "지원하지 않는 원본 참조 schema입니다.")
    for name in ("owner_uid", "project_id", "patient_issuer", "patient_id", "study_id", "series_id", "object_id"):
        if not isinstance(source_ref[name], str) or not source_ref[name]:
            raise MeasurementInputError("source_mismatch", f"원본 참조의 {name}가 비어 있거나 문자열이 아닙니다.")
    if not isinstance(source_ref["source_id"], str) or not _SHA256.fullmatch(source_ref["source_id"]):
        raise MeasurementInputError("source_mismatch", "source_id는 원본 바이트의 소문자 SHA-256 64자리여야 합니다.")
    if source_ref["file_format"] not in FILE_FORMATS:
        raise MeasurementInputError("source_mismatch", "file_format은 DICOM, PNG, JPEG 중 하나여야 합니다.")
    if not _is_int(source_ref["frame_index"]):
        raise MeasurementInputError("source_mismatch", "frame_index는 정수여야 합니다.")
    if int(source_ref["frame_index"]) != SUPPORTED_FRAME_INDEX:
        raise MeasurementInputError("unsupported_frame", "현재는 첫 프레임(frame_index 0)만 측정할 수 있습니다.")
    for name in ("width", "height"):
        if not _is_int(source_ref[name]) or int(source_ref[name]) <= 0:
            raise MeasurementInputError("source_mismatch", f"원본 참조의 {name}는 양의 정수여야 합니다.")
    if source_ref["coordinate_space"] != COORDINATE_SYSTEM:
        raise MeasurementInputError("source_mismatch", f"좌표계가 {COORDINATE_SYSTEM}가 아닙니다.")
    return source_ref


def _validate_geometry(source_ref: dict, geometry) -> tuple[str, tuple]:
    if not isinstance(geometry, dict) or "kind" not in geometry:
        raise MeasurementInputError("invalid_geometry", "geometry는 kind를 가진 사전이어야 합니다.")
    width, height = int(source_ref["width"]), int(source_ref["height"])
    kind = geometry["kind"]
    if kind == "line":
        if set(geometry) != {"kind", "points"}:
            raise MeasurementInputError("invalid_geometry", "선은 kind와 points만 가집니다.")
        points = geometry["points"]
        if not isinstance(points, (list, tuple)) or len(points) != 2 or not all(
                isinstance(point, (list, tuple)) and len(point) == 2 for point in points):
            raise MeasurementInputError("invalid_geometry", "선은 (x, y) 점 두 개가 필요합니다.")
        for x, y in points:
            if not (_is_int(x) and _is_int(y)):
                raise MeasurementInputError("invalid_geometry", "선의 좌표는 원본 픽셀의 정수 인덱스여야 합니다.")
            if not (0 <= x < width and 0 <= y < height):
                raise MeasurementInputError(
                    "invalid_geometry", f"선의 점은 0 ≤ x < {width}, 0 ≤ y < {height} 범위여야 합니다.")
        (x1, y1), (x2, y2) = ((int(x), int(y)) for x, y in points)
        if (x1, y1) == (x2, y2):
            raise MeasurementInputError("invalid_geometry", "선의 두 점이 같습니다.")
        return "line", (x1, y1, x2, y2)
    if kind == "rectangle":
        if set(geometry) != {"kind", "bounds"}:
            raise MeasurementInputError("invalid_geometry", "사각형은 kind와 bounds만 가집니다.")
        try:
            bounds = validate_roi(geometry["bounds"], (width, height))
        except (TypeError, ValueError) as error:
            raise MeasurementInputError("invalid_geometry", f"사각형 경계가 올바르지 않습니다. {error}") from None
        return "rectangle", bounds
    raise MeasurementInputError("invalid_geometry", "kind는 'line' 또는 'rectangle'이어야 합니다.")


def _calibration(status, reason, source_id, row=None, column=None, kind="none", attributes=None) -> dict:
    return {
        "status": status, "reason": reason,
        "row_spacing_mm": None if row is None else float(row),
        "column_spacing_mm": None if column is None else float(column),
        "source_id": source_id,
        "evidence": {"kind": kind, "attributes": dict(attributes or {})},
        "method_version": CALIBRATION_METHOD_VERSION,
    }


def validate_calibration(source_ref: dict, calibration) -> dict:
    """Shape and self-consistency of a calibration. Inconsistent or foreign calibrations are rejected."""
    if not isinstance(calibration, dict) or set(calibration) != set(CALIBRATION_FIELDS):
        raise MeasurementInputError("source_mismatch", "보정 정보의 필드가 계약과 다릅니다.")
    if calibration["source_id"] != source_ref["source_id"]:
        raise MeasurementInputError("source_mismatch", "다른 원본에서 만든 보정 정보입니다.")
    evidence = calibration["evidence"]
    if (calibration["status"] not in CALIBRATION_STATUSES or not isinstance(calibration["reason"], str)
            or calibration["method_version"] != CALIBRATION_METHOD_VERSION
            or not isinstance(evidence, dict) or set(evidence) != {"kind", "attributes"}
            or evidence["kind"] not in EVIDENCE_KINDS or not isinstance(evidence["attributes"], dict)):
        raise MeasurementInputError("source_mismatch", "보정 정보의 상태나 근거 형식이 올바르지 않습니다.")
    for name in ("row_spacing_mm", "column_spacing_mm"):
        value = calibration[name]
        if value is not None and not _is_positive_finite(value):
            raise MeasurementInputError("source_mismatch", f"보정 정보의 {name}는 null 또는 유한한 양수여야 합니다.")
    if calibration["status"] == "verified" and (
            calibration["row_spacing_mm"] is None or calibration["column_spacing_mm"] is None
            or evidence["kind"] == "none"):
        # A verified status without spacing or evidence is not something B produces: treat it as forged.
        raise MeasurementInputError("source_mismatch", "근거와 간격이 없는 verified 보정 정보는 받을 수 없습니다.")
    return calibration


def calibration_from_metadata(source_ref, metadata) -> dict:
    """Decide whether physical units may be used, from metadata B read from the original.

    `metadata` is `DicomImport.pixel_geometry` for DICOM, or None. It must not come from user input.
    Missing or unsupported calibration is not an error: the result says unverified or unsupported.
    """
    source_ref = validate_source_ref(source_ref)
    source_id = source_ref["source_id"]
    if source_ref["file_format"] in ("PNG", "JPEG"):
        return _calibration("unsupported", "raster_has_no_physical_geometry", source_id)
    if metadata is None:
        return _calibration("unverified", "no_calibration_metadata", source_id)
    if not isinstance(metadata, dict) or metadata.get("schema") != "b_dicom_pixel_geometry_v1":
        raise MeasurementInputError("source_mismatch", "B가 원본에서 읽은 DICOM 메타데이터가 아닙니다.")
    if metadata.get("source_id") != source_id or metadata.get("file_format") != source_ref["file_format"]:
        raise MeasurementInputError("source_mismatch", "다른 원본의 메타데이터입니다.")
    if (metadata.get("rows"), metadata.get("columns")) != (source_ref["height"], source_ref["width"]):
        raise MeasurementInputError("source_mismatch", "메타데이터의 행·열 크기가 원본 참조와 다릅니다.")
    if metadata.get("frame_count") != 1:
        raise MeasurementInputError("unsupported_frame", "단일 프레임 DICOM만 측정할 수 있습니다.")
    sop_class = metadata.get("sop_class_uid")
    spacing = metadata.get("pixel_spacing") or {}
    values = spacing.get("values_mm")
    attributes = {"sop_class_uid": sop_class, "modality": metadata.get("modality", ""),
                  "rows": metadata["rows"], "columns": metadata["columns"],
                  "pixel_spacing_raw": spacing.get("raw")}
    if sop_class in VERIFIED_SOP_CLASSES:
        if spacing.get("status") == "absent":
            return _calibration("unverified", "pixel_spacing_absent", source_id)
        if spacing.get("status") != "present" or not all(_is_positive_finite(value) for value in values):
            # Zero (the standard's single row/column case), negative or malformed spacing: no unit.
            return _calibration("unverified", "pixel_spacing_invalid", source_id)
        row, column = values  # DICOM order: row spacing (vertical) first, column spacing (horizontal) second
        return _calibration("verified", "dicom_image_plane_pixel_spacing", source_id, row, column,
                            "dicom_supported_geometry", attributes)
    if sop_class in PROJECTION_SOP_CLASSES:
        # Detector or estimated spacing; magnification at the patient is not established here.
        return _calibration("unverified", "projection_radiograph_spacing_not_verified", source_id)
    return _calibration("unsupported", "sop_class_without_physical_geometry", source_id)


def synthetic_calibration(source_ref, row_spacing_mm, column_spacing_mm) -> dict:
    """Verified calibration for synthetic test fixtures only. calibration_matches rejects it for real originals."""
    source_ref = validate_source_ref(source_ref)
    if not (_is_positive_finite(row_spacing_mm) and _is_positive_finite(column_spacing_mm)):
        raise MeasurementInputError("source_mismatch", "합성 보정의 간격은 유한한 양수여야 합니다.")
    return _calibration("verified", "synthetic_fixture_for_tests", source_ref["source_id"],
                        row_spacing_mm, column_spacing_mm, "synthetic_fixture",
                        {"row_spacing_mm": float(row_spacing_mm), "column_spacing_mm": float(column_spacing_mm)})


def measure_annotation(source_ref, geometry, calibration=None) -> dict:
    """Pixel length or area and, with a verified calibration, the physical value."""
    source_ref = validate_source_ref(source_ref)
    kind, coordinates = _validate_geometry(source_ref, geometry)
    if calibration is None:
        calibration = _calibration("unverified", "no_calibration", source_ref["source_id"])
    calibration = validate_calibration(source_ref, calibration)
    row, column = calibration["row_spacing_mm"], calibration["column_spacing_mm"]
    verified = calibration["status"] == "verified"
    if kind == "line":
        x1, y1, x2, y2 = coordinates
        dx, dy = x2 - x1, y2 - y1
        pixel_value, pixel_unit, physical_unit = math.hypot(dx, dy), "px", "mm"
        physical = math.hypot(dx * column, dy * row) if verified else None
    else:
        left, top, right, bottom = coordinates
        pixel_value, pixel_unit, physical_unit = (right - left) * (bottom - top), "px2", "mm2"
        physical = pixel_value * column * row if verified else None
    if physical is not None and not math.isfinite(physical):
        raise MeasurementInputError("source_mismatch", "보정 간격으로 계산한 실제 값이 유한하지 않습니다.")
    return {
        "pixel_value": pixel_value,
        "pixel_unit": pixel_unit,
        "physical_value": physical,
        "physical_unit": physical_unit if physical is not None else None,
        "calibration_status": calibration["status"],
        "calibration_reason": calibration["reason"],
        "method_version": MEASUREMENT_METHOD_VERSION,
    }


def calibration_matches(source_ref, calibration, metadata) -> bool:
    """For C: the submitted calibration equals what B derives from the trusted original's metadata."""
    try:
        return validate_calibration(source_ref, calibration) == calibration_from_metadata(source_ref, metadata)
    except MeasurementInputError:
        return False


def measurement_matches(source_ref, geometry, calibration, measurement) -> bool:
    """For C: recompute with B and compare (numbers with 1e-9 absolute and relative tolerance)."""
    try:
        expected = measure_annotation(source_ref, geometry, calibration)
    except MeasurementInputError:
        return False
    if not isinstance(measurement, dict) or set(measurement) != set(expected):
        return False
    for name, value in expected.items():
        stored = measurement[name]
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not (isinstance(stored, (int, float)) and not isinstance(stored, bool)
                    and math.isclose(stored, value, rel_tol=TOLERANCE, abs_tol=TOLERANCE)):
                return False
        elif stored != value:
            return False
    return True
