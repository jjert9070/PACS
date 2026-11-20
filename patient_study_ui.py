"""Patient/study selection and registration screens, using B/C's storage services."""

from pathlib import Path
import logging

import streamlit as st

from case_records_ui import _dirty as case_dirty, _switch as switch_case
from firebase_client import FirebaseRequestError
from local_storage import storage_root
from patient_study_api import BackendUnavailable, get_api
from viewer import clear_viewer

log = logging.getLogger(__name__)
PREFIX = "patient_ui_"
LINKAGES = {"미확인": "unconfirmed", "확인됨": "confirmed", "충돌": "conflict", "보류": "on_hold"}
RECEIPTS = {"미상": "unknown", "수신 중": "receiving", "일부 수신": "partial", "수신 실패": "failed"}
STATUS = {"registered": "등록 완료", "reused": "기존 원본 재사용", "rejected": "지원 조건 거절",
          "conflict": "연결 충돌·보류", "failed": "등록 실패", "saved_not_linked": "원본 보관·연결 미확인"}
ERRORS = {"permission_denied": "접근 권한이 거절됐습니다 (403).",
          "session_expired": "로그인이 만료됐습니다. 다시 로그인하세요.",
          "not_found": "등록 정보를 찾을 수 없습니다 (404).",
          "original_unavailable": "원본 파일을 읽거나 해시를 확인하지 못했습니다.",
          "link_mismatch": "선택한 환자·검사와 원본 연결이 일치하지 않습니다.",
          "conflict": "기존 식별 정보와 충돌해 보류했습니다.",
          "identity_conflict": "기존 식별 정보와 충돌해 보류했습니다.",
          "invalid_input": "입력과 식별 근거를 확인하세요.",
          "local_storage_failure": "개발용 원본 저장소에 접근하지 못했습니다.",
          "unsupported": "현재 지원하지 않는 영상 형식입니다.",
          "corrupt": "손상된 영상 파일입니다.", "missing_tag": "필수 DICOM 정보가 없습니다.",
          "invalid_value": "DICOM 정보가 올바르지 않습니다."}


def clear_patient_state():
    for key in list(st.session_state):
        if key.startswith(PREFIX):
            del st.session_state[key]


def _end_session():
    # Widget callbacks execute before app.main's exception handlers.
    from app import clear_session
    clear_session()
    st.rerun()


def _error(error):
    code = getattr(error, "code", "")
    if isinstance(error, FirebaseRequestError):
        code = {401: "session_expired", 403: "permission_denied", 404: "not_found"}.get(error.status, "request_failed")
    elif "Conflict" in type(error).__name__:
        code = "conflict"
    elif isinstance(error, FileNotFoundError) or type(error).__name__ == "LocalStorageError":
        code = "original_unavailable"
    elif isinstance(error, ValueError) and not code:
        code = "invalid_input"
    if code == "session_expired":
        _end_session()
    log.warning("patient_study_request_failed code=%s type=%s", code or "request_failed", type(error).__name__)
    return code or "request_failed", ERRORS.get(code, "조회 또는 저장 요청에 실패했습니다. 다시 시도하세요.")


def _show_error(value):
    code, message = value
    st.error(message + " · " + code)


def _detail_values():
    return (st.session_state.get(PREFIX + "date", ""),
            st.session_state.get(PREFIX + "linkage", "미확인"),
            st.session_state.get(PREFIX + "receipt", "미상"))


def details_dirty():
    initial = st.session_state.get(PREFIX + "details_initial")
    return initial is not None and _detail_values() != initial


def allow_selection_change():
    return (not case_dirty() and not details_dirty()) or st.session_state.get("case_record_discard", False)


def _prepare_switch(project_id, session):
    if not allow_selection_change():
        st.session_state[PREFIX + "notice"] = "저장하지 않은 변경이 있습니다. 현재 내용을 저장하거나 아래 케이스 영역의 폐기 확인을 선택하세요."
        return False
    if details_dirty():
        # Explicit discard also consumes this screen's pending detail edits.
        for key, value in zip(("date", "linkage", "receipt"), st.session_state[PREFIX + "details_initial"]):
            st.session_state[PREFIX + key] = value
    # Reuse C's transition cleanup before assigning a new patient image.
    switch_case(project_id, session, None)
    clear_viewer()
    st.session_state["viewer_mode"] = "검사 선택 영상"
    for suffix in ("source", "context", "open_error", "notice"):
        st.session_state.pop(PREFIX + suffix, None)
    return True


def _choose(project_id, session, level, item):
    if st.session_state.get(PREFIX + level) == item:
        return
    if not _prepare_switch(project_id, session):
        return
    children = {"patient": ("study", "series"), "study": ("series",), "series": ()}[level]
    pages = {"patient": ("studies", "series", "objects"), "study": ("series", "objects"), "series": ("objects",)}[level]
    for child in children:
        st.session_state.pop(PREFIX + child, None)
    st.session_state.pop(PREFIX + "kind", None)
    for page in pages:
        st.session_state.pop(PREFIX + page + "_page", None)
        st.session_state.pop(PREFIX + page + "_search", None)
    for key in list(st.session_state):
        if key.startswith(PREFIX + "register_") or key in {PREFIX + "registration_results", PREFIX + "registration_error"}:
            st.session_state.pop(key, None)
    if level in {"patient", "study"}:
        for key in list(st.session_state):
            if key.startswith(PREFIX + "details_") or key in {PREFIX + x for x in ("date", "linkage", "receipt")}:
                st.session_state.pop(key, None)
    st.session_state[PREFIX + level] = item


def _set_kind(project_id, session, kind):
    if kind != st.session_state.get(PREFIX + "kind") and _prepare_switch(project_id, session):
        st.session_state[PREFIX + "kind"] = kind
        st.session_state.pop(PREFIX + "objects_page", None)
        st.session_state.pop(PREFIX + "objects_search", None)


def _load_page(name, loader, next_page=False):
    key = PREFIX + name + "_page"
    previous = st.session_state.get(key, {})
    token = previous.get("token", "") if next_page else ""
    try:
        items, token = loader(token)
    except (FirebaseRequestError, ValueError, OSError) as error:
        st.session_state[key] = {**previous, "error": _error(error)}
        return
    old = previous.get("items", []) if next_page else []
    merged = old + [item for item in items if item not in old]
    st.session_state[key] = {"items": merged, "token": token}


def _page(name, title, loader, label, choose):
    st.markdown("#### " + title)
    key = PREFIX + name + "_page"
    if key not in st.session_state:
        _load_page(name, loader)
    page = st.session_state[key]
    st.button(title + " 새로고침 / 재시도", key=PREFIX + name + "_refresh",
              on_click=_load_page, args=(name, loader))
    if page.get("error"):
        _show_error(page["error"])
        if page.get("items"):
            st.caption("이전에 불러온 목록입니다. 최신 조회는 실패했습니다.")
    if "items" not in page:
        return
    st.caption(f"현재 불러온 {len(page['items'])}건에서 검색합니다. 전체 자료 검색이 아닙니다.")
    query = st.text_input(title + " 검색", key=PREFIX + name + "_search").strip().casefold()
    matches = [(index, item) for index, item in enumerate(page["items"]) if query in label(item).casefold()]
    if not matches and not page.get("error"):
        st.info("검색 결과가 없습니다." if query else title + "이 없습니다.")
    for index, item in matches:
        st.button(label(item), key=PREFIX + name + "_choose_" + str(index), on_click=choose, args=(item,))
    if page.get("token"):
        st.button(title + " 다음 페이지", key=PREFIX + name + "_next",
                  on_click=_load_page, args=(name, loader, True))


def _load_details(api, project_id, session, study, preserve=False):
    try:
        saved = api.details.get_study_details(project_id, session, study.study_id)
        details, version = saved.details, saved.update_time
    except FirebaseRequestError as error:
        if error.status != 404:
            st.session_state[PREFIX + "details_error"] = _error(error)
            return
        details, version = api.details.StudyDetails(study.study_id), None
    st.session_state[PREFIX + "details_saved"] = (details, version)
    context = st.session_state.get(PREFIX + "context")
    if context and context["study_id"] == study.study_id and version is not None:
        st.session_state[PREFIX + "context"] = dict(context, acquired_on=details.acquired_on or "미상")
    st.session_state.pop(PREFIX + "details_error", None)
    if not preserve:
        values = (details.acquired_on,
                  next(k for k, v in LINKAGES.items() if v == details.linkage_status),
                  next(k for k, v in RECEIPTS.items() if v == details.receipt_status))
        for key, value in zip(("date", "linkage", "receipt"), values):
            st.session_state[PREFIX + key] = value
        st.session_state[PREFIX + "details_initial"] = values


def _save_details(api, project_id, session, study):
    try:
        acquired, linkage, receipt = _detail_values()
        details = api.details.StudyDetails(study.study_id, acquired, LINKAGES[linkage], RECEIPTS[receipt])
        version = st.session_state[PREFIX + "details_saved"][1]
        api.details.save_study_details(project_id, session, details, version)
    except (FirebaseRequestError, ValueError) as error:
        st.session_state[PREFIX + "details_error"] = _error(error)
        if isinstance(error, api.details.StudyDetailsConflict):
            _load_details(api, project_id, session, study, preserve=True)
            st.session_state[PREFIX + "details_error"] = ("conflict", "다른 창에서 수정됐습니다. 현재 입력을 유지했습니다. 최신 저장 내용과 비교 후 다시 저장하세요.")
        return
    st.session_state[PREFIX + "details_initial"] = _detail_values()
    _load_details(api, project_id, session, study, preserve=True)


def _details(api, project_id, session, study):
    if PREFIX + "details_saved" not in st.session_state and PREFIX + "details_error" not in st.session_state:
        _load_details(api, project_id, session, study)
    with st.expander("검사 촬영일과 연결·수신 상태"):
        if st.session_state.get(PREFIX + "details_error"):
            _show_error(st.session_state[PREFIX + "details_error"])
        st.button("검사 상세 다시 조회", key=PREFIX + "details_reload", on_click=_load_details,
                  args=(api, project_id, session, study, True))
        saved = st.session_state.get(PREFIX + "details_saved")
        if saved is None:
            return
        # Reload after an initial query failure initializes widgets on the next run.
        if PREFIX + "details_initial" not in st.session_state:
            details = saved[0]
            for key, value in (("date", details.acquired_on),
                               ("linkage", next(k for k,v in LINKAGES.items() if v == details.linkage_status)),
                               ("receipt", next(k for k,v in RECEIPTS.items() if v == details.receipt_status))):
                st.session_state[PREFIX + key] = value
            st.session_state[PREFIX + "details_initial"] = _detail_values()
        st.text_input("검사 촬영일 (YYYY-MM-DD, 미상은 빈칸)", key=PREFIX + "date")
        st.selectbox("환자 연결 상태", list(LINKAGES), key=PREFIX + "linkage")
        st.selectbox("영상 수신 상태", list(RECEIPTS), key=PREFIX + "receipt")
        st.caption("환자 연결 확인과 영상 수신 상태는 별개입니다. 수신 완료는 현재 지정하지 않습니다.")
        st.caption("최신 저장값: " + (saved[0].acquired_on or "촬영일 미상") + " / " + saved[0].linkage_status + " / " + saved[0].receipt_status)
        st.button("검사 상세 저장", key=PREFIX + "details_save", on_click=_save_details,
                  args=(api, project_id, session, study))


def _open(api, project_id, session, kind, item):
    if not _prepare_switch(project_id, session):
        return
    patient = st.session_state[PREFIX + "patient"]
    study = st.session_state[PREFIX + "study"]
    series = st.session_state[PREFIX + "series"]
    try:
        if kind == "dicom":
            opened = api.flow.open_dicom_display(project_id, session, storage_root(), study,
                                                  series.series_id, item.metadata.sop_uid)
            source = opened.source
            frame_count = opened.displayable_frame_count
            if frame_count != 1 or opened.object_frame_count != 1:
                raise ValueError("지원하지 않는 프레임 수입니다.")
            sop_uid = opened.sop_uid
            acquired = opened.imported.study_date
        else:
            opened = api.service.open_selected_original(project_id, session, storage_root(), patient,
                                                        study.study_id, series.series_id, item.image_id)
            source, frame_count, sop_uid, acquired = opened.source, 1, "", ""
    except (FirebaseRequestError, ValueError, OSError) as error:
        st.session_state[PREFIX + "open_error"] = _error(error)
        return
    saved = st.session_state.get(PREFIX + "details_saved")
    acquired = ((saved[0].acquired_on or "미상") if saved and saved[1] is not None else acquired or "미상")
    context = {"issuer": patient.issuer, "patient_id": patient.patient_id, "study_id": study.study_id,
               "series_id": series.series_id, "modality": series.modality or "미상",
               "acquired_on": acquired,
               "object_id": sop_uid or item.image_id, "frame_index": 0, "frame_count": frame_count,
               "source_id": source.source_id}
    st.session_state[PREFIX + "source"] = source
    st.session_state[PREFIX + "context"] = context


def _create_patient(api, project_id, session):
    try:
        patient = api.identity.PatientIdentity(st.session_state.get(PREFIX + "new_issuer", ""),
                                               st.session_state.get(PREFIX + "new_patient_id", ""))
        api.store.save_patient(project_id, session, patient)
    except (FirebaseRequestError, ValueError) as error:
        st.session_state[PREFIX + "create_result"] = _error(error)
        return
    st.session_state[PREFIX + "create_result"] = ("ok", "가상 환자 식별 정보를 저장했습니다. 목록에서 선택하세요.")
    st.session_state.pop(PREFIX + "patients_page", None)


def _create_study(api, project_id, session):
    try:
        study = api.identity.StudyIdentity(st.session_state.get(PREFIX + "new_study_id", ""),
                                           st.session_state[PREFIX + "patient"])
        api.store.save_study(project_id, session, study)
    except (FirebaseRequestError, ValueError) as error:
        st.session_state[PREFIX + "create_result"] = _error(error)
        return
    st.session_state[PREFIX + "create_result"] = ("ok", "검사 식별 정보를 저장했습니다. 목록에서 선택하세요.")
    st.session_state.pop(PREFIX + "studies_page", None)


def _registration(api, project_id, session, study):
    with st.expander("선택한 검사에 영상 등록"):
        st.caption("사용이 허용된 가상 자료를 등록합니다. 선택한 환자·검사·시리즈와 파일의 식별 정보가 일치해야 합니다.")
        st.text_input("등록할 시리즈 ID / DICOM Series UID", key=PREFIX + "register_series")
        st.text_input("PNG/JPEG 영상 ID", key=PREFIX + "register_image_id")
        st.text_input("PNG/JPEG 영상 종류", key=PREFIX + "register_modality")
        st.text_input("PNG/JPEG 촬영 부위", key=PREFIX + "register_body")
        files = st.file_uploader("검사에 등록할 원본", type=["png", "jpg", "jpeg", "dcm"],
                                 accept_multiple_files=True, max_upload_size=20, key=PREFIX + "register_files")
        if st.button("선택 원본 등록", key=PREFIX + "register", disabled=not files):
            register_files(api, project_id, session, study, [(f.name, f.getvalue()) for f in files])
        for kind, name, result in st.session_state.get(PREFIX + "registration_results", []):
            _registration_result(kind, name, result)
        results = st.session_state.get(PREFIX + "registration_results", [])
        if results:
            dcm_results = [r for kind, _, r in results if kind == "dicom"]
            summary = api.flow.summarize(dcm_results) if dcm_results else {"completed": 0}
            completed = summary["completed"] + sum(r.success for kind, _, r in results if kind == "sample")
            st.caption(f"파일 {len(results)}개 중 연결 완료 {completed}개 · 원본 보관만 된 항목은 완료에 포함하지 않습니다.")


def register_files(api, project_id, session, study, files):
    """Called by the actual upload screen; preserve input order and backend results."""
    results = []
    series_id = st.session_state.get(PREFIX + "register_series", "")
    for name, data in files:
        if Path(name).suffix.lower() == ".dcm":
            if api.flow is None:
                results.append(("error", name, ("unavailable", "DICOM 입력 기능에 연결할 수 없습니다.")))
                continue
            result = api.flow.register_dicom_file(project_id, session, storage_root(), study, series_id, name, data)
            kind = "dicom"
        else:
            try:
                series = api.series.Series(study.study_id, series_id,
                                           st.session_state.get(PREFIX + "register_modality", ""),
                                           st.session_state.get(PREFIX + "register_body", ""))
            except ValueError as error:
                result = api.service.RegistrationResult(False, "input", "invalid_input")
                results.append(("sample", name, result))
                continue
            result = api.service.register_sample_original(project_id, session, storage_root(), study.patient,
                         series, st.session_state.get(PREFIX + "register_image_id", ""), name, data)
            kind = "sample"
        if getattr(result, "error_code", "") == "session_expired":
            _end_session()
        results.append((kind, name, result))
    st.session_state[PREFIX + "registration_results"] = results
    for page in ("series", "objects"):
        st.session_state.pop(PREFIX + page + "_page", None)


def _registration_result(kind, name, result):
    if kind == "error":
        _show_error(result)
    elif kind == "dicom":
        text = f"{name}: {STATUS[result.status]} · stage={result.stage} · {result.message}"
        (st.success if result.completed else st.error)(text)
        if result.error_code:
            st.caption("오류 코드: " + result.error_code)
    else:
        text = f"{name}: " + ("원본 연결 완료" if result.success else "등록 미완료") + " · stage=" + result.stage
        (st.success if result.success else st.error)(text)
        if not result.success:
            st.caption(ERRORS.get(result.error_code, "원본 연결을 완료하지 못했습니다.") + " · " + result.error_code)
            if result.original_key:
                st.warning("원본은 보관됐지만 전체 등록 완료가 아닙니다. " +
                           ("미완료 목록에서 같은 영상 ID로 재시도하세요." if result.retry_image_id else "같은 파일과 식별 정보로 다시 등록하세요."))


def _retry(api, project_id, session, kind, item):
    try:
        if kind == "dicom":
            result = api.flow.retry_pending(project_id, session, storage_root(), item.sop_uid)
        else:
            api.recovery.retry_pending_link(storage_root(), project_id, session, item.image.image_id)
            result = api.service.RegistrationResult(True, "linked")
    except (FirebaseRequestError, ValueError, OSError) as error:
        st.session_state[PREFIX + "retry_error"] = _error(error)
        return
    if getattr(result, "error_code", "") == "session_expired":
        _end_session()
    st.session_state[PREFIX + "retry_result"] = (kind, "같은 원본 재시도", result)
    st.session_state.pop(PREFIX + "retry_error", None)
    st.session_state.pop(PREFIX + "pending", None)
    st.session_state.pop(PREFIX + "objects_page", None)


def _load_pending(api, project_id, session):
    try:
        samples = api.recovery.list_pending_links(storage_root(), project_id, session)
        dicoms = api.flow.list_pending(project_id, session, storage_root()) if api.flow else []
    except (FirebaseRequestError, ValueError, OSError) as error:
        st.session_state.pop(PREFIX + "pending", None)
        st.session_state[PREFIX + "retry_error"] = _error(error)
        return
    st.session_state[PREFIX + "pending"] = [("sample", item) for item in samples] + [("dicom", item) for item in dicoms]
    st.session_state.pop(PREFIX + "retry_error", None)


def _pending(api, project_id, session):
    with st.expander("미완료 원본 연결과 재시도"):
        st.caption("본인과 현재 프로젝트에 보관된 같은 원본으로 재시도합니다. 화면을 초기화해도 보관 기록은 유지됩니다.")
        st.button("미완료 목록 조회 / 새로고침", key=PREFIX + "pending_reload",
                  on_click=_load_pending, args=(api, project_id, session))
        if st.session_state.get(PREFIX + "retry_error"):
            _show_error(st.session_state[PREFIX + "retry_error"])
        if st.session_state.get(PREFIX + "retry_result"):
            _registration_result(*st.session_state[PREFIX + "retry_result"])
        items = st.session_state.get(PREFIX + "pending")
        if items == []:
            st.info("미완료 원본 연결이 없습니다.")
        for index, (kind, item) in enumerate(items or []):
            image_id = item.sop_uid if kind == "dicom" else item.image.image_id
            study_id = item.selected.study_id if kind == "dicom" else item.image.study_id
            st.text(f"{kind} / 검사 {study_id} / 영상 {image_id} / {item.state}")
            st.button("같은 원본으로 재시도", key=PREFIX + "retry_" + str(index),
                      on_click=_retry, args=(api, project_id, session, kind, item))


def render_patient_studies(project_id, session):
    st.subheader("환자와 검사")
    owner = (project_id, session.uid)
    if st.session_state.get(PREFIX + "owner") != owner:
        enabled = st.session_state.get(PREFIX + "enabled", False)
        clear_patient_state()
        st.session_state[PREFIX + "enabled"] = enabled
        st.session_state[PREFIX + "owner"] = owner
    if not st.checkbox("환자·검사 목록 사용", key=PREFIX + "enabled"):
        return
    try:
        api = get_api()
    except BackendUnavailable as error:
        st.error(str(error))
        return
    st.caption("가상 환자는 ID와 발급 주체로 구분합니다. 이름은 식별 기준으로 사용하지 않습니다.")
    if st.session_state.get(PREFIX + "notice"):
        st.warning(st.session_state[PREFIX + "notice"])
        st.button("미저장 변경 폐기 허용", key=PREFIX + "allow_discard", on_click=_allow_discard)
    with st.expander("가상 환자와 검사 생성"):
        st.text_input("환자 ID 발급 주체 / 연구 식별 주체", key=PREFIX + "new_issuer")
        st.text_input("가상 환자 ID", key=PREFIX + "new_patient_id")
        st.button("가상 환자 저장", key=PREFIX + "create_patient", on_click=_create_patient, args=(api, project_id, session))
        if st.session_state.get(PREFIX + "patient"):
            st.text_input("새 검사 ID / Study UID", key=PREFIX + "new_study_id")
            st.button("선택한 환자에 검사 저장", key=PREFIX + "create_study", on_click=_create_study, args=(api, project_id, session))
        if st.session_state.get(PREFIX + "create_result"):
            code, message = st.session_state[PREFIX + "create_result"]
            (st.success if code == "ok" else st.error)(message)
    with st.expander("환자 → 검사 → 시리즈 → 영상", expanded=True):
        _page("patients", "환자 목록", lambda token: api.store.list_patients(project_id, session, token),
              lambda p: f"환자 {p.patient_id} · 발급 주체 {p.issuer}",
              lambda item: _choose(project_id, session, "patient", item))
        patient = st.session_state.get(PREFIX + "patient")
        if patient is None:
            st.info("환자를 선택하면 검사 목록을 표시합니다.")
        else:
            st.caption(f"선택 환자: {patient.patient_id} / {patient.issuer}")
            _page("studies", "검사 목록", lambda token: api.store.list_patient_studies(project_id, session, patient, token),
                  lambda s: "검사 " + s.study_id, lambda item: _choose(project_id, session, "study", item))
        study = st.session_state.get(PREFIX + "study")
        if study is not None:
            _details(api, project_id, session, study)
            saved = st.session_state.get(PREFIX + "details_saved")
            st.caption("선택 검사: " + study.study_id + " / 촬영일: " + ((saved[0].acquired_on if saved else "") or "미상"))
            _page("series", "시리즈 목록", lambda token: api.series.list_study_series(project_id, session, study.study_id, token),
                  lambda s: f"시리즈 {s.series_id} · 영상 종류 {s.modality or '미상'} · 부위 {s.body_region or '미상'}",
                  lambda item: _choose(project_id, session, "series", item))
            _registration(api, project_id, session, study)
        series = st.session_state.get(PREFIX + "series")
        if series is not None:
            st.caption(f"선택 시리즈: {series.series_id} / 영상 종류: {series.modality or '미상'}")
            st.button("DICOM 목록", key=PREFIX + "kind_dicom", on_click=_set_kind, args=(project_id, session, "dicom"))
            st.button("PNG/JPEG 목록", key=PREFIX + "kind_sample", on_click=_set_kind, args=(project_id, session, "sample"))
            kind = st.session_state.get(PREFIX + "kind")
            if kind is None:
                st.info("DICOM 또는 PNG/JPEG 목록을 선택하면 연결 영상을 조회합니다.")
            elif kind == "dicom" and api.flow is None:
                st.error("DICOM 조회·표시 기능에 연결할 수 없습니다.")
            else:
                st.caption("현재 원본 목록: " + ("DICOM" if kind == "dicom" else "PNG/JPEG"))
                loader = (lambda token: api.dicom.list_series_instances(project_id, session, study.study_id, series.series_id, token)) if kind == "dicom" else (
                    lambda token: api.images.list_series_images(project_id, session, study.study_id, series.series_id, token))
                label = (lambda i: "DICOM 열기 · " + i.metadata.sop_uid + f" · 객체 프레임 {i.metadata.frame_count}") if kind == "dicom" else (
                    lambda i: "PNG/JPEG 열기 · " + i.image_id)
                _page("objects", "연결 영상 목록", loader, label,
                      lambda item: _open(api, project_id, session, kind, item))
        if st.session_state.get(PREFIX + "open_error"):
            _show_error(st.session_state[PREFIX + "open_error"])
            st.caption("선택한 영상은 열리지 않았습니다. 목록의 같은 영상 열기를 눌러 재시도하세요.")
        elif st.session_state.get(PREFIX + "source") is not None:
            st.markdown("[선택 영상 뷰어로 이동](#pacs-viewer)")
    _pending(api, project_id, session)


def _allow_discard():
    st.session_state["case_record_discard"] = True
    st.session_state[PREFIX + "notice"] = "미저장 변경 폐기를 허용했습니다. 전환할 대상을 다시 선택하세요."
