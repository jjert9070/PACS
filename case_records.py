"""C01-C03: private image-backed cases and immutable versions."""

from dataclasses import dataclass, field
from io import BytesIO
import json
import logging
from pathlib import Path
import re

from PIL import Image

from case_client import _collection, _request, validate_draft
from firebase_client import FirebaseRequestError, Session
from local_storage import open_original, save_original
from viewer import ViewerSource, load_image


logger = logging.getLogger(__name__)


class CaseConflict(ValueError):
    pass


@dataclass(frozen=True)
class ProcessingRecord:
    source_id: str
    algorithm_id: str
    algorithm_version: str
    parameters: dict
    result: Image.Image = field(repr=False)
    roi: tuple[int, int, int, int] | None = None
    statistics: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CaseRecord:
    case_id: str
    values: dict
    update_time: str
    saved_at: str


def _id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("케이스/작업 ID가 올바르지 않습니다.")
    return value


def _cases(project_id: str, session: Session) -> str:
    return _collection(project_id, session).removesuffix("case_drafts") + "cases"


def _fields(values: dict) -> dict:
    return {
        key: {"integerValue": str(value)} if type(value) is int else {"stringValue": value}
        for key, value in values.items()
    }


def _decode(document: dict, collection: str, expected_case_id: str | None = None) -> CaseRecord:
    try:
        fields = document["fields"]
        values = {
            key: int(value["integerValue"]) if "integerValue" in value else value["stringValue"]
            for key, value in fields.items()
            if key != "saved_at"
        }
        case_id = expected_case_id or document["name"].removeprefix(collection + "/")
        _id(case_id)
        if expected_case_id is None and document["name"] != collection + "/" + case_id:
            raise ValueError()
        if expected_case_id is not None and document["name"] != collection + "/" + case_id + "/versions/" + values["operation_id"]:
            raise ValueError()
        uid = collection.split("/users/")[1].split("/")[0]
        if values["owner_uid"] != uid or values["state"] != "draft":
            raise ValueError()
        title, _ = validate_draft(values["title"], values["note"])
        if title != values["title"] or type(values["version"]) is not int or values["version"] < 1:
            raise ValueError()
        if (not isinstance(values["provenance"], str) or not values["provenance"].strip()
                or len(values["provenance"]) > 2000
                or not isinstance(values["original_name"], str)
                or not 1 <= len(values["original_name"]) <= 512
                or not isinstance(document["updateTime"], str)
                or not isinstance(fields["saved_at"]["timestampValue"], str)):
            raise ValueError()
        _id(values["operation_id"])
        if not re.fullmatch(r"[0-9a-f]{64}", values["source_id"]):
            raise ValueError()
        if values["original_key"] != values["source_id"] + (
            ".png" if values["file_format"] == "PNG" else ".jpg"
        ):
            raise ValueError()
        if values["file_format"] not in {"PNG", "JPEG"}:
            raise ValueError()
        if not all(type(values[k]) is int and values[k] > 0 for k in ("width", "height", "byte_count")):
            raise ValueError()
        if not isinstance(json.loads(values["record_json"]), dict):
            raise ValueError()
        return CaseRecord(
            case_id, values, document["updateTime"], fields["saved_at"]["timestampValue"]
        )
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        raise FirebaseRequestError() from None


def get_case(project_id: str, session: Session, case_id: str) -> CaseRecord:
    collection = _cases(project_id, session)
    record = _decode(_request(collection + "/" + _id(case_id), session), collection)
    if record.case_id != case_id:
        raise FirebaseRequestError()
    return record


def list_cases(
    project_id: str, session: Session, page_token: str = ""
) -> tuple[list[CaseRecord], str]:
    from urllib.parse import quote, urlencode
    from urllib.request import Request
    from firebase_client import request_json

    collection = _cases(project_id, session)
    url = "https://firestore.googleapis.com/v1/" + quote(collection, safe="/()")
    query = urlencode({"pageSize": 50, "pageToken": page_token})
    data = request_json(Request(url + "?" + query, headers={"Authorization": f"Bearer {session.id_token}"}))
    documents, token = data.get("documents", []), data.get("nextPageToken", "")
    if not isinstance(documents, list) or not isinstance(token, str):
        raise FirebaseRequestError()
    return [_decode(document, collection) for document in documents], token


def list_versions(project_id: str, session: Session, case_id: str) -> list[CaseRecord]:
    collection = _cases(project_id, session)
    case_path = collection + "/" + _id(case_id)
    from urllib.parse import quote, urlencode
    from urllib.request import Request
    from firebase_client import request_json

    versions, token = [], ""
    while True:
        url = "https://firestore.googleapis.com/v1/" + quote(case_path, safe="/()")
        data = request_json(Request(
            url + "/versions?" + urlencode({"pageSize": 50, "pageToken": token}),
            headers={"Authorization": f"Bearer {session.id_token}"},
        ))
        documents = data.get("documents", [])
        if not isinstance(documents, list):
            raise FirebaseRequestError()
        for document in documents:
            record = _decode(document, collection, case_id)
            if document["name"] != case_path + "/versions/" + record.values["operation_id"]:
                raise FirebaseRequestError()
            versions.append(record)
        next_token = data.get("nextPageToken", "")
        if not isinstance(next_token, str) or (next_token and next_token == token):
            raise FirebaseRequestError()
        if not next_token:
            return sorted(versions, key=lambda item: item.values["version"], reverse=True)
        token = next_token


def _processing_json(processing: ProcessingRecord | None, source: ViewerSource) -> dict:
    if processing is None:
        return {}
    if not isinstance(processing, ProcessingRecord):
        raise ValueError("처리 기록 형식이 올바르지 않습니다.")
    if processing.source_id != source.source_id:
        raise ValueError("처리 기록이 현재 원본과 다릅니다.")
    if not all(isinstance(value, str) and value.strip() for value in (processing.algorithm_id, processing.algorithm_version)):
        raise ValueError("알고리즘 이름과 버전이 필요합니다.")
    if not isinstance(processing.result, Image.Image) or processing.result.size != source.image.size or processing.result.mode != "RGB":
        raise ValueError("처리 결과는 원본과 같은 크기의 RGB 이미지여야 합니다.")
    if not isinstance(processing.parameters, dict) or not isinstance(processing.statistics, dict):
        raise ValueError("처리 설정과 통계 형식이 올바르지 않습니다.")
    roi = processing.roi
    if roi is not None:
        if not isinstance(roi, (tuple, list)) or len(roi) != 4 or any(type(value) is not int for value in roi):
            raise ValueError("ROI는 정수 좌표 네 개여야 합니다.")
        left, top, right, bottom = roi
        if not (0 <= left < right <= source.image.width and 0 <= top < bottom <= source.image.height):
            raise ValueError("ROI가 원본 범위를 벗어났습니다.")
    return {
        "source_id": source.source_id,
        "algorithm_id": processing.algorithm_id,
        "algorithm_version": processing.algorithm_version,
        "parameters": processing.parameters,
        "roi": roi,
        "statistics": processing.statistics,
        "coordinate_space": "exif_transposed_original_pixels_v1",
    }


def save_case(
    root: Path, project_id: str, session: Session, case_id: str, operation_id: str,
    source: ViewerSource, title: str, note: str, provenance: str,
    base: CaseRecord | None = None, processing: ProcessingRecord | None = None,
) -> None:
    collection = _cases(project_id, session)
    case_id, operation_id = _id(case_id), _id(operation_id)
    title, note = validate_draft(title, note)
    checked_source = load_image(source.name, source.original_bytes)
    if (checked_source.source_id != source.source_id or checked_source.image.size != source.image.size
            or checked_source.file_format != source.file_format or checked_source.byte_count != source.byte_count):
        raise ValueError("원본 바이트와 영상 정보가 일치하지 않습니다.")
    if not provenance.strip() or len(provenance) > 2000:
        raise ValueError("원본 출처를 1~2,000자로 입력하세요.")
    if len(source.name) > 512:
        raise ValueError("파일명이 너무 깁니다.")
    if base and (base.case_id != case_id or base.values["owner_uid"] != session.uid
                 or base.values["source_id"] != source.source_id):
        raise ValueError("수정할 케이스와 원본 연결이 일치하지 않습니다.")
    if base and (source.name != base.values["original_name"]
                 or source.image.size != (base.values["width"], base.values["height"])):
        raise ValueError("기존 케이스의 원본 정보는 변경할 수 없습니다.")
    metadata = _processing_json(processing, source)
    if base and processing is None:
        reopen_case(root, session, base)
        metadata = json.loads(base.values["record_json"])
    # Validate JSON before preserving any file; NaN/Infinity are not valid records.
    try:
        json.dumps(metadata, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError("처리 설정과 통계에는 유한한 숫자와 JSON으로 저장 가능한 값만 사용할 수 있습니다.") from None
    original_key = save_original(root, session.uid, source.name, source.original_bytes)
    if original_key.split(".")[0] != source.source_id:
        raise ValueError("원본 바이트와 영상 식별자가 일치하지 않습니다.")
    if processing:
        stream = BytesIO()
        processing.result.save(stream, format="PNG")
        metadata["result_key"] = save_original(root / "results", session.uid, "result.png", stream.getvalue())
    record_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if len(record_json) > 60000:
        raise ValueError("처리 기록이 너무 큽니다.")
    values = {
        "owner_uid": session.uid, "title": title, "note": note,
        "provenance": provenance.strip(), "source_id": source.source_id,
        "original_key": original_key, "original_name": source.name,
        "file_format": source.file_format, "width": source.image.width,
        "height": source.image.height, "byte_count": source.byte_count,
        "state": "draft", "version": base.values["version"] + 1 if base else 1,
        "operation_id": operation_id, "record_json": record_json,
    }
    case_path = collection + "/" + case_id
    version_path = case_path + "/versions/" + operation_id
    fields = _fields(values)
    transform = [{"fieldPath": "saved_at", "setToServerValue": "REQUEST_TIME"}]
    writes = [
        {"update": {"name": case_path, "fields": fields},
         "currentDocument": {"updateTime": base.update_time} if base else {"exists": False},
         "updateTransforms": transform},
        {"update": {"name": version_path, "fields": fields},
         "currentDocument": {"exists": False}, "updateTransforms": transform},
    ]
    try:
        _request(collection.split("/documents/")[0] + "/documents:commit", session, {"writes": writes})
    except FirebaseRequestError as error:
        logger.warning("case_save_failed uid=%s case=%s operation=%s status=%s original=%s",
                       session.uid, case_id, operation_id, error.status, original_key)
        # A previous response may have been lost. Immutable version proves this exact write.
        if error.status not in {400, 409}:
            raise
        try:
            existing = _decode(_request(version_path, session), collection, case_id)
        except FirebaseRequestError as read_error:
            if read_error.status == 404:
                raise CaseConflict("다른 변경이 먼저 저장됐습니다. 최신 버전을 다시 열어 비교하세요.") from None
            raise
        if existing.values != values:
            raise CaseConflict("같은 작업 ID에 다른 내용이 있습니다. 덮어쓰지 않았습니다.")


def reopen_case(root: Path, session: Session, record: CaseRecord) -> tuple[ViewerSource, Image.Image | None]:
    if session.is_expired():
        raise FirebaseRequestError(401)
    values = record.values
    if values["owner_uid"] != session.uid:
        raise FirebaseRequestError(403)
    source = open_original(root, session.uid, values["original_key"])
    if (source.source_id != values["source_id"] or source.image.size != (values["width"], values["height"])
            or source.byte_count != values["byte_count"]):
        raise ValueError("저장 기록과 원본이 일치하지 않습니다.")
    metadata = json.loads(values["record_json"])
    result = None
    if metadata:
        if metadata.get("source_id") != source.source_id or metadata.get("coordinate_space") != "exif_transposed_original_pixels_v1":
            raise ValueError("처리 기록의 원본/좌표 기준이 일치하지 않습니다.")
        try:
            result = open_original(root / "results", session.uid, metadata["result_key"]).image
            processing = ProcessingRecord(
                source.source_id, metadata["algorithm_id"], metadata["algorithm_version"],
                metadata["parameters"], result, metadata.get("roi"), metadata.get("statistics", {}),
            )
        except (KeyError, TypeError):
            raise ValueError("저장된 처리 기록 형식이 올바르지 않습니다.") from None
        _processing_json(processing, source)
    # Restore the user's original display name, not the content-addressed disk key.
    from dataclasses import replace
    return replace(source, name=values["original_name"]), result
