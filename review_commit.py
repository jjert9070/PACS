"""Firestore commit payload for a checked, serialized review snapshot."""

from copy import deepcopy
from hashlib import sha256
import json
import re

from case_client import _collection


def review_commit(project_id, session, snapshot, expected_version, operation_id, revision):
    """Build one atomic head and immutable-version commit; does not perform I/O.

    The caller must validate the source and snapshot, and derive revision from a
    trusted head. Firestore enforces the head's opaque update-time precondition.
    """
    collection = _collection(project_id, session).removesuffix("case_drafts") + "review_records"
    if (not isinstance(snapshot, dict) or snapshot.get("schema") != "pacs_review_v1"
            or not isinstance(snapshot.get("review_id"), str)
            or not re.fullmatch(r"[0-9a-f]{32}", snapshot["review_id"])
            or not isinstance(operation_id, str) or not re.fullmatch(r"[0-9a-f]{32}", operation_id)
            or type(revision) is not int or revision < 1
            or (expected_version is not None and (not isinstance(expected_version, str) or not expected_version))
            or (expected_version is None) != (revision == 1)):
        raise ValueError("검토 버전과 작업 식별자를 확인하세요.")
    try:
        canonical = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        if len(canonical.encode("utf-8")) > 60000:
            raise ValueError()
        frozen = json.loads(canonical)
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("검토 기록은 60,000바이트 이하의 유한한 JSON이어야 합니다.") from None
    request = json.dumps({"snapshot": frozen, "expected_version": expected_version},
                         sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    values = {
        "owner_uid": session.uid,
        "review_id": snapshot["review_id"],
        "operation_id": operation_id,
        "revision": revision,
        "request_hash": sha256(request.encode("utf-8")).hexdigest(),
        "snapshot_json": canonical,
    }
    fields = {key: {"integerValue": str(value)} if type(value) is int else {"stringValue": value}
              for key, value in values.items()}
    path = collection + "/" + snapshot["review_id"]
    timestamp = [{"fieldPath": "saved_at", "setToServerValue": "REQUEST_TIME"}]
    head = {"update": {"name": path, "fields": fields},
            "currentDocument": {"exists": False} if expected_version is None else {"updateTime": expected_version},
            "updateTransforms": timestamp}
    version = {"update": {"name": path + "/versions/" + operation_id, "fields": deepcopy(fields)},
               "currentDocument": {"exists": False}, "updateTransforms": deepcopy(timestamp)}
    return {"writes": [head, version]}
