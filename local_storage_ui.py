"""Explicit local-storage test controls; independent from Firestore case drafts."""

import streamlit as st

from firebase_client import Session
from local_storage import (
    LocalStorageError,
    list_originals,
    open_original,
    save_original,
    storage_root,
)
from viewer import ViewerInputError, ViewerSource


def _reopen(uid: str, key: str) -> None:
    try:
        source = open_original(storage_root(), uid, key)
    except (LocalStorageError, ViewerInputError) as error:
        st.session_state["viewer_local_error"] = str(error)
    else:
        st.session_state.pop("viewer_local_error", None)
        st.session_state["viewer_stored_source"] = source
        st.session_state["viewer_stored_owner"] = uid
        st.session_state.pop("viewer_stored_result", None)
        st.session_state["viewer_mode"] = "로컬 저장 영상"


def render_local_storage(session: Session, source: ViewerSource | None) -> None:
    st.caption("개발용 PC 저장입니다. Firestore 케이스와 연결되지 않으며 JCloud 영구 저장 검증도 아닙니다.")
    enabled = st.checkbox("개발용 로컬 저장소 사용", key="viewer_local_enabled")
    if not enabled:
        return
    st.caption("저장 위치: " + str(storage_root()))
    st.caption("실제 환자 자료 대신 합성 예시 또는 사용이 허용된 공개 영상으로 시험하세요.")
    if st.button("현재 원본을 로컬에 저장", disabled=source is None):
        try:
            key = save_original(storage_root(), session.uid, source.name, source.original_bytes)
        except (LocalStorageError, ViewerInputError) as error:
            st.error(str(error))
        else:
            st.success("이 PC에 원본 파일을 저장했습니다. Firestore 케이스에는 저장하지 않았습니다.")
            st.code(key, language=None)
    if st.button("로컬 저장 목록 불러오기", key="viewer_local_reload"):
        st.session_state.pop("viewer_local_files", None)
        try:
            st.session_state["viewer_local_files"] = list_originals(storage_root(), session.uid)
        except LocalStorageError as error:
            st.error(str(error))
    if "viewer_local_files" in st.session_state:
        keys = st.session_state["viewer_local_files"]
        if not keys:
            st.info("이 사용자로 저장한 로컬 원본이 없습니다.")
        else:
            selected = st.selectbox("저장한 원본 선택", keys, key="viewer_local_selected")
            st.button(
                "선택 원본을 뷰어로 다시 열기",
                on_click=_reopen,
                args=(session.uid, selected),
            )
    if st.session_state.get("viewer_local_error"):
        st.error(st.session_state["viewer_local_error"])
