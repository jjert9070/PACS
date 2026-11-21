"""Commit checked review metadata and resolve retry outcomes using stored evidence."""

from dataclasses import dataclass
import json

from case_client import _request
from firebase_client import FirebaseRequestError
from review_commit import review_commit


@dataclass(frozen=True)
class CommitOutcome:
    status: str
    code: str
    snapshot: dict | None = None
    version_token: str | None = None


def _decode(document, path, uid):
    try:
        fields = document["fields"]
        if (document["name"] != path or fields["owner_uid"] != {"stringValue": uid}
                or not isinstance(document["updateTime"], str) or not document["updateTime"]
                or not isinstance(fields["saved_at"]["timestampValue"], str)):
            raise ValueError()
        values = {key: value["stringValue"] for key, value in fields.items()
                  if key not in ("saved_at", "revision")}
        revision = int(fields["revision"]["integerValue"])
        snapshot = json.loads(values["snapshot_json"])
        if (revision < 1 or not isinstance(snapshot, dict)
                or snapshot.get("review_id") != values["review_id"]):
            raise ValueError()
        return values, snapshot, document["updateTime"], revision
    except (KeyError, TypeError, ValueError, AttributeError):
        raise FirebaseRequestError() from None


def commit_review(project_id, session, snapshot, expected_version, operation_id, revision):
    """Return evidence-based metadata outcome; callers must validate and authorize first."""
    payload = review_commit(project_id, session, snapshot, expected_version, operation_id, revision)
    head = payload["writes"][0]["update"]["name"]
    version = payload["writes"][1]["update"]["name"]
    requested = payload["writes"][1]["update"]["fields"]
    endpoint = head.split("/documents/")[0] + "/documents:commit"
    try:
        _request(endpoint, session, payload)
    except FirebaseRequestError as error:
        if error.status not in (400, 403, 409, 412):
            raise
        try:
            doc = _request(version, session)
        except FirebaseRequestError as read_error:
            if read_error.status != 404:
                raise
            try:
                latest = _request(head, session)
            except FirebaseRequestError as latest_error:
                if latest_error.status == 404:
                    raise error from None
                raise
            _, _, latest_token, _ = _decode(latest, head, session.uid)
            if expected_version is None or latest_token != expected_version:
                return CommitOutcome("conflict", "stale_version", version_token=latest_token)
            raise error from None
        values, saved, token, saved_revision = _decode(doc, version, session.uid)
        if (values.get("review_id") != snapshot["review_id"]
                or values.get("operation_id") != operation_id):
            raise FirebaseRequestError() from None
        if (values.get("request_hash") != requested["request_hash"]["stringValue"]
                or values.get("snapshot_json") != requested["snapshot_json"]["stringValue"]
                or saved_revision != revision):
            return CommitOutcome("conflict", "operation_payload_mismatch")
        return CommitOutcome("reused", "ok", saved, token)
    # Read the immutable version to obtain the server-assigned token and timestamp.
    values, saved, token, saved_revision = _decode(_request(version, session), version, session.uid)
    if (values.get("operation_id") != operation_id or values.get("review_id") != snapshot["review_id"]
            or values.get("request_hash") != requested["request_hash"]["stringValue"]
            or values.get("snapshot_json") != requested["snapshot_json"]["stringValue"]
            or saved_revision != revision):
        raise FirebaseRequestError()
    return CommitOutcome("saved", "ok", saved, token)
