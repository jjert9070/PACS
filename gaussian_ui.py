"""Explicit Gaussian application with session-only results for the current source."""

import logging

import streamlit as st
from PIL import Image

from image_processing import (
    GAUSSIAN_ALGORITHM_ID,
    GAUSSIAN_ALGORITHM_VERSION,
    MAX_GAUSSIAN_RADIUS,
    gaussian_blur,
)
from viewer import ProcessingPending, ViewerSource

PREFIX = "viewer_processing_"
logger = logging.getLogger(__name__)


def render_gaussian_controls(source: ViewerSource) -> None:
    st.markdown("#### 가우시안 처리")
    algorithm = st.selectbox(
        "처리 알고리즘", ["선택 안 함", "가우시안"], index=1,
        key=PREFIX + "algorithm",
    )
    if algorithm == "선택 안 함":
        for name in ("result", "applied", "request", "error"):
            st.session_state.pop(PREFIX + name, None)
        st.info("가우시안을 선택하면 강도 설정을 표시합니다.")
        return
    st.caption("강도는 원본 픽셀 단위 표준편차 σ입니다. 0은 원본과 같습니다.")
    radius = st.number_input(
        "가우시안 강도 (σ, px)",
        min_value=0.0,
        max_value=MAX_GAUSSIAN_RADIUS,
        value=1.0,
        step=0.5,
        key=PREFIX + "radius",
    )
    if st.button("가우시안 적용", key=PREFIX + "apply"):
        st.session_state.pop(PREFIX + "result", None)
        st.session_state.pop(PREFIX + "applied", None)
        st.session_state[PREFIX + "request"] = {
            "source_id": source.source_id,
            "radius": radius,
        }
        st.session_state.pop(PREFIX + "error", None)
    # Draft settings never relabel a previously applied result.
    applied = st.session_state.get(PREFIX + "applied")
    if applied and radius != applied["radius"]:
        st.info(f"설정이 변경됐습니다. 현재 표시 결과는 적용 강도 {applied['radius']:g} px입니다.")


def process_gaussian(image: Image.Image) -> Image.Image:
    """Called by viewer.processor; only an explicit request performs calculation."""
    request = st.session_state.pop(PREFIX + "request", None)
    if request is not None:
        try:
            result = gaussian_blur(image, request["radius"])
            if not isinstance(result, Image.Image) or result.mode != "RGB" or result.size != image.size:
                raise ValueError("Invalid processing result")
        except Exception:
            # The screen shows only a generic failure; keep the cause for developers.
            # Log the short source ID and setting, never pixel data or file bytes.
            logger.exception(
                "Gaussian processing failed: source_id=%s radius=%r",
                str(request.get("source_id", ""))[:12], request.get("radius"),
            )
            st.session_state.pop(PREFIX + "result", None)
            st.session_state.pop(PREFIX + "applied", None)
            st.session_state[PREFIX + "error"] = True
        else:
            st.session_state[PREFIX + "result"] = result
            st.session_state[PREFIX + "applied"] = {
                "source_id": request["source_id"],
                "algorithm_id": GAUSSIAN_ALGORITHM_ID,
                "algorithm_version": GAUSSIAN_ALGORITHM_VERSION,
                "radius": float(request["radius"]),
            }
    if st.session_state.get(PREFIX + "error"):
        raise ValueError("Gaussian processing failed")
    result = st.session_state.get(PREFIX + "result")
    applied = st.session_state.get(PREFIX + "applied")
    if result is None or applied is None:
        if st.session_state.get(PREFIX + "algorithm") == "선택 안 함":
            raise ProcessingPending("처리 알고리즘을 선택하세요.")
        raise ProcessingPending("강도를 설정하고 가우시안 적용을 누르세요.")
    st.caption(
        f"가우시안 · {applied['algorithm_id']} · 버전 {applied['algorithm_version']} · "
        f"실제 적용 σ={applied['radius']:g} px (원본 픽셀)"
    )
    return result
