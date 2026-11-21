"""Deterministic synthetic DICOM samples for the B DICOM input (no patient data).

Every pixel and identifier is generated here, so the files carry no real patient
information and need no external licence. They exist to test reading, display
mapping and error handling, not to represent clinical images.

Run from the PACS root:  uv run --locked python experiments/dicom_samples.py
Files are written to samples/dicom/ together with manifest.json (SHA-256 per file).
"""

from __future__ import annotations

import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path

import numpy as np
from pydicom import __version__ as pydicom_version
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.encaps import encapsulate
from pydicom.filewriter import dcmwrite
from pydicom.uid import (
    CTImageStorage,
    ComputedRadiographyImageStorage,
    ExplicitVRLittleEndian,
    GrayscaleSoftcopyPresentationStateStorage,
    ImplicitVRLittleEndian,
    JPEGBaseline8Bit,
    MRImageStorage,
    SecondaryCaptureImageStorage,
)

ISSUER = "PACS-EDU-SYNTH"
STUDY_DATE = "20261119"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "samples" / "dicom"


def uid(label: str) -> str:
    """Stable 2.25 UID (UUID-derived form, PS3.5 B.2) from a label, so regenerated files match.

    pydicom's generate_uid(prefix=None) is random, so the 128-bit value is derived here.
    """
    return "2.25." + str(int(sha256(("pacs-edu-synthetic:" + label).encode()).hexdigest()[:32], 16))


def _base(patient_id: str, study: str, series: str, sop: str, sop_class: str,
          modality: str, body_part: str) -> Dataset:
    ds = Dataset()
    ds.SOPClassUID = sop_class
    ds.SOPInstanceUID = uid(sop)
    ds.PatientName = "Synthetic^Phantom"
    ds.PatientID = patient_id
    ds.IssuerOfPatientID = ISSUER
    ds.StudyInstanceUID = uid(study)
    ds.SeriesInstanceUID = uid(series)
    ds.StudyID = study.upper()
    ds.StudyDate = STUDY_DATE
    ds.Modality = modality
    ds.BodyPartExamined = body_part
    ds.SeriesNumber = 1
    ds.InstanceNumber = 1
    ds.ImageComments = "Synthetic phantom generated for the educational PACS; not a medical image."
    return ds


def _pixels(ds: Dataset, array: np.ndarray, *, bits_stored: int, photometric: str = "MONOCHROME2") -> Dataset:
    ds.Rows, ds.Columns = int(array.shape[0]), int(array.shape[1])
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = photometric
    ds.BitsAllocated = array.dtype.itemsize * 8
    ds.BitsStored = bits_stored
    ds.HighBit = bits_stored - 1
    ds.PixelRepresentation = 1 if array.dtype.kind == "i" else 0
    ds.PixelData = np.ascontiguousarray(array).tobytes()
    return ds


def _write(ds: Dataset, transfer_syntax: str) -> bytes:
    ds.file_meta = FileMetaDataset()
    ds.file_meta.MediaStorageSOPClassUID = ds.SOPClassUID
    ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    ds.file_meta.TransferSyntaxUID = transfer_syntax
    stream = BytesIO()
    dcmwrite(stream, ds, enforce_file_format=True)
    return stream.getvalue()


def ct_phantom_array() -> np.ndarray:
    """256×256 stored values: air 0, water 1024, bone 2024 (HU = stored − 1024)."""
    y, x = np.mgrid[0:256, 0:256]
    array = np.zeros((256, 256), dtype=np.uint16)
    array[(x - 128) ** 2 + (y - 128) ** 2 <= 100 ** 2] = 1024
    array[(x - 128) ** 2 + (y - 160) ** 2 <= 20 ** 2] = 2024
    array[(x - 90) ** 2 + (y - 100) ** 2 <= 12 ** 2] = 924   # fat, HU −100
    array[0:4, 0:4] = 0                                      # orientation marker corner stays air
    return array


def ct_phantom() -> bytes:
    ds = _base("SYN-0001", "study-ct", "series-ct", "sop-ct", CTImageStorage, "CT", "HEAD")
    _pixels(ds, ct_phantom_array(), bits_stored=12)
    ds.RescaleSlope = 1
    ds.RescaleIntercept = -1024
    ds.RescaleType = "HU"
    ds.WindowCenter = 40
    ds.WindowWidth = 400
    return _write(ds, ExplicitVRLittleEndian)


def mr_phantom_array() -> np.ndarray:
    """160 rows × 192 columns (non-square) gradient with a bright ellipse; top-left marker 4095."""
    y, x = np.mgrid[0:160, 0:192]
    array = (x * 1500 // 191 + 200).astype(np.uint16)
    array[((x - 96) / 60.0) ** 2 + ((y - 80) / 40.0) ** 2 <= 1] = 3000
    array[0:3, 0:3] = 4095
    return array


def mr_phantom() -> bytes:
    ds = _base("SYN-0001", "study-mr", "series-mr", "sop-mr", MRImageStorage, "MR", "KNEE")
    _pixels(ds, mr_phantom_array(), bits_stored=12)
    return _write(ds, ImplicitVRLittleEndian)


def cr_monochrome1_array() -> np.ndarray:
    """96 rows × 128 columns, 8-bit horizontal ramp 0..254."""
    return np.tile((np.arange(128) * 2).astype(np.uint8), (96, 1))


def cr_monochrome1() -> bytes:
    # Same patient name as SYN-0001 but a different ID: must not be merged by name.
    ds = _base("SYN-0002", "study-cr", "series-cr", "sop-cr", ComputedRadiographyImageStorage, "CR", "CHEST")
    _pixels(ds, cr_monochrome1_array(), bits_stored=8, photometric="MONOCHROME1")
    return _write(ds, ExplicitVRLittleEndian)


# ---- negative samples (reading must fail with a specific reason) ----

def compressed_jpeg() -> bytes:
    ds = _base("SYN-0001", "study-ct", "series-ct-jpeg", "sop-ct-jpeg", CTImageStorage, "CT", "HEAD")
    _pixels(ds, ct_phantom_array(), bits_stored=12)
    ds.PixelData = encapsulate([b"\xff\xd8\xff\xd9"])  # placeholder fragment; never decoded
    ds["PixelData"].VR = "OB"
    return _write(ds, JPEGBaseline8Bit)


def multiframe() -> bytes:
    ds = _base("SYN-0001", "study-ct", "series-ct-multi", "sop-ct-multi", CTImageStorage, "CT", "HEAD")
    frames = np.stack([ct_phantom_array()] * 3)
    _pixels(ds, frames[0], bits_stored=12)
    ds.NumberOfFrames = 3
    ds.PixelData = frames.tobytes()
    return _write(ds, ExplicitVRLittleEndian)


def color_rgb() -> bytes:
    ds = _base("SYN-0003", "study-sc", "series-sc", "sop-sc", SecondaryCaptureImageStorage, "OT", "")
    rgb = np.zeros((32, 32, 3), dtype=np.uint8)
    rgb[..., 0] = 200
    ds.Rows, ds.Columns = 32, 32
    ds.SamplesPerPixel = 3
    ds.PlanarConfiguration = 0
    ds.PhotometricInterpretation = "RGB"
    ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 8, 8, 7, 0
    ds.PixelData = rgb.tobytes()
    return _write(ds, ExplicitVRLittleEndian)


def without(tag_keyword: str) -> bytes:
    """CT phantom with one required attribute removed."""
    ds = _base("SYN-0001", "study-ct", "series-ct", "sop-ct-missing-" + tag_keyword, CTImageStorage, "CT", "HEAD")
    _pixels(ds, ct_phantom_array(), bits_stored=12)
    delattr(ds, tag_keyword)
    return _write(ds, ExplicitVRLittleEndian)


def presentation_state() -> bytes:
    ds = _base("SYN-0001", "study-ct", "series-pr", "sop-pr", GrayscaleSoftcopyPresentationStateStorage, "PR", "HEAD")
    return _write(ds, ExplicitVRLittleEndian)


def truncated() -> bytes:
    data = ct_phantom()
    return data[: len(data) * 6 // 10]


SAMPLES = {
    "ct_phantom.dcm": ("supported", ct_phantom,
                       "CT, 256×256, 12-bit unsigned, Rescale −1024 (HU), Window 40/400, Explicit VR LE"),
    "mr_phantom.dcm": ("supported", mr_phantom,
                       "MR, 192×160 (non-square), 12-bit unsigned, no window (min–max), Implicit VR LE"),
    "cr_monochrome1.dcm": ("supported", cr_monochrome1,
                           "CR, 128×96, 8-bit MONOCHROME1 (inverted display), other patient with same name"),
    "unsupported_jpeg_baseline.dcm": ("unsupported", compressed_jpeg, "JPEG Baseline transfer syntax"),
    "unsupported_multiframe.dcm": ("unsupported", multiframe, "3 frames"),
    "unsupported_rgb.dcm": ("unsupported", color_rgb, "Secondary Capture RGB"),
    "missing_issuer.dcm": ("missing_tag", lambda: without("IssuerOfPatientID"), "IssuerOfPatientID removed"),
    "truncated.dcm": ("corrupt", truncated, "first 60% of ct_phantom.dcm"),
}


def write_samples(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    manifest = {"generator": "experiments/dicom_samples.py", "pydicom": pydicom_version,
                "issuer_of_patient_id": ISSUER, "patient_data": "none (synthetic)", "files": {}}
    for name, (expected, build, description) in SAMPLES.items():
        data = build()
        (output / name).write_bytes(data)
        manifest["files"][name] = {"expected": expected, "description": description,
                                   "bytes": len(data), "sha256": sha256(data).hexdigest()}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8", newline="\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    manifest = write_samples(parser.parse_args().output)
    for name, entry in manifest["files"].items():
        print(f"{entry['expected']:<12} {entry['bytes']:>7} B  {name}")


if __name__ == "__main__":
    main()
