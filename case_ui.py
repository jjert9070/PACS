"""First C milestone: save and read private case metadata, without image storage."""

from uuid import uuid4

import streamlit as st

from case_client import MAX_NOTE, MAX_TITLE, list_drafts, save_draft, validate_draft
from firebase_client import FirebaseRequestError, Session


def clear_cases() -> None:
    for key in list(st.session_state):
        if key.startswith("case_"):
            del st.session_state[key]


def _show_error(error: FirebaseRequestError) -> None:
    if error.status == 401:
        raise error
    if error.status == 403:
        st.error("초안 접근이 거절됐습니다. 케이스 초안용 Firestore 규칙 게시 여부를 확인하세요.")
    elif error.status == 404:
        st.error("Firestore 기본 데이터베이스를 찾을 수 없습니다. 프로젝트 설정을 확인하세요.")
    elif error.status == 409:
        st.error("같은 초안 ID에 다른 내용이 있습니다. 기존 내용을 덮어쓰지 않았습니다.")
    else:
        st.error("초안 요청에 실패했습니다. 저장은 같은 요청으로 다시 시도할 수 있습니다.")


def render_cases(project_id: str, session: Session) -> None:
    if st.session_state.get("case_owner") != session.uid:
        clear_cases()
        st.session_state["case_owner"] = session.uid
    st.subheader("내 케이스 초안")
    st.info("제목과 메모만 저장합니다. 영상 파일, 처리 결과와 ROI는 저장되지 않습니다.")

    pending = st.session_state.get("case_pending")
    saved = st.session_state.get("case_saved", False)
    submitted = False
    if pending is None:
        with st.form("case_create"):
            title = st.text_input("케이스 제목", max_chars=MAX_TITLE, key="case_title")
            note = st.text_area("초안 메모", max_chars=MAX_NOTE, key="case_note")
            submitted = st.form_submit_button("제목과 메모 저장")
        if submitted:
            try:
                title, note = validate_draft(title, note)
            except ValueError as error:
                st.warning(str(error))
            else:
                pending = {"draft_id": uuid4().hex, "title": title, "note": note}
                st.session_state["case_pending"] = pending

    if pending is not None:
        st.write("저장 요청 제목: " + pending["title"])
        st.text(pending["note"])
        if not saved:
            # Freeze the submitted payload so retry uses the exact same ID and data.
            if st.button("같은 저장 요청 재시도", key="case_retry") or submitted:
                try:
                    save_draft(project_id, session, **pending)
                except FirebaseRequestError as error:
                    _show_error(error)
                else:
                    st.session_state["case_saved"] = True
                    saved = True
        if saved:
            st.success("제목과 메모를 저장했습니다. 영상은 아직 저장되지 않았습니다.")
            st.caption("초안 ID: " + pending["draft_id"])
            if st.button("새 초안 작성", key="case_new"):
                for key in ("case_pending", "case_saved", "case_title", "case_note"):
                    st.session_state.pop(key, None)
                st.rerun()

    if st.button("내 초안 목록 불러오기", key="case_reload"):
        st.session_state.pop("case_items", None)
        st.session_state.pop("case_next", None)
        try:
            items, next_token = list_drafts(project_id, session)
        except FirebaseRequestError as error:
            _show_error(error)
        else:
            st.session_state["case_items"] = items
            st.session_state["case_next"] = next_token
    if st.session_state.get("case_next") and st.button("다음 초안 불러오기"):
        try:
            items, next_token = list_drafts(project_id, session, st.session_state["case_next"])
        except FirebaseRequestError as error:
            _show_error(error)
        else:
            by_id = {item.draft_id: item for item in st.session_state["case_items"] + items}
            st.session_state["case_items"] = list(by_id.values())
            st.session_state["case_next"] = next_token
    if "case_items" in st.session_state:
        items = st.session_state["case_items"]
        if not items:
            st.info("저장된 초안이 없습니다.")
        else:
            search = st.text_input("불러온 목록에서 제목 검색", key="case_search")
            matches = [item for item in items if search.casefold() in item.title.casefold()]
            if not matches:
                st.info("불러온 목록에 일치하는 제목이 없습니다.")
            for item in matches:
                with st.expander(item.title + " / " + item.draft_id[:8]):
                    st.text(item.note)
                    st.caption("생성 시각(UTC): " + item.created_at)
                    st.caption("메타데이터 초안 / 영상 미저장 / ID: " + item.draft_id)
