from io import BytesIO
import json
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from firebase_client import FirebaseRequestError, Session, check_firestore, sign_in


class FirebaseClientTests(unittest.TestCase):
    @patch("firebase_client.urlopen")
    def test_login_and_firestore_use_the_authenticated_account(self, urlopen):
        urlopen.side_effect = [
            BytesIO(
                json.dumps(
                    {
                        "localId": "example-uid",
                        "email": "test@example.com",
                        "idToken": "example-token",
                        "expiresIn": "3600",
                    }
                ).encode()
            ),
            BytesIO(b'{"fields":{}}'),
        ]
        session = sign_in("example-key", "test@example.com", "test-password")
        check_firestore("example-project", session)

        auth_request = urlopen.call_args_list[0].args[0]
        self.assertEqual(auth_request.get_method(), "POST")
        self.assertIn("accounts:signInWithPassword?key=example-key", auth_request.full_url)
        self.assertTrue(json.loads(auth_request.data)["returnSecureToken"])
        firestore_request = urlopen.call_args_list[1].args[0]
        self.assertEqual(firestore_request.get_method(), "GET")
        self.assertTrue(firestore_request.full_url.endswith("/starter_checks/example-uid"))
        self.assertEqual(firestore_request.get_header("Authorization"), "Bearer example-token")
        self.assertNotIn("example-token", repr(session))

    @patch("firebase_client.urlopen")
    def test_expired_session_does_not_contact_firestore(self, urlopen):
        session = Session("uid", "test@example.com", "token", time.monotonic() - 1)
        with self.assertRaises(FirebaseRequestError) as error:
            check_firestore("example-project", session)
        self.assertEqual(error.exception.status, 401)
        urlopen.assert_not_called()

    @patch("firebase_client.urlopen")
    def test_http_error_keeps_status_and_hides_response_body(self, urlopen):
        urlopen.side_effect = HTTPError(
            "https://example.invalid", 403, "Forbidden", {}, BytesIO(b"raw upstream body")
        )
        session = Session("uid", "test@example.com", "token", time.monotonic() + 60)
        with self.assertRaises(FirebaseRequestError) as error:
            check_firestore("example-project", session)
        self.assertEqual(error.exception.status, 403)
        self.assertNotIn("raw upstream body", str(error.exception))

    @patch("firebase_client.urlopen", return_value=BytesIO(b'{"localId":"uid"}'))
    def test_incomplete_login_response_cannot_create_session(self, _urlopen):
        with self.assertRaises(FirebaseRequestError):
            sign_in("example-key", "test@example.com", "test-password")


if __name__ == "__main__":
    unittest.main()
