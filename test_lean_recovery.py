"""Finite real-function observations of StudyRecoveryRules, with mock transport."""

from copy import deepcopy
from dataclasses import asdict
from itertools import product
import unittest
from unittest.mock import patch

from firebase_client import FirebaseRequestError, Session
import study_details_store as store


WRITE_STATUSES = (None, 0, 200, 400, 401, 403, 404, 409, 412, 429, 500, 503)
READ_ERRORS = (None, 400, 401, 403, 404, 409, 503)
SESSION = Session("owner", "synthetic", "token", float("inf"))
DETAILS = store.StudyDetails("study-1", "2026-11-19", "confirmed", "partial")


def document(details, version):
    collection = store._collection_for("p", SESSION, "study_details")
    values = {
        "owner_uid": SESSION.uid,
        "study_key": store._key(details.study_id),
        **asdict(details),
    }
    return {
        "name": collection + "/" + store._key(details.study_id),
        "fields": {key: {"stringValue": value} for key, value in values.items()},
        "updateTime": version,
    }


def observe(status, expected, read, *, details=DETAILS, stored=None):
    """Call actual save/get/decode; substitute only parent lookup and HTTP transport."""
    kind, value, same_payload = read
    if stored is None and kind == "found":
        current = details if same_payload else store.StudyDetails(details.study_id)
        stored = document(current, value)
    before = deepcopy(stored)
    input_before = asdict(details)
    writes = []
    reads = []

    def request(path, session, payload=None):
        assert session is SESSION
        if payload is not None:
            writes.append(deepcopy(payload))
            raise FirebaseRequestError(status)
        reads.append(path)
        if kind == "failed":
            raise FirebaseRequestError(value)
        return deepcopy(stored)

    with patch.object(store, "get_study"), patch.object(store, "_request", side_effect=request):
        try:
            store.save_study_details("p", SESSION, details, expected)
        except store.StudyDetailsConflict:
            observed = ("conflict", None)
        except FirebaseRequestError as error:
            observed = ("requestError", error.status)
        else:
            observed = ("accepted", None)

    assert len(writes) == 1, "Error recovery must not retry or overwrite the write"
    assert len(reads) == int(status in (400, 403, 409, 412))
    condition = {"exists": False} if expected is None else {"updateTime": expected}
    assert writes[0]["writes"][0]["currentDocument"] == condition
    assert stored == before and asdict(details) == input_before
    return observed


def recovery_observations():
    reads = [
        ("found", version, same)
        for version, same in product(("v1", "v2", "v3"), (False, True))
    ] + [("failed", status, False) for status in READ_ERRORS]
    for status, expected, read in product(WRITE_STATUSES, (None, "v1", "v2"), reads):
        yield status, expected, read, observe(status, expected, read)


class LeanRecoveryTests(unittest.TestCase):
    def test_error_version_and_followup_matrix(self):
        count = 0
        for status, expected, read, actual in recovery_observations():
            with self.subTest(status=status, expected=expected, read=read):
                kind, value, same = read
                if status not in (400, 403, 409, 412):
                    wanted = ("requestError", status)
                elif kind == "failed":
                    wanted = ("requestError", status if value == 404 else value)
                elif expected is None and same:
                    wanted = ("accepted", None)
                elif expected is None or value != expected:
                    wanted = ("conflict", None)
                else:
                    wanted = ("requestError", status)
                self.assertEqual(actual, wanted)
                count += 1
        self.assertEqual(count, 468)

    def test_create_retry_compares_every_detail_field(self):
        details = [
            store.StudyDetails("study-1", date, linkage, receipt)
            for date, linkage, receipt in product(
                ("", "2026-11-19"),
                ("unconfirmed", "confirmed", "conflict", "on_hold"),
                ("unknown", "receiving", "partial", "failed"),
            )
        ]
        count = 0
        for status, submitted, saved in product((400, 403, 409, 412), details, details):
            observed = observe(
                status,
                None,
                ("found", "v2", submitted == saved),
                details=submitted,
                stored=document(saved, "v2"),
            )
            self.assertEqual(observed, ("accepted" if submitted == saved else "conflict", None))
            count += 1
        self.assertEqual(count, 4096)

    def test_malformed_followup_cannot_become_success_or_conflict(self):
        mutations = (
            ("name", "untrusted/path"),
            ("updateTime", ""),
            ("updateTime", None),
            ("owner_uid", "other-owner"),
            ("study_id", "other-study"),
            ("study_key", "wrong-key"),
            ("acquired_on", "2026-02-30"),
            ("linkage_status", "invalid"),
            ("receipt_status", "complete"),
        )
        count = 0
        for status, expected, (field, value) in product(
            (400, 403, 409, 412), (None, "v1"), mutations
        ):
            bad = document(DETAILS, "v2")
            if field in ("name", "updateTime"):
                bad[field] = value
            else:
                bad["fields"][field] = {"stringValue": value}
            self.assertEqual(
                observe(status, expected, ("found", "v2", True), stored=bad),
                ("requestError", None),
            )
            count += 1
        self.assertEqual(count, 72)


if __name__ == "__main__":
    unittest.main()
