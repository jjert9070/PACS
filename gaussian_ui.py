"""Explicit processing (Gaussian, gamma) with session-only results for the current source."""

import streamlit as st
from PIL import Image

from image_processing import (
    GAMMA_ALGORITHM_ID,
    GAMMA_ALGORITHM_VERSION,
    GAUSSIAN_ALGORITHM_ID,
    GAUSSIAN_ALGORITHM_VERSION,
    MAX_GAMMA,
    MAX_GAUSSIAN_RADIUS,
    MIN_GAMMA,
    gamma_correction,
    gaussian_blur,
)
from viewer import ProcessingPending, ViewerSource

PREFIX = "viewer_processing_"
NONE = "선택 안 함"
GAUSSIAN = "가우시안"
GAMMA = "감마 보정"
ALGORITHM_IDS = {GAUSSIAN: GAUSSIAN_ALGORITHM_ID, GAMMA: GAMMA_ALGORITHM_ID}
ALGORITHM_LABELS = {value: key for key, value in ALGORITHM_IDS.items()}
STATE_NAMES = ("result", "applied", "request", "error")


def _clear_processing() -> None:
    for name in STATE_NAMES:
        st.session_state.pop(PREFIX + name, None)


def _describe(algorithm_id: str, parameters: dict) -> str:
    if algorithm_id == GAMMA_ALGORITHM_ID:
        return f"γ={parameters['gamma']:g}"
    return f"σ={parameters['radius']:g} px"


def render_gaussian_controls(source: ViewerSource) -> None:
    st.markdown("#### 영상 처리")
    algorithm = st.selectbox(
        "처리 알고리즘", [NONE, GAUSSIAN, GAMMA], index=1,
        key=PREFIX + "algorithm",
    )
    # A result made by another algorithm must not stay on screen as the new choice.
    if st.session_state.get(PREFIX + "selected") != algorithm:
        _clear_processing()
        st.session_state[PREFIX + "selected"] = algorithm
    if algorithm == NONE:
        st.info("알고리즘을 선택하면 해당 설정을 표시합니다.")
        return
    if algorithm == GAMMA:
        st.caption("출력 = 255 × (입력 / 255)^γ · γ < 1 밝게, γ > 1 어둡게, 1은 원본과 같습니다.")
        value = st.number_input(
            "감마 (γ)",
            min_value=MIN_GAMMA,
            max_value=MAX_GAMMA,
            value=1.0,
            step=0.1,
            format="%.2f",
            key=PREFIX + "gamma",
        )
        parameters = {"gamma": float(value)}
    else:
        st.caption("강도는 원본 픽셀 단위 표준편차 σ입니다. 0은 원본과 같습니다.")
        value = st.number_input(
            "가우시안 강도 (σ, px)",
            min_value=0.0,
            max_value=MAX_GAUSSIAN_RADIUS,
            value=1.0,
            step=0.5,
            key=PREFIX + "radius",
        )
        parameters = {"radius": float(value)}
    algorithm_id = ALGORITHM_IDS[algorithm]
    if st.button(f"{algorithm} 적용", key=PREFIX + "apply"):
        _clear_processing()
        st.session_state[PREFIX + "request"] = {
            "source_id": source.source_id,
            "algorithm_id": algorithm_id,
            "parameters": parameters,
        }
    # Draft settings never relabel a previously applied result.
    applied = st.session_state.get(PREFIX + "applied")
    if applied and applied["parameters"] != parameters:
        st.info(
            f"설정이 변경됐습니다. 현재 표시 결과는 적용 "
            f"{_describe(applied['algorithm_id'], applied['parameters'])}입니다."
        )


def _run(image: Image.Image, request: dict) -> tuple[Image.Image, str]:
    parameters = request["parameters"]
    if request["algorithm_id"] == GAMMA_ALGORITHM_ID:
        return gamma_correction(image, parameters["gamma"]), GAMMA_ALGORITHM_VERSION
    if request["algorithm_id"] == GAUSSIAN_ALGORITHM_ID:
        return gaussian_blur(image, parameters["radius"]), GAUSSIAN_ALGORITHM_VERSION
    raise ValueError("Unsupported algorithm")


def process_gaussian(image: Image.Image) -> Image.Image:
    """Called by viewer.processor; only an explicit request performs calculation."""
    request = st.session_state.pop(PREFIX + "request", None)
    if request is not None:
        try:
            result, version = _run(image, request)
            if not isinstance(result, Image.Image) or result.mode != "RGB" or result.size != image.size:
                raise ValueError("Invalid processing result")
        except Exception:
            st.session_state.pop(PREFIX + "result", None)
            st.session_state.pop(PREFIX + "applied", None)
            st.session_state[PREFIX + "error"] = True
        else:
            st.session_state[PREFIX + "result"] = result
            st.session_state[PREFIX + "applied"] = {
                "source_id": request["source_id"],
                "algorithm_id": request["algorithm_id"],
                "algorithm_version": version,
                "parameters": dict(request["parameters"]),
            }
    if st.session_state.get(PREFIX + "error"):
        raise ValueError("Image processing failed")
    result = st.session_state.get(PREFIX + "result")
    applied = st.session_state.get(PREFIX + "applied")
    selected = st.session_state.get(PREFIX + "algorithm")
    if result is None or applied is None:
        if selected == NONE:
            raise ProcessingPending("처리 알고리즘을 선택하세요.")
        raise ProcessingPending(f"설정을 정하고 {selected or GAUSSIAN} 적용을 누르세요.")
    label = ALGORITHM_LABELS.get(applied["algorithm_id"], applied["algorithm_id"])
    st.caption(
        f"{label} · {applied['algorithm_id']} · 버전 {applied['algorithm_version']} · "
        f"실제 적용 {_describe(applied['algorithm_id'], applied['parameters'])}"
        + (" (원본 픽셀)" if applied["algorithm_id"] == GAUSSIAN_ALGORITHM_ID else "")
    )
    return result
