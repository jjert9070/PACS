"""Store DICOM references after the trusted B importer has inspected original bytes."""

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Callable

from case_client import _request
from dicom_original_storage import read_dicom_original
from firebase_client import FirebaseRequestError, Session
from patient_study_identity import PatientIdentity, StudyIdentity, validate_study_patient
from patient_study_store import _collection_for, _create, _key, get_study
from series_store import Series, get_series
from storage_query import query_page
from viewer import MAX_FILE_BYTES


class DicomInputError(ValueError):
    pass


class DicomInstanceConflict(ValueError):
    pass


def _uid(value: str) -> str:
    if (not isinstance(value, str) or len(value) > 64
            or not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:[.](?:0|[1-9][0-9]*))+", value)):
        raise DicomInputError("DICOM UID 형식이 올바르지 않습니다.")
    return value


@dataclass(frozen=True)
class DicomMetadata:
    patient: PatientIdentity
    study_uid: str
    series_uid: str
    sop_uid: str
    sop_class_uid: str
    frame_count: int
    modality: str = ""
    body_region: str = ""

    def __post_init__(self):
        if not isinstance(self.patient, PatientIdentity):
            raise DicomInputError("발급 주체를 포함한 환자 식별 정보가 필요합니다.")
        for field in ("study_uid", "series_uid", "sop_uid", "sop_class_uid"):
            _uid(getattr(self, field))
        if len({self.study_uid, self.series_uid, self.sop_uid, self.sop_class_uid}) != 4:
            raise DicomInputError("검사, 시리즈, 원본 객체와 SOP Class는 서로 다른 UID여야 합니다.")
        if type(self.frame_count) is not int or not 0 <= self.frame_count <= 2147483647:
            raise DicomInputError("확인된 프레임 수가 필요합니다. 비영상 객체는 0으로 전달합니다.")
        series = Series(self.study_uid, self.series_uid, self.modality, self.body_region)
        object.__setattr__(self, "modality", series.modality)
        object.__setattr__(self, "body_region", series.body_region)


@dataclass(frozen=True)
class DicomInstance:
    metadata: DicomMetadata
    original_key: str

    def __post_init__(self):
        if (not isinstance(self.metadata, DicomMetadata)
                or not isinstance(self.original_key, str)
                or not re.fullmatch(r"[0-9a-f]{64}[.]dcm", self.original_key)):
            raise DicomInputError("DICOM 원본 연결 정보가 올바르지 않습니다.")


Decoder = Callable[[bytes], DicomMetadata]


def _inspect(data: bytes, decoder: Decoder) -> DicomMetadata:
    if not isinstance(data, bytes) or not data or len(data) > MAX_FILE_BYTES or not callable(decoder):
        raise DicomInputError("지원 범위의 원본과 신뢰할 수 있는 DICOM 해석 함수가 필요합니다.")
    try:
        metadata = decoder(data)
    except Exception:
        raise DicomInputError("DICOM 해석 또는 지원 조건 확인에 실패했습니다.") from None
    if not isinstance(metadata, DicomMetadata):
        raise DicomInputError("DICOM 해석 결과가 저장 계약과 일치하지 않습니다.")
    return metadata


def _values(uid: str, instance: DicomInstance) -> dict:
    m = instance.metadata
    return {
        "owner_uid": uid,
        "issuer": m.patient.issuer,
        "patient_id": m.patient.patient_id,
        "study_id": m.study_uid,
        "study_key": _key(m.study_uid),
        "series_id": m.series_uid,
        "series_key": _key(m.series_uid),
        "sop_uid": m.sop_uid,
        "sop_class_uid": m.sop_class_uid,
        "frame_count": m.frame_count,
        "modality": m.modality,
        "body_region": m.body_region,
        "original_key": instance.original_key,
        "content_hash": instance.original_key[:-4],
    }


def _decode(document: dict, collection: str, uid: str) -> DicomInstance:
    try:
        fields = document["fields"]
        value = {k: int(v["integerValue"]) if "integerValue" in v else v["stringValue"]
                 for k, v in fields.items()}
        metadata = DicomMetadata(PatientIdentity(value["issuer"], value["patient_id"]),
                                  value["study_id"], value["series_id"], value["sop_uid"],
                                  value["sop_class_uid"], value["frame_count"], value["modality"], value["body_region"])
        instance = DicomInstance(metadata, value["original_key"])
        expected = _values(uid, instance)
        expected_fields = {
            key: {"integerValue": str(item)} if type(item) is int else {"stringValue": item}
            for key, item in expected.items()
        }
        if fields != expected_fields or document["name"] != collection + "/" + _key(metadata.sop_uid):
            raise ValueError()
        return instance
    except (KeyError, TypeError, ValueError, AttributeError):
        raise FirebaseRequestError() from None


def get_dicom_instance(project_id: str, session: Session, sop_uid: str) -> DicomInstance:
    sop_uid = _uid(sop_uid)
    collection = _collection_for(project_id, session, "dicom_instances")
    instance = _decode(_request(collection + "/" + _key(sop_uid), session), collection, session.uid)
    if instance.metadata.sop_uid != sop_uid:
        raise FirebaseRequestError()
    return instance


def claim_dicom_uids(project_id: str, session: Session, metadata: DicomMetadata) -> None:
    collection = _collection_for(project_id, session, "dicom_uid_claims")
    for value, role in ((metadata.study_uid, "study"), (metadata.series_uid, "series"), (metadata.sop_uid, "instance")):
        values = {"owner_uid": session.uid, "dicom_uid": value, "role": role}
        try:
            _create(collection, session, _key(value), values)
        except FirebaseRequestError as error:
            if error.status not in (403, 409, 412):
                raise
            try:
                old = _request(collection + "/" + _key(value), session)
            except FirebaseRequestError as read_error:
                if read_error.status == 404:
                    raise error from None
                raise
            expected = {k: {"stringValue": v} for k, v in values.items()}
            if old.get("name") != collection + "/" + _key(value):
                raise FirebaseRequestError()
            if old.get("fields") != expected:
                raise DicomInstanceConflict("다른 객체 종류에 사용된 DICOM UID입니다. 보류했습니다.") from None


def link_dicom_original(project_id: str, session: Session, root: Path,
                        instance: DicomInstance, decoder: Decoder) -> None:
    if not isinstance(instance, DicomInstance):
        raise DicomInputError("DICOM 원본 연결 정보가 필요합니다.")
    collection = _collection_for(project_id, session, "dicom_instances")
    data = read_dicom_original(root, session.uid, instance.original_key)
    if _inspect(data, decoder) != instance.metadata:
        raise DicomInstanceConflict("원본에서 읽은 정보와 저장할 정보가 다릅니다.")
    m = instance.metadata
    validate_study_patient(get_study(project_id, session, m.study_uid), StudyIdentity(m.study_uid, m.patient))
    series = get_series(project_id, session, m.series_uid)
    if series != Series(m.study_uid, m.series_uid, m.modality, m.body_region):
        raise DicomInstanceConflict("검사와 시리즈 연결 정보가 일치하지 않습니다.")
    claim_dicom_uids(project_id, session, m)
    values = _values(session.uid, instance)
    fields = {k: {"integerValue": str(v)} if type(v) is int else {"stringValue": v} for k, v in values.items()}
    try:
        _request(collection.split("/documents/")[0] + "/documents:commit", session,
                 {"writes": [{"update": {"name": collection + "/" + _key(m.sop_uid), "fields": fields},
                              "currentDocument": {"exists": False}}]})
    except FirebaseRequestError as error:
        if error.status not in (403, 409, 412):
            raise
        try:
            old = get_dicom_instance(project_id, session, m.sop_uid)
        except FirebaseRequestError as read_error:
            if read_error.status == 404:
                raise error from None
            raise
        if old != instance:
            raise DicomInstanceConflict("같은 SOP UID에 다른 원본 또는 연결 정보가 있습니다. 보류했습니다.") from None


def open_dicom_original(project_id: str, session: Session, root: Path,
                        selected: StudyIdentity, series_uid: str, sop_uid: str, decoder: Decoder) -> bytes:
    if not isinstance(selected, StudyIdentity):
        raise DicomInputError("확인된 환자와 검사 선택이 필요합니다.")
    instance = get_dicom_instance(project_id, session, sop_uid)
    m = instance.metadata
    if m.study_uid != selected.study_id or m.patient != selected.patient or m.series_uid != _uid(series_uid):
        raise DicomInstanceConflict("선택한 환자와 검사, 시리즈가 원본과 일치하지 않습니다.")
    validate_study_patient(get_study(project_id, session, m.study_uid), selected)
    if get_series(project_id, session, m.series_uid).study_id != m.study_uid:
        raise DicomInstanceConflict("시리즈가 선택한 검사에 속하지 않습니다.")
    data = read_dicom_original(root, session.uid, instance.original_key)
    if _inspect(data, decoder) != m:
        raise DicomInstanceConflict("원본 해석 결과가 저장된 정보와 다릅니다.")
    return data


def list_series_instances(project_id: str, session: Session, study_uid: str, series_uid: str,
                          page_token: str = "") -> tuple[list[DicomInstance], str]:
    study_uid, series_uid = _uid(study_uid), _uid(series_uid)
    get_study(project_id, session, study_uid)
    if get_series(project_id, session, series_uid).study_id != study_uid:
        raise DicomInstanceConflict("시리즈가 선택한 검사에 속하지 않습니다.")
    instances, token = query_page(project_id, session, "dicom_instances", "series_key", _key(series_uid), _decode, page_token)
    if any(item.metadata.study_uid != study_uid for item in instances):
        raise FirebaseRequestError()
    return instances, token
