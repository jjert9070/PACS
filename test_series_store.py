from copy import deepcopy
import time
import unittest
from unittest.mock import patch

from firebase_client import FirebaseRequestError, Session
from series_store import Series, SeriesConflict
import series_store as store


class SeriesStoreTests(unittest.TestCase):
    def setUp(self):
        self.session = Session("owner", "test", "token", time.monotonic() + 3600)
        self.series = Series("study-1", "series-1", "CT", "HEAD")
        self.documents = {}
        for target, action in (("series_store.get_study", lambda *args: object()),
                               ("series_store._create", self.create),
                               ("series_store._request", self.request)):
            active = patch(target, side_effect=action)
            active.start()
            self.addCleanup(active.stop)

    def create(self, collection, session, document_id, values):
        name = collection + "/" + document_id
        if name in self.documents:
            raise FirebaseRequestError(403)
        self.documents[name] = {"name": name, "fields": {
            key: {"stringValue": value} for key, value in values.items()
        }}

    def request(self, path, session):
        if path not in self.documents:
            raise FirebaseRequestError(404)
        return deepcopy(self.documents[path])

    def test_register_lookup_and_repeat(self):
        store.save_series("p", self.session, self.series)
        store.save_series("p", self.session, self.series)
        self.assertEqual(store.get_series("p", self.session, "series-1"), self.series)
        self.assertEqual(len(self.documents), 1)

    def test_existing_series_cannot_be_moved_or_replaced(self):
        store.save_series("p", self.session, self.series)
        for other in (Series("study-2", "series-1", "CT", "HEAD"),
                      Series("study-1", "series-1", "MR", "HEAD")):
            with self.assertRaises(SeriesConflict):
                store.save_series("p", self.session, other)
        self.assertEqual(store.get_series("p", self.session, "series-1"), self.series)

    def test_missing_study_does_not_create_series(self):
        with patch("series_store.get_study", side_effect=FirebaseRequestError(404)):
            with self.assertRaises(FirebaseRequestError):
                store.save_series("p", self.session, self.series)
        self.assertFalse(self.documents)

    def test_tampered_study_key_is_rejected(self):
        store.save_series("p", self.session, self.series)
        next(iter(self.documents.values()))["fields"]["study_key"] = {"stringValue": "b" * 64}
        with self.assertRaises(FirebaseRequestError):
            store.get_series("p", self.session, "series-1")

    def test_list_filters_page_and_preserves_next_token(self):
        store.save_series("p", self.session, self.series)
        store.save_series("p", self.session, Series("study-2", "series-2"))
        with patch("series_store.request_json", return_value={
            "documents": list(self.documents.values()), "nextPageToken": "next"
        }):
            self.assertEqual(store.list_study_series("p", self.session, "study-1"), ([self.series], "next"))


if __name__ == "__main__":
    unittest.main()
