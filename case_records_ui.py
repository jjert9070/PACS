"""Owner-only case records using the explicitly enabled development file store."""

from dataclasses import replace
from uuid import uuid4

import streamlit as st

from case_records import (
    CaseConflict, ProcessingRecord, get_case, list_cases, list_versions,
    reopen_case_processing, save_case,
)
from firebase_client import FirebaseRequestError, Session
from local_storage import storage_root
from viewer import ViewerSource, reset_view
from case_processing import freeze_processing, validate_processing


def _error(error: Exception) -> None:
    if isinstance(error, FirebaseRequestError):
        if error.status == 401:
            raise error
        if error.status == 403:
            st.error("Firestore가 케이스 접근을 거절했습니다 (403). 사용 중인 프로젝트와 본인 케이스용 보안 규칙을 확인하세요.")
        else:
            st.error("케이스 요청에 실패했습니다. 연결과 본인 케이스용 Firestore 규칙을 확인하세요.")
    else:
        st.error(str(error))


def _dirty() -> bool:
    initial = st.session_state.get("case_record_initial", ("", "", ""))
    current = tuple(st.session_state.get("case_record_" + key, "") for key in ("title", "note", "provenance"))
    return (current != initial or st.session_state.get("case_record_pending") is not None
            or st.session_state.get("case_record_processing_current")
            != st.session_state.get("case_record_processing_initial"))


def _switch(project_id: str, session: Session, case_id: str | None) -> None:
    if _dirty() and not st.session_state.get("case_record_discard", False):
        st.session_state["case_record_error"] = "저장하지 않은 내용이 있습니다. 현재 내용을 저장하거나 폐기 확인을 선택하세요."
        return
    try:
        record = get_case(project_id, session, case_id) if case_id else None
        restored = reopen_case_processing(storage_root(), session, record) if record else None
        if restored:
            validate_processing(*restored)
    except (FirebaseRequestError, ValueError) as error:
        if isinstance(error, FirebaseRequestError) and error.status == 401:
            from app import clear_session
            clear_session()
        else:
            st.session_state["case_record_error"] = str(error)
        return
    values = record.values if record else {"title": "", "note": "", "provenance": ""}
    st.session_state["case_record_loaded"] = record
    for key in ("title", "note", "provenance"):
        st.session_state["case_record_" + key] = values[key]
    st.session_state["case_record_initial"] = tuple(values[key] for key in ("title", "note", "provenance"))
    processing = restored[1] if restored else None
    st.session_state["case_record_processing_initial"] = (
        replace(processing, roi=tuple(processing.roi) if processing.roi is not None else None)
        if processing is not None else None
    )
    for key in ("case_record_pending", "case_record_history", "case_record_error", "case_record_saved"):
        st.session_state.pop(key, None)
    st.session_state["case_record_discard"] = False
    # Clear even when switching between two cases with identical source bytes.
    for key in list(st.session_state):
        if key.startswith(("viewer_current_", "viewer_displayed_", "viewer_stored_",
                           "viewer_processing_", "viewer_roi_", "viewer_restore_")):
            st.session_state.pop(key, None)
    reset_view()
    if restored:
        source, processing = restored
        st.session_state["viewer_stored_source"] = source
        st.session_state["viewer_stored_owner"] = session.uid
        st.session_state["viewer_stored_result"] = processing.result if processing else None
        st.session_state["viewer_stored_processing"] = processing
        st.session_state["viewer_restore_controls"] = {
            "source_id": source.source_id,
            "algorithm_id": processing.algorithm_id if processing else "",
            "parameters": processing.parameters if processing else {},
        }
        st.session_state["viewer_restore_roi"] = {
            "source_id": source.source_id, "bounds": processing.roi if processing else None,
        }
        st.session_state["viewer_mode"] = "로컬 저장 영상"


def render_case_records(
    project_id: str, session: Session, source: ViewerSource | None,
    processing: ProcessingRecord | None = None,
    processing_error: str | None = None,
) -> None:
    # Compare applied records on a switch; ROI lists and tuples mean the same bounds.
    st.session_state["case_record_processing_current"] = (
        replace(processing, roi=tuple(processing.roi) if processing.roi is not None else None)
        if processing is not None else None
    )
    st.subheader("원본과 연결된 내 케이스")
    st.caption("원본은 개발용 PC에, 제목과 메모, 출처와 버전은 Firestore에 저장합니다. 검토 요청과 역할 배정은 이번 범위에서 제외했습니다.")
    if not st.checkbox("케이스용 개발 저장소 사용", key="case_record_enabled"):
        return
    st.caption("파일 위치: " + str(storage_root()) + " / JCloud 영구 보관은 미검증입니다.")
    st.checkbox("전환할 때 저장 전 입력과 실패한 요청을 폐기해도 됩니다", key="case_record_discard")
    st.button("새 케이스 작성", key="case_record_new", on_click=_switch, args=(project_id, session, None))
    if st.session_state.get("case_record_error"):
        st.warning(st.session_state["case_record_error"])

    if st.button("내 케이스 목록 불러오기", key="case_record_reload"):
        st.session_state.pop("case_record_items", None)
        st.session_state.pop("case_record_next", None)
        try:
            items, token = list_cases(project_id, session)
            st.session_state["case_record_items"] = items
            st.session_state["case_record_next"] = token
        except FirebaseRequestError as error:
            _error(error)
    if st.session_state.get("case_record_next") and st.button("다음 케이스 50건"):
        try:
            items, token = list_cases(project_id, session, st.session_state["case_record_next"])
            combined = st.session_state["case_record_items"] + items
            st.session_state["case_record_items"] = list({item.case_id: item for item in combined}.values())
            st.session_state["case_record_next"] = token
        except FirebaseRequestError as error:
            _error(error)
    if "case_record_items" in st.session_state:
        search = st.text_input("불러온 케이스에서 제목 또는 ID 검색", key="case_record_search").casefold()
        matches = [item for item in st.session_state["case_record_items"]
                   if search in item.values["title"].casefold() or search in item.case_id]
        if not matches:
            st.info("불러온 목록에 해당 케이스가 없습니다.")
        for item in matches:
            st.caption(f"{item.values['title']} / 원본 1개 / 초안 / v{item.values['version']} / 저장 시각(UTC): {item.saved_at}")
            st.button("열기 / " + item.case_id, key="case_record_open_" + item.case_id,
                      on_click=_switch, args=(project_id, session, item.case_id))

    loaded = st.session_state.get("case_record_loaded")
    st.caption("수정 중인 케이스: " + (loaded.case_id if loaded else "새 케이스"))
    title = st.text_input("케이스 제목", max_chars=120, key="case_record_title")
    note = st.text_area("케이스 메모", max_chars=4000, key="case_record_note")
    provenance = st.text_area("원본 출처와 사용 허용 근거", max_chars=2000, key="case_record_provenance")
    if loaded and (source is None or source.source_id != loaded.values["source_id"]):
        st.warning("수정 대상과 뷰어 원본이 다릅니다. 같은 케이스를 다시 열거나 새 케이스로 작성하세요.")
    if processing_error:
        st.error(processing_error)
    pending = st.session_state.get("case_record_pending")
    submit = st.button("원본과 케이스 저장", disabled=bool(processing_error) or source is None or pending is not None or bool(st.session_state.get("case_record_saved")), key="case_record_save")
    if submit:
        try:
            processing = freeze_processing(source, processing)
        except ValueError as error:
            st.error(str(error))
            return
        pending = dict(case_id=loaded.case_id if loaded else uuid4().hex,
                       operation_id=uuid4().hex, source=source, title=title, note=note,
                       provenance=provenance, base=loaded, processing=processing)
        st.session_state["case_record_pending"] = pending
        st.session_state.pop("case_record_saved", None)
    retry = False
    if pending:
        st.caption("입력을 고정한 저장 요청: " + pending["operation_id"])
        retry = st.button("같은 케이스 저장 요청 재시도", key="case_record_retry")
    if submit or retry:
        try:
            save_case(storage_root(), project_id, session, **pending)
        except CaseConflict as error:
            _error(error)
            st.warning("현재 입력을 복사해 두고, 폐기 확인 후 같은 케이스를 다시 열어 최신 메모와 비교하세요.")
        except (FirebaseRequestError, ValueError) as error:
            _error(error)
            st.warning("전체 저장 완료가 아닙니다. 파일이 먼저 저장됐을 수 있으며 같은 요청으로 재시도할 수 있습니다.")
        else:
            st.session_state["case_record_saved"] = pending["case_id"]
            st.session_state.pop("case_record_pending", None)
            st.session_state["case_record_initial"] = (pending["title"], pending["note"], pending["provenance"])
            st.session_state["case_record_processing_initial"] = pending["processing"]
    saved = st.session_state.get("case_record_saved")
    if saved:
        st.success("개발용 원본 파일과 Firestore 케이스를 저장했습니다. 계속 수정하려면 저장본을 다시 여세요.")
        st.button("저장본 다시 열기", on_click=_switch, args=(project_id, session, saved))
    if loaded and st.button("이 케이스의 변경 이력 불러오기"):
        try:
            st.session_state["case_record_history"] = list_versions(project_id, session, loaded.case_id)
        except FirebaseRequestError as error:
            st.session_state.pop("case_record_history", None)
            _error(error)
    for record in st.session_state.get("case_record_history", []):
        with st.expander(f"v{record.values['version']} / {record.saved_at} / {record.values['title']}"):
            st.text(record.values["note"])
            st.text("출처: " + record.values["provenance"])
            st.text("작성자 UID: " + record.values["owner_uid"])
            st.json(record.values["record_json"])
