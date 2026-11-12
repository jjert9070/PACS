from copy import deepcopy
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from case_records import (
    CaseConflict, ProcessingRecord, _decode, get_case, list_cases, list_versions,
    reopen_case, save_case,
)
from firebase_client import FirebaseRequestError, Session
from viewer import load_image, sample_image_bytes


class CaseRecordTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.session = Session("owner", "e", "token", float("inf"))
        self.source = load_image("sample.png", sample_image_bytes())
        self.case_id, self.operation = "a" * 32, "b" * 32
        self.collection = "projects/p/databases/(default)/documents/users/owner/cases"
        self.documents = {}
        self.commits = []
        patcher = patch("case_records._request", side_effect=self.request)
        patcher.start()
        self.addCleanup(patcher.stop)

    def request(self, path, session, payload=None):
        if payload is None:
            if path not in self.documents:
                raise FirebaseRequestError(404)
            return deepcopy(self.documents[path])
        writes = payload["writes"]
        for write in writes:
            old = self.documents.get(write["update"]["name"])
            condition = write["currentDocument"]
            if (condition.get("exists") is False and old) or (
                "updateTime" in condition and (not old or old["updateTime"] != condition["updateTime"])
            ):
                raise FirebaseRequestError(409)
        self.commits.append(deepcopy(writes))
        for write in writes:
            document = deepcopy(write["update"])
            stamp = f"2026-11-13T00:00:0{len(self.commits)}Z"
            document["updateTime"] = stamp
            document["fields"]["saved_at"] = {"timestampValue": stamp}
            self.documents[document["name"]] = document
        return {}

    def save(self, **changes):
        kwargs = dict(root=self.root, project_id="p", session=self.session,
                      case_id=self.case_id, operation_id=self.operation,
                      source=self.source, title="테스트", note="첫 메모", provenance="직접 만든 합성 영상")
        kwargs.update(changes)
        save_case(**kwargs)
        return get_case("p", self.session, self.case_id)

    def test_save_reopen_and_same_request_retry(self):
        record = self.save()
        self.assertEqual(self.save(), record)
        self.assertEqual(len(self.commits), 1)
        restored, result = reopen_case(self.root, self.session, record)
        self.assertEqual(restored.original_bytes, self.source.original_bytes)
        self.assertEqual(restored.name, "sample.png")
        self.assertIsNone(result)
        self.assertEqual(len(self.documents), 2)

    def test_stale_update_conflicts_and_history_survives(self):
        base = self.save()
        latest = self.save(operation_id="c" * 32, note="두 번째 메모", base=base)
        with self.assertRaises(CaseConflict):
            self.save(operation_id="d" * 32, note="오래된 수정", base=base)
        self.assertEqual(get_case("p", self.session, self.case_id), latest)
        self.assertEqual(self.commits[1][0]["currentDocument"], {"updateTime": base.update_time})
        self.assertEqual(len(self.documents), 3)

    def test_processing_is_preserved_on_note_edit(self):
        processing = ProcessingRecord(self.source.source_id, "test", "1", {"value": 10},
                                      self.source.image.copy(), (0, 0, 10, 10), {"mean": 4})
        base = self.save(processing=processing)
        edited = self.save(operation_id="c" * 32, note="메모만 변경", base=base)
        self.assertEqual(base.values["record_json"], edited.values["record_json"])
        restored, result = reopen_case(self.root, self.session, edited)
        self.assertEqual(restored.source_id, processing.source_id)
        self.assertEqual(result.tobytes(), processing.result.tobytes())

    def test_invalid_source_roi_and_nonfinite_statistics_prevent_commit(self):
        for processing in (
            ProcessingRecord("0" * 64, "test", "1", {}, self.source.image),
            ProcessingRecord(self.source.source_id, "test", "1", {}, self.source.image, (-1, 0, 2, 2)),
            ProcessingRecord(self.source.source_id, "test", "1", {}, self.source.image, statistics={"x": float("nan")}),
        ):
            with self.assertRaises(ValueError):
                self.save(processing=processing)
        with self.assertRaises(ValueError):
            self.save(source=replace(self.source, source_id="0" * 64))
        self.assertFalse(self.commits)

    def test_foreign_owner_expiry_and_corrupt_original_fail_closed(self):
        record = self.save()
        for session in (replace(self.session, uid="other"), replace(self.session, expires_at=0)):
            with self.assertRaises(FirebaseRequestError):
                reopen_case(self.root, session, record)
        next(self.root.rglob("*.png")).write_bytes(b"broken")
        with self.assertRaises(ValueError):
            reopen_case(self.root, self.session, record)

    def test_file_and_cloud_failures_never_create_false_success(self):
        with patch("case_records.save_original", side_effect=ValueError("disk")):
            with self.assertRaises(ValueError):
                self.save()
        self.assertFalse(self.commits)
        with patch("case_records._request", side_effect=FirebaseRequestError(403)):
            with self.assertRaises(FirebaseRequestError):
                self.save()
        self.assertTrue(list(self.root.rglob("*.png")))
        self.assertFalse(self.documents)

    def test_list_paging_history_and_foreign_response(self):
        self.save()
        entry = self.documents[self.collection + "/" + self.case_id]
        with patch("firebase_client.request_json", return_value={"documents": [entry], "nextPageToken": "next"}) as request:
            items, token = list_cases("p", self.session, "old/+=")
            self.assertEqual(token, "next")
            self.assertEqual(items[0].case_id, self.case_id)
            self.assertIn("pageToken=old%2F%2B%3D", request.call_args.args[0].full_url)
        version = self.documents[self.collection + "/" + self.case_id + "/versions/" + self.operation]
        with patch("firebase_client.request_json", return_value={"documents": [version]}):
            self.assertEqual(list_versions("p", self.session, self.case_id)[0].values["version"], 1)
        foreign = deepcopy(entry)
        foreign["name"] = foreign["name"].replace("/owner/", "/other/")
        with self.assertRaises(FirebaseRequestError):
            _decode(foreign, self.collection)

    def screen(self):
        return AppTest.from_string(
            "from case_records_ui import render_case_records\n"
            "from firebase_client import Session\n"
            "from viewer import load_image, sample_image_bytes\n"
            "render_case_records('p', Session('owner','e','t',float('inf')), load_image('sample.png', sample_image_bytes()))"
        ).run()

    def test_ui_retry_freezes_input_and_blocks_unsaved_switch(self):
        with patch("case_records_ui.save_case", side_effect=[FirebaseRequestError(403), None]) as save:
            app = self.screen()
            app.checkbox(key="case_record_enabled").check().run()
            app.text_input(key="case_record_title").set_value("원본 시험")
            app.text_area(key="case_record_note").set_value("메모")
            app.text_area(key="case_record_provenance").set_value("합성")
            app.button(key="case_record_save").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(app.error)
            first = save.call_args.kwargs.copy()
            app.text_area(key="case_record_note").set_value("나중 입력").run()
            app.button(key="case_record_retry").click().run()
            self.assertEqual(save.call_args.kwargs, first)
            self.assertTrue(app.success)
            app.button(key="case_record_new").click().run()
            self.assertTrue(app.warning)
            self.assertEqual(app.text_area(key="case_record_note").value, "나중 입력")
            app.checkbox(key="case_record_discard").check().run()
            app.button(key="case_record_new").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.text_area(key="case_record_note").value, "")

    def test_app_reopens_saved_case_edits_note_and_displays_history(self):
        env = {"PACS_FIREBASE_API_KEY": "test", "PACS_FIREBASE_PROJECT_ID": "p"}
        with patch.dict(os.environ, env), patch("case_records_ui.storage_root", return_value=self.root):
            app = AppTest.from_file("app.py")
            app.session_state["pacs_session"] = self.session
            app.run()
            app.radio(key="viewer_mode").set_value("합성 예시").run()
            app.checkbox(key="case_record_enabled").check().run()
            app.text_input(key="case_record_title").set_value("앱 저장 확인")
            app.text_area(key="case_record_note").set_value("첫 메모")
            app.text_area(key="case_record_provenance").set_value("직접 만든 합성 예시")
            app.button(key="case_record_save").click().run()
            self.assertFalse(app.exception)
            case_id = app.session_state["case_record_saved"]
            next(b for b in app.button if b.label == "저장본 다시 열기").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.radio(key="viewer_mode").value, "로컬 저장 영상")
            self.assertEqual(app.session_state["case_record_loaded"].case_id, case_id)
            app.text_area(key="case_record_note").set_value("수정 메모")
            app.button(key="case_record_save").click().run()
            next(b for b in app.button if b.label == "저장본 다시 열기").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["case_record_loaded"].values["version"], 2)
            self.assertEqual(app.text_area(key="case_record_note").value, "수정 메모")
            versions = [doc for path, doc in self.documents.items() if "/versions/" in path]
            with patch("firebase_client.request_json", return_value={"documents": versions}):
                next(b for b in app.button if b.label == "이 케이스의 변경 이력 불러오기").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.session_state["case_record_history"]), 2)


if __name__ == "__main__":
    unittest.main()
