# 교육용 웹 PACS 스타터

팀원 세 명이 같은 환경에서 앱을 띄우고 PACS 뷰어를 개발하기 위한 저장소다. 지금은 Firebase 이메일 로그인과 Firestore 연결을 확인할 수 있다. 로그인하면 원본 영상과 처리 결과를 나란히 보여 줄 자리가 나온다.

## 1. 내 PC에서 앱 열기

Linux의 Bash를 기준으로 안내한다. 먼저 `uv --version`으로 `uv`가 설치되어 있는지 확인한다. 설치가 필요하면 공식 안내를 따른다. [uv 설치 안내](https://docs.astral.sh/uv/getting-started/installation/)

1. PACS 폴더에서 개발 환경 준비. `uv sync --locked`
2. 앱 실행. `uv run --locked streamlit run app.py`

브라우저가 자동으로 열리지 않으면 터미널에 나온 `Local URL`을 연다. Firebase를 처음 연결하는 중이라면 **교육용 PACS** 화면에 환경 변수 설정 안내가 뜬다.

`uv`는 프로젝트의 기본 Python 버전인 3.12로 `.venv`를 준비하고, `uv.lock`에 기록된 버전으로 패키지를 설치한다. 새 터미널에서도 PACS 폴더에서 `uv run`으로 앱을 실행한다. Windows PowerShell에서 Firebase를 설정할 때는 [PowerShell에서 실행하기](#powershell에서-실행하기)를 참고한다.

## 2. 팀 개발용 Firebase 준비하기

팀 프로젝트와 테스트 계정이 이미 준비되어 있다면 웹 API 키, 프로젝트 ID, 본인 테스트 계정 정보를 받아 3번으로 넘어간다. 팀 프로젝트를 처음 준비할 때는 Firebase 콘솔에서 다음 순서로 진행한다.

1. 팀 개발용 프로젝트의 **Authentication**에서 **이메일/비밀번호** 로그인을 켜고 팀 테스트 계정을 추가한다.
2. 같은 프로젝트에서 Cloud Firestore 기본 데이터베이스 `(default)`를 만들고 [firestore.rules](firestore.rules)의 읽기 규칙을 적용한다. 기존 데이터가 있는 프로젝트라면 현재 규칙과 함께 검토한다.
3. Authentication의 사용자 목록에서 테스트 계정의 UID를 복사한다. Firestore에서 `starter_checks` 컬렉션을 열고, 복사한 UID를 문서 ID로 사용해 확인 문서를 만든다. 필드 `status`에는 문자열 값 `"ready"`를 넣는다. 테스트 계정마다 문서를 하나씩 만든다.

## 3. 환경 변수 설정하고 다시 실행하기

**두 값을 설정한 터미널에서 앱을 실행한다.** 앱이 이미 켜져 있으면 그 터미널에서 `Ctrl+C`를 눌러 먼저 종료한다.

Firebase 콘솔의 **프로젝트 설정 → 일반 → 내 앱**에서 웹 앱을 선택하고 **구성(Config)**을 연다. 웹 앱이 없으면 **프로젝트 개요**의 웹 아이콘으로 등록한다. 구성에 나온 `apiKey`와 `projectId`를 복사하고, 아래 따옴표 안의 예시 문구를 실제 값으로 바꿔 입력한다. [Firebase 콘솔에서 웹 앱 구성 찾기](https://firebase.google.com/docs/projects/api-keys), [웹 앱 등록하기](https://firebase.google.com/docs/web/setup)

1. 웹 API 키 설정. `export PACS_FIREBASE_API_KEY="여기에_웹_API_키"`
2. 프로젝트 ID 설정. `export PACS_FIREBASE_PROJECT_ID="여기에_프로젝트_ID"`
3. 앱 다시 실행. `uv run --locked streamlit run app.py`

새 터미널을 열면 두 값을 다시 `export`한 뒤 앱을 실행한다.

브라우저에서 팀 테스트 계정으로 로그인한 뒤 **Firestore 연결 확인**을 누른다. “Firestore에서 내 확인용 문서를 읽었습니다.”라는 메시지가 뜨면 연결된 상태다. 앱은 로그인한 사용자의 ID 토큰으로 본인 확인 문서를 읽는다. 세션이 만료되면 다시 로그인한다.

### 연결이 안 될 때

| 화면에 보이는 내용 | 확인할 곳 |
| --- | --- |
| 환경 변수 설정 안내가 계속 뜬다 | 두 값의 이름을 확인한다. 두 값을 입력한 터미널에서 앱을 다시 실행한다. |
| 로그인에 실패했습니다 | 테스트 계정의 이메일과 비밀번호, 이메일/비밀번호 로그인 설정, 웹 API 키를 확인한다. |
| Firestore 접근이 거절됐습니다 | 웹 API 키와 프로젝트 ID가 같은 프로젝트의 값인지, Firestore에 읽기 규칙을 적용했는지 확인한다. |
| Firestore 데이터베이스 또는 내 확인용 문서가 없습니다 | `(default)` 데이터베이스에 `starter_checks` 컬렉션이 있고, 문서 ID가 로그인한 계정의 UID와 같은지 확인한다. |

앱 연결에는 웹 API 키와 프로젝트 ID를 쓴다. 계정 비밀번호, 사용자 토큰, 서비스 계정 키와 실제 환자 자료는 GitHub에 올리지 않는다.

## PowerShell에서 실행하기

개발 환경을 준비하는 명령은 1번과 같다. Firebase 환경 변수는 아래 방식으로 설정한다.

1. 웹 API 키 설정. `$env:PACS_FIREBASE_API_KEY = "여기에_웹_API_키"`
2. 프로젝트 ID 설정. `$env:PACS_FIREBASE_PROJECT_ID = "여기에_프로젝트_ID"`
3. 같은 터미널에서 앱 실행. `uv run --locked streamlit run app.py`

## 팀 작업 시작하기

- [app.py](app.py)에는 화면과 로그인 흐름이 있다. [firebase_client.py](firebase_client.py)는 Firebase 로그인과 Firestore 읽기 요청을 보내고, [firestore.rules](firestore.rules)는 본인 확인 문서 읽기를 허용한다.
- `uv run --locked python -m unittest discover -v`로 [단위 테스트](test_firebase_client.py)를 실행한다. 테스트는 네트워크 응답을 흉내 내므로 Firebase 계정 없이 실행할 수 있다.
- 팀 개발용 Firebase에서는 정상 로그인, 잘못된 비밀번호, 본인 문서 조회, 다른 사용자 문서 조회 거절과 로그아웃을 확인한다.
- 팀원마다 GitHub Issue와 작업 브랜치를 정한다. 뷰어, 영상처리, 케이스와 검토 기능의 첫 작업을 나눈다. 웹 템플릿은 팀 회의에서 고르고, 역할별 접근 권한과 실제 데이터 모델은 팀 설계에 맞춰 추가한다.
- 뷰어는 영상 파일 열기, 원본 표시, 확대와 초기화, 같은 영상의 처리 전후 비교 순으로 만든다.

## 배포

배포 대상은 전북대학교 JCloud다. 학교의 배포 지침을 받으면 실행 환경, 환경 변수 등록과 HTTPS 접속 설정을 확인하고 이 문서에 실제 절차를 추가한다.

## 공식 참고 자료

- [Streamlit 실행 안내](https://docs.streamlit.io/get-started/fundamentals/main-concepts)
- [Firebase Authentication 이메일 로그인 API](https://firebase.google.com/docs/reference/rest/auth#section-sign-in-email-password)
- [Cloud Firestore REST API와 보안 규칙](https://firebase.google.com/docs/firestore/use-rest-api)
