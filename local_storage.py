"""Development-only original files, separated by authenticated user on one host."""

from hashlib import sha256
import logging
import os
from pathlib import Path
import re
import tempfile

from viewer import MAX_FILE_BYTES, ViewerSource, load_image


logger = logging.getLogger(__name__)


class LocalStorageError(ValueError):
    pass


def _cleanup_temporary(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        # Cleanup must not replace the publish result or its original exception.
        logger.warning("original_temp_cleanup_failed errno=%s", error.errno)


def storage_root() -> Path:
    default = Path(__file__).parent / ".local-data" / "originals"
    return Path(os.environ.get("PACS_LOCAL_STORAGE_DIR") or default).resolve()


def _user_dir(root: Path, uid: str) -> Path:
    if not isinstance(uid, str) or not uid:
        raise LocalStorageError("로그인 사용자 정보가 필요합니다.")
    root = root.resolve()
    user_dir = root / sha256(uid.encode("utf-8")).hexdigest()
    # The child name is a fixed hash; reject filesystem links instead of comparing path spellings.
    if user_dir.is_symlink() or user_dir.is_junction():
        raise LocalStorageError("저장 경로를 확인할 수 없습니다.")
    return user_dir


def _file(root: Path, uid: str, key: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}\.(png|jpg)", key):
        raise LocalStorageError("저장 파일 식별자가 올바르지 않습니다.")
    directory = _user_dir(root, uid)
    path = directory / key
    if path.is_symlink() or path.is_junction():
        raise LocalStorageError("저장 경로를 확인할 수 없습니다.")
    return path


def open_original(root: Path, uid: str, key: str) -> ViewerSource:
    path = _file(root, uid, key)
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
    except OSError:
        raise LocalStorageError("원본 파일을 읽을 수 없습니다. 누락 여부를 확인하세요.") from None
    if len(data) > MAX_FILE_BYTES or sha256(data).hexdigest() != key.split(".")[0]:
        raise LocalStorageError("저장 원본의 해시가 일치하지 않습니다. 파일을 열지 않았습니다.")
    return load_image(key, data)


def save_original(root: Path, uid: str, name: str, data: bytes) -> str:
    source = load_image(name, data)
    extension = "png" if source.file_format == "PNG" else "jpg"
    key = source.source_id + "." + extension
    target = _file(root, uid, key)
    temporary = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # Publish complete bytes without ever replacing an existing original.
            os.link(temporary, target)
        except FileExistsError:
            existing = open_original(root, uid, key)
            if existing.original_bytes != data:
                raise LocalStorageError("기존 원본과 충돌했습니다. 덮어쓰지 않았습니다.")
    except OSError:
        raise LocalStorageError("원본 파일을 저장하지 못했습니다. 저장 공간과 권한을 확인하세요.") from None
    finally:
        _cleanup_temporary(temporary)
    return key


def list_originals(root: Path, uid: str) -> list[str]:
    directory = _user_dir(root, uid)
    try:
        if not directory.exists():
            return []
        return sorted(
            item.name
            for item in directory.iterdir()
            if re.fullmatch(r"[0-9a-f]{64}\.(png|jpg)", item.name)
            and item.is_file()
            and not item.is_symlink()
            and not item.is_junction()
        )
    except OSError:
        raise LocalStorageError("저장 목록을 읽지 못했습니다.") from None
