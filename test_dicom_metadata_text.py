"""C-only metadata normalization must not depend on B's DICOM reader."""
from dataclasses import replace
import unittest

from dicom_instance_store import DicomMetadata
from patient_study_identity import PatientIdentity
from series_store import Series


class DicomMetadataTextTests(unittest.TestCase):
    def setUp(self):
        self.metadata = DicomMetadata(PatientIdentity("test", "patient"),
                                      "1.2.3", "1.2.4", "1.2.5", "1.2.6", 1, "CT", "HEAD")

    def test_metadata_and_series_keep_the_same_trimmed_values(self):
        metadata = replace(self.metadata, modality=" CT ", body_region=" HEAD ")
        series = Series(metadata.study_uid, metadata.series_uid, metadata.modality, metadata.body_region)
        self.assertEqual((metadata.modality, metadata.body_region), ("CT", "HEAD"))
        self.assertEqual((metadata.modality, metadata.body_region), (series.modality, series.body_region))

    def test_normalization_still_rejects_invalid_optional_text(self):
        for field in ("modality", "body_region"):
            for value in (None, "X" * 129, "CT\x00HEAD"):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        replace(self.metadata, **{field: value})


if __name__ == "__main__":
    unittest.main()
