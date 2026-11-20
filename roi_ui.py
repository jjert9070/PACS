"""Numeric ROI selection and current-source statistics, with no storage writes."""

import streamlit as st

from roi import compare_roi, validate_roi
from viewer import ViewerSource

PREFIX = "viewer_roi_"


def render_roi_controls(source: ViewerSource):
    st.markdown("#### ROI 영역")
    st.caption("방향 보정된 원본 정수 픽셀 좌표 · 좌상단 (0,0), 오른쪽/아래 끝 제외")
    width, height = source.image.size
    st.caption(f"0 ≤ left < right ≤ {width}, 0 ≤ top < bottom ≤ {height}")
    # Source-specific widget keys prevent old draft coordinates surviving a swap.
    widget_key = PREFIX + source.source_id + "_"
    restored = st.session_state.pop("viewer_restore_roi", None)
    if restored and restored["source_id"] == source.source_id:
        bounds = (0, 0, width, height)
        if restored["bounds"] is not None:
            bounds = validate_roi(restored["bounds"], source.image.size)
            st.session_state[PREFIX + "bounds"] = bounds
        for name, value in zip(("left", "top", "right", "bottom"), bounds):
            st.session_state[widget_key + name] = value
    with st.form(widget_key + "form"):
        left = st.number_input("ROI left", value="min" if widget_key + "left" in st.session_state else 0,
                               step=1, key=widget_key + "left")
        top = st.number_input("ROI top", value="min" if widget_key + "top" in st.session_state else 0,
                              step=1, key=widget_key + "top")
        right = st.number_input("ROI right (제외)", value="min" if widget_key + "right" in st.session_state else width,
                                step=1, key=widget_key + "right")
        bottom = st.number_input("ROI bottom (제외)", value="min" if widget_key + "bottom" in st.session_state else height,
                                 step=1, key=widget_key + "bottom")
        submitted = st.form_submit_button("ROI 적용")
    if submitted:
        st.session_state[PREFIX + "cleared"] = False
        st.session_state.pop(PREFIX + "bounds", None)
        st.session_state.pop(PREFIX + "statistics", None)
        try:
            bounds = validate_roi((left, top, right, bottom), source.image.size)
        except ValueError as error:
            st.session_state[PREFIX + "error"] = str(error)
        else:
            st.session_state[PREFIX + "bounds"] = bounds
            st.session_state.pop(PREFIX + "error", None)
    if st.button("ROI 해제", key=PREFIX + "clear"):
        st.session_state[PREFIX + "cleared"] = True
        for name in ("bounds", "statistics", "error"):
            st.session_state.pop(PREFIX + name, None)
    if st.session_state.get(PREFIX + "error"):
        st.error(st.session_state[PREFIX + "error"])
    return st.session_state.get(PREFIX + "bounds")


def render_roi_statistics(source: ViewerSource, result, bounds):
    st.subheader("ROI 밝기 통계")
    if bounds is None:
        st.session_state.pop(PREFIX + "statistics", None)
        st.info("ROI 좌표를 입력하고 ROI 적용을 누르세요. 미선택은 0이 아닙니다.")
        return
    try:
        statistics = compare_roi(source.image, result, bounds)
    except (ValueError, TypeError) as error:
        st.session_state.pop(PREFIX + "statistics", None)
        st.error(str(error))
        return
    statistics["source_id"] = source.source_id
    st.session_state[PREFIX + "statistics"] = statistics
    st.caption(
        f"적용 ROI {tuple(statistics['bounds'])} · {statistics['width']} × {statistics['height']} px · "
        "노란 테두리 · 원본 좌표 기준 / Pillow L 밝기 / 모집단 분산(ddof=0)"
    )
    original_column, result_column = st.columns(2)
    for column, label, values in (
        (original_column, "원본 ROI", statistics["original"]),
        (result_column, "처리 결과 ROI", statistics["result"]),
    ):
        with column:
            st.markdown(f"**{label}**")
            if values is None:
                st.info("결과 통계 없음: 유효한 처리 결과가 없습니다.")
            else:
                st.text(f"픽셀 수: {values['pixel_count']:,}")
                st.text(f"평균 밝기: {values['mean']:.6f}")
                st.text(f"모집단 분산: {values['population_variance']:.6f}")
