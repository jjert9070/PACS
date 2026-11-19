"""Confirmed DICOM registration with durable, scoped retries and no implicit decoder."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from dicom_instance_store import (Decoder, DicomInstance, DicomInstanceConflict, DicomInputError,
                                  _inspect, _uid, claim_dicom_uids, get_dicom_instance, link_dicom_original)
from dicom_original_storage import read_dicom_original, save_dicom_original
from firebase_client import FirebaseRequestError, Session
from local_storage import LocalStorageError
from patient_study_identity import PatientIdentity, StudyIdentity, validate_study_patient
from patient_study_store import get_study
from series_store import Series, save_series
from study_link_recovery import _database


class DicomOriginalSavedError(Exception):
    def __init__(self, original_key: str, retry_recorded: bool):
        super().__init__("DICOM 원본은 보관됐지만 연결 저장을 확인하지 못했습니다.")
        self.original_key = original_key
        self.retry_recorded = retry_recorded


@dataclass(frozen=True)
class PendingDicom:
    selected: StudyIdentity
    series_uid: str
    sop_uid: str
    original_key: str
    state: str = "pending"


def _table(db):
    db.execute("""CREATE TABLE IF NOT EXISTS pending_dicom (
        project_id TEXT NOT NULL, sop_uid TEXT NOT NULL,
        issuer TEXT NOT NULL, patient_id TEXT NOT NULL,
        study_uid TEXT NOT NULL, series_uid TEXT NOT NULL,
        original_key TEXT NOT NULL, state TEXT NOT NULL,
        PRIMARY KEY (project_id, sop_uid))""")


def _identity(pending: PendingDicom) -> tuple:
    return (pending.selected.patient.issuer, pending.selected.patient.patient_id,
            pending.selected.study_id, pending.series_uid, pending.original_key)


def _remember(root: Path, project_id: str, session: Session, pending: PendingDicom):
    with _database(root, project_id, session) as db:
        _table(db)
        old = db.execute("SELECT issuer, patient_id, study_uid, series_uid, original_key FROM pending_dicom "
                         "WHERE project_id=? AND sop_uid=?", (project_id, pending.sop_uid)).fetchone()
        if old is not None and old != _identity(pending):
            raise DicomInstanceConflict("같은 SOP UID의 다른 재시도 기록이 있습니다. 보류했습니다.")
        db.execute("INSERT OR IGNORE INTO pending_dicom VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (project_id, pending.sop_uid, *_identity(pending), "pending"))


def list_pending_dicom(root: Path, project_id: str, session: Session) -> list[PendingDicom]:
    with _database(root, project_id, session) as db:
        _table(db)
        rows = db.execute("SELECT issuer, patient_id, study_uid, series_uid, sop_uid, original_key, state "
                          "FROM pending_dicom WHERE project_id=? ORDER BY sop_uid", (project_id,)).fetchall()
    pending = []
    try:
        for issuer, patient_id, study, series, sop, key, state in rows:
            if state not in {"pending", "permission_denied", "session_expired", "request_failed", "conflict", "original_error", "invalid_input"}:
                raise ValueError()
            for value in (study, series, sop):
                _uid(value)
            if not isinstance(key, str) or len(key) != 68 or not key.endswith(".dcm") or any(c not in "0123456789abcdef" for c in key[:-4]):
                raise ValueError()
            pending.append(PendingDicom(StudyIdentity(study, PatientIdentity(issuer, patient_id)), series, sop, key, state))
    except (TypeError, ValueError):
        raise LocalStorageError("DICOM 재시도 기록이 올바르지 않습니다.") from None
    return pending


def _finish(root: Path, project_id: str, session: Session, pending: PendingDicom, state: str):
    with _database(root, project_id, session) as db:
        _table(db)
        parameters = (project_id, pending.sop_uid, *_identity(pending))
        where = "project_id=? AND sop_uid=? AND issuer=? AND patient_id=? AND study_uid=? AND series_uid=? AND original_key=?"
        if state == "linked":
            db.execute("DELETE FROM pending_dicom WHERE " + where, parameters)
        else:
            db.execute("UPDATE pending_dicom SET state=? WHERE " + where, (state, *parameters))


def retry_dicom_registration(root: Path, project_id: str, session: Session, sop_uid: str,
                             decoder: Decoder) -> DicomInstance:
    sop_uid = _uid(sop_uid)
    pending = next((p for p in list_pending_dicom(root, project_id, session) if p.sop_uid == sop_uid), None)
    if pending is None:
        raise DicomInputError("해당 DICOM 재시도 기록이 없습니다.")
    try:
        data = read_dicom_original(root, session.uid, pending.original_key)
        metadata = _inspect(data, decoder)
        if (metadata.patient != pending.selected.patient or metadata.study_uid != pending.selected.study_id
                or metadata.series_uid != pending.series_uid or metadata.sop_uid != sop_uid):
            raise DicomInstanceConflict("보관한 원본과 확인된 선택 정보가 다릅니다.")
        instance = DicomInstance(metadata, pending.original_key)
        link_dicom_original(project_id, session, root, instance, decoder)
    except (FirebaseRequestError, ValueError) as error:
        if isinstance(error, FirebaseRequestError):
            state = "request_failed"
            if error.status == 403:
                state = "permission_denied"
            elif error.status == 401:
                state = "session_expired"
        elif isinstance(error, LocalStorageError):
            state = "original_error"
        elif isinstance(error, DicomInputError):
            state = "invalid_input"
        else:
            state = "conflict"
        try:
            _finish(root, project_id, session, pending, state)
        except (LocalStorageError, FirebaseRequestError) as record_error:
            raise error from record_error
        raise
    _finish(root, project_id, session, pending, "linked")
    return instance


def register_dicom_original(project_id: str, session: Session, root: Path, selected: StudyIdentity,
                            series_uid: str, data: bytes, decoder: Decoder) -> DicomInstance:
    if not isinstance(selected, StudyIdentity):
        raise DicomInputError("확인된 환자와 검사 선택이 필요합니다.")
    _uid(selected.study_id)
    series_uid = _uid(series_uid)
    metadata = _inspect(data, decoder)
    if (metadata.patient != selected.patient or metadata.study_uid != selected.study_id
            or metadata.series_uid != series_uid):
        raise DicomInstanceConflict("DICOM 해석 결과와 선택한 환자, 검사 또는 시리즈가 다릅니다.")
    validate_study_patient(get_study(project_id, session, selected.study_id), selected)
    key = sha256(data).hexdigest() + ".dcm"
    candidate = DicomInstance(metadata, key)
    try:
        old = get_dicom_instance(project_id, session, metadata.sop_uid)
    except FirebaseRequestError as error:
        if error.status != 404:
            raise
    else:
        if old != candidate:
            raise DicomInstanceConflict("같은 SOP UID의 다른 원본 또는 연결이 있습니다. 보류했습니다.")
    claim_dicom_uids(project_id, session, metadata)
    save_series(project_id, session, Series(metadata.study_uid, metadata.series_uid, metadata.modality, metadata.body_region))
    key = save_dicom_original(root, session.uid, data)
    pending = PendingDicom(selected, series_uid, metadata.sop_uid, key)
    recorded = False
    try:
        _remember(root, project_id, session, pending)
        recorded = True
        return retry_dicom_registration(root, project_id, session, metadata.sop_uid, decoder)
    except (FirebaseRequestError, ValueError) as error:
        raise DicomOriginalSavedError(key, recorded) from error
