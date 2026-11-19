# A와 B 담당에게 전달할 C 저장 함수

이 문서는 통합 설계서 UC01, UC02, UC03, 9.2와 13.2를 현재 C 코드에 연결하는 사용 안내다. 환자와 검사 관계, UID와 내용 충돌 원칙은 설계서에 정의돼 있다. 여기에 쓰인 함수 이름과 결과 형식은 현재 C 구현의 인터페이스이며 팀 합의 완료를 뜻하지 않는다.

## A 담당의 등록 흐름

사용자가 확인한 환자를 `PatientIdentity`로, 선택한 검사와 시리즈를 `Series`로 전달한다. 이름만으로 환자를 선택하거나 파일 메타데이터를 읽었다는 이유만으로 연결을 확인 처리하지 않는다.

`register_sample_original`은 프로젝트 ID, 로그인 세션, 로컬 원본 저장소, 확인된 환자, 시리즈, 영상 ID, 파일명과 원본 바이트를 받는다. 현재 PNG와 JPEG만 허용한다. 파일 검증 후 환자, 검사, 시리즈와 원본 연결을 차례로 저장한다. 단계 전체를 하나의 트랜잭션으로 묶지 않으므로 중간 실패 전에 생성된 식별 정보는 남을 수 있다.

| 결과 필드 | 화면에서 사용할 의미 |
|---|---|
| success | 참이면 원본 연결이 확인됨. 거짓이면 전체 성공으로 표시하지 않음 |
| stage | "input", "patient", "study", "series", "original", "link", "linked" 중 완료 또는 실패 단계 |
| error_code | "invalid_input", "identity_conflict", "session_expired", "permission_denied", "local_storage_failure", "remote_failure" 중 오류 구분 |
| original_key | 이미 저장한 원본의 내부 키. 사용자 경로 입력으로 바꾸지 않음 |
| retry_image_id | 값이 있으면 저장된 재시도 기록으로 이어갈 수 있음. 빈 값이면 기록이 있다고 가정하지 않음 |

연결 실패 시 `study_link_recovery.list_pending_links`로 로그인 사용자와 프로젝트의 미완료 기록을 조회한다. 각 항목에는 원본 연결, 처리 상태와 오류 코드가 있다. 오류 코드는 권한 거절, 세션 만료, 요청 실패, 식별 충돌과 원본 오류를 구분하며 서버의 원문 응답이나 접속 토큰은 기록하지 않는다. 사용자가 재시도를 선택하면 `retry_pending_link`에 같은 영상 ID를 전달한다. 충돌은 재시도만으로 다른 원본에 덮어쓰지 않는다. 세션 만료 시 다시 로그인한 뒤 같은 사용자와 프로젝트에서 조회한다.

## 목록과 선택

| 함수 | 입력과 결과 |
|---|---|
| list_patients | 본인의 환자 목록과 다음 페이지 토큰 |
| list_patient_studies | 확인된 환자에 속한 검사 목록과 다음 토큰 |
| list_study_series | 선택한 검사에 속한 시리즈 목록과 다음 토큰 |
| list_study_images | 선택한 검사에 속한 원본 참조 목록과 다음 토큰 |
| list_series_images | 선택한 검사와 시리즈에 속한 원본 참조 목록과 다음 토큰 |
| get_study_details | 촬영일, 환자 연결 상태와 영상 수신 상태 및 수정 기준 버전 |
| open_selected_original | 선택한 환자, 검사, 시리즈와 영상의 관계를 확인하고 뷰어 원본을 반환 |

선택을 바꾸면 이전 목록의 페이지 토큰을 비운다. 다음 토큰이 비어 있으면 마지막 페이지다. 오류를 잡아서 빈 목록으로 표시하지 않는다. 연결 오류, 권한 거절과 정상 조회 0건은 별도로 표시한다. 원본을 연 결과의 `source`가 기존 뷰어와 영상 처리 함수에 전달할 `ViewerSource`다.

## B 담당과 연결할 경계

`StudyImage`의 영상 ID는 샘플 식별자다. DICOM은 별도의 `DicomMetadata`와 `DicomInstance`를 사용한다. DICOM 파일을 PNG로 바꾸어 원본으로 저장하면 안 된다.

| 함수 또는 모델 | 전달 계약 |
|---|---|
| DicomMetadata | 환자 ID와 발급 주체, Study UID, Series UID, SOP Instance UID, SOP Class UID, 확인한 프레임 수와 선택적인 영상 종류, 부위 |
| register_dicom_original | 프로젝트, 세션, 로컬 저장소, 확인한 StudyIdentity, 시리즈 UID, 원본 바이트와 B의 해석 함수 |
| Decoder | 원본 바이트를 입력받아 DicomMetadata를 반환하는 신뢰된 앱 내부 함수. 형식과 지원 객체 조건을 확인하고, 미지원이나 손상 입력은 예외로 거절 |
| DicomOriginalSavedError | 원본 키와 재시도 기록 여부. 전체 성공으로 표시하지 않음 |
| list_pending_dicom | 본인과 해당 프로젝트의 미완료 SOP UID, 확인된 연결 정보, 원본 키와 실패 상태 |
| retry_dicom_registration | 같은 SOP UID와 저장된 원본을 다시 해석하고 연결 재시도 |
| list_series_instances | 선택한 검사와 시리즈의 DICOM 객체 목록과 다음 토큰 |
| open_dicom_original | 선택한 환자와 검사, 시리즈, SOP UID의 연결 및 원본을 확인하고 변환 전 바이트 반환 |

B의 해석기는 파일 확장자나 "DICM" 표지만으로 정상 입력이라고 판단하지 않는다. 지원 SOP Class와 전송 구문, 메타데이터와 프레임 조건을 확인한다. C는 해석 함수를 원본 바이트로 호출하고 해석 결과와 사용자가 확인한 선택을 비교한다. 프레임 수 0은 B가 확인한 비영상 객체에만 사용하며 원본 객체 수를 사진 장수로 표시하지 않는다.

해석 함수를 요청 데이터나 UI에서 동적으로 실행시키면 안 된다. B의 실제 해석기는 아직 연결되지 않았다. 현재 원본 보관 함수와 시험용 스텁만으로 실제 DICOM 입력과 영상 표시가 검증됐다고 안내하지 않는다. 원본 크기 제한은 개발용 20 MB다. 열기 함수는 원본 바이트를 반환하므로 실제 픽셀 디코딩과 뷰어 표시는 B와 A의 연결이 필요하다.

UID 문법의 기준은 [DICOM PS3.5 9.1](https://dicom.nema.org/medical/dicom/current/output/chtml/part05/chapter_9.html)이며 UID 발급의 전 세계 유일성을 이 저장 코드에서 증명하지 않는다. 같은 사용자 안에서 다른 객체 종류로 UID를 재사용하려는 요청은 보류한다.

## 모아서 확인할 테스트

재시도 기록은 `test_study_link_recovery.py`, 서버 조건과 페이지 경계는 `test_storage_query.py`, 화면 호출 경계는 `test_patient_storage_service.py`에 확인 항목을 작성했다. 기존 저장 흐름 예는 `test_patient_storage_integration.py`에 있다. DICOM 보관과 충돌, 재시도는 `test_dicom_storage.py`에 작성했다. DICOM 테스트의 원본 바이트와 해석기는 인공 시험값이며 실제 DICOM 파일의 지원 시험이 아니다. 추가 경로 수정 후 Git Bash에서 전체 코드 테스트 228개가 통과했다. Ubuntu 백엔드 분리 시험 48개와 두 운영체제의 프로세스 시험 결과는 backend-command-checks.md에 기록했다. 테스트의 Firestore 응답은 모의 구현이며 DICOM 해석기는 스텁이다. 실제 Firebase 보안 규칙과 색인, 서버 조회 응답 및 화면 연결도 검증 대기다.
