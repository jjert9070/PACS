from copy import deepcopy
from pathlib import Path
import time
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from firebase_client import FirebaseRequestError, Session
from local_storage import LocalStorageError
from study_image_store import OriginalSavedLinkError, StudyImage, StudyImageConflict
import study_image_store as store


class StudyImageStoreTests(unittest.TestCase):
    def setUp(self):
        self.session = Session("owner", "test", "token", time.monotonic() + 3600)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = StudyImage("study-1", "series-1", "image-1", "a" * 64 + ".png")
        self.documents = {}
        for target, replacement in (
            ("study_image_store.get_study", lambda *args: object()),
            ("study_image_store.get_series", lambda project, session, series_id: SimpleNamespace(study_id={"series-1": "study-1", "series-2": "study-2"}[series_id])),
            ("study_image_store.open_original", lambda *args: "original"),
            ("study_image_store._create", self.create),
            ("study_image_store._request", self.request),
        ):
            active = patch(target, side_effect=replacement)
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

    def test_link_again_and_open(self):
        store.link_original("p", self.session, self.root, self.image)
        store.link_original("p", self.session, self.root, self.image)
        self.assertEqual(store.get_image("p", self.session, "image-1"), self.image)
        self.assertEqual(store.open_study_original("p", self.session, self.root, "study-1", "image-1"), "original")
        self.assertEqual(len(self.documents), 1)

    def test_same_id_different_original_or_study_conflicts(self):
        store.link_original("p", self.session, self.root, self.image)
        for other in (StudyImage("study-1", "series-1", "image-1", "b" * 64 + ".png"),
                      StudyImage("study-2", "series-2", "image-1", self.image.original_key)):
            with self.assertRaises(StudyImageConflict):
                store.link_original("p", self.session, self.root, other)
        self.assertEqual(store.get_image("p", self.session, "image-1"), self.image)
        with self.assertRaises(StudyImageConflict):
            store.open_study_original("p", self.session, self.root, "study-2", "image-1")

    def test_missing_study_or_original_does_not_create_link(self):
        with patch("study_image_store.get_study", side_effect=FirebaseRequestError(404)):
            with self.assertRaises(FirebaseRequestError):
                store.link_original("p", self.session, self.root, self.image)
        with patch("study_image_store.open_original", side_effect=LocalStorageError("missing")):
            with self.assertRaises(LocalStorageError):
                store.link_original("p", self.session, self.root, self.image)
        self.assertFalse(self.documents)

    def test_denied_link_without_existing_record_stays_denied(self):
        with patch("study_image_store._create", side_effect=FirebaseRequestError(403)):
            with self.assertRaises(FirebaseRequestError) as caught:
                store.link_original("p", self.session, self.root, self.image)
        self.assertEqual(caught.exception.status, 403)

    def test_file_success_and_link_failure_are_reported_separately(self):
        with patch("study_image_store.save_original", return_value=self.image.original_key):
            with patch("study_image_store._create", side_effect=FirebaseRequestError(403)):
                with self.assertRaises(OriginalSavedLinkError) as caught:
                    store.save_and_link_original("p", self.session, self.root, "study-1", "series-1", "image-1", "a.png", b"data")
        self.assertEqual(caught.exception.original_key, self.image.original_key)
        self.assertEqual(caught.exception.__cause__.status, 403)

    def test_list_filters_each_page_and_keeps_token(self):
        store.link_original("p", self.session, self.root, self.image)
        other = StudyImage("study-2", "series-2", "image-2", "b" * 64 + ".jpg")
        store.link_original("p", self.session, self.root, other)
        with patch("study_image_store.query_page", return_value=([self.image], "next")) as query:
            self.assertEqual(store.list_study_images("p", self.session, "study-1"), ([self.image], "next"))
        self.assertEqual(query.call_args.args[2:5], ("study_images", "study_key", store._key("study-1")))

    def test_series_images_are_filtered_without_losing_pagination(self):
        with patch("study_image_store.query_page", return_value=([self.image], "next")) as query:
            self.assertEqual(store.list_series_images("p", self.session, "study-1", "series-1"), ([self.image], "next"))
        self.assertEqual(query.call_args.args[3:5], ("series_key", store._key("series-1")))
        with self.assertRaises(StudyImageConflict):
            store.list_series_images("p", self.session, "study-1", "series-2")

    def test_tampered_reference_is_rejected(self):
        store.link_original("p", self.session, self.root, self.image)
        next(iter(self.documents.values()))["fields"]["study_key"] = {"stringValue": "b" * 64}
        with self.assertRaises(FirebaseRequestError):
            store.get_image("p", self.session, "image-1")

    def test_wrong_study_series_pair_does_not_create_link(self):
        image = StudyImage("study-1", "series-2", "image-1", self.image.original_key)
        with self.assertRaises(StudyImageConflict):
            store.link_original("p", self.session, self.root, image)
        self.assertFalse(self.documents)


if __name__ == "__main__":
    unittest.main()
