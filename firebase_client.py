from dataclasses import dataclass, field
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


class FirebaseRequestError(Exception):
    def __init__(self, status: int | None = None):
        super().__init__("Firebase 요청을 완료하지 못했습니다.")
        self.status = status


@dataclass(frozen=True)
class Session:
    uid: str
    email: str
    id_token: str = field(repr=False)
    expires_at: float

    def is_expired(self) -> bool:
        return time.monotonic() >= self.expires_at


def request_json(request: Request) -> dict:
    try:
        with urlopen(request, timeout=10) as response:
            data = json.load(response)
    except HTTPError as error:
        error.close()
        raise FirebaseRequestError(error.code) from None
    except (URLError, OSError, ValueError):
        raise FirebaseRequestError() from None

    if not isinstance(data, dict):
        raise FirebaseRequestError()
    return data


def sign_in(api_key: str, email: str, password: str) -> Session:
    query = urlencode({"key": api_key})
    endpoint = (
        "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?"
        + query
    )
    payload = json.dumps(
        {"email": email, "password": password, "returnSecureToken": True}
    ).encode("utf-8")
    started_at = time.monotonic()
    data = request_json(
        Request(endpoint, data=payload, headers={"Content-Type": "application/json"})
    )
    try:
        uid = data["localId"]
        account_email = data["email"]
        token = data["idToken"]
        lifetime = int(data["expiresIn"])
    except (KeyError, TypeError, ValueError):
        raise FirebaseRequestError() from None

    if lifetime <= 0 or not all(
        isinstance(value, str) and value for value in (uid, account_email, token)
    ):
        raise FirebaseRequestError()
    return Session(uid, account_email, token, started_at + lifetime)


def check_firestore(project_id: str, session: Session) -> None:
    if session.is_expired():
        raise FirebaseRequestError(401)
    project = quote(project_id, safe="")
    uid = quote(session.uid, safe="")
    endpoint = (
        f"https://firestore.googleapis.com/v1/projects/{project}"
        f"/databases/(default)/documents/starter_checks/{uid}"
    )
    request_json(
        Request(endpoint, headers={"Authorization": f"Bearer {session.id_token}"})
    )
