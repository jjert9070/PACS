"""Result-file primitives for reviews; metadata commits belong to review_store."""

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re

from case_processing import freeze_processing, validate_processing
from case_records import ProcessingRecord, _processing_json
from firebase_client import FirebaseRequestError, Session
from local_storage import open_original, save_original
from roi import COORDINATE_SYSTEM
from viewer import ViewerSource


MAX_METADATA_BYTES = 60_000
_REQUIRED = {
    "source_id", "algorithm_id", "algorithm_version", "parameters", "roi",
    "statistics", "coordinate_space", "result_key",
}


def _metadata_copy(metadata: dict) -> dict:
    try:
        encoded = json.dumps(metadata, ensure_ascii=False, sort_keys=True, allow_nan=False)
        if len(encoded.encode("utf-8")) > MAX_METADATA_BYTES:
            raise ValueError()
        return json.loads(encoded)
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("처리 기록은 60,000바이트 이하의 유한한 JSON 값이어야 합니다.") from None


def save_processing_result(
    root: Path, session: Session, source: ViewerSource, processing: ProcessingRecord | None,
) -> dict | None:
    """Preserve applied RGB pixels separately; this is not a completed review save.

    The review service must first obtain source through open_review_source and
    validate the applied algorithm. No original bytes are written by this helper.
    """
    if session.is_expired():
        raise FirebaseRequestError(401)
    frozen = freeze_processing(source, processing)
    if frozen is None:
        return None
    stream = BytesIO()
    frozen.result.save(stream, format="PNG")
    data = stream.getvalue()
    key = sha256(data).hexdigest() + ".png"
    metadata = _processing_json(frozen, source)
    metadata["result_key"] = key
    # Validate the byte limit before publishing a result file.
    metadata = _metadata_copy(metadata)
    saved_key = save_original(root / "results", session.uid, "result.png", data)
    if saved_key != key:
        raise ValueError("결과 파일의 해시가 저장 참조와 다릅니다.")
    return metadata


def load_processing_result(
    root: Path, session: Session, source: ViewerSource, metadata: dict | None,
) -> ProcessingRecord | None:
    """Read saved pixels without rerunning an algorithm or replacing its environment.

    The caller must authorize the review and reopen its original before calling.
    """
    if session.is_expired():
        raise FirebaseRequestError(401)
    if metadata is None:
        return None
    if (not isinstance(metadata, dict) or not _REQUIRED <= metadata.keys()
            or metadata.keys() - (_REQUIRED | {"environment"})):
        raise ValueError("저장된 처리 기록의 필드가 올바르지 않습니다.")
    metadata = _metadata_copy(metadata)
    key = metadata["result_key"]
    if (metadata["source_id"] != source.source_id
            or metadata["coordinate_space"] != COORDINATE_SYSTEM
            or not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}[.]png", key)):
        raise ValueError("저장된 처리 기록의 원본, 좌표계 또는 결과 참조가 다릅니다.")
    result = open_original(root / "results", session.uid, key)
    record = ProcessingRecord(
        source.source_id, metadata["algorithm_id"], metadata["algorithm_version"],
        metadata["parameters"], result.image, metadata["roi"], metadata["statistics"],
        environment=metadata.get("environment"),
    )
    return validate_processing(source, record)
