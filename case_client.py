"""Private metadata-only case drafts. Original images are not stored here."""

from dataclasses import dataclass
import json
import re
from urllib.parse import quote, urlencode
from urllib.request import Request

from firebase_client import FirebaseRequestError, Session, request_json


MAX_TITLE = 120
MAX_NOTE = 4000


@dataclass(frozen=True)
class CaseDraft:
    draft_id: str
    title: str
    note: str
    created_at: str


def validate_draft(title: str, note: str) -> tuple[str, str]:
    if not isinstance(title, str) or not isinstance(note, str):
        raise ValueError("제목과 메모는 문자열이어야 합니다.")
    title = title.strip()
    if not title or len(title) > MAX_TITLE:
        raise ValueError(f"제목은 공백을 제외하고 1~{MAX_TITLE}자로 입력하세요.")
    if len(note) > MAX_NOTE:
        raise ValueError(f"메모는 {MAX_NOTE}자 이하로 입력하세요.")
    return title, note


def _collection(project_id: str, session: Session) -> str:
    if session.is_expired():
        raise FirebaseRequestError(401)
    for segment in (project_id, session.uid):
        if not segment or "/" in segment or segment in {".", ".."}:
            raise ValueError("프로젝트 또는 사용자 식별자가 올바르지 않습니다.")
    return (
        f"projects/{project_id}/databases/(default)/documents"
        f"/users/{session.uid}/case_drafts"
    )


def _request(resource: str, session: Session, payload: dict | None = None) -> dict:
    url = "https://firestore.googleapis.com/v1/" + quote(resource, safe="/():")
    return request_json(
        Request(
            url,
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            headers={
                "Authorization": f"Bearer {session.id_token}",
                "Content-Type": "application/json",
            },
        )
    )


def _decode(document: dict, collection: str, uid: str) -> CaseDraft:
    try:
        name = document["name"]
        draft_id = name.removeprefix(collection + "/")
        fields = document["fields"]
        title = fields["title"]["stringValue"]
        note = fields["note"]["stringValue"]
        created_at = fields["created_at"]["timestampValue"]
        valid = (
            name == collection + "/" + draft_id
            and re.fullmatch(r"[0-9a-f]{32}", draft_id)
            and fields["owner_uid"] == {"stringValue": uid}
            and fields["stage"] == {"stringValue": "metadata_only"}
            and fields["version"] == {"integerValue": "1"}
            and isinstance(created_at, str)
            and bool(created_at)
        )
        clean_title, _ = validate_draft(title, note)
        if not valid or clean_title != title:
            raise ValueError()
        return CaseDraft(draft_id, title, note, created_at)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise FirebaseRequestError() from None


def save_draft(
    project_id: str, session: Session, draft_id: str, title: str, note: str
) -> None:
    collection = _collection(project_id, session)
    if not re.fullmatch(r"[0-9a-f]{32}", draft_id):
        raise ValueError("초안 식별자가 올바르지 않습니다.")
    title, note = validate_draft(title, note)
    name = collection + "/" + draft_id
    fields = {
        "owner_uid": {"stringValue": session.uid},
        "title": {"stringValue": title},
        "note": {"stringValue": note},
        "stage": {"stringValue": "metadata_only"},
        "version": {"integerValue": "1"},
    }
    database = collection.split("/documents/")[0]
    try:
        _request(
            database + "/documents:commit",
            session,
            {
                "writes": [
                    {
                        "update": {"name": name, "fields": fields},
                        "currentDocument": {"exists": False},
                        "updateTransforms": [
                            {
                                "fieldPath": "created_at",
                                "setToServerValue": "REQUEST_TIME",
                            }
                        ],
                    }
                ]
            },
        )
    except FirebaseRequestError as error:
        if error.status != 409:
            raise
        # Retry after a lost response must not create or overwrite a second draft.
        existing = _decode(_request(name, session), collection, session.uid)
        if (existing.title, existing.note) != (title, note):
            raise FirebaseRequestError(409) from None


def list_drafts(
    project_id: str, session: Session, page_token: str = ""
) -> tuple[list[CaseDraft], str]:
    collection = _collection(project_id, session)
    query = urlencode({"pageSize": 50, "pageToken": page_token})
    url = "https://firestore.googleapis.com/v1/" + quote(collection, safe="/()")
    data = request_json(
        Request(url + "?" + query, headers={"Authorization": f"Bearer {session.id_token}"})
    )
    documents = data.get("documents", [])
    next_token = data.get("nextPageToken", "")
    if not isinstance(documents, list) or not isinstance(next_token, str):
        raise FirebaseRequestError()
    return [_decode(doc, collection, session.uid) for doc in documents], next_token
