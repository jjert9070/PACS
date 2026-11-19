"""Immutable raw-byte preservation; decoding and clinical validation belong to the importer."""

from hashlib import sha256
import os
from pathlib import Path
import re
import tempfile

from local_storage import LocalStorageError, _cleanup_temporary, _user_dir
from viewer import MAX_FILE_BYTES


def _file(root: Path, uid: str, key: str) -> Path:
    if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}[.]dcm", key):
        raise LocalStorageError("DICOM 원본 참조가 올바르지 않습니다.")
    directory = _user_dir(root, uid)
    path = directory / key
    if path.is_symlink() or path.is_junction():
        raise LocalStorageError("DICOM 원본 저장 경로를 확인할 수 없습니다.")
    return path


def read_dicom_original(root: Path, uid: str, key: str) -> bytes:
    try:
        with _file(root, uid, key).open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
    except OSError:
        raise LocalStorageError("DICOM 원본을 읽지 못했습니다. 파일 누락과 권한을 확인하세요.") from None
    if not data or len(data) > MAX_FILE_BYTES or sha256(data).hexdigest() + ".dcm" != key:
        raise LocalStorageError("DICOM 원본 크기 또는 해시가 일치하지 않습니다.")
    return data


def save_dicom_original(root: Path, uid: str, data: bytes) -> str:
    # Keep the starter's 20 MB file limit until larger-series handling is implemented.
    if not isinstance(data, bytes) or not data or len(data) > MAX_FILE_BYTES:
        raise LocalStorageError("DICOM 원본은 비어 있지 않은 20 MB 이하의 바이트여야 합니다.")
    key = sha256(data).hexdigest() + ".dcm"
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
            os.link(temporary, target)
        except FileExistsError:
            if read_dicom_original(root, uid, key) != data:
                raise LocalStorageError("기존 DICOM 원본과 충돌했습니다. 덮어쓰지 않았습니다.")
    except OSError:
        raise LocalStorageError("DICOM 원본을 저장하지 못했습니다.") from None
    finally:
        _cleanup_temporary(temporary)
    return key
