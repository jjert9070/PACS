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

## 뷰어 스타터 (feature/viewer-starter)

로그인 뒤 왼쪽에서 PNG/JPEG 파일 또는 합성 예시를 선택한다. 중앙에서 원본을
확대하고 가로·세로 위치를 조절하며, 오른쪽에서 파일 형식·크기·영상 ID와 표시
상태를 확인한다. 확대는 원본의 일부를 잘라 같은 화면 크기로 보여주는 방식이다.
보기 초기화 또는 다른 파일 선택 시 전체 영상으로 돌아온다. 원본 파일은 수정하지 않는다.

- 지원: 단일 프레임 PNG/JPEG, 20 MB 이하, 2,000만 픽셀 이하, 8비트 표시 영상.
- 미지원: DICOM/CT 시리즈, 16비트 정량 분석, MPR·3D, ROI와 실제 단위 측정,
  영구 저장·케이스 목록·AI. CT 예시를 사용한다면 PNG/JPEG로 내보낸 2D 단면이다.
- 확장자뿐 아니라 실제 형식과 디코딩을 검사한다. 손상 파일·다른 형식·제한 초과는
  이유를 표시한다. PNG는 디코딩 전에 IHDR 헤더의 채널당 비트 깊이를 확인하여
  16비트 회색조·RGB·RGBA 영상을 거절한다. Pillow가 표시하는 RGB/RGBA 모드만으로
  비트 깊이를 판단하지 않는다. JPEG 방향 정보는 화면에 반영한다.
- 시안의 영상 선택/중앙 뷰어/정보 패널 배치를 참고했다. 처리 결과가 없을 때
  원본을 처리 결과라고 표시하지 않는다. PACS 연결·저장 완료를 가정하지 않는다.
- 합성 예시는 코드에서 생성한 명암/사각형 패턴으로, 환자 자료나 외부 영상이 아니다.
- 로그아웃·세션 만료 시 뷰어 상태를 지운다. 새로고침 시 파일이 유지되는 것을
  보장하지 않으며, Firebase에는 이 단계의 영상이나 처리 결과를 저장하지 않는다.

### 영상처리 담당 연결 지점

`viewer.py`의 `render_viewer(processor=None)`에 함수를 전달한다. 함수는 현재 원본의
**RGB PIL 이미지 복사본**을 받고, 같은 크기의 PIL 이미지를 반환한다. 가로·세로 위치와
확대는 두 영상에 동일하게 적용한다. 결과 크기가 다르거나 처리에 실패하면 오류를
표시하고 원본을 유지한다. 콜백은 화면 재실행마다 호출하므로 비싼 처리는 원본 ID와
설정값을 키로 캐싱하는 방법을 후속 작업에서 합의한다.

`render_viewer` 반환값은 `ViewerSource | None`이다. 영상 선택 시 `source_id`(파일 내용의
SHA-256), `name`, `file_format`, `byte_count`, `image`를 제공한다. 처리 이력과 ROI 연결은
이 ID와 원본 픽셀 좌표를 기준으로 합의한다. 처리 함수가 원본 크기나 좌표를 바꾸려면
현재 계약과 표시 로직을 함께 수정해야 한다.

### 첫 확인 순서

1. 로그인 → 합성 예시 선택 → 중앙 원본과 오른쪽 정보 확인.
2. 확대 2배 → 가로·세로 위치 변경 → 보기 초기화.
3. 정상 PNG와 JPEG 선택 → 파일 이름과 픽셀 크기 확인.
4. 손상 PNG 선택 → 이전 영상 대신 오류 안내 확인.
5. 새 영상 선택 → 확대와 위치가 기본값으로 돌아오는지 확인.
6. 로그아웃 → 로그인 화면으로 돌아오는지 확인.

자동 확인: `uv run --locked python -m unittest discover -v`.
파일 검증·방향·확대 좌표, 화면 초기화, 처리 함수의 원본 보존과 결과 크기,
로그아웃을 Firebase 네트워크 없이 검사한다. 실제 계정 로그인과 파일 선택은 별도로 확인한다.

### 브랜치 작업과 리뷰

`develop`에서 `feature/viewer-starter`를 만들고 이 브랜치에서 작업한다.
커밋/푸시 뒤 GitHub에서 **base: develop / compare: feature/viewer-starter**로 PR을 만든다.
팀원 리뷰 후 합친다. `/`는 이름을 구분하는 관례이며 별도 프로젝트 폴더가 아니다.

## JCloud 배포

배포 대상은 전북대학교 JCloud다. 학교의 배포 지침을 받으면 실행 환경, 환경 변수 등록과 HTTPS 접속 설정을 확인하고 이 문서에 실제 절차를 추가한다.

## 공식 참고 자료

- [Streamlit 실행 안내](https://docs.streamlit.io/get-started/fundamentals/main-concepts)
- [Firebase Authentication 이메일 로그인 API](https://firebase.google.com/docs/reference/rest/auth#section-sign-in-email-password)
- [Cloud Firestore REST API와 보안 규칙](https://firebase.google.com/docs/firestore/use-rest-api)
