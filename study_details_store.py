"""Development study date and separate linkage and receipt states."""

from dataclasses import dataclass
from datetime import date

from case_client import _request
from firebase_client import FirebaseRequestError, Session
from patient_study_identity import _identifier
from patient_study_store import _collection_for, _key, get_study


class StudyDetailsConflict(ValueError):
    """Another request has changed the study details."""


@dataclass(frozen=True)
class StudyDetails:
    study_id: str
    acquired_on: str = ""
    linkage_status: str = "unconfirmed"
    receipt_status: str = "unknown"

    def __post_init__(self):
        object.__setattr__(self, "study_id", _identifier(self.study_id, "검사 ID"))
        if not isinstance(self.acquired_on, str):
            raise ValueError("촬영일은 날짜 문자열이어야 합니다.")
        if self.acquired_on:
            try:
                if date.fromisoformat(self.acquired_on).isoformat() != self.acquired_on:
                    raise ValueError()
            except ValueError:
                raise ValueError("촬영일은 YYYY-MM-DD 형식의 실제 날짜여야 합니다.") from None
        if not isinstance(self.linkage_status, str) or self.linkage_status not in {"unconfirmed", "confirmed", "conflict", "on_hold"}:
            raise ValueError("환자 연결 상태가 올바르지 않습니다.")
        # shortcut: receipt completion is blocked until trusted completion evidence is agreed.
        if not isinstance(self.receipt_status, str) or self.receipt_status not in {"unknown", "receiving", "partial", "failed"}:
            raise ValueError("수신 완료를 포함한 해당 상태는 현재 저장할 수 없습니다.")


@dataclass(frozen=True)
class SavedStudyDetails:
    details: StudyDetails
    update_time: str


def _decode(document: dict, collection: str, uid: str) -> SavedStudyDetails:
    try:
        fields = document["fields"]
        details = StudyDetails(fields["study_id"]["stringValue"],
                               fields["acquired_on"]["stringValue"],
                               fields["linkage_status"]["stringValue"],
                               fields["receipt_status"]["stringValue"])
        expected = {"owner_uid": uid, "study_id": details.study_id,
                    "study_key": _key(details.study_id), "acquired_on": details.acquired_on,
                    "linkage_status": details.linkage_status, "receipt_status": details.receipt_status}
        update_time = document["updateTime"]
        if (document["name"] != collection + "/" + _key(details.study_id)
                or not isinstance(update_time, str) or not update_time
                or any(fields[k] != {"stringValue": v} for k, v in expected.items())):
            raise ValueError()
        return SavedStudyDetails(details, update_time)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise FirebaseRequestError() from None


def get_study_details(project_id: str, session: Session, study_id: str) -> SavedStudyDetails:
    study_id = _identifier(study_id, "검사 ID")
    get_study(project_id, session, study_id)
    collection = _collection_for(project_id, session, "study_details")
    saved = _decode(_request(collection + "/" + _key(study_id), session), collection, session.uid)
    if saved.details.study_id != study_id:
        raise FirebaseRequestError()
    return saved


def save_study_details(
    project_id: str, session: Session, details: StudyDetails, update_time: str | None = None
) -> None:
    if not isinstance(details, StudyDetails):
        raise ValueError("검사 상세 정보가 필요합니다.")
    if update_time is not None and (not isinstance(update_time, str) or not update_time):
        raise ValueError("이전에 읽은 검사 상세 버전이 필요합니다.")
    get_study(project_id, session, details.study_id)
    collection = _collection_for(project_id, session, "study_details")
    name = collection + "/" + _key(details.study_id)
    values = {"owner_uid": session.uid, "study_id": details.study_id,
              "study_key": _key(details.study_id), "acquired_on": details.acquired_on,
              "linkage_status": details.linkage_status, "receipt_status": details.receipt_status}
    fields = {key: {"stringValue": value} for key, value in values.items()}
    condition = {"exists": False} if update_time is None else {"updateTime": update_time}
    try:
        _request(collection.split("/documents/")[0] + "/documents:commit", session,
                 {"writes": [{"update": {"name": name, "fields": fields}, "currentDocument": condition}]})
    except FirebaseRequestError as error:
        # Firestore can return 400 for an update-time mismatch; verify the saved version.
        if error.status not in (400, 403, 409, 412):
            raise
        try:
            existing = get_study_details(project_id, session, details.study_id)
        except FirebaseRequestError as read_error:
            if read_error.status == 404:
                raise error from None
            raise
        if update_time is None and existing.details == details:
            return
        if update_time is None or existing.update_time != update_time:
            raise StudyDetailsConflict("다른 변경이 있어 검사 상세 정보를 다시 열어야 합니다.") from None
        raise error
