"""Finite Python checks against the Lean specification; no proof of all Python inputs."""

from itertools import product
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from firebase_client import FirebaseRequestError, Session
import patient_study_ui as ui
from patient_study_identity import PatientIdentity, StudyIdentity
from study_details_store import StudyDetails, StudyDetailsConflict
import test_patient_study_integration as integration
import test_patient_study_ui as screen_contract

FIELDS = tuple(product(("", "2026-11-18"), ("unconfirmed", "confirmed"), ("unknown", "partial")))


def widget_values(fields):
    date, linkage, receipt = fields
    return (date, next(k for k, v in ui.LINKAGES.items() if v == linkage),
            next(k for k, v in ui.RECEIPTS.items() if v == receipt))


def reload_observations():
    """Yield actual _load_details results for 1,024 finite state/flag combinations."""
    for form, baseline, latest, preserve in product(FIELDS, FIELDS, FIELDS, (False, True)):
        state = dict(zip(("patient_ui_date", "patient_ui_linkage", "patient_ui_receipt"),
                         widget_values(form)))
        state["patient_ui_details_initial"] = widget_values(baseline)
        details = StudyDetails("1.2.3", *latest)
        api = NS(details=NS(get_study_details=Mock(return_value=NS(details=details, update_time="v2"))))
        with patch.object(ui, "st", NS(session_state=state)):
            ui._load_details(api, "p", NS(uid="owner"), NS(study_id="1.2.3"), preserve)
        raw = tuple(state[k] for k in ("patient_ui_date", "patient_ui_linkage", "patient_ui_receipt"))
        old = state["patient_ui_details_initial"]
        observed = (raw[0], ui.LINKAGES[raw[1]], ui.RECEIPTS[raw[2]])
        observed_baseline = (old[0], ui.LINKAGES[old[1]], ui.RECEIPTS[old[2]])
        yield form, baseline, latest, preserve, observed, observed_baseline, state["patient_ui_details_saved"][1]


class LeanCorrespondenceTests(unittest.TestCase):
    def test_reload_finite_state_correspondence(self):
        count = 0
        for form, baseline, latest, preserve, observed, observed_baseline, version in reload_observations():
            keep_draft = preserve and form != baseline
            self.assertEqual(observed, form if keep_draft else latest)
            self.assertEqual(observed_baseline, baseline if keep_draft else latest)
            self.assertEqual(version, "v2")
            count += 1
        self.assertEqual(count, 1024)

    def test_actual_C_two_writers_using_one_version(self):
        fixture = integration.PatientStudyIntegrationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        session = Session("owner", "synthetic", "token", float("inf"))
        patient = PatientIdentity("SYNTHETIC-LEAN", "patient-1")
        fixture.api.store.save_patient("p", session, patient)
        for index, (first, second) in enumerate(product(FIELDS, repeat=2)):
            with self.subTest(first=first, second=second):
                study_id = "1.2.3." + str(index)
                fixture.api.store.save_study("p", session, StudyIdentity(study_id, patient))
                fixture.api.details.save_study_details("p", session, StudyDetails(study_id))
                before = fixture.api.details.get_study_details("p", session, study_id)
                winner = StudyDetails(study_id, *first)
                fixture.api.details.save_study_details("p", session, winner, before.update_time)
                with self.assertRaises(StudyDetailsConflict):
                    fixture.api.details.save_study_details(
                        "p", session, StudyDetails(study_id, *second), before.update_time)
                self.assertEqual(fixture.api.details.get_study_details("p", session, study_id).details, winner)

    def test_repeated_failure_hide_reload_and_owner_change(self):
        fixture = screen_contract.PatientStudyScreenTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        app = fixture.selected()
        app.text_input(key="patient_ui_date").set_value("2026-11-19")
        app.selectbox(key="patient_ui_linkage").select("보류")
        app.selectbox(key="patient_ui_receipt").select("수신 실패").run()
        baseline = app.session_state["patient_ui_details_initial"]
        for _ in range(3):
            fixture.api.details.get_study_details.side_effect = FirebaseRequestError(503)
            app.button(key="patient_ui_details_reload").click().run()
            app.checkbox(key="patient_ui_enabled").uncheck().run()
            app.run()
            app.checkbox(key="patient_ui_enabled").check().run()
            fixture.api.details.get_study_details.side_effect = None
            fixture.api.details.get_study_details.return_value = NS(
                details=screen_contract.Details("1.2.3", "2026-11-20", "confirmed", "partial"),
                update_time="v2")
            app.button(key="patient_ui_details_reload").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.text_input(key="patient_ui_date").value, "2026-11-19")
            self.assertEqual(app.selectbox(key="patient_ui_linkage").value, "보류")
            self.assertEqual(app.selectbox(key="patient_ui_receipt").value, "수신 실패")
            self.assertEqual(app.session_state["patient_ui_details_initial"], baseline)
        app.checkbox(key="patient_ui_enabled").uncheck().run()
        app.session_state["pacs_session"] = Session("other", "other", "token", float("inf"))
        app.run()
        self.assertFalse(app.exception)
        self.assertNotIn("patient_ui_date", app.session_state.filtered_state)
        self.assertNotIn("patient_ui_details_initial", app.session_state.filtered_state)


if __name__ == "__main__":
    unittest.main()
