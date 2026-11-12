import json
import os
import time
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from case_client import CaseDraft, list_drafts, save_draft, validate_draft
from firebase_client import FirebaseRequestError, Session


DRAFT_ID = "a" * 32
COLLECTION = "projects/test-project/databases/(default)/documents/users/user-a/case_drafts"


def document():
    return {
        "name": COLLECTION + "/" + DRAFT_ID,
        "fields": {
            "owner_uid": {"stringValue": "user-a"},
            "title": {"stringValue": "테스트 케이스"},
            "note": {"stringValue": "초안"},
            "stage": {"stringValue": "metadata_only"},
            "version": {"integerValue": "1"},
            "created_at": {"timestampValue": "2026-11-12T00:00:00Z"},
        },
    }


class CaseClientTests(unittest.TestCase):
    def setUp(self):
        self.session = Session("user-a", "test@example.com", "fake-token", float("inf"))

    @patch("case_client.request_json", return_value={"writeResults": [{}]})
    def test_save_uses_create_precondition_and_server_time(self, request):
        save_draft("test-project", self.session, DRAFT_ID, " 테스트 케이스 ", "초안")
        sent = request.call_args.args[0]
        self.assertEqual(sent.get_method(), "POST")
        self.assertTrue(sent.full_url.endswith("/documents:commit"))
        self.assertEqual(sent.get_header("Authorization"), "Bearer fake-token")
        write = json.loads(sent.data)["writes"][0]
        self.assertEqual(write["currentDocument"], {"exists": False})
        self.assertEqual(write["update"]["name"], COLLECTION + "/" + DRAFT_ID)
        self.assertEqual(write["update"]["fields"]["title"]["stringValue"], "테스트 케이스")
        self.assertEqual(write["update"]["fields"]["owner_uid"]["stringValue"], "user-a")
        self.assertEqual(write["updateTransforms"][0]["setToServerValue"], "REQUEST_TIME")
        self.assertNotIn("created_at", write["update"]["fields"])

    @patch("case_client.request_json")
    def test_invalid_input_never_contacts_firestore(self, request):
        for title, note in [("  ", ""), ("x" * 121, ""), ("x", "n" * 4001)]:
            with self.subTest(title=title[:8]), self.assertRaises(ValueError):
                save_draft("test-project", self.session, DRAFT_ID, title, note)
        with self.assertRaises(ValueError):
            save_draft("test-project", self.session, "../other", "x", "")
        request.assert_not_called()

    @patch("case_client.request_json")
    def test_expired_session_cannot_list_or_save(self, request):
        expired = Session("user-a", "e", "t", time.monotonic() - 1)
        for action in (
            lambda: save_draft("test-project", expired, DRAFT_ID, "x", ""),
            lambda: list_drafts("test-project", expired),
        ):
            with self.assertRaises(FirebaseRequestError) as caught:
                action()
            self.assertEqual(caught.exception.status, 401)
        request.assert_not_called()

    @patch("case_client.request_json")
    def test_lost_response_retry_reuses_identical_draft(self, request):
        request.side_effect = [FirebaseRequestError(409), document()]
        save_draft("test-project", self.session, DRAFT_ID, "테스트 케이스", "초안")
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args.args[0].get_method(), "GET")

    @patch("case_client.request_json")
    def test_conflicting_retry_does_not_overwrite(self, request):
        request.side_effect = [FirebaseRequestError(409), document()]
        with self.assertRaises(FirebaseRequestError) as caught:
            save_draft("test-project", self.session, DRAFT_ID, "다른 제목", "초안")
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(request.call_count, 2)

    @patch("case_client.request_json")
    def test_denied_write_stays_denied(self, request):
        request.side_effect = FirebaseRequestError(403)
        with self.assertRaises(FirebaseRequestError) as caught:
            save_draft("test-project", self.session, DRAFT_ID, "x", "")
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(request.call_count, 1)

    @patch("case_client.request_json")
    def test_list_decodes_and_preserves_pagination(self, request):
        request.return_value = {"documents": [document()], "nextPageToken": "next/+="}
        items, token = list_drafts("test-project", self.session, "old/+=" )
        self.assertEqual(items[0].title, "테스트 케이스")
        self.assertEqual(token, "next/+=")
        self.assertIn("pageToken=old%2F%2B%3D", request.call_args.args[0].full_url)
        self.assertIn("/users/user-a/case_drafts?", request.call_args.args[0].full_url)
        request.return_value = {}
        self.assertEqual(list_drafts("test-project", self.session), ([], ""))

    @patch("case_client.request_json")
    def test_foreign_and_malformed_documents_fail_closed(self, request):
        foreign = document()
        foreign["name"] = foreign["name"].replace("user-a", "user-b")
        malformed = document()
        malformed["fields"]["title"] = {"integerValue": "10"}
        for entry in (foreign, malformed, None):
            request.return_value = {"documents": [entry]}
            with self.subTest(entry=entry), self.assertRaises(FirebaseRequestError):
                list_drafts("test-project", self.session)


class CaseScreenTests(unittest.TestCase):
    def screen(self):
        return AppTest.from_string(
            "from case_ui import render_cases\n"
            "from firebase_client import Session\n"
            "render_cases('test-project', Session('user-a','e','t',float('inf')))"
        ).run()

    @patch("case_ui.save_draft")
    def test_save_failure_retries_frozen_payload(self, save):
        save.side_effect = [FirebaseRequestError(), None]
        app = self.screen()
        app.text_input(key="case_title").set_value("테스트 케이스")
        app.text_area(key="case_note").set_value("초안")
        next(b for b in app.button if b.label == "제목과 메모 저장").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(app.error)
        first = (save.call_args.args, dict(save.call_args.kwargs))
        app.button(key="case_retry").click().run()
        self.assertFalse(app.exception)
        self.assertEqual((save.call_args.args, save.call_args.kwargs), first)
        self.assertTrue(app.session_state["case_saved"])
        self.assertTrue(any("영상은 아직" in message.value for message in app.success))
        app.button(key="case_new").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.text_input(key="case_title").value, "")

    @patch("case_ui.list_drafts")
    def test_empty_list_and_failure_are_different(self, listing):
        listing.return_value = ([], "")
        app = self.screen()
        app.button(key="case_reload").click().run()
        self.assertTrue(any("초안이 없습니다" in item.value for item in app.info))
        listing.side_effect = FirebaseRequestError(403)
        app.button(key="case_reload").click().run()
        self.assertTrue(app.error)
        self.assertFalse(any("초안이 없습니다" in item.value for item in app.info))

    @patch("case_ui.list_drafts")
    def test_list_paging_and_title_search(self, listing):
        one = CaseDraft(DRAFT_ID, "폐 영상", "메모", "2026-11-12T00:00:00Z")
        two = CaseDraft("b" * 32, "테스트", "다음 메모", "2026-11-12T00:00:00Z")
        listing.side_effect = [([one], "next"), ([two], "")]
        app = self.screen()
        app.button(key="case_reload").click().run()
        next(b for b in app.button if b.label == "다음 초안 불러오기").click().run()
        self.assertEqual(len(app.expander), 2)
        app.text_input(key="case_search").set_value("테스트").run()
        self.assertEqual(len(app.expander), 1)
        self.assertIn("테스트", app.expander[0].label)

    @patch("case_ui.list_drafts", return_value=([], ""))
    def test_session_rejection_clears_all_user_state(self, listing):
        env = {"PACS_FIREBASE_API_KEY": "test-key", "PACS_FIREBASE_PROJECT_ID": "test-project"}
        for action in ("logout", "expired", "rejected"):
            with self.subTest(action=action), patch.dict(os.environ, env):
                app = AppTest.from_file("app.py")
                app.session_state["pacs_session"] = Session("user-a", "e", "t", float("inf"))
                app.run()
                app.session_state["case_note"] = "private note"
                if action == "logout":
                    next(b for b in app.button if b.label == "로그아웃").click().run()
                elif action == "expired":
                    app.session_state["pacs_session"] = Session("user-a", "e", "t", 0)
                    app.run()
                else:
                    listing.side_effect = FirebaseRequestError(401)
                    app.button(key="case_reload").click().run()
                    listing.side_effect = None
                self.assertFalse(app.exception)
                self.assertNotIn("case_note", app.session_state)
                self.assertNotIn("pacs_session", app.session_state)


if __name__ == "__main__":
    unittest.main()
