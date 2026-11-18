"""Explicit processing (Gaussian, gamma, threshold) with session-only results for the current source."""

import logging

import streamlit as st
from PIL import Image

from image_processing import (
    GAMMA_ALGORITHM_ID,
    GAMMA_ALGORITHM_VERSION,
    GAUSSIAN_ALGORITHM_ID,
    GAUSSIAN_ALGORITHM_VERSION,
    MAX_GAMMA,
    MAX_GAUSSIAN_RADIUS,
    MAX_THRESHOLD,
    MIN_GAMMA,
    MIN_THRESHOLD,
    THRESHOLD_ALGORITHM_ID,
    THRESHOLD_ALGORITHM_VERSION,
    gamma_correction,
    gaussian_blur,
    threshold_segmentation,
)
from processing_compat import check_compatibility, processing_environment
from viewer import ProcessingPending, ViewerSource

PREFIX = "viewer_processing_"
NONE = "선택 안 함"
GAUSSIAN = "가우시안"
GAMMA = "감마 보정"
THRESHOLD = "임계값 분할"
ALGORITHM_IDS = {
    GAUSSIAN: GAUSSIAN_ALGORITHM_ID,
    GAMMA: GAMMA_ALGORITHM_ID,
    THRESHOLD: THRESHOLD_ALGORITHM_ID,
}
ALGORITHM_LABELS = {value: key for key, value in ALGORITHM_IDS.items()}
LOG_NAMES = {
    GAUSSIAN_ALGORITHM_ID: "Gaussian",
    GAMMA_ALGORITHM_ID: "Gamma",
    THRESHOLD_ALGORITHM_ID: "Threshold",
}
STATE_NAMES = ("result", "applied", "request", "error")
logger = logging.getLogger(__name__)


def _clear_processing() -> None:
    for name in STATE_NAMES:
        st.session_state.pop(PREFIX + name, None)


def _describe(algorithm_id: str, parameters: dict) -> str:
    if algorithm_id == GAMMA_ALGORITHM_ID:
        return f"γ={parameters['gamma']:g}"
    if algorithm_id == THRESHOLD_ALGORITHM_ID:
        return f"임계값={parameters['threshold']} (L ≥ 임계값 → 흰색)"
    return f"σ={parameters['radius']:g} px"


def _check_restored(restored: dict, source: ViewerSource):
    """Compatibility of the record C reopened; version/environment come from C's record."""
    stored = st.session_state.get("viewer_stored_processing")
    if stored is not None and getattr(stored, "source_id", None) != source.source_id:
        stored = None
    version = restored.get("algorithm_version", getattr(stored, "algorithm_version", None))
    environment = restored.get("environment", getattr(stored, "environment", None))
    return check_compatibility(restored["algorithm_id"], version, restored["parameters"], environment)


def _show_stored_check(check) -> None:
    """Explain why stored settings were not restored and whether conditions changed."""
    if check is None:
        return
    if not check.can_restore_settings:
        st.warning(
            "저장된 설정을 현재 처리 도구에 적용할 수 없습니다. 새 설정을 선택하세요. "
            "저장 당시 결과는 다시 계산하지 않고 그대로 표시합니다.\n\n" + "\n".join(check.reasons)
        )
    elif check.environment_status == "changed":
        st.info("\n\n".join(check.environment_notes))
    elif check.environment_status == "missing":
        st.caption(check.environment_notes[0])


def render_gaussian_controls(source: ViewerSource) -> None:
    st.markdown("#### 영상 처리")
    algorithms = [NONE, GAUSSIAN, GAMMA, THRESHOLD]
    restored = st.session_state.pop("viewer_restore_controls", None)
    if restored and restored["source_id"] == source.source_id:
        st.session_state[PREFIX + "algorithm"] = GAUSSIAN
        for name, value in (("radius", 1.0), ("gamma", 1.0), ("threshold", 128)):
            st.session_state[PREFIX + name] = value
        st.session_state.pop(PREFIX + "stored_check", None)
        if restored["algorithm_id"]:
            check = _check_restored(restored, source)
            st.session_state[PREFIX + "stored_check"] = check
            if check.can_restore_settings:
                # Fill the controls only; the stored result is shown as saved, not re-run.
                name, value = next(iter(restored["parameters"].items()))
                st.session_state[PREFIX + "algorithm"] = ALGORITHM_LABELS[check.algorithm_id]
                st.session_state[PREFIX + name] = value if name == "threshold" else float(value)
    algorithm = st.selectbox(
        "처리 알고리즘", algorithms,
        index=0 if PREFIX + "algorithm" in st.session_state else 1,
        key=PREFIX + "algorithm",
    )
    # A result made by another algorithm must not stay on screen as the new choice.
    if st.session_state.get(PREFIX + "selected") != algorithm:
        _clear_processing()
        st.session_state[PREFIX + "selected"] = algorithm
    if algorithm == NONE:
        st.info("알고리즘을 선택하면 해당 설정을 표시합니다.")
        _show_stored_check(st.session_state.get(PREFIX + "stored_check"))
        return
    if algorithm == GAMMA:
        st.caption("출력 = 255 × (입력 / 255)^γ · γ < 1 밝게, γ > 1 어둡게, 1은 원본과 같습니다.")
        value = st.number_input(
            "감마 (γ)",
            min_value=MIN_GAMMA,
            max_value=MAX_GAMMA,
            value="min" if PREFIX + "gamma" in st.session_state else 1.0,
            step=0.1,
            format="%.2f",
            key=PREFIX + "gamma",
        )
        parameters = {"gamma": float(value)}
    elif algorithm == THRESHOLD:
        st.caption("Pillow L 밝기(0~255)가 임계값 이상이면 흰색, 미만이면 검은색입니다. 같은 값은 흰색입니다.")
        value = st.number_input(
            "임계값 (L 밝기, 정수)",
            min_value=MIN_THRESHOLD,
            max_value=MAX_THRESHOLD,
            value="min" if PREFIX + "threshold" in st.session_state else 128,
            step=1,
            key=PREFIX + "threshold",
        )
        parameters = {"threshold": int(value)}
    else:
        st.caption("강도는 원본 픽셀 단위 표준편차 σ입니다. 0은 원본과 같습니다.")
        value = st.number_input(
            "가우시안 강도 (σ, px)",
            min_value=0.0,
            max_value=MAX_GAUSSIAN_RADIUS,
            value="min" if PREFIX + "radius" in st.session_state else 1.0,
            step=0.5,
            key=PREFIX + "radius",
        )
        parameters = {"radius": float(value)}
    algorithm_id = ALGORITHM_IDS[algorithm]
    if st.button(f"{algorithm} 적용", key=PREFIX + "apply"):
        _clear_processing()
        # The notice is about the stored record; a newly applied result replaces it.
        st.session_state.pop(PREFIX + "stored_check", None)
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
    # After the apply button, so a newly applied result removes the stored-record notice at once.
    _show_stored_check(st.session_state.get(PREFIX + "stored_check"))


def _run(image: Image.Image, request: dict) -> tuple[Image.Image, str]:
    parameters = request["parameters"]
    if request["algorithm_id"] == GAMMA_ALGORITHM_ID:
        return gamma_correction(image, parameters["gamma"]), GAMMA_ALGORITHM_VERSION
    if request["algorithm_id"] == THRESHOLD_ALGORITHM_ID:
        return threshold_segmentation(image, parameters["threshold"]), THRESHOLD_ALGORITHM_VERSION
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
            # The screen shows only a generic failure; keep the cause for developers.
            # Log the short source ID and settings, never pixel data or file bytes.
            settings = " ".join(f"{name}={value!r}" for name, value in request.get("parameters", {}).items())
            logger.exception(
                "%s processing failed: source_id=%s %s",
                LOG_NAMES.get(request.get("algorithm_id"), "Image"),
                str(request.get("source_id", ""))[:12], settings,
            )
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
                # Kept per applied result, so it survives an absent or cleared ROI.
                "environment": processing_environment(request["algorithm_id"]),
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
