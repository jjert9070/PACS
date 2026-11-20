"""B: read supported DICOM originals and hand display images and metadata to A and C.

Scope of this stage (Issue #23). Anything else is rejected with a reason; nothing
is silently converted:

- DICOM Part 10 files (128-byte preamble + "DICM") up to 20 MB, as for PNG/JPEG.
- Transfer syntax: Implicit or Explicit VR Little Endian (uncompressed) only.
- SOP Class: CT, MR, CR, DX (for presentation) and Secondary Capture image storage.
- One frame, one sample per pixel, MONOCHROME1 or MONOCHROME2, 8 or 16 bits allocated.

The original bytes are never modified: the SHA-256 of the input is the source ID,
and the stored pixel values are kept in their original dtype (read-only). The
8-bit image given to the viewer is a *display* image made by an explicit, recorded
mapping (DICOM VOI LUT LINEAR window, or min–max when the file has no window).
Statistics on that image are display values, not CT HU or other physical units.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from io import BytesIO
import math
import re

import numpy as np
from PIL import Image
from pydicom import __version__ as pydicom_version
from pydicom import dcmread
from pydicom.errors import InvalidDicomError
from pydicom.uid import (
    CTImageStorage,
    ComputedRadiographyImageStorage,
    DigitalXRayImageStorageForPresentation,
    ExplicitVRLittleEndian,
    ImplicitVRLittleEndian,
    MRImageStorage,
    SecondaryCaptureImageStorage,
)

from viewer import MAX_FILE_BYTES, MAX_PIXELS, ViewerInputError, ViewerSource

DISPLAY_MAPPING_VERSION = "1.0.0"
SUPPORTED_TRANSFER_SYNTAXES = {
    str(ImplicitVRLittleEndian): "Implicit VR Little Endian",
    str(ExplicitVRLittleEndian): "Explicit VR Little Endian",
}
SUPPORTED_SOP_CLASSES = {
    str(CTImageStorage): "CT Image Storage",
    str(MRImageStorage): "MR Image Storage",
    str(ComputedRadiographyImageStorage): "Computed Radiography Image Storage",
    str(DigitalXRayImageStorageForPresentation): "Digital X-Ray Image Storage - For Presentation",
    str(SecondaryCaptureImageStorage): "Secondary Capture Image Storage",
}
_UID = re.compile(r"(?:0|[1-9][0-9]*)(?:[.](?:0|[1-9][0-9]*))+")


class DicomReadError(ViewerInputError):
    """Why a file was not accepted. Messages never contain patient identifiers.

    Subclassing the viewer's input error lets A's existing error path show it as a
    failed input (never as an image); it is still a ValueError for C's importer.
    """

    code = "invalid"

    def __init__(self, message: str):
        super().__init__(message)


class DicomCorruptError(DicomReadError):
    code = "corrupt"


class DicomMissingTagError(DicomReadError):
    code = "missing_tag"


class DicomInvalidValueError(DicomReadError):
    code = "invalid_value"


class DicomUnsupportedError(DicomReadError):
    code = "unsupported"


@dataclass(frozen=True)
class DisplayMapping:
    """How stored values became 0..255 display values (recorded with the import)."""

    method: str            # "dicom_window" | "min_max"
    center: float
    width: float
    inverted: bool         # MONOCHROME1
    version: str = DISPLAY_MAPPING_VERSION


@dataclass(frozen=True)
class DicomImport:
    """Everything A (display) and C (identification and storage) need from one file."""

    source_id: str                 # SHA-256 of the original bytes
    file_name: str
    byte_count: int
    transfer_syntax_uid: str
    sop_class_uid: str
    sop_uid: str
    study_uid: str
    series_uid: str
    patient_issuer: str
    patient_id: str
    modality: str
    body_region: str
    study_date: str                # YYYY-MM-DD or "" when absent/invalid
    frame_count: int
    rows: int
    columns: int
    bits_allocated: int
    bits_stored: int
    pixel_representation: int
    photometric: str
    rescale_slope: float
    rescale_intercept: float
    display: DisplayMapping
    stored_pixels: np.ndarray = field(repr=False, compare=False)   # original dtype, read-only
    display_image: Image.Image = field(repr=False, compare=False)  # RGB 8-bit for the viewer
    original_bytes: bytes = field(repr=False, compare=False)

    def provenance(self) -> dict:
        """JSON-ready record of the reading and display conversion (no patient IDs)."""
        return {
            "source_id": self.source_id,
            "format": "DICOM",
            "transfer_syntax_uid": self.transfer_syntax_uid,
            "sop_class_uid": self.sop_class_uid,
            "frame_count": self.frame_count,
            "pixel": {"rows": self.rows, "columns": self.columns, "bits_allocated": self.bits_allocated,
                      "bits_stored": self.bits_stored, "pixel_representation": self.pixel_representation,
                      "photometric": self.photometric, "rescale_slope": self.rescale_slope,
                      "rescale_intercept": self.rescale_intercept},
            "display_mapping": {"method": self.display.method, "center": self.display.center,
                                "width": self.display.width, "inverted": self.display.inverted,
                                "version": self.display.version},
            "reader": {"pydicom": pydicom_version},
        }


def _text(ds, keyword: str, *, required: bool) -> str:
    value = ds.get(keyword)
    text = "" if value is None else str(value).strip()
    if required and not text:
        raise DicomMissingTagError(f"필수 정보가 없습니다: {keyword}")
    if any(ord(char) < 32 for char in text) or len(text) > 128:
        raise DicomInvalidValueError(f"{keyword} 값에 제어문자가 있거나 128자를 넘습니다.")
    return text


def _uid(ds, keyword: str) -> str:
    value = _text(ds, keyword, required=True)
    if len(value) > 64 or not _UID.fullmatch(value):
        raise DicomInvalidValueError(f"{keyword}가 DICOM UID 형식(PS3.5 9.1)이 아닙니다.")
    return value


def _integer(ds, keyword: str) -> int:
    value = ds.get(keyword)
    if value is None or str(value).strip() == "":
        raise DicomMissingTagError(f"필수 정보가 없습니다: {keyword}")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise DicomInvalidValueError(f"{keyword}가 정수가 아닙니다.") from None


def _number(ds, keyword: str, default: float) -> float:
    value = ds.get(keyword)
    if value is None or str(value).strip() == "":
        return default
    if isinstance(value, (list, tuple)) or getattr(value, "VM", 1) > 1:
        value = list(value)[0]          # multi-valued window: use the first (recorded)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise DicomInvalidValueError(f"{keyword}가 숫자가 아닙니다.") from None
    if not math.isfinite(number):
        raise DicomInvalidValueError(f"{keyword}가 유한한 숫자가 아닙니다.")
    return number


def _date(ds) -> str:
    value = str(ds.get("StudyDate", "") or "").strip()
    if re.fullmatch(r"\d{8}", value):
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return ""


def display_values(modality_values: np.ndarray, center: float | None, width: float | None,
                   inverted: bool) -> tuple[np.ndarray, DisplayMapping]:
    """Map modality values to uint8 for display only.

    With a window (width ≥ 1) this is the DICOM VOI LUT "LINEAR" function
    (PS3.3 C.11.2.1.2.1); otherwise the full value range is stretched (min–max).
    MONOCHROME1 is inverted after mapping so that higher values look darker.
    """
    values = modality_values.astype(np.float64)
    if center is not None and width is not None and width >= 1:
        low = center - 0.5 - (width - 1) / 2
        high = center - 0.5 + (width - 1) / 2
        scaled = ((values - (center - 0.5)) / (width - 1) + 0.5) * 255.0
        mapped = np.where(values <= low, 0.0, np.where(values > high, 255.0, scaled))
        mapping = DisplayMapping("dicom_window", float(center), float(width), inverted)
    else:
        lowest, highest = float(values.min()), float(values.max())
        span = highest - lowest
        mapped = np.zeros_like(values) if span == 0 else (values - lowest) / span * 255.0
        mapping = DisplayMapping("min_max", (lowest + highest) / 2, span, inverted)
    result = np.clip(np.rint(mapped), 0, 255).astype(np.uint8)
    if inverted:
        result = 255 - result
    return result, mapping


def read_dicom(name: str, data: bytes) -> DicomImport:
    """Validate one DICOM file and return its identifiers, stored pixels and display image."""
    if not isinstance(data, bytes):
        raise TypeError("DICOM 원본은 bytes여야 합니다.")
    if not data:
        raise DicomCorruptError("비어 있는 파일입니다.")
    if len(data) > MAX_FILE_BYTES:
        raise DicomUnsupportedError("파일 크기가 20 MB를 초과했습니다. 이번 단계의 지원 범위가 아닙니다.")
    if len(data) < 132 or data[128:132] != b"DICM":
        raise DicomCorruptError("DICOM Part 10 파일 표지(128바이트 머리말 뒤 'DICM')가 없습니다. "
                                "손상됐거나 DICOM 파일이 아닙니다.")
    digest_before = sha256(data).hexdigest()
    try:
        ds = dcmread(BytesIO(data), force=False)
    except (InvalidDicomError, EOFError, ValueError, KeyError, OSError, TypeError) as error:
        raise DicomCorruptError("DICOM 구조를 읽지 못했습니다. 파일이 손상됐는지 확인하세요.") from error

    meta = getattr(ds, "file_meta", None)
    if not meta:
        raise DicomCorruptError("DICOM File Meta 정보를 읽지 못했습니다. 파일이 손상됐는지 확인하세요.")
    transfer_syntax = str(getattr(meta, "TransferSyntaxUID", "") or "").strip()
    if not transfer_syntax:
        raise DicomMissingTagError("필수 정보가 없습니다: TransferSyntaxUID (File Meta)")
    if transfer_syntax not in SUPPORTED_TRANSFER_SYNTAXES:
        raise DicomUnsupportedError(f"지원하지 않는 전송 구문입니다: {transfer_syntax}. "
                                    "이번 단계는 비압축 Little Endian만 지원합니다.")

    sop_class = _uid(ds, "SOPClassUID")
    if sop_class not in SUPPORTED_SOP_CLASSES:
        raise DicomUnsupportedError(f"지원하지 않는 SOP Class입니다: {sop_class}. "
                                    "이번 단계는 CT, MR, CR, DX, Secondary Capture 영상만 지원합니다.")
    media_class = str(getattr(meta, "MediaStorageSOPClassUID", "") or "").strip()
    if media_class and media_class != sop_class:
        raise DicomInvalidValueError("File Meta와 데이터셋의 SOP Class UID가 다릅니다.")
    identifiers = {keyword: _uid(ds, keyword)
                   for keyword in ("StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID")}
    if len({sop_class, *identifiers.values()}) != 4:
        raise DicomInvalidValueError("검사, 시리즈, 객체와 SOP Class UID는 서로 달라야 합니다.")
    patient_id = _text(ds, "PatientID", required=True)
    issuer = _text(ds, "IssuerOfPatientID", required=True)

    frames = _integer(ds, "NumberOfFrames") if "NumberOfFrames" in ds else 1
    if frames != 1:
        raise DicomUnsupportedError(f"프레임이 {frames}개인 영상입니다. 이번 단계는 단일 프레임만 지원합니다.")
    for keyword in ("Rows", "Columns", "BitsAllocated", "BitsStored", "PixelRepresentation",
                    "SamplesPerPixel", "PhotometricInterpretation", "PixelData"):
        if keyword not in ds:
            raise DicomMissingTagError(f"필수 정보가 없습니다: {keyword}")
    samples = _integer(ds, "SamplesPerPixel")
    photometric = _text(ds, "PhotometricInterpretation", required=True)
    if samples != 1 or photometric not in ("MONOCHROME1", "MONOCHROME2"):
        raise DicomUnsupportedError(f"흑백 단일 채널 영상만 지원합니다 (현재 {photometric}, "
                                    f"채널 {samples}개). 컬러 DICOM은 이번 단계 범위가 아닙니다.")
    rows, columns = _integer(ds, "Rows"), _integer(ds, "Columns")
    allocated, stored = _integer(ds, "BitsAllocated"), _integer(ds, "BitsStored")
    representation = _integer(ds, "PixelRepresentation")
    if allocated not in (8, 16) or not 1 <= stored <= allocated or representation not in (0, 1):
        raise DicomUnsupportedError(f"지원하지 않는 픽셀 형식입니다 (할당 {allocated}비트, 저장 {stored}비트, "
                                    f"부호 {representation}). 8 또는 16비트 정수만 지원합니다.")
    if rows < 1 or columns < 1 or rows * columns > MAX_PIXELS:
        raise DicomUnsupportedError("영상 크기는 1픽셀 이상, 2,000만 픽셀 이하여야 합니다.")
    expected = rows * columns * allocated // 8
    pixel_bytes = ds.PixelData
    if not isinstance(pixel_bytes, (bytes, bytearray)) or len(pixel_bytes) < expected:
        raise DicomCorruptError(f"픽셀 데이터가 부족합니다 (필요 {expected}바이트). 파일이 잘렸는지 확인하세요.")
    try:
        stored_pixels = np.array(ds.pixel_array, copy=True)
    except Exception as error:  # pydicom raises several types for broken pixel data
        raise DicomCorruptError("픽셀 데이터를 해석하지 못했습니다.") from error
    if stored_pixels.shape != (rows, columns):
        raise DicomCorruptError("픽셀 배열 크기가 Rows/Columns와 다릅니다.")
    stored_pixels.setflags(write=False)

    slope = _number(ds, "RescaleSlope", 1.0)
    intercept = _number(ds, "RescaleIntercept", 0.0)
    if slope == 0:
        raise DicomInvalidValueError("RescaleSlope가 0입니다.")
    center = _number(ds, "WindowCenter", math.nan) if "WindowCenter" in ds else None
    width = _number(ds, "WindowWidth", math.nan) if "WindowWidth" in ds else None
    modality_values = stored_pixels.astype(np.float64) * slope + intercept
    display, mapping = display_values(modality_values, center, width, photometric == "MONOCHROME1")

    if sha256(data).hexdigest() != digest_before:   # reading must never alter the original
        raise DicomCorruptError("읽는 중 원본 바이트가 바뀌었습니다.")
    return DicomImport(
        source_id=digest_before, file_name=str(name), byte_count=len(data),
        transfer_syntax_uid=transfer_syntax, sop_class_uid=sop_class,
        sop_uid=identifiers["SOPInstanceUID"], study_uid=identifiers["StudyInstanceUID"],
        series_uid=identifiers["SeriesInstanceUID"], patient_issuer=issuer, patient_id=patient_id,
        modality=_text(ds, "Modality", required=False), body_region=_text(ds, "BodyPartExamined", required=False),
        study_date=_date(ds), frame_count=frames, rows=rows, columns=columns,
        bits_allocated=allocated, bits_stored=stored, pixel_representation=representation,
        photometric=photometric, rescale_slope=slope, rescale_intercept=intercept, display=mapping,
        stored_pixels=stored_pixels, display_image=Image.fromarray(np.stack([display] * 3, axis=-1)),
        original_bytes=data,
    )


def to_viewer_source(imported: DicomImport) -> ViewerSource:
    """The viewer/processing contract: full-size RGB display image + untouched original bytes."""
    return ViewerSource(imported.source_id, imported.file_name, "DICOM", imported.byte_count,
                        imported.display_image.copy(), original_bytes=imported.original_bytes)


def load_dicom_source(name: str, data: bytes) -> ViewerSource:
    """Drop-in counterpart of viewer.load_image for .dcm files (A wires the branch)."""
    return to_viewer_source(read_dicom(name, data))


def storage_metadata(data: bytes):
    """Decoder for C's register_dicom_original: original bytes -> C's DicomMetadata.

    C's modules live on feature/C-patient-study-storage; they are imported lazily so
    this branch works on its own. Unsupported or broken input raises, as C expects.
    """
    from dicom_instance_store import DicomMetadata
    from patient_study_identity import PatientIdentity

    imported = read_dicom("", data)
    return DicomMetadata(PatientIdentity(imported.patient_issuer, imported.patient_id),
                         imported.study_uid, imported.series_uid, imported.sop_uid,
                         imported.sop_class_uid, imported.frame_count, imported.modality, imported.body_region)
