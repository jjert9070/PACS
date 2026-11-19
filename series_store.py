"""Series identities and study associations for the development store."""

from dataclasses import dataclass

from case_client import _request
from firebase_client import FirebaseRequestError, Session
from storage_query import query_page
from patient_study_identity import _identifier
from patient_study_store import _collection_for, _create, _key, get_study


class SeriesConflict(ValueError):
    """The series identifier is already associated with different information."""


@dataclass(frozen=True)
class Series:
    study_id: str
    series_id: str
    modality: str = ""
    body_region: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "study_id", _identifier(self.study_id, "검사 ID"))
        object.__setattr__(self, "series_id", _identifier(self.series_id, "시리즈 ID"))
        for field in ("modality", "body_region"):
            value = getattr(self, field)
            if not isinstance(value, str):
                raise ValueError("시리즈 정보는 문자열이어야 합니다.")
            if value:
                object.__setattr__(self, field, _identifier(value, "시리즈 정보"))


def _decode(document: dict, collection: str, uid: str) -> Series:
    try:
        fields = document["fields"]
        series = Series(fields["study_id"]["stringValue"], fields["series_id"]["stringValue"],
                        fields["modality"]["stringValue"], fields["body_region"]["stringValue"])
        expected = {"owner_uid": uid, "study_id": series.study_id,
                    "study_key": _key(series.study_id), "series_id": series.series_id,
                    "modality": series.modality, "body_region": series.body_region}
        if (document["name"] != collection + "/" + _key(series.series_id)
                or any(fields[key] != {"stringValue": value} for key, value in expected.items())):
            raise ValueError()
        return series
    except (KeyError, TypeError, ValueError, AttributeError):
        raise FirebaseRequestError() from None


def get_series(project_id: str, session: Session, series_id: str) -> Series:
    series_id = _identifier(series_id, "시리즈 ID")
    collection = _collection_for(project_id, session, "series")
    series = _decode(_request(collection + "/" + _key(series_id), session), collection, session.uid)
    if series.series_id != series_id:
        raise FirebaseRequestError()
    return series


def save_series(project_id: str, session: Session, series: Series) -> None:
    if not isinstance(series, Series):
        raise ValueError("시리즈 정보가 필요합니다.")
    get_study(project_id, session, series.study_id)
    collection = _collection_for(project_id, session, "series")
    values = {"owner_uid": session.uid, "study_id": series.study_id,
              "study_key": _key(series.study_id), "series_id": series.series_id,
              "modality": series.modality, "body_region": series.body_region}
    try:
        _create(collection, session, _key(series.series_id), values)
    except FirebaseRequestError as error:
        if error.status not in (403, 409):
            raise
        try:
            existing = get_series(project_id, session, series.series_id)
        except FirebaseRequestError as read_error:
            if read_error.status == 404:
                raise error from None
            raise
        if existing != series:
            raise SeriesConflict("이미 다른 검사 또는 정보에 연결된 시리즈 ID입니다.") from None


def list_study_series(
    project_id: str, session: Session, study_id: str, page_token: str = ""
) -> tuple[list[Series], str]:
    study_id = _identifier(study_id, "검사 ID")
    if not isinstance(page_token, str):
        raise ValueError("페이지 정보가 올바르지 않습니다.")
    get_study(project_id, session, study_id)
    return query_page(project_id, session, "series", "study_key", _key(study_id), _decode, page_token)
