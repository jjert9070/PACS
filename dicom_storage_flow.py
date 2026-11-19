"""B: app-side DICOM registration, retry and reopen on top of C's storage functions.

C (feature/C-patient-study-storage) owns the storage, identity checks and retry
records; this module only drives them with B's trusted decoder and turns every
outcome into an explicit per-file result. C modules are imported when a function
runs, so this file also loads on branches without C's code.

Never done here: inventing identifiers, retrying under a new SOP UID, linking to
another patient to get past a conflict, or treating a saved-but-unlinked
original as a completed registration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

from dicom_input import DicomImport, DicomReadError, read_dicom, storage_metadata, to_viewer_source
from viewer import ViewerSource

# status values
REGISTERED = "registered"            # new original saved and linked
REUSED = "reused"                    # same SOP UID, content and link already stored
REJECTED = "rejected"                # B could not accept the file (nothing saved)
CONFLICT = "conflict"                # selection or existing SOP UID differs (nothing saved, nothing overwritten)
FAILED = "failed"                    # request/storage failure before the original was saved
SAVED_NOT_LINKED = "saved_not_linked"  # original kept, link not confirmed -> retry

# stage values
BEFORE_SAVE = "before_save"
AFTER_SAVE = "after_save"


@dataclass(frozen=True)
class DicomFileResult:
    """Outcome for one file. Only REGISTERED and REUSED are completed registrations."""

    file_name: str
    status: str
    stage: str
    message: str
    error_code: str = ""
    sop_uid: str = ""
    original_key: str = ""
    retry_recorded: bool = False
    instance: object = field(default=None, repr=False, compare=False)   # C's DicomInstance

    @property
    def completed(self) -> bool:
        return self.status in (REGISTERED, REUSED)


@dataclass(frozen=True)
class DicomDisplay:
    """What the viewer needs from one reopened DICOM object."""

    source: ViewerSource          # display image + untouched original bytes
    sop_uid: str
    object_frame_count: int       # frames in the stored DICOM object (C's frame_count)
    displayable_frame_count: int  # frames B decoded for display in this stage
    imported: DicomImport = field(repr=False, compare=False)


class DicomOpenError(ValueError):
    """Reopening failed; never shown as a normal image."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code   # not_found | original_unavailable | link_mismatch | invalid_input | permission_denied | session_expired | request_failed


def _request_code(error) -> str:
    status = getattr(error, "status", None)
    return {401: "session_expired", 403: "permission_denied", 404: "not_found"}.get(status, "request_failed")


def _saved_message(retry_recorded: bool) -> str:
    if retry_recorded:
        return ("원본은 보관됐지만 연결 저장을 확인하지 못했습니다. 미완료 목록에서 같은 원본으로 재시도하세요. "
                "등록 완료가 아닙니다.")
    return ("원본은 보관됐지만 연결 저장과 재시도 기록을 모두 확인하지 못했습니다. 같은 파일로 다시 등록하세요. "
            "등록 완료가 아닙니다.")


def register_dicom_file(project_id: str, session, root: Path, selected, series_uid: str,
                        file_name: str, data: bytes) -> DicomFileResult:
    """Decode with B first, then call C's register_dicom_original with the original bytes."""
    from dicom_instance_store import DicomInputError, DicomInstanceConflict, get_dicom_instance
    from dicom_registration import DicomOriginalSavedError, register_dicom_original
    from firebase_client import FirebaseRequestError
    from local_storage import LocalStorageError

    try:
        imported = read_dicom(file_name, data)       # precise B reason before anything is stored
    except DicomReadError as error:
        return DicomFileResult(file_name, REJECTED, BEFORE_SAVE, str(error), error.code)
    try:
        get_dicom_instance(project_id, session, imported.sop_uid)
        existed = True
    except FirebaseRequestError as error:
        if error.status != 404:
            return DicomFileResult(file_name, FAILED, BEFORE_SAVE, "기존 등록 여부를 확인하지 못했습니다.",
                                   _request_code(error), imported.sop_uid)
        existed = False
    try:
        instance = register_dicom_original(project_id, session, root, selected, series_uid, data, storage_metadata)
    except DicomOriginalSavedError as error:
        return DicomFileResult(file_name, SAVED_NOT_LINKED, AFTER_SAVE, _saved_message(error.retry_recorded),
                               "link_not_confirmed", imported.sop_uid, error.original_key, error.retry_recorded)
    except DicomInstanceConflict as error:
        return DicomFileResult(file_name, CONFLICT, BEFORE_SAVE,
                               str(error) + " 기존 자료는 바뀌지 않았습니다.", "conflict", imported.sop_uid)
    except DicomInputError as error:
        return DicomFileResult(file_name, REJECTED, BEFORE_SAVE, str(error), "invalid_input", imported.sop_uid)
    except FirebaseRequestError as error:
        return DicomFileResult(file_name, FAILED, BEFORE_SAVE, "원본을 저장하기 전에 요청이 실패했습니다.",
                               _request_code(error), imported.sop_uid)
    except LocalStorageError as error:
        return DicomFileResult(file_name, FAILED, BEFORE_SAVE, str(error), "local_storage_failure", imported.sop_uid)
    except ValueError as error:   # e.g. selection/study validation in C
        return DicomFileResult(file_name, CONFLICT, BEFORE_SAVE, str(error), "invalid_selection", imported.sop_uid)
    return DicomFileResult(file_name, REUSED if existed else REGISTERED, AFTER_SAVE,
                           "같은 원본과 연결을 재사용했습니다." if existed else "원본을 보관하고 연결했습니다.",
                           "", instance.metadata.sop_uid, instance.original_key, False, instance)


def register_dicom_files(project_id: str, session, root: Path, selected, series_uid: str,
                         files: list[tuple[str, bytes]]) -> list[DicomFileResult]:
    """One result per file, in input order; a failure never hides other files' outcomes."""
    return [register_dicom_file(project_id, session, root, selected, series_uid, name, data)
            for name, data in files]


def summarize(results: list[DicomFileResult]) -> dict:
    """Counts for the screen; 'completed' is never inflated by saved-but-unlinked files."""
    counts = {status: 0 for status in (REGISTERED, REUSED, REJECTED, CONFLICT, FAILED, SAVED_NOT_LINKED)}
    for result in results:
        counts[result.status] += 1
    counts["completed"] = counts[REGISTERED] + counts[REUSED]
    counts["total"] = len(results)
    return counts


def list_pending(project_id: str, session, root: Path) -> list:
    """C's unfinished links for this user and project (survives app restarts)."""
    from dicom_registration import list_pending_dicom

    return list_pending_dicom(root, project_id, session)


def retry_pending(project_id: str, session, root: Path, sop_uid: str) -> DicomFileResult:
    """Retry the same SOP UID with the original C kept; never a new UID or another patient."""
    from dicom_instance_store import DicomInputError, DicomInstanceConflict
    from dicom_registration import retry_dicom_registration
    from firebase_client import FirebaseRequestError
    from local_storage import LocalStorageError

    try:
        instance = retry_dicom_registration(root, project_id, session, sop_uid, storage_metadata)
    except FirebaseRequestError as error:
        return DicomFileResult("", FAILED, AFTER_SAVE, "재시도 요청이 실패했습니다. 원본은 보관돼 있습니다.",
                               _request_code(error), sop_uid)
    except LocalStorageError as error:
        return DicomFileResult("", FAILED, AFTER_SAVE, str(error), "original_unavailable", sop_uid)
    except DicomInstanceConflict as error:
        return DicomFileResult("", CONFLICT, AFTER_SAVE, str(error) + " 기존 자료는 바뀌지 않았습니다.", "conflict", sop_uid)
    except DicomInputError as error:
        return DicomFileResult("", REJECTED, AFTER_SAVE, str(error), "invalid_input", sop_uid)
    return DicomFileResult("", REGISTERED, AFTER_SAVE, "재시도로 연결을 완료했습니다.", "",
                           instance.metadata.sop_uid, instance.original_key, False, instance)


def open_dicom_display(project_id: str, session, root: Path, selected, series_uid: str, sop_uid: str) -> DicomDisplay:
    """Reopen through C (relationship, hash and re-decode checks), then decode for display."""
    from dicom_instance_store import DicomInputError, DicomInstanceConflict, get_dicom_instance, open_dicom_original
    from firebase_client import FirebaseRequestError
    from local_storage import LocalStorageError

    try:
        stored = get_dicom_instance(project_id, session, sop_uid)
        data = open_dicom_original(project_id, session, root, selected, series_uid, sop_uid, storage_metadata)
    except FirebaseRequestError as error:
        raise DicomOpenError(_request_code(error), "저장된 DICOM 정보를 조회하지 못했습니다.") from None
    except LocalStorageError as error:
        raise DicomOpenError("original_unavailable", str(error)) from None
    except DicomInstanceConflict as error:
        raise DicomOpenError("link_mismatch", str(error)) from None
    except DicomInputError as error:
        raise DicomOpenError("invalid_input", str(error)) from None
    if sha256(data).hexdigest() + ".dcm" != stored.original_key:
        raise DicomOpenError("original_unavailable", "보관한 원본의 해시가 저장 참조와 다릅니다.")
    imported = read_dicom(sop_uid + ".dcm", data)
    if imported.sop_uid != sop_uid:
        raise DicomOpenError("link_mismatch", "다시 연 원본의 SOP UID가 요청과 다릅니다.")
    return DicomDisplay(to_viewer_source(imported), sop_uid, stored.metadata.frame_count, 1, imported)
