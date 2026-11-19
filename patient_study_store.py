"""Owner-scoped Firestore storage for provisional patient and study identities."""

from hashlib import sha256
import json
from urllib.parse import quote, urlencode
from urllib.request import Request

from case_client import _collection, _request
from firebase_client import FirebaseRequestError, Session, request_json
from patient_study_identity import PatientIdentity, StudyIdentity, _identifier, validate_study_patient


def _key(*parts: str) -> str:
    encoded = json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def _collection_for(project_id: str, session: Session, kind: str) -> str:
    return _collection(project_id, session).removesuffix("case_drafts") + kind


def _patient_key(patient: PatientIdentity) -> str:
    return _key(patient.issuer, patient.patient_id)


def _decode_patient(document: dict, collection: str, uid: str) -> PatientIdentity:
    try:
        fields = document["fields"]
        patient = PatientIdentity(fields["issuer"]["stringValue"], fields["patient_id"]["stringValue"])
        if (fields["issuer"] != {"stringValue": patient.issuer}
                or fields["patient_id"] != {"stringValue": patient.patient_id}
                or fields["owner_uid"] != {"stringValue": uid}
                or document["name"] != collection + "/" + _patient_key(patient)):
            raise ValueError()
        return patient
    except (KeyError, TypeError, ValueError, AttributeError):
        raise FirebaseRequestError() from None


def _decode_study(document: dict, collection: str, uid: str) -> StudyIdentity:
    try:
        fields = document["fields"]
        patient = PatientIdentity(fields["issuer"]["stringValue"], fields["patient_id"]["stringValue"])
        study = StudyIdentity(fields["study_id"]["stringValue"], patient)
        if (fields["issuer"] != {"stringValue": patient.issuer}
                or fields["patient_id"] != {"stringValue": patient.patient_id}
                or fields["study_id"] != {"stringValue": study.study_id}
                or fields["owner_uid"] != {"stringValue": uid}
                or fields["patient_key"] != {"stringValue": _patient_key(patient)}
                or document["name"] != collection + "/" + _key(study.study_id)):
            raise ValueError()
        return study
    except (KeyError, TypeError, ValueError, AttributeError):
        raise FirebaseRequestError() from None


def get_patient(project_id: str, session: Session, patient: PatientIdentity) -> PatientIdentity:
    if not isinstance(patient, PatientIdentity):
        raise ValueError("환자 식별 정보가 필요합니다.")
    collection = _collection_for(project_id, session, "patients")
    result = _decode_patient(_request(collection + "/" + _patient_key(patient), session), collection, session.uid)
    if result != patient:
        raise FirebaseRequestError()
    return result


def get_study(project_id: str, session: Session, study_id: str) -> StudyIdentity:
    study_id = _identifier(study_id, "검사 ID")
    collection = _collection_for(project_id, session, "studies")
    result = _decode_study(_request(collection + "/" + _key(study_id), session), collection, session.uid)
    if result.study_id != study_id:
        raise FirebaseRequestError()
    return result


def _create(collection: str, session: Session, document_id: str, values: dict) -> None:
    fields = {key: {"stringValue": value} for key, value in values.items()}
    database = collection.split("/documents/")[0]
    _request(database + "/documents:commit", session, {"writes": [{
        "update": {"name": collection + "/" + document_id, "fields": fields},
        "currentDocument": {"exists": False},
    }]})


def save_patient(project_id: str, session: Session, patient: PatientIdentity) -> None:
    if not isinstance(patient, PatientIdentity):
        raise ValueError("환자 식별 정보가 필요합니다.")
    collection = _collection_for(project_id, session, "patients")
    values = {"owner_uid": session.uid, "issuer": patient.issuer, "patient_id": patient.patient_id}
    try:
        _create(collection, session, _patient_key(patient), values)
    except FirebaseRequestError as error:
        if error.status not in (403, 409):
            raise
        try:
            get_patient(project_id, session, patient)
        except FirebaseRequestError as read_error:
            if read_error.status == 404:
                raise error from None
            raise


def save_study(project_id: str, session: Session, study: StudyIdentity) -> None:
    if not isinstance(study, StudyIdentity):
        raise ValueError("검사 식별 정보가 필요합니다.")
    get_patient(project_id, session, study.patient)
    collection = _collection_for(project_id, session, "studies")
    values = {"owner_uid": session.uid, "study_id": study.study_id,
              "patient_key": _patient_key(study.patient), "issuer": study.patient.issuer,
              "patient_id": study.patient.patient_id}
    try:
        _create(collection, session, _key(study.study_id), values)
    except FirebaseRequestError as error:
        if error.status not in (403, 409):
            raise
        try:
            existing = get_study(project_id, session, study.study_id)
        except FirebaseRequestError as read_error:
            if read_error.status == 404:
                raise error from None
            raise
        validate_study_patient(existing, study)


def _list(project_id: str, session: Session, kind: str, page_token: str) -> tuple[list, str]:
    if not isinstance(page_token, str):
        raise ValueError("페이지 정보가 올바르지 않습니다.")
    collection = _collection_for(project_id, session, kind)
    url = "https://firestore.googleapis.com/v1/" + quote(collection, safe="/()")
    query = urlencode({"pageSize": 50, "pageToken": page_token})
    data = request_json(Request(url + "?" + query, headers={"Authorization": f"Bearer {session.id_token}"}))
    documents, next_token = data.get("documents", []), data.get("nextPageToken", "")
    if not isinstance(documents, list) or not isinstance(next_token, str):
        raise FirebaseRequestError()
    decode = _decode_patient if kind == "patients" else _decode_study
    return [decode(document, collection, session.uid) for document in documents], next_token


def list_patients(project_id: str, session: Session, page_token: str = "") -> tuple[list[PatientIdentity], str]:
    return _list(project_id, session, "patients", page_token)


def list_studies(project_id: str, session: Session, page_token: str = "") -> tuple[list[StudyIdentity], str]:
    return _list(project_id, session, "studies", page_token)
