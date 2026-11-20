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
from datetime import date
from hashlib import sha256
from io import BytesIO
import math
import re

import numpy as np
from PIL import Image
from pydicom import __version__ as pydicom_version
from pydicom import dcmread
from pydicom.errors import InvalidDicomError
from pydicom.multival import MultiValue
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

DISPLAY_MAPPING_VERSION = "1.1.1"
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
    if isinstance(value, (list, tuple, MultiValue)):
        if not value:
            raise DicomInvalidValueError(f"{keyword}에 숫자 값이 없습니다.")
        value = value[0]          # multi-valued window: use the first (recorded)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise DicomInvalidValueError(f"{keyword}가 숫자가 아닙니다.") from None
    if not math.isfinite(number):
        raise DicomInvalidValueError(f"{keyword}가 유한한 숫자가 아닙니다.")
    return number


def _date(ds) -> str:
    value = str(ds.get("StudyDate", "") or "").strip()
    if not re.fullmatch(r"[0-9]{8}", value):
        return ""
    try:
        return date(int(value[:4]), int(value[4:6]), int(value[6:])).isoformat()
    except ValueError:
        return ""


def _validate_window(center: float | None, width: float | None) -> None:
    if ((center is None) != (width is None)
            or (center is not None and (not math.isfinite(center) or not math.isfinite(width) or width < 1))):
        raise DicomInvalidValueError("Window Center와 Width가 함께 필요하며 LINEAR 폭은 1 이상이어야 합니다.")


def _window(ds) -> tuple[float | None, float | None]:
    function = _text(ds, "VOILUTFunction", required=False) or "LINEAR"
    if function != "LINEAR":
        raise DicomUnsupportedError("지원하지 않는 VOI LUT Function입니다. 이번 단계는 LINEAR만 지원합니다.")
    center = _number(ds, "WindowCenter", math.nan) if "WindowCenter" in ds else None
    width = _number(ds, "WindowWidth", math.nan) if "WindowWidth" in ds else None
    _validate_window(center, width)
    return center, width


def display_values(modality_values: np.ndarray, center: float | None, width: float | None,
                   inverted: bool) -> tuple[np.ndarray, DisplayMapping]:
    """Apply LINEAR, or min-max only when both window settings are absent."""
    values = modality_values.astype(np.float64)
    if not values.size or not np.isfinite(values).all():
        raise DicomInvalidValueError("표시할 픽셀 값이 유한한 숫자가 아닙니다.")
    _validate_window(center, width)
    if center is not None:
        if width == 1:
            threshold = center - 0.5
            # A rounded-up boundary must not classify a value above the exact threshold as black.
            if math.fsum((center, -0.5, -threshold)) < 0:
                threshold = math.nextafter(threshold, -math.inf)
            mapped = np.where(values <= threshold, 0.0, 255.0)
        else:
            # Subtract the center before adding half a unit; c - 0.5 can round back to c.
            with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
                scaled = ((values - center) + 0.5) / (width - 1) + 0.5
                mapped = np.clip(scaled, 0.0, 1.0) * 255.0
        mapping = DisplayMapping("dicom_window", float(center), float(width), inverted)
    else:
        lowest, highest = float(values.min()), float(values.max())
        span = highest - lowest
        if not math.isfinite(span):
            raise DicomInvalidValueError("표시할 픽셀 값의 범위가 너무 큽니다.")
        mapped = np.zeros_like(values) if span == 0 else (values - lowest) / span * 255.0
        mapping = DisplayMapping("min_max", lowest + span / 2, span, inverted)
    if not np.isfinite(mapped).all():
        raise DicomInvalidValueError("표시 변환 결과가 유한한 숫자가 아닙니다.")
    result = np.clip(np.rint(mapped), 0, 255).astype(np.uint8)
    if inverted:
        result = 255 - result
    return result, mapping


def _inspect_dicom(data: bytes):
    """Validate original bytes and pixels without allocating a display image.

    Return the dataset, immutable stored pixels, modality values and source hash.
    """
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
    if _uid(meta, "MediaStorageSOPClassUID") != sop_class:
        raise DicomInvalidValueError("File Meta와 데이터셋의 SOP Class UID가 다릅니다.")
    identifiers = {keyword: _uid(ds, keyword)
                   for keyword in ("StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID")}
    if _uid(meta, "MediaStorageSOPInstanceUID") != identifiers["SOPInstanceUID"]:
        raise DicomInvalidValueError("File Meta와 데이터셋의 SOP Instance UID가 다릅니다.")
    if len({sop_class, *identifiers.values()}) != 4:
        raise DicomInvalidValueError("검사, 시리즈, 객체와 SOP Class UID는 서로 달라야 합니다.")
    _text(ds, "PatientID", required=True)
    _text(ds, "IssuerOfPatientID", required=True)

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
    with np.errstate(over="ignore", invalid="ignore"):
        modality_values = stored_pixels.astype(np.float64) * slope + intercept
    if not np.isfinite(modality_values).all():
        raise DicomInvalidValueError("Rescale 계산 결과가 유한한 숫자가 아닙니다.")
    if sha256(data).hexdigest() != digest_before:
        raise DicomCorruptError("읽는 중 원본 바이트가 바뀌었습니다.")
    return ds, stored_pixels, modality_values, digest_before


def read_dicom(name: str, data: bytes) -> DicomImport:
    """Validate one DICOM file and return its identifiers, stored pixels and display image."""
    ds, stored_pixels, modality_values, source_id = _inspect_dicom(data)
    center, width = _window(ds)
    photometric = _text(ds, "PhotometricInterpretation", required=True)
    display, mapping = display_values(modality_values, center, width, photometric == "MONOCHROME1")
    return DicomImport(
        source_id=source_id, file_name=str(name), byte_count=len(data),
        transfer_syntax_uid=str(ds.file_meta.TransferSyntaxUID), sop_class_uid=str(ds.SOPClassUID),
        sop_uid=str(ds.SOPInstanceUID), study_uid=str(ds.StudyInstanceUID),
        series_uid=str(ds.SeriesInstanceUID), patient_issuer=_text(ds, "IssuerOfPatientID", required=True),
        patient_id=_text(ds, "PatientID", required=True),
        modality=_text(ds, "Modality", required=False), body_region=_text(ds, "BodyPartExamined", required=False),
        study_date=_date(ds), frame_count=int(ds.get("NumberOfFrames", 1)), rows=int(ds.Rows), columns=int(ds.Columns),
        bits_allocated=int(ds.BitsAllocated), bits_stored=int(ds.BitsStored),
        pixel_representation=int(ds.PixelRepresentation), photometric=photometric,
        rescale_slope=_number(ds, "RescaleSlope", 1.0), rescale_intercept=_number(ds, "RescaleIntercept", 0.0),
        display=mapping, stored_pixels=stored_pixels,
        display_image=Image.fromarray(np.stack([display] * 3, axis=-1)), original_bytes=data,
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

    ds, _, _, _ = _inspect_dicom(data)
    _window(ds)
    return DicomMetadata(PatientIdentity(_text(ds, "IssuerOfPatientID", required=True),
                                        _text(ds, "PatientID", required=True)),
                         str(ds.StudyInstanceUID), str(ds.SeriesInstanceUID), str(ds.SOPInstanceUID),
                         str(ds.SOPClassUID), int(ds.get("NumberOfFrames", 1)),
                         _text(ds, "Modality", required=False), _text(ds, "BodyPartExamined", required=False))
