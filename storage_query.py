"""User-scoped equality queries with bound document-name cursors."""

import base64
import binascii
import json
import re
from urllib.parse import quote
from urllib.request import Request

from firebase_client import FirebaseRequestError, Session, request_json
from patient_study_store import _collection_for


def query_page(project_id: str, session: Session, kind: str, field: str, value: str,
               decode, page_token: str = "") -> tuple[list, str]:
    if (kind, field) not in {("studies", "patient_key"), ("series", "study_key"),
                            ("study_images", "study_key"), ("study_images", "series_key"),
                            ("dicom_instances", "series_key")}:
        raise ValueError("지원하지 않는 목록 조회입니다.")
    collection = _collection_for(project_id, session, kind)
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("조회 대상 식별자가 올바르지 않습니다.")
    if not isinstance(page_token, str) or len(page_token) > 4096:
        raise ValueError("페이지 정보가 올바르지 않습니다.")
    query = {
        "from": [{"collectionId": kind}],
        "where": {
            "fieldFilter": {
                "field": {"fieldPath": field},
                "op": "EQUAL",
                "value": {"stringValue": value},
            }
        },
        "orderBy": [{"field": {"fieldPath": "__name__"}, "direction": "ASCENDING"}],
        "limit": 51,
    }
    binding = [collection, field, value]
    if page_token:
        try:
            cursor = json.loads(base64.b64decode(page_token, altchars=b"-_", validate=True))
            if (not isinstance(cursor, list) or len(cursor) != 4 or cursor[:3] != binding
                    or not isinstance(cursor[3], str) or not re.fullmatch(r"[0-9a-f]{64}", cursor[3])):
                raise ValueError()
        except (ValueError, TypeError, binascii.Error, UnicodeError):
            raise ValueError("선택한 목록과 페이지 정보가 일치하지 않습니다.") from None
        query["startAt"] = {"values": [{"referenceValue": collection + "/" + cursor[3]}], "before": False}
    parent = collection.rsplit("/", 1)[0]
    url = "https://firestore.googleapis.com/v1/" + quote(parent, safe="/()") + ":runQuery"
    request = Request(
        url,
        data=json.dumps({"structuredQuery": query}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {session.id_token}",
            "Content-Type": "application/json",
        },
    )
    rows = request_json(request, array_response=True)
    if not isinstance(rows, list) or any(not isinstance(row, dict) or "error" in row for row in rows):
        raise FirebaseRequestError()
    for row in rows:
        if "document" not in row:
            if not isinstance(row.get("readTime"), str) or not row["readTime"]:
                raise FirebaseRequestError()
            continue
        doc = row["document"]
        if not isinstance(doc, dict) or not isinstance(doc.get("name"), str) or not isinstance(doc.get("fields"), dict):
            raise FirebaseRequestError()
        key = doc["name"].removeprefix(collection + "/")
        if doc["name"] != collection + "/" + key or not re.fullmatch(r"[0-9a-f]{64}", key):
            raise FirebaseRequestError()
    documents = [row["document"] for row in rows if "document" in row]
    if len(documents) > 51:
        raise FirebaseRequestError()
    results, last_name = [], ""
    for doc in documents:
        item = decode(doc, collection, session.uid)
        name = doc["name"]
        if (doc.get("fields", {}).get(field) != {"stringValue": value}
                or name <= last_name
                or (page_token and name <= collection + "/" + cursor[3])):
            raise FirebaseRequestError()
        results.append(item)
        last_name = name
    next_token = ""
    if len(documents) > 50:
        last_key = documents[49]["name"].rsplit("/", 1)[1]
        next_token = base64.urlsafe_b64encode(json.dumps([*binding, last_key]).encode("utf-8")).decode("ascii")
    return results[:50], next_token
