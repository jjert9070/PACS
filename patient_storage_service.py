"""Small UI-facing entry points for confirmed sample registrations."""

from dataclasses import dataclass
from pathlib import Path

from firebase_client import FirebaseRequestError, Session
from local_storage import LocalStorageError, open_original
from patient_study_identity import PatientIdentity, StudyIdentity, StudyPatientConflict
from patient_study_store import get_study, save_patient, save_study
from series_store import Series, SeriesConflict, get_series, save_series
from study_image_store import (OriginalSavedLinkError, StudyImage, StudyImageConflict,
                               get_image, save_and_link_original)
from viewer import ViewerSource


@dataclass(frozen=True)
class RegistrationResult:
    success: bool
    stage: str
    error_code: str = ""
    original_key: str = ""
    retry_image_id: str = ""


def _error_code(error: Exception) -> str:
    if isinstance(error, (StudyPatientConflict, SeriesConflict, StudyImageConflict)):
        return "identity_conflict"
    if isinstance(error, FirebaseRequestError):
        if error.status == 401:
            return "session_expired"
        if error.status == 403:
            return "permission_denied"
        return "remote_failure"
    if isinstance(error, LocalStorageError):
        return "local_storage_failure"
    return "invalid_input"


def register_sample_original(
    project_id: str, session: Session, root: Path, patient: PatientIdentity,
    series: Series, image_id: str, name: str, data: bytes,
) -> RegistrationResult:
    """The caller must obtain the user's confirmed patient and study selection first."""
    stage = "input"
    try:
        if not isinstance(patient, PatientIdentity) or not isinstance(series, Series):
            raise ValueError("확인된 환자와 검사, 시리즈 정보가 필요합니다.")
        # Validate the file before persisting patient or study records.
        from viewer import load_image
        from patient_study_identity import _identifier

        image_id = _identifier(image_id, "영상 ID")
        load_image(name, data)
        stage = "patient"
        save_patient(project_id, session, patient)
        stage = "study"
        save_study(project_id, session, StudyIdentity(series.study_id, patient))
        stage = "series"
        save_series(project_id, session, series)
        stage = "original"
        image = save_and_link_original(project_id, session, root, series.study_id,
                                       series.series_id, image_id, name, data)
        return RegistrationResult(True, "linked", original_key=image.original_key)
    except OriginalSavedLinkError as error:
        retry_id = image_id if error.retry_recorded else ""
        return RegistrationResult(False, "link", _error_code(error.__cause__), error.original_key, retry_id)
    except (FirebaseRequestError, ValueError) as error:
        return RegistrationResult(False, stage, _error_code(error))


@dataclass(frozen=True)
class OpenedOriginal:
    study: StudyIdentity
    series: Series
    image: StudyImage
    source: ViewerSource


def open_selected_original(
    project_id: str, session: Session, root: Path, patient: PatientIdentity,
    study_id: str, series_id: str, image_id: str,
) -> OpenedOriginal:
    study = get_study(project_id, session, study_id)
    series = get_series(project_id, session, series_id)
    image = get_image(project_id, session, image_id)
    if (not isinstance(patient, PatientIdentity) or study.patient != patient
            or series.study_id != study.study_id or image.study_id != study.study_id
            or image.series_id != series.series_id):
        raise StudyImageConflict("선택한 환자와 검사, 시리즈 또는 원본 연결이 일치하지 않습니다.")
    return OpenedOriginal(study, series, image, open_original(root, session.uid, image.original_key))
