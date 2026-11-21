import unittest

from patient_study_identity import (
    PatientIdentity,
    StudyIdentity,
    StudyPatientConflict,
    validate_study_patient,
)


class PatientStudyIdentityTests(unittest.TestCase):
    def test_same_patient_id_from_different_issuers_is_not_same_patient(self):
        first = PatientIdentity("hospital-a", "123")
        second = PatientIdentity("hospital-b", "123")
        self.assertNotEqual(first, second)
        with self.assertRaises(StudyPatientConflict):
            validate_study_patient(StudyIdentity("study-1", first), StudyIdentity("study-1", second))

    def test_same_patient_and_study_can_be_checked_again(self):
        existing = StudyIdentity("study-1", PatientIdentity("research", "patient-1"))
        requested = StudyIdentity(" study-1 ", PatientIdentity(" research ", " patient-1 "))
        self.assertIsNone(validate_study_patient(existing, requested))

    def test_different_studies_are_not_compared_as_same_study(self):
        patient = PatientIdentity("research", "patient-1")
        with self.assertRaisesRegex(ValueError, "서로 다른 검사 ID"):
            validate_study_patient(StudyIdentity("study-1", patient), StudyIdentity("study-2", patient))

    def test_missing_or_invalid_identifiers_are_rejected(self):
        for invalid in ("", "   ", "x" * 129, "patient\n2", "patient\t", "patient\x85", None, 42):
            with self.subTest(value=invalid):
                with self.assertRaises(ValueError):
                    PatientIdentity("research", invalid)
                with self.assertRaises(ValueError):
                    PatientIdentity(invalid, "patient-1")
                with self.assertRaises(ValueError):
                    StudyIdentity(invalid, PatientIdentity("research", "patient-1"))

    def test_study_requires_a_confirmed_patient(self):
        with self.assertRaisesRegex(ValueError, "환자 식별"):
            StudyIdentity("study-1", None)


if __name__ == "__main__":
    unittest.main()
