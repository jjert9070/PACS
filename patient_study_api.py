"""A's runtime bindings to the existing B/C contracts (Issue #24)."""

from importlib import import_module
from types import SimpleNamespace


class BackendUnavailable(RuntimeError):
    pass


def get_api():
    """Keep A independently importable; never substitute a fake production store."""
    modules = {
        "identity": "patient_study_identity", "store": "patient_study_store",
        "series": "series_store", "images": "study_image_store",
        "details": "study_details_store", "service": "patient_storage_service",
        "recovery": "study_link_recovery",
    }
    try:
        bindings = {key: import_module(name) for key, name in modules.items()}
    except ModuleNotFoundError as error:
        if error.name not in modules.values():
            raise
        raise BackendUnavailable("환자·검사 조회 기능에 연결할 수 없습니다. 서비스 연결 상태를 확인하세요.") from None
    try:
        bindings["dicom"] = import_module("dicom_instance_store")
        bindings["flow"] = import_module("dicom_storage_flow")
    except ModuleNotFoundError as error:
        if error.name not in {"dicom_instance_store", "dicom_storage_flow", "dicom_input", "pydicom"}:
            raise
        bindings["dicom"] = bindings["flow"] = None
    return SimpleNamespace(**bindings)
