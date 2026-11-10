# 교육용 웹 PACS 스타터

팀원 세 명이 같은 앱을 실행하고, 인증과 데이터베이스 연결을 확인한 뒤 PACS 뷰어를 함께 개발하기 위한 첫 작업판이다. Python 3.12 기준으로 안내한다.

## 지금 열 수 있는 화면

- Firebase 설정 안내 또는 이메일과 비밀번호 로그인
- 로그인 후 원본 영상과 처리 결과의 자리 표시
- 로그인한 계정의 확인용 Firestore 문서 읽기

화면은 Streamlit 기본 구성으로 작성했다. 웹 템플릿은 팀 회의에서 선택한다. 1차 뷰어 작업은 영상 파일 열기, 원본 표시, 확대와 초기화, 같은 영상의 처리 전후 비교 순으로 연결한다.

## 로컬에서 실행하기

PACS 폴더에서 터미널을 열고 아래 순서대로 실행한다.

1. 가상환경 생성. `python -m venv .venv`
2. 의존성 설치. Windows에서는 `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`를 실행한다. macOS와 Linux에서는 Python 실행 경로를 `./.venv/bin/python`으로 바꾼다.
3. 앱 실행. Windows에서는 `.\.venv\Scripts\python.exe -m streamlit run app.py`를 실행한다. Firebase 설정 전에도 설정 안내 화면을 확인할 수 있다.
4. 브라우저에서 주소가 자동으로 열리지 않으면 터미널의 Local URL을 열어 확인한다.

## Firebase 연결하기

1. 팀 개발용 Firebase 프로젝트를 정한다. Authentication에서 이메일과 비밀번호 로그인을 켜고, 팀 테스트용 계정을 콘솔에서 추가한다.
2. 같은 프로젝트에서 Cloud Firestore 기본 데이터베이스를 만든다. 이 저장소의 [읽기 규칙](firestore.rules)을 개발 프로젝트에 검토 후 적용한다. 기존 데이터가 있는 프로젝트라면 현재 규칙과 함께 검토한다.
3. Authentication에서 테스트 계정의 UID를 복사한다. Firestore의 `starter_checks` 컬렉션에 그 UID를 문서 ID로 사용하고 문자열 필드 `status`에 값 `"ready"`를 넣는다. 이 문서는 콘솔에서 만들며, 앱에서는 읽기만 한다.
4. 앱을 실행할 터미널에 `PACS_FIREBASE_API_KEY`와 `PACS_FIREBASE_PROJECT_ID`를 설정한다. 각각 Firebase 웹 앱 구성의 API 키와 프로젝트 ID다. PowerShell 입력 예시는 `$env:PACS_FIREBASE_API_KEY = "복사한 웹 API 키"`와 `$env:PACS_FIREBASE_PROJECT_ID = "프로젝트 ID"`다. 두 값을 설정한 같은 터미널에서 앱을 다시 실행한다.
5. 테스트 계정으로 로그인하고 **Firestore 연결 확인**을 누른다. 본인의 확인 문서를 읽었다는 메시지가 보이면 연결 점검을 마친다.

Firebase Authentication의 사용자 ID 토큰으로 Firestore를 읽는다. 기본 읽기 규칙은 로그인한 사용자의 본인 확인 문서 한 건에만 적용된다. 앱은 관리자 서비스 계정 키를 사용하지 않는다. 비밀번호, 토큰, 서비스 계정 키와 실제 환자 자료는 GitHub에 올리지 않는다. 로그인 세션은 현재 앱 접속에만 유지되며, 만료 시 다시 로그인한다.

## 첫 작업 전에 확인할 항목

- Windows에서 `.\.venv\Scripts\python.exe -m unittest discover -v`로 인증 및 Firestore 요청 단위 테스트를 실행한다. 이 테스트는 실제 클라우드에 요청하지 않는다.
- 팀 개발용 Firebase로 정상 로그인, 잘못된 비밀번호, 본인 확인 문서 조회, 다른 사용자의 문서 조회 거절과 로그아웃을 확인한다.
- GitHub 첫 커밋에는 소스와 안내, 규칙, 의존성 목록을 포함한다. 팀원마다 Issue와 작업 브랜치를 정한다.
- 뷰어, 영상처리, 케이스와 검토 기능의 첫 작업을 나눈다. 역할별 접근 권한과 실제 데이터 모델은 팀 설계에 맞춰 추가한다.

## 배포

배포 대상은 전북대학교 JCloud다. 학교의 배포 지침을 받으면 실행 환경, 환경 변수 등록과 HTTPS 접속 설정을 확인하고 이 문서에 실제 절차를 추가한다.

공식 참고 자료

- [Streamlit 실행 안내](https://docs.streamlit.io/get-started/fundamentals/main-concepts)
- [Firebase Authentication 이메일 로그인 API](https://firebase.google.com/docs/reference/rest/auth#section-sign-in-email-password)
- [Cloud Firestore REST API와 보안 규칙](https://firebase.google.com/docs/firestore/use-rest-api)
