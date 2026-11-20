import os

import streamlit as st

from firebase_client import FirebaseRequestError, check_firestore, sign_in
from viewer import clear_viewer, render_viewer
from case_ui import clear_cases, render_cases
from local_storage_ui import render_local_storage
from case_records_ui import render_case_records, _dirty as case_has_unsaved_changes
from case_processing import processing_from_state
from gaussian_ui import process_gaussian, render_gaussian_controls
from roi_ui import render_roi_controls, render_roi_statistics
from patient_study_ui import clear_patient_state, render_patient_studies


def clear_session() -> None:
    st.session_state.pop("pacs_session", None)
    clear_viewer()
    clear_cases()
    clear_patient_state()


def allow_viewer_source_change() -> bool:
    """Use C's existing dirty check and explicit discard choice for image changes."""
    return not case_has_unsaved_changes() or st.session_state.get("case_record_discard", False)


def show_login(api_key: str) -> None:
    st.subheader("로그인")
    with st.form("pacs-login", clear_on_submit=True):
        email = st.text_input("이메일")
        password = st.text_input("비밀번호", type="password")
        submitted = st.form_submit_button("로그인")

    if not submitted:
        return
    if not email.strip() or not password:
        st.warning("이메일과 비밀번호를 입력하세요.")
        return
    try:
        st.session_state["pacs_session"] = sign_in(api_key, email.strip(), password)
    except FirebaseRequestError as error:
        if error.status == 429:
            st.error("요청이 너무 많습니다. 잠시 뒤 다시 시도하세요.")
        elif error.status in {400, 401, 403}:
            st.error("로그인에 실패했습니다. 계정과 Firebase 설정을 확인하세요.")
        else:
            st.error("Firebase에 연결하지 못했습니다. 잠시 뒤 다시 시도하세요.")
        return
    st.rerun()


def main() -> None:
    st.set_page_config(page_title="PACS", layout="wide")
    st.title("교육용 PACS")
    st.caption("팀 공통 스타터")

    api_key = os.environ.get("PACS_FIREBASE_API_KEY", "").strip()
    project_id = os.environ.get("PACS_FIREBASE_PROJECT_ID", "").strip()
    if not api_key or not project_id:
        st.info("README의 Firebase 환경 변수 두 개를 설정한 뒤 앱을 다시 실행하세요.")
        return

    session = st.session_state.get("pacs_session")
    if session and session.is_expired():
        clear_session()
        st.warning("로그인 시간이 만료됐습니다. 다시 로그인하세요.")
        session = None
    if session is None:
        show_login(api_key)
        return

    st.text(f"로그인 계정  {session.email}")
    if st.button("로그아웃"):
        clear_session()
        st.rerun()

    if st.session_state.get("case_owner") != session.uid:
        clear_cases()
        clear_viewer()
        clear_patient_state()
        st.session_state["case_owner"] = session.uid
    with st.expander("환자·검사 선택과 영상 등록", expanded=True):
        try:
            render_patient_studies(project_id, session)
        except FirebaseRequestError:
            clear_session()
            st.rerun()
    stored_source = None
    if st.session_state.get("viewer_stored_owner") == session.uid:
        stored_source = st.session_state.get("viewer_stored_source")
    source = render_viewer(processor=process_gaussian,
                           processing_controls=render_gaussian_controls,
                           roi_controls=render_roi_controls,
                           roi_summary=render_roi_statistics,
                           stored_source=stored_source,
                           stored_result=st.session_state.get("viewer_stored_result") if stored_source else None,
                           processing_record=st.session_state.get("viewer_current_processing"),
                           stored_processing=st.session_state.get("viewer_stored_processing") if stored_source else None,
                           selected_source=st.session_state.get("patient_ui_source"),
                           selected_context=st.session_state.get("patient_ui_context"),
                           allow_source_change=allow_viewer_source_change)

    try:
        processing = processing_from_state(source, st.session_state)
    except (ValueError, KeyError, TypeError) as error:
        processing = None
        st.session_state["viewer_processing_contract_error"] = str(error)
    else:
        if processing is not None:
            st.session_state["viewer_displayed_processing"] = processing

    with st.expander("케이스 등록과 다시 열기, 변경 이력"):
        try:
            case_source = source if source is None or source.file_format in {"PNG", "JPEG"} else None
            if source is not None and case_source is None:
                st.info("DICOM 원본은 위 환자·검사 등록 영역에서 저장하세요. 이 케이스 저장은 PNG/JPEG용이며 DICOM 처리 결과와 ROI는 현재 세션에 유지됩니다.")
            render_case_records(
                project_id, session, case_source,
                processing=processing,
                processing_error=st.session_state.get("viewer_processing_contract_error"),
            )
        except FirebaseRequestError:
            clear_session()
            st.rerun()

    with st.expander("개발용 로컬 원본 저장과 재열기"):
        render_local_storage(session, case_source)

    with st.expander("이전 단계: 제목과 메모만 저장"):
        try:
            render_cases(project_id, session)
        except FirebaseRequestError:
            clear_session()
            st.rerun()

    if st.button("Firestore 연결 확인"):
        try:
            check_firestore(project_id, session)
        except FirebaseRequestError as error:
            if error.status == 401:
                clear_session()
                st.rerun()
            elif error.status == 403:
                st.error("Firestore 접근이 거절됐습니다. 프로젝트와 읽기 규칙을 확인하세요.")
            elif error.status == 404:
                st.warning("Firestore 데이터베이스 또는 내 확인용 문서가 없습니다.")
            else:
                st.error("Firestore에 연결하지 못했습니다. 잠시 뒤 다시 시도하세요.")
        else:
            st.success("Firestore에서 내 확인용 문서를 읽었습니다.")


if __name__ == "__main__":
    main()
