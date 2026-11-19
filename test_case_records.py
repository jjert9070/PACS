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


    def processing_fixture(self):
        from PIL import ImageFilter
        from statistics import mean, pvariance
        bounds = (210, 135, 225, 145)
        result = self.source.image.filter(ImageFilter.GaussianBlur(1))
        def stats(image):
            values = list(image.crop(bounds).convert("L").get_flattened_data())
            return {"pixel_count": len(values), "mean": mean(values),
                    "population_variance": pvariance(values)}
        return ProcessingRecord(
            self.source.source_id, "synthetic_gaussian_fixture", "test-1", {"radius": 1.0}, result,
            bounds, {"source_id": self.source.source_id, "bounds": list(bounds),
                     "coordinate_system": "exif_transposed_original_pixels_v1",
                     "brightness_method": "pillow_L_v1",
                     "calculation": {"variance_definition": "population", "ddof": 0},
                     "original": stats(self.source.image), "result": stats(result)},
        )

    def test_processing_snapshot_is_detached_and_rejects_bad_references(self):
        from case_processing import freeze_processing
        processing = self.processing_fixture()
        frozen = freeze_processing(self.source, processing)
        pixels = frozen.result.tobytes()
        processing.parameters["radius"] = 9
        processing.result.paste((255, 0, 0), (0, 0, 640, 480))
        processing.statistics["bounds"][0] = 0
        self.assertEqual(frozen.parameters, {"radius": 1.0})
        self.assertEqual(frozen.result.tobytes(), pixels)
        self.assertEqual(frozen.statistics["bounds"], list(frozen.roi))
        for invalid in (replace(frozen, source_id="0" * 64),
                        replace(frozen, statistics={}),
                        replace(frozen, parameters={"x": float("nan")})):
            with self.assertRaises(ValueError):
                freeze_processing(self.source, invalid)

    def test_processing_survives_fresh_app_session_without_algorithm_execution(self):
        from case_records import reopen_case_processing
        processing = self.processing_fixture()
        before = self.source.image.tobytes()
        env = {"PACS_FIREBASE_API_KEY": "test", "PACS_FIREBASE_PROJECT_ID": "p"}
        with patch.dict(os.environ, env), patch("case_records_ui.storage_root", return_value=self.root):
            app = AppTest.from_file("app.py").run()
            app.session_state["pacs_session"] = self.session
            app.run()
            app.radio(key="viewer_mode").set_value("합성 예시").run()
            app.session_state["viewer_current_processing"] = processing
            app.run()
            self.assertFalse(app.exception)
            self.assertTrue(any("현재 세션의 처리 기록" in c.value for c in app.caption))
            app.checkbox(key="case_record_enabled").check().run()
            app.text_input(key="case_record_title").set_value("처리 결과 복원")
            app.text_area(key="case_record_note").set_value("메모")
            app.text_area(key="case_record_provenance").set_value("직접 만든 합성 영상")
            app.button(key="case_record_save").click().run()
            self.assertFalse(app.exception)
            case_id = app.session_state["case_record_saved"]
            record = get_case("p", self.session, case_id)
            source, restored = reopen_case_processing(self.root, self.session, record)
            self.assertEqual(source.original_bytes, self.source.original_bytes)
            self.assertEqual(restored.result.tobytes(), processing.result.tobytes())
            self.assertEqual(tuple(restored.roi), processing.roi)
            self.assertEqual(restored.parameters, processing.parameters)
            self.assertEqual(restored.statistics, processing.statistics)
            self.assertEqual(self.source.image.tobytes(), before)

            fresh = AppTest.from_file("app.py")
            fresh.session_state["pacs_session"] = self.session
            fresh.run()
            fresh.checkbox(key="case_record_enabled").check().run()
            with patch("case_records_ui.list_cases", return_value=([record], "")):
                fresh.button(key="case_record_reload").click().run()
            fresh.button(key="case_record_open_" + case_id).click().run()
            self.assertFalse(fresh.exception)
            self.assertEqual(fresh.session_state["viewer_stored_processing"].statistics, processing.statistics)
            self.assertTrue(any(c.value == "저장 당시 처리 기록" for c in fresh.caption))
            self.assertFalse(any("현재 세션의 처리 기록" in c.value for c in fresh.caption))
            fresh.slider(key="viewer_zoom").set_value(2.0).run()
            self.assertEqual(fresh.session_state["viewer_displayed_processing"].statistics, processing.statistics)
            # Changing metadata does not rerun the historical algorithm.
            fresh.text_area(key="case_record_note").set_value("메모만 수정")
            fresh.button(key="case_record_save").click().run()
            self.assertFalse(fresh.exception)
            latest = get_case("p", self.session, case_id)
            self.assertEqual(latest.values["record_json"], record.values["record_json"])
            fresh.session_state["viewer_current_processing"] = replace(processing, parameters={"radius": 2.0})
            fresh.run()
            self.assertTrue(any("현재 세션의 처리 기록" in c.value for c in fresh.caption))
            self.assertFalse(any(c.value == "저장 당시 처리 기록" for c in fresh.caption))
            fresh.checkbox(key="case_record_discard").check().run()
            fresh.button(key="case_record_open_" + case_id).click().run()
            self.assertFalse(fresh.exception)
            self.assertNotIn("viewer_current_processing", fresh.session_state)
            self.assertTrue(any(c.value == "저장 당시 처리 기록" for c in fresh.caption))
            next(b for b in fresh.button if b.label == "로그아웃").click().run()
            self.assertNotIn("viewer_current_processing", fresh.session_state)
            self.assertNotIn("viewer_stored_processing", fresh.session_state)

    def test_invalid_current_record_blocks_save_and_hides_previous_result(self):
        env = {"PACS_FIREBASE_API_KEY": "test", "PACS_FIREBASE_PROJECT_ID": "p"}
        with patch.dict(os.environ, env):
            app = AppTest.from_file("app.py")
            app.session_state["pacs_session"] = self.session
            app.run()
            app.radio(key="viewer_mode").set_value("합성 예시").run()
            app.checkbox(key="case_record_enabled").check().run()
            app.session_state["viewer_current_processing"] = replace(self.processing_fixture(), source_id="0" * 64)
            app.run()
            self.assertFalse(app.exception)
            self.assertTrue(app.error)
            self.assertTrue(app.button(key="case_record_save").disabled)
            self.assertNotIn("viewer_displayed_processing", app.session_state)
            self.assertFalse(self.commits)
            app.radio(key="viewer_mode").set_value("파일 가져오기").run()
            self.assertNotIn("viewer_current_processing", app.session_state)

    def test_retry_keeps_processing_snapshot_when_caller_mutates_result(self):
        processing = self.processing_fixture()
        script = ("from case_records_ui import render_case_records\n"
                  "from firebase_client import Session\n"
                  "from viewer import load_image, sample_image_bytes\n"
                  "import streamlit as st\n"
                  "render_case_records('p',Session('owner','e','t',float('inf')),"
                  "load_image('sample.png',sample_image_bytes()),st.session_state['fixture'])")
        with patch("case_records_ui.save_case", side_effect=[FirebaseRequestError(403), None]) as save:
            app = AppTest.from_string(script)
            app.session_state["fixture"] = processing
            app.run()
            app.checkbox(key="case_record_enabled").check().run()
            app.text_input(key="case_record_title").set_value("재시도")
            app.text_area(key="case_record_provenance").set_value("합성")
            app.button(key="case_record_save").click().run()
            submitted = save.call_args.kwargs["processing"]
            pixels = submitted.result.tobytes()
            processing.parameters["radius"] = 20
            processing.result.paste((0, 0, 0), (0, 0, 640, 480))
            app.button(key="case_record_retry").click().run()
            self.assertFalse(app.exception)
            retried = save.call_args.kwargs["processing"]
            self.assertEqual(retried.parameters, {"radius": 1.0})
            self.assertEqual(retried.result.tobytes(), pixels)



    def reject_commit_with_403(self, path, session, payload=None):
        if payload is not None:
            raise FirebaseRequestError(403)
        return self.request(path, session)

    def test_403_stale_version_is_conflict_and_keeps_first_save(self):
        base = self.save()
        latest = self.save(operation_id="c" * 32, title="first-window", base=base)
        with patch("case_records._request", side_effect=self.reject_commit_with_403):
            with self.assertLogs("case_records", level="WARNING") as log:
                with self.assertRaisesRegex(CaseConflict, "다른 창에서 먼저 저장"):
                    self.save(operation_id="d" * 32, title="second-window", base=base)
        self.assertEqual(get_case("p", self.session, self.case_id), latest)
        self.assertEqual(len(self.commits), 2)
        self.assertEqual(len(self.documents), 3)
        output = "\n".join(log.output)
        self.assertIn("reason=stale_case_version", output)
        self.assertIn("base_version=1 latest_version=2", output)
        self.assertNotIn("first-window", output)
        self.assertNotIn("second-window", output)
        self.assertNotIn(self.session.id_token, output)

    def test_403_without_version_change_remains_permission_error(self):
        base = self.save()
        with patch("case_records._request", side_effect=self.reject_commit_with_403):
            with self.assertLogs("case_records", level="WARNING") as log:
                with self.assertRaises(FirebaseRequestError) as caught:
                    self.save(operation_id="c" * 32, base=base)
            self.assertEqual(caught.exception.status, 403)
            self.assertIn("reason=permission_denied_or_rule_rejected", "\n".join(log.output))
            with self.assertRaises(FirebaseRequestError) as caught:
                self.save(case_id="f" * 32, operation_id="d" * 32)
            self.assertEqual(caught.exception.status, 403)
        self.assertEqual(len(self.commits), 1)

    def test_403_retry_confirms_previous_operation_without_duplicate(self):
        base = self.save()
        latest = self.save(operation_id="c" * 32, base=base, note="later")
        with patch("case_records._request", side_effect=self.reject_commit_with_403):
            with self.assertLogs("case_records", level="WARNING") as log:
                self.assertEqual(self.save(), latest)
            self.assertIn("reason=previous_save_confirmed_no_duplicate", "\n".join(log.output))
            with self.assertRaises(CaseConflict):
                self.save(title="changed payload")
        self.assertEqual(len(self.commits), 2)
        self.assertEqual(len(self.documents), 3)

    def test_403_recovery_read_denied_is_not_reported_as_conflict(self):
        base = self.save()
        def denied_latest(path, session, payload=None):
            if payload is not None or path == self.collection + "/" + self.case_id:
                raise FirebaseRequestError(403)
            return self.request(path, session)
        with patch("case_records._request", side_effect=denied_latest):
            with self.assertLogs("case_records", level="WARNING") as log:
                with self.assertRaises(FirebaseRequestError) as caught:
                    self.save(operation_id="c" * 32, base=base)
        self.assertEqual(caught.exception.status, 403)
        self.assertIn("reason=latest_read_failed", "\n".join(log.output))
        self.assertEqual(len(self.commits), 1)

    def test_ui_conflict_preserves_input_and_shows_specific_guidance(self):
        with patch("case_records_ui.save_case", side_effect=CaseConflict("다른 창에서 먼저 저장했습니다.")):
            app = self.screen()
            app.checkbox(key="case_record_enabled").check().run()
            app.text_input(key="case_record_title").set_value("충돌 입력 유지")
            app.text_area(key="case_record_note").set_value("복사할 메모")
            app.text_area(key="case_record_provenance").set_value("합성 영상")
            app.button(key="case_record_save").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("다른 창에서 먼저 저장" in e.value for e in app.error))
            self.assertTrue(any("최신 메모와 비교" in w.value for w in app.warning))
            self.assertFalse(any("전체 저장 완료가 아닙니다" in w.value for w in app.warning))
            self.assertEqual(app.text_area(key="case_record_note").value, "복사할 메모")


if __name__ == "__main__":
    unittest.main()
