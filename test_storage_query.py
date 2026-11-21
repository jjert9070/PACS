import json
from io import BytesIO
import time
import unittest
from unittest.mock import patch
from urllib.request import Request

from firebase_client import FirebaseRequestError, Session, request_json
from patient_study_store import _collection_for
from storage_query import query_page


class StorageQueryTests(unittest.TestCase):
    def setUp(self):
        self.session = Session("owner", "test", "token", time.monotonic() + 3600)
        self.collection = _collection_for("p", self.session, "series")
        self.value = "a" * 64

    def row(self, index):
        return {"document": {"name": self.collection + "/" + format(index, "064x"),
                             "fields": {"study_key": {"stringValue": self.value}}}}

    def query(self, token=""):
        return query_page("p", self.session, "series", "study_key", self.value,
                          lambda doc, collection, uid: doc["name"], token)

    def test_cursor_continues_after_last_returned_item_without_skipping_lookahead(self):
        with patch("storage_query.request_json", return_value=[self.row(i) for i in range(51)]) as request:
            rows, token = self.query()
        self.assertEqual(len(rows), 50)
        payload = json.loads(request.call_args.args[0].data)
        self.assertEqual(payload["structuredQuery"]["where"]["fieldFilter"]["value"], {"stringValue": self.value})
        with patch("storage_query.request_json", return_value=[self.row(50)]) as request:
            more, end = self.query(token)
        self.assertEqual(more, [self.row(50)["document"]["name"]])
        self.assertFalse(end)
        cursor = json.loads(request.call_args.args[0].data)["structuredQuery"]["startAt"]
        self.assertEqual(cursor["values"][0]["referenceValue"], rows[-1])
        self.assertFalse(cursor["before"])

    def test_cursor_cannot_be_reused_for_another_patient_or_collection(self):
        with patch("storage_query.request_json", return_value=[self.row(i) for i in range(51)]):
            _, token = self.query()
        with self.assertRaises(ValueError):
            query_page("p", self.session, "series", "study_key", "b" * 64, lambda *args: None, token)

    def test_wrong_filter_duplicate_or_error_rows_are_rejected(self):
        wrong = self.row(1)
        wrong["document"]["fields"]["study_key"] = {"stringValue": "b" * 64}
        for rows in ([wrong], [self.row(1), self.row(1)], [{"error": {"code": 403}}]):
            with patch("storage_query.request_json", return_value=rows):
                with self.assertRaises(FirebaseRequestError):
                    self.query()

    def test_empty_query_is_not_a_request_failure(self):
        with patch("storage_query.request_json", return_value=[{"readTime": "time"}]):
            self.assertEqual(self.query(), ([], ""))

    def test_malformed_response_is_not_treated_as_an_empty_result(self):
        for rows in ([{}], [{"document": None}], [{"document": {"name": 123, "fields": {}}}]):
            with patch("storage_query.request_json", return_value=rows):
                with self.assertRaises(FirebaseRequestError):
                    self.query()

    def test_array_response_is_only_accepted_when_explicitly_requested(self):
        request = Request("https://example.invalid")
        with patch("firebase_client.urlopen", return_value=BytesIO(b"[]")):
            with self.assertRaises(FirebaseRequestError):
                request_json(request)
        with patch("firebase_client.urlopen", return_value=BytesIO(b"[]")):
            self.assertEqual(request_json(request, array_response=True), [])


if __name__ == "__main__":
    unittest.main()
