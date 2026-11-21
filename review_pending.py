"""Persist immutable review requests locally for explicit retry after a restart."""

from dataclasses import dataclass
import json
from pathlib import Path
import re

from local_storage import LocalStorageError
from review_commit import review_commit
from study_link_recovery import _database


class PendingReviewConflict(ValueError):
    pass


@dataclass(frozen=True)
class PendingReview:
    operation_id: str
    snapshot: dict
    expected_version: str | None
    revision: int


def _table(db):
    db.execute("""CREATE TABLE IF NOT EXISTS pending_reviews (
        project_id TEXT NOT NULL, operation_id TEXT NOT NULL,
        snapshot_json TEXT NOT NULL, expected_version TEXT, revision INTEGER NOT NULL,
        PRIMARY KEY (project_id, operation_id))""")


def remember_review(root: Path, project_id, session, snapshot, expected_version, operation_id, revision):
    """Persist the already validated snapshot before its DB commit is attempted."""
    payload = review_commit(project_id, session, snapshot, expected_version, operation_id, revision)
    canonical = payload["writes"][0]["update"]["fields"]["snapshot_json"]["stringValue"]
    values = (canonical, expected_version, revision)
    with _database(root, project_id, session) as db:
        _table(db)
        row = db.execute("SELECT snapshot_json, expected_version, revision FROM pending_reviews "
                         "WHERE project_id=? AND operation_id=?", (project_id, operation_id)).fetchone()
        if row is not None and row != values:
            raise PendingReviewConflict("같은 작업 ID의 다른 재시도 요청이 있습니다.")
        db.execute("INSERT OR IGNORE INTO pending_reviews VALUES (?, ?, ?, ?, ?)",
                   (project_id, operation_id, *values))


def get_pending_review(root: Path, project_id, session, operation_id) -> PendingReview:
    if not isinstance(operation_id, str) or not re.fullmatch(r"[0-9a-f]{32}", operation_id):
        raise ValueError("작업 ID가 올바르지 않습니다.")
    with _database(root, project_id, session) as db:
        _table(db)
        row = db.execute("SELECT snapshot_json, expected_version, revision FROM pending_reviews "
                         "WHERE project_id=? AND operation_id=?", (project_id, operation_id)).fetchone()
    if row is None:
        raise ValueError("재시도할 검토 기록이 없습니다.")
    try:
        snapshot = json.loads(row[0])
        payload = review_commit(project_id, session, snapshot, row[1], operation_id, row[2])
        if payload["writes"][0]["update"]["fields"]["snapshot_json"]["stringValue"] != row[0]:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise LocalStorageError("검토 재시도 기록이 올바르지 않습니다.") from None
    return PendingReview(operation_id, snapshot, row[1], row[2])
