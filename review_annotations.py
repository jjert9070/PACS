"""Validate annotations against the checked original and trusted B calculations."""

from copy import deepcopy
import json
import re

from roi import validate_roi


def validate_annotations(source_ref, annotations, calibration_from_metadata, measure_annotation, metadata):
    """Validate a whole snapshot; callbacks must be trusted B functions, never UI input."""
    if not isinstance(annotations, list):
        raise ValueError("주석 목록이 필요합니다.")
    if not callable(calibration_from_metadata) or not callable(measure_annotation):
        raise ValueError("신뢰할 수 있는 측정 검증 기능이 필요합니다.")
    checked = deepcopy(annotations)
    ids = set()
    for item in checked:
        common = {"schema", "annotation_id", "source_ref", "kind", "label", "calibration", "measurement"}
        if not isinstance(item, dict) or item.get("kind") not in ("line", "rectangle"):
            raise ValueError("지원하는 주석 형식이 아닙니다.")
        geometry_key = "points" if item["kind"] == "line" else "bounds"
        if set(item) != common | {geometry_key} or item["schema"] != "pacs_annotation_v1":
            raise ValueError("주석 필드가 올바르지 않습니다.")
        identifier = item["annotation_id"]
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier) or identifier in ids:
            raise ValueError("주석 ID가 올바르지 않거나 중복됐습니다.")
        ids.add(identifier)
        try:
            same_source = json.dumps(item["source_ref"], sort_keys=True, allow_nan=False) == json.dumps(source_ref, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError):
            same_source = False
        if (not same_source or not isinstance(item["label"], str) or len(item["label"]) > 500):
            raise ValueError("주석 원본 또는 이름이 올바르지 않습니다.")
        geometry = item[geometry_key]
        if geometry_key == "points":
            if (not isinstance(geometry, list) or len(geometry) != 2
                    or any(not isinstance(point, list) or len(point) != 2 for point in geometry)
                    or any(type(value) is not int for point in geometry for value in point)
                    or any(not (0 <= point[0] < source_ref["width"] and 0 <= point[1] < source_ref["height"])
                           for point in geometry) or geometry[0] == geometry[1]):
                raise ValueError("선 주석의 좌표가 올바르지 않습니다.")
        else:
            if (not isinstance(geometry, list) or len(geometry) != 4
                    or any(type(value) is not int for value in geometry)):
                raise ValueError("사각형 주석 좌표는 정수 경계 네 개여야 합니다.")
            validate_roi(geometry, (source_ref["width"], source_ref["height"]))
        calibration = calibration_from_metadata(deepcopy(source_ref), deepcopy(metadata))
        measurement = measure_annotation(deepcopy(source_ref), {"kind": item["kind"], geometry_key: deepcopy(geometry)},
                                         deepcopy(calibration))
        try:
            submitted = json.dumps([item["calibration"], item["measurement"]], sort_keys=True, allow_nan=False)
            trusted = json.dumps([calibration, measurement], sort_keys=True, allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError("측정 기록은 유한한 JSON 값이어야 합니다.") from None
        if submitted != trusted:
            raise ValueError("주석 보정 또는 측정 결과가 실제 원본의 계산 결과와 다릅니다.")
    return checked
