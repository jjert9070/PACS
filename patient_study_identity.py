"""Explicit patient and study identifiers for the educational PACS workflow."""

from dataclasses import dataclass
from unicodedata import category


def _identifier(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label}은 문자열이어야 합니다.")
    if any(category(char) == "Cc" for char in value):
        raise ValueError(f"{label}에 제어문자를 사용할 수 없습니다.")
    value = value.strip()
    if not value or len(value) > 128:
        raise ValueError(f"{label}은 제어문자 없이 1~128자로 입력하세요.")
    return value


@dataclass(frozen=True)
class PatientIdentity:
    """Identify a patient by both the issuing namespace and the assigned ID."""

    issuer: str
    patient_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "issuer", _identifier(self.issuer, "환자 ID 발급 주체"))
        object.__setattr__(self, "patient_id", _identifier(self.patient_id, "환자 ID"))


@dataclass(frozen=True)
class StudyIdentity:
    """An explicit study identifier and the patient it is connected to."""

    study_id: str
    patient: PatientIdentity

    def __post_init__(self) -> None:
        object.__setattr__(self, "study_id", _identifier(self.study_id, "검사 ID"))
        if not isinstance(self.patient, PatientIdentity):
            raise ValueError("확인된 환자 식별 정보가 필요합니다.")


class StudyPatientConflict(ValueError):
    """The same study is already associated with a different patient."""


def validate_study_patient(existing: StudyIdentity, requested: StudyIdentity) -> None:
    """Validate a repeated association without changing the existing one."""
    if not isinstance(existing, StudyIdentity) or not isinstance(requested, StudyIdentity):
        raise ValueError("기존 검사와 요청한 검사 식별 정보가 필요합니다.")
    if existing.study_id != requested.study_id:
        raise ValueError("서로 다른 검사 ID는 같은 검사로 비교할 수 없습니다.")
    if existing.patient != requested.patient:
        raise StudyPatientConflict("이미 다른 환자에 연결된 검사입니다. 연결을 변경하지 않았습니다.")
