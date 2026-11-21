"""Reopen a review's original through the existing owner-scoped storage services."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import re

from firebase_client import Session
from local_storage import LocalStorageError
from patient_study_identity import PatientIdentity, StudyIdentity, StudyPatientConflict, _identifier
from patient_study_store import _collection_for
from roi import COORDINATE_SYSTEM
from study_image_store import StudyImageConflict
from viewer import ViewerSource


class ReviewSourceError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


_FIELDS = {
    "schema", "owner_uid", "project_id", "patient_issuer", "patient_id",
    "study_id", "series_id", "object_id", "source_id", "file_format",
    "frame_index", "width", "height", "coordinate_space",
}


def _validate_ref(project_id: str, session: Session, source_ref: dict) -> dict:
    _collection_for(project_id, session, "review_records")
    if not isinstance(source_ref, dict) or set(source_ref) != _FIELDS:
        raise ReviewSourceError("invalid_input", "원본 참조의 필수 필드와 형식을 확인하세요.")
    ref = deepcopy(source_ref)
    if (ref["schema"] != "pacs_source_ref_v1"
            or ref["coordinate_space"] != COORDINATE_SYSTEM
            or ref["file_format"] not in ("DICOM", "PNG", "JPEG")):
        raise ReviewSourceError("invalid_input", "지원하지 않는 원본 참조 형식입니다.")
    if ref["owner_uid"] != session.uid or ref["project_id"] != project_id:
        raise ReviewSourceError("source_mismatch", "인증 범위와 원본 참조가 다릅니다.")
    for key in ("patient_issuer", "patient_id", "study_id", "series_id", "object_id"):
        try:
            if _identifier(ref[key], "원본 식별자") != ref[key]:
                raise ValueError()
        except ValueError:
            raise ReviewSourceError("invalid_input", "원본 식별자 형식이 올바르지 않습니다.") from None
    if (not isinstance(ref["source_id"], str)
            or re.fullmatch(r"[0-9a-f]{64}", ref["source_id"]) is None
            or any(type(ref[key]) is not int or ref[key] <= 0 for key in ("width", "height"))):
        raise ReviewSourceError("invalid_input", "원본 해시와 픽셀 크기를 확인하세요.")
    if type(ref["frame_index"]) is not int or ref["frame_index"] != 0:
        raise ReviewSourceError("unsupported_frame", "현재는 첫 번째 단일 프레임만 지원합니다.")
    return ref


def open_review_source(
    project_id: str, session: Session, root: Path, source_ref: dict,
) -> tuple[ViewerSource, dict | None]:
    """Return checked original pixels and B's display provenance, without writes.

    project_id, session and root come from the application, never the draft.
    Firebase errors remain distinguishable from missing or invalid local files.
    """
    ref = _validate_ref(project_id, session, source_ref)
    patient = PatientIdentity(ref["patient_issuer"], ref["patient_id"])
    mapping = None
    try:
        if ref["file_format"] == "DICOM":
            from dicom_storage_flow import DicomOpenError, open_dicom_display

            try:
                opened = open_dicom_display(
                    project_id, session, root, StudyIdentity(ref["study_id"], patient),
                    ref["series_id"], ref["object_id"],
                )
            except DicomOpenError as error:
                code = {"link_mismatch": "source_mismatch", "not_found": "original_unavailable",
                        "request_failed": "remote_failure"}.get(error.code, error.code)
                raise ReviewSourceError(code, "DICOM 원본의 연결과 읽기를 확인하지 못했습니다.") from None
            source = opened.source
            mapping = opened.imported.provenance()
        else:
            from patient_storage_service import open_selected_original

            source = open_selected_original(
                project_id, session, root, patient, ref["study_id"],
                ref["series_id"], ref["object_id"],
            ).source
    except LocalStorageError:
        raise ReviewSourceError("original_unavailable", "보관한 원본을 확인하지 못했습니다.") from None
    except (StudyImageConflict, StudyPatientConflict):
        raise ReviewSourceError("source_mismatch", "원본의 환자와 검사 연결이 다릅니다.") from None
    if (source.source_id != ref["source_id"]
            or sha256(source.original_bytes).hexdigest() != ref["source_id"]
            or source.file_format != ref["file_format"]
            or source.image.size != (ref["width"], ref["height"])):
        raise ReviewSourceError("source_mismatch", "실제 원본의 해시, 형식 또는 크기가 참조와 다릅니다.")
    return source, mapping
