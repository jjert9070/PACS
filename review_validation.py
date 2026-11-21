"""Validate and detach a complete review draft before publishing any files."""

from copy import deepcopy
import json
import math
from pathlib import Path
import re

from case_processing import freeze_processing
from case_records import _processing_json
from firebase_client import Session
from review_sources import open_review_source
from review_storage import _metadata_copy
from roi import compare_roi, validate_roi
from viewer import ViewerSource


_FIELDS = {
    "schema", "review_id", "source_ref", "annotations", "roi",
    "roi_statistics", "processing", "display_mapping",
}


def _check_statistics(actual: dict, expected: dict) -> None:
    if not isinstance(actual, dict) or actual.keys() != expected.keys():
        raise ValueError("ROI 통계의 필드가 올바르지 않습니다.")
    if (any(type(actual[key]) is not int for key in ("width", "height"))
            or not isinstance(actual["bounds"], list)
            or any(type(value) is not int for value in actual["bounds"])):
        raise ValueError("ROI 통계의 좌표와 크기는 정수여야 합니다.")
    for key in ("source_id", "bounds", "width", "height", "coordinate_system", "brightness_method"):
        if actual[key] != expected[key]:
            raise ValueError("ROI 통계의 원본과 좌표가 다릅니다.")
    calculation = actual["calculation"]
    if (not isinstance(calculation, dict) or calculation.keys() != expected["calculation"].keys()
            or any(calculation[key] != expected["calculation"][key]
                   for key in ("brightness_method", "variance_definition", "calculation_dtype", "ddof"))
            or type(calculation["ddof"]) is not int
            or any(not isinstance(calculation[key], str) or not calculation[key] for key in ("numpy", "pillow"))):
        raise ValueError("ROI 통계의 계산 정의가 다릅니다.")
    for name in ("original", "result"):
        values, checked = actual[name], expected[name]
        if checked is None:
            if values is not None:
                raise ValueError("처리 결과가 없으면 결과 통계도 없어야 합니다.")
            continue
        if (not isinstance(values, dict) or values.keys() != checked.keys()
                or type(values["pixel_count"]) is not int or values["pixel_count"] != checked["pixel_count"]):
            raise ValueError("ROI 통계의 픽셀 수가 다릅니다.")
        for field in ("mean", "population_variance"):
            value = values[field]
            upper = 255 if field == "mean" else 255 * 255
            if (type(value) not in (int, float) or not 0 <= value <= upper or not math.isfinite(value)
                    or not math.isclose(value, checked[field], rel_tol=1e-12, abs_tol=1e-9)):
                raise ValueError("ROI 통계가 실제 원본과 결과에서 계산한 값과 다릅니다.")


def prepare_review_draft(
    project_id: str, session: Session, root: Path, draft: dict,
) -> tuple[ViewerSource, dict]:
    """Return checked original and detached draft, without file or DB writes.

    Annotation measurement and new algorithm compatibility must be connected
    before the public save API enables those operations.
    """
    if (not isinstance(draft, dict) or set(draft) != _FIELDS
            or draft["schema"] != "pacs_review_v1"
            or not isinstance(draft["review_id"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", draft["review_id"])):
        raise ValueError("검토 초안의 형식과 필수 필드를 확인하세요.")
    if not isinstance(draft["annotations"], list):
        raise ValueError("주석 목록 형식이 올바르지 않습니다.")
    if draft["annotations"]:
        raise ValueError("주석 저장은 B 측정 재검증 연결 후 지원합니다.")
    checked = deepcopy({key: value for key, value in draft.items() if key != "processing"})
    source, mapping = open_review_source(project_id, session, root, checked["source_ref"])
    try:
        same_mapping = json.dumps(checked["display_mapping"], sort_keys=True, allow_nan=False) == json.dumps(mapping, sort_keys=True)
    except (TypeError, ValueError):
        raise ValueError("표시 변환은 유한한 JSON 값이어야 합니다.") from None
    if not same_mapping:
        raise ValueError("표시 변환이 실제 원본 해석 결과와 다릅니다.")
    processing = freeze_processing(source, draft["processing"])
    bounds = checked["roi"]
    if bounds is None:
        if checked["roi_statistics"] is not None:
            raise ValueError("ROI가 없으면 통계도 없어야 합니다.")
    else:
        if (not isinstance(bounds, (list, tuple)) or any(type(value) is not int for value in bounds)):
            raise ValueError("ROI는 정수 좌표 네 개여야 합니다.")
        bounds = list(validate_roi(bounds, source.image.size))
        expected = compare_roi(source.image, None if processing is None else processing.result, bounds)
        expected["source_id"] = source.source_id
        _check_statistics(checked["roi_statistics"], expected)
    if processing is not None:
        if ((list(processing.roi) if processing.roi is not None else None) != bounds
                or processing.statistics != (checked["roi_statistics"] if bounds is not None else {})):
            raise ValueError("처리 기록과 검토 초안의 ROI 및 통계가 다릅니다.")
    checked["roi"] = bounds
    checked["processing"] = processing
    serializable = dict(checked)
    if processing is not None:
        serializable["processing"] = _processing_json(processing, source)
        # A SHA-256 PNG key always occupies the same number of bytes.
        serializable["processing"]["result_key"] = "0" * 64 + ".png"
    _metadata_copy(serializable)
    return source, checked
