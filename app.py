import os

import streamlit as st

from firebase_client import FirebaseRequestError, check_firestore, sign_in


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
        st.session_state.pop("pacs_session", None)
        st.warning("로그인 시간이 만료됐습니다. 다시 로그인하세요.")
        session = None
    if session is None:
        show_login(api_key)
        return

    st.text(f"로그인 계정  {session.email}")
    if st.button("로그아웃"):
        st.session_state.pop("pacs_session", None)
        st.rerun()

    st.subheader("PACS 뷰어")
    original, processed = st.columns(2)
    original.info("원본 영상 영역")
    processed.info("처리 결과 영역")

    if st.button("Firestore 연결 확인"):
        try:
            check_firestore(project_id, session)
        except FirebaseRequestError as error:
            if error.status == 401:
                st.session_state.pop("pacs_session", None)
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
