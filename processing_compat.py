"""P03: can a stored processing record be restored or re-run by the current B code?

B owns this decision; C stores and shows the record. Nothing here re-executes an
algorithm or rewrites a stored record. A stored result stays "as saved"; the
checks only say whether its settings can be put back into the current controls
and whether re-running would use the same algorithm version and libraries.
"""

from dataclasses import dataclass
import math

import cv2
import numpy as np
import PIL

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
)

ENVIRONMENT_SCHEMA = "b_processing_environment_v1"

# (algorithm ID, version) -> (parameter name, kind, lower, upper, libraries used).
# A version not listed here was produced by different code and is never mapped
# silently onto the current implementation.
SUPPORTED = {
    (GAUSSIAN_ALGORITHM_ID, GAUSSIAN_ALGORITHM_VERSION):
        ("radius", "real", 0.0, MAX_GAUSSIAN_RADIUS, ("numpy", "pillow", "opencv")),
    (GAMMA_ALGORITHM_ID, GAMMA_ALGORITHM_VERSION):
        ("gamma", "real", MIN_GAMMA, MAX_GAMMA, ("numpy", "pillow")),
    (THRESHOLD_ALGORITHM_ID, THRESHOLD_ALGORITHM_VERSION):
        ("threshold", "integer", MIN_THRESHOLD, MAX_THRESHOLD, ("numpy", "pillow")),
}
CURRENT_VERSIONS = {algorithm_id: version for algorithm_id, version in SUPPORTED}


def library_versions() -> dict:
    """Versions of the libraries that the B algorithms call at run time."""
    return {"numpy": np.__version__, "pillow": PIL.__version__, "opencv": cv2.__version__}


def processing_environment(algorithm_id: str) -> dict:
    """Environment to keep with an applied result, independent of any ROI.

    Only libraries the algorithm actually uses are listed: OpenCV for Gaussian,
    NumPy and Pillow for every algorithm.
    """
    version = CURRENT_VERSIONS.get(algorithm_id)
    if version is None:
        raise ValueError(f"지원하지 않는 알고리즘입니다: {algorithm_id!r}")
    used = SUPPORTED[(algorithm_id, version)][4]
    current = library_versions()
    return {"schema": ENVIRONMENT_SCHEMA, "libraries": {name: current[name] for name in used}}


@dataclass(frozen=True)
class Compatibility:
    """Result of checking one stored record against the current B code."""

    algorithm_id: str
    algorithm_version: str
    status: str  # supported | unknown_algorithm | unsupported_version | invalid_parameters
    reasons: tuple[str, ...]
    environment_status: str  # same | changed | missing | not_checked
    environment_notes: tuple[str, ...]

    @property
    def can_restore_settings(self) -> bool:
        """Settings may be placed into the current controls (never auto-applied)."""
        return self.status == "supported"

    @property
    def can_rerun(self) -> bool:
        """The current algorithm version accepts these settings if the user re-applies."""
        return self.status == "supported"

    @property
    def same_conditions(self) -> bool:
        """Re-running now matches the stored algorithm version and libraries."""
        return self.status == "supported" and self.environment_status == "same"


def _check_parameters(parameters, name: str, kind: str, lower: float, upper: float) -> str | None:
    if not isinstance(parameters, dict) or set(parameters) != {name}:
        keys = sorted(parameters) if isinstance(parameters, dict) else type(parameters).__name__
        return f"설정 항목이 '{name}' 하나여야 하는데 {keys}입니다."
    value = parameters[name]
    if kind == "integer":
        if type(value) is not int:
            return f"{name}은 정수여야 하는데 {type(value).__name__} 값({value!r})입니다."
    elif type(value) not in (int, float):
        return f"{name}은 숫자여야 하는데 {type(value).__name__} 값({value!r})입니다."
    try:
        finite = math.isfinite(float(value))
    except OverflowError:
        finite = False
    if not finite or not lower <= value <= upper:
        return f"{name}={value!r}은 현재 허용 범위 {lower:g}~{upper:g}를 벗어납니다."
    return None


def _check_environment(environment, used: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    libraries = environment.get("libraries") if isinstance(environment, dict) else None
    if not isinstance(libraries, dict) or not libraries:
        return "missing", ("환경 정보 없음: 이 기록에는 계산 라이브러리 버전이 저장되지 않아 재현 조건을 확인할 수 없습니다.",)
    current = library_versions()
    notes = []
    for name in used:
        stored = libraries.get(name)
        if not isinstance(stored, str) or not stored:
            notes.append(f"{name}: 저장 당시 버전 기록 없음 (현재 {current[name]})")
        elif stored != current[name]:
            notes.append(f"{name}: 저장 당시 {stored} → 현재 {current[name]}")
    if notes:
        return "changed", ("재현 조건이 달라졌습니다. 다시 처리하면 저장 당시 결과와 다를 수 있습니다.", *notes)
    return "same", ()


def check_compatibility(algorithm_id, algorithm_version, parameters, environment=None) -> Compatibility:
    """Classify a stored record; the stored result itself is never replaced."""
    algorithm_id = algorithm_id if isinstance(algorithm_id, str) else repr(algorithm_id)
    algorithm_version = algorithm_version if isinstance(algorithm_version, str) else repr(algorithm_version)
    current_version = CURRENT_VERSIONS.get(algorithm_id)
    if current_version is None:
        return Compatibility(
            algorithm_id, algorithm_version, "unknown_algorithm",
            (f"현재 지원하지 않는 알고리즘입니다: {algorithm_id}. 지원: {', '.join(sorted(CURRENT_VERSIONS))}.",),
            "not_checked", (),
        )
    if algorithm_version != current_version:
        return Compatibility(
            algorithm_id, algorithm_version, "unsupported_version",
            (f"저장된 {algorithm_id} 버전 {algorithm_version}은 현재 버전 {current_version}과 다릅니다. "
             "다른 계산이므로 현재 알고리즘으로 대신 실행하거나 설정을 자동 복원하지 않습니다.",),
            "not_checked", (),
        )
    name, kind, lower, upper, used = SUPPORTED[(algorithm_id, algorithm_version)]
    environment_status, environment_notes = _check_environment(environment, used)
    problem = _check_parameters(parameters, name, kind, lower, upper)
    if problem is not None:
        return Compatibility(algorithm_id, algorithm_version, "invalid_parameters",
                             (problem,), environment_status, environment_notes)
    return Compatibility(algorithm_id, algorithm_version, "supported", (),
                         environment_status, environment_notes)
