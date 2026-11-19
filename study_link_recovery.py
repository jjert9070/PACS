"""Durable, user-scoped retry intents for development-only original links."""

from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
import sqlite3

from firebase_client import FirebaseRequestError, Session
from local_storage import LocalStorageError, _user_dir
from patient_study_identity import _identifier
from patient_study_store import _collection_for
from study_image_store import StudyImage, StudyImageConflict, link_original


@dataclass(frozen=True)
class PendingLink:
    image: StudyImage
    state: str
    error_code: str = ""


@contextmanager
def _database(root: Path, project_id: str, session: Session):
    _collection_for(project_id, session, "study_images")
    directory = _user_dir(root, session.uid)
    path = directory / "pending-links.sqlite3"
    if path.is_symlink() or path.is_junction():
        raise LocalStorageError("원본 연결 재시도 기록의 저장 경로를 확인할 수 없습니다.")
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=10)) as db:
            with db:
                # Serialize schema upgrades and writes across browser sessions.
                db.execute("BEGIN IMMEDIATE")
                db.execute("""CREATE TABLE IF NOT EXISTS pending_links (
                    project_id TEXT NOT NULL, image_id TEXT NOT NULL,
                    study_id TEXT NOT NULL, series_id TEXT NOT NULL,
                    original_key TEXT NOT NULL, state TEXT NOT NULL,
                    error_code TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (project_id, image_id))""")
                columns = {column[1] for column in db.execute("PRAGMA table_info(pending_links)")}
                if "error_code" not in columns:
                    db.execute("ALTER TABLE pending_links ADD COLUMN error_code TEXT NOT NULL DEFAULT ''")
                yield db
    except (OSError, sqlite3.Error):
        raise LocalStorageError("원본 연결 재시도 기록을 읽거나 저장하지 못했습니다.") from None


def remember_link(root: Path, project_id: str, session: Session, image: StudyImage) -> None:
    if not isinstance(image, StudyImage):
        raise ValueError("검사와 원본 연결 정보가 필요합니다.")
    with _database(root, project_id, session) as db:
        old = db.execute("SELECT study_id, series_id, original_key FROM pending_links "
                         "WHERE project_id=? AND image_id=?", (project_id, image.image_id)).fetchone()
        expected = (image.study_id, image.series_id, image.original_key)
        if old is not None and old != expected:
            raise StudyImageConflict("같은 영상 ID에 다른 재시도 요청이 남아 있습니다.")
        db.execute("INSERT OR IGNORE INTO pending_links "
                   "(project_id, image_id, study_id, series_id, original_key, state) VALUES (?, ?, ?, ?, ?, ?)",
                   (project_id, image.image_id, *expected, "pending"))


def list_pending_links(root: Path, project_id: str, session: Session) -> list[PendingLink]:
    with _database(root, project_id, session) as db:
        rows = db.execute("SELECT study_id, series_id, image_id, original_key, state, error_code "
                          "FROM pending_links WHERE project_id=? ORDER BY image_id", (project_id,)).fetchall()
    try:
        if any(row[4] not in {"pending", "retryable", "conflict", "original_error"} for row in rows):
            raise ValueError()
        if any(row[5] not in {"", "identity_conflict", "original_error", "permission_denied", "session_expired", "request_failed"} for row in rows):
            raise ValueError()
        return [PendingLink(StudyImage(*row[:4]), row[4], row[5]) for row in rows]
    except (ValueError, TypeError):
        raise LocalStorageError("원본 연결 재시도 기록이 올바르지 않습니다.") from None


def retry_pending_link(root: Path, project_id: str, session: Session, image_id: str) -> StudyImage:
    image_id = _identifier(image_id, "영상 ID")
    pending = next((item for item in list_pending_links(root, project_id, session)
                    if item.image.image_id == image_id), None)
    if pending is None:
        raise ValueError("해당 원본 연결 재시도 기록이 없습니다.")
    try:
        link_original(project_id, session, root, pending.image)
    except (FirebaseRequestError, ValueError) as error:
        state = "retryable"
        code = "request_failed"
        if isinstance(error, StudyImageConflict):
            state, code = "conflict", "identity_conflict"
        elif isinstance(error, LocalStorageError):
            state, code = "original_error", "original_error"
        elif isinstance(error, FirebaseRequestError):
            if error.status == 403:
                code = "permission_denied"
            elif error.status == 401:
                code = "session_expired"
        try:
            with _database(root, project_id, session) as db:
                db.execute("UPDATE pending_links SET state=?, error_code=? WHERE project_id=? AND image_id=? "
                           "AND study_id=? AND series_id=? AND original_key=?",
                           (state, code, project_id, image_id, pending.image.study_id,
                            pending.image.series_id, pending.image.original_key))
        except (LocalStorageError, FirebaseRequestError) as record_error:
            raise error from record_error
        raise
    with _database(root, project_id, session) as db:
        db.execute("DELETE FROM pending_links WHERE project_id=? AND image_id=? "
                   "AND study_id=? AND series_id=? AND original_key=?",
                   (project_id, image_id, pending.image.study_id, pending.image.series_id, pending.image.original_key))
    return pending.image
