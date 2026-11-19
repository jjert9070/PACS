"""Immutable references between a study and development-only local originals."""

from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import quote, urlencode
from urllib.request import Request

from firebase_client import FirebaseRequestError, Session, request_json
from local_storage import open_original, save_original
from patient_study_identity import _identifier
from patient_study_store import _collection_for, _create, _key, get_study
from series_store import get_series
from case_client import _request
from viewer import ViewerSource


class StudyImageConflict(ValueError):
    """An image ID already refers to another study or original."""


class OriginalSavedLinkError(Exception):
    """The local original exists but the study association was not confirmed."""

    def __init__(self, original_key: str):
        super().__init__("원본 파일은 저장됐지만 검사 연결을 완료하지 못했습니다.")
        self.original_key = original_key


@dataclass(frozen=True)
class StudyImage:
    study_id: str
    series_id: str
    image_id: str
    original_key: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "study_id", _identifier(self.study_id, "검사 ID"))
        object.__setattr__(self, "series_id", _identifier(self.series_id, "시리즈 ID"))
        object.__setattr__(self, "image_id", _identifier(self.image_id, "영상 ID"))
        if not isinstance(self.original_key, str) or not re.fullmatch(
            r"[0-9a-f]{64}\.(png|jpg)", self.original_key
        ):
            raise ValueError("원본 파일 식별자가 올바르지 않습니다.")


def _decode(document: dict, collection: str, uid: str) -> StudyImage:
    try:
        fields = document["fields"]
        image = StudyImage(fields["study_id"]["stringValue"], fields["series_id"]["stringValue"],
                           fields["image_id"]["stringValue"],
                           fields["original_key"]["stringValue"])
        if (fields["owner_uid"] != {"stringValue": uid}
                or fields["study_id"] != {"stringValue": image.study_id}
                or fields["series_id"] != {"stringValue": image.series_id}
                or fields["series_key"] != {"stringValue": _key(image.series_id)}
                or fields["image_id"] != {"stringValue": image.image_id}
                or fields["study_key"] != {"stringValue": _key(image.study_id)}
                or document["name"] != collection + "/" + _key(image.image_id)):
            raise ValueError()
        return image
    except (KeyError, TypeError, ValueError, AttributeError):
        raise FirebaseRequestError() from None


def get_image(project_id: str, session: Session, image_id: str) -> StudyImage:
    image_id = _identifier(image_id, "영상 ID")
    collection = _collection_for(project_id, session, "study_images")
    result = _decode(_request(collection + "/" + _key(image_id), session), collection, session.uid)
    if result.image_id != image_id:
        raise FirebaseRequestError()
    return result


def link_original(project_id: str, session: Session, root: Path, image: StudyImage) -> None:
    if not isinstance(image, StudyImage):
        raise ValueError("검사와 원본 연결 정보가 필요합니다.")
    get_study(project_id, session, image.study_id)
    if get_series(project_id, session, image.series_id).study_id != image.study_id:
        raise StudyImageConflict("선택한 검사와 시리즈 연결이 일치하지 않습니다.")
    open_original(root, session.uid, image.original_key)
    collection = _collection_for(project_id, session, "study_images")
    values = {"owner_uid": session.uid, "study_id": image.study_id,
              "study_key": _key(image.study_id), "series_id": image.series_id,
              "series_key": _key(image.series_id), "image_id": image.image_id,
              "original_key": image.original_key}
    try:
        _create(collection, session, _key(image.image_id), values)
    except FirebaseRequestError as error:
        if error.status not in (403, 409):
            raise
        try:
            existing = get_image(project_id, session, image.image_id)
        except FirebaseRequestError as read_error:
            if read_error.status == 404:
                raise error from None
            raise
        if existing != image:
            raise StudyImageConflict("이미 다른 검사 또는 원본에 연결된 영상 ID입니다.") from None


def save_and_link_original(
    project_id: str, session: Session, root: Path, study_id: str, series_id: str, image_id: str,
    name: str, data: bytes,
) -> StudyImage:
    study_id = _identifier(study_id, "검사 ID")
    series_id = _identifier(series_id, "시리즈 ID")
    image_id = _identifier(image_id, "영상 ID")
    get_study(project_id, session, study_id)
    if get_series(project_id, session, series_id).study_id != study_id:
        raise StudyImageConflict("선택한 검사와 시리즈 연결이 일치하지 않습니다.")
    original_key = save_original(root, session.uid, name, data)
    image = StudyImage(study_id, series_id, image_id, original_key)
    try:
        link_original(project_id, session, root, image)
    except (FirebaseRequestError, ValueError) as error:
        raise OriginalSavedLinkError(original_key) from error
    return image


def open_study_original(
    project_id: str, session: Session, root: Path, study_id: str, image_id: str
) -> ViewerSource:
    study_id = _identifier(study_id, "검사 ID")
    image = get_image(project_id, session, image_id)
    if image.study_id != study_id:
        raise StudyImageConflict("선택한 검사와 영상 연결이 일치하지 않습니다.")
    get_study(project_id, session, study_id)
    if get_series(project_id, session, image.series_id).study_id != study_id:
        raise StudyImageConflict("저장된 검사와 시리즈 연결이 일치하지 않습니다.")
    return open_original(root, session.uid, image.original_key)


def list_study_images(
    project_id: str, session: Session, study_id: str, page_token: str = ""
) -> tuple[list[StudyImage], str]:
    study_id = _identifier(study_id, "검사 ID")
    if not isinstance(page_token, str):
        raise ValueError("페이지 정보가 올바르지 않습니다.")
    get_study(project_id, session, study_id)
    collection = _collection_for(project_id, session, "study_images")
    url = "https://firestore.googleapis.com/v1/" + quote(collection, safe="/()")
    data = request_json(Request(
        url + "?" + urlencode({"pageSize": 50, "pageToken": page_token}),
        headers={"Authorization": f"Bearer {session.id_token}"},
    ))
    documents, token = data.get("documents", []), data.get("nextPageToken", "")
    if not isinstance(documents, list) or not isinstance(token, str):
        raise FirebaseRequestError()
    images = [_decode(document, collection, session.uid) for document in documents]
    # shortcut: filtering occurs per page across all studies, use a server query when the schema is agreed.
    return [image for image in images if image.study_id == study_id], token


def list_series_images(
    project_id: str, session: Session, study_id: str, series_id: str, page_token: str = ""
) -> tuple[list[StudyImage], str]:
    study_id = _identifier(study_id, "검사 ID")
    series_id = _identifier(series_id, "시리즈 ID")
    if get_series(project_id, session, series_id).study_id != study_id:
        raise StudyImageConflict("선택한 검사와 시리즈 연결이 일치하지 않습니다.")
    images, token = list_study_images(project_id, session, study_id, page_token)
    return [image for image in images if image.series_id == series_id], token
