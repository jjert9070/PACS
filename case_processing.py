"""C's storage contract; independent of processing algorithms and their UI."""

from copy import deepcopy
from dataclasses import replace
import json

import streamlit as st

from case_records import ProcessingRecord, _processing_json
from viewer import ViewerSource


def processing_from_state(source: ViewerSource | None, state) -> ProcessingRecord | None:
    """Export the displayed result with applied settings, never draft inputs."""
    if source is None:
        return None
    record = state.get("viewer_displayed_processing")
    result = state.get("viewer_displayed_result")
    applied = state.get("viewer_processing_applied")
    bounds = state.get("viewer_roi_bounds")
    statistics = state.get("viewer_roi_statistics", {}) if bounds is not None else {}
    if (applied is not None and result is not None
            and result is state.get("viewer_processing_result")):
        record = ProcessingRecord(
            applied["source_id"], applied["algorithm_id"], applied["algorithm_version"],
            deepcopy(applied["parameters"]), result,
            tuple(bounds) if bounds is not None else None, deepcopy(statistics),
        )
    elif record is not None:
        if bounds is not None and tuple(bounds) != tuple(record.roi or ()):
            record = replace(record, roi=tuple(bounds), statistics=deepcopy(statistics))
        elif state.get("viewer_roi_cleared"):
            record = replace(record, roi=None, statistics={})
    if record is not None and state.get("viewer_roi_error"):
        raise ValueError("ROI 입력을 수정하거나 ROI를 해제한 뒤 저장하세요.")
    return validate_processing(source, record)


def validate_processing(source: ViewerSource, record: ProcessingRecord | None) -> ProcessingRecord | None:
    """Check the storage contract without copying pixels on every view change."""
    if record is None:
        return None
    _processing_json(record, source)
    if record.roi is not None:
        stats = record.statistics
        calculation = stats.get("calculation")
        if (stats.get("source_id") != source.source_id
                or stats.get("bounds") != list(record.roi)
                or stats.get("coordinate_system") != "exif_transposed_original_pixels_v1"
                or stats.get("brightness_method") != "pillow_L_v1"
                or not isinstance(calculation, dict)
                or calculation.get("variance_definition") != "population"
                or type(calculation.get("ddof")) is not int
                or calculation.get("ddof") != 0):
            raise ValueError("ROI와 통계의 원본 참조, 좌표 또는 계산 정의가 일치하지 않습니다.")
        count = (record.roi[2] - record.roi[0]) * (record.roi[3] - record.roi[1])
        for name in ("original", "result"):
            values = stats.get(name)
            if (not isinstance(values, dict) or values.get("pixel_count") != count
                    or type(values.get("mean")) not in (int, float)
                    or not 0 <= values["mean"] <= 255
                    or type(values.get("population_variance")) not in (int, float)
                    or values["population_variance"] < 0):
                raise ValueError("ROI 통계의 픽셀 수, 평균 또는 모집단 분산이 올바르지 않습니다.")
    try:
        json.dumps(_processing_json(record, source), allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError("처리 설정과 통계는 유한한 숫자와 JSON 값이어야 합니다.") from None
    return record


def freeze_processing(source: ViewerSource, record: ProcessingRecord | None) -> ProcessingRecord | None:
    """Detach submitted pixels and metadata so later edits cannot change a retry."""
    validate_processing(source, record)
    if record is None:
        return None
    return ProcessingRecord(
        record.source_id, record.algorithm_id, record.algorithm_version,
        deepcopy(record.parameters), record.result.copy(),
        tuple(record.roi) if record.roi is not None else None,
        deepcopy(record.statistics),
    )


def show_processing(record: ProcessingRecord, *, saved: bool) -> None:
    st.caption("저장 당시 처리 기록" if saved else "현재 세션의 처리 기록 (저장 전)")
    st.text(f"{record.algorithm_id} / 버전 {record.algorithm_version}")
    with st.expander("처리 기록 상세", expanded=False):
        st.json({"실제 적용 설정": record.parameters, "원본 해시": record.source_id,
                 "ROI": record.roi, "저장된 통계" if saved else "현재 통계": record.statistics})
