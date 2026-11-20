# 담당 B 기여 기록 (영상처리와 ROI)

보고서의 "팀원별 역할과 기여" 표에 넣을 B 담당 근거를 모은다. 2026-11-20 기준이며, 열린 PR이 병합되거나 새 작업이 생기면 갱신한다.

## 1. 담당자

| 항목 | 내용 |
|---|---|
| 성명 | 박지성 |
| 학번 | (기입 필요) |
| GitHub | [JiseongParkk](https://github.com/JiseongParkk) |
| 역할 | 담당 B: 영상처리 알고리즘, ROI 통계, DICOM 입력의 B 범위 |

## 2. 보고서용 요약 표

| 구분 | 내용 | 근거 |
|---|---|---|
| 뷰어 시작 코드 | 초기 Streamlit 뷰어 골격과 이미지 입력 | PR #2, `a1e3b61`, `292c2bf` |
| 가우시안 필터 | OpenCV 기반 가우시안(2.0.0), 웹 연결, 처리 계약 | PR #6, `032f0bb`, `6e6e8d4` |
| ROI 통계 | 원본 좌표 기준 사각형 ROI, Pillow L 밝기 평균·모집단 분산 | `849d194`, `c238702` |
| 감마 보정 | LUT 기반 감마 보정(1.0.0), 웹 연결 | `b595f30`, Issue #12–#16 |
| 임계값 분할 | 고정 임계값(같은 값은 흰색, 1.0.0), 웹 연결 | `6b99edb` |
| 통합과 리뷰 반영 | #6 리뷰 반영, 원본/결과 높이 정렬, 실패 원인 로그 | `bc9e3e7`, `42218d6`, `6e65f8e`, `6560bee`, `62f90f4`, `8c6eec7`, PR #4 |
| DICOM 입력 | 비압축 단일 프레임 DICOM 읽기, VOI LUT LINEAR 표시, 오류 분류, C 저장 연결 흐름, 합성 표본 8개 | PR #26 (Refs #23), `585e2d9`, `8eeea01`, `8a39bfc` |
| 비교 실험 | 감마·임계값 조건별 비교 실험과 해석 | PR #29 (Closes #28), `55fe554` |
| 처리 기록 호환성 | 저장 기록의 알고리즘 버전·설정·실행 환경 검사(P03) | PR #31 (Closes #30), `eba1415`, `d0e8ead` |
| 리뷰 | A의 PR #8 리뷰 코멘트 | PR #8 |

## 3. 구현 기능과 코드 위치

| 기능 | 파일 | 문서 |
|---|---|---|
| 가우시안, 감마, 임계값 처리 함수 | `image_processing.py` | `docs/image-processing/gaussian.md`, `gaussian-web.md`, `gamma-web.md`, `threshold-web.md` |
| 처리 알고리즘 선택과 적용 화면 | `gaussian_ui.py` | 위와 같음 |
| ROI 입력과 통계 | `roi.py`, `roi_ui.py` | `docs/image-processing/roi.md` |
| DICOM 읽기와 표시 변환 | `dicom_input.py` (PR #26) | PR #26 본문 |
| DICOM 저장·재시도·재열기 흐름 | `dicom_storage_flow.py` (PR #26) | PR #26 본문 |
| 합성 DICOM 표본 | `experiments/dicom_samples.py`, `samples/dicom/` (PR #26) | `samples/dicom/` 매니페스트 |
| 감마·임계값 비교 실험 | `experiments/gamma_threshold_experiment.py` (PR #29) | `gamma-web.md`, `threshold-web.md` 6절 (PR #29) |
| 처리 기록 호환성 | `processing_compat.py` (PR #31) | `docs/image-processing/processing-compat.md` (PR #31) |

괄호 안에 PR 번호가 있는 항목은 아직 `develop`에 들어오지 않았다.

## 4. Issue와 PR

### PR

| PR | 브랜치 | 상태 | 내용 |
|---|---|---|---|
| [#2](https://github.com/jjert9070/PACS/pull/2) | `viewer-starter` → `develop` | 병합 | 뷰어 시작 코드 |
| [#6](https://github.com/jjert9070/PACS/pull/6) | `gaussian-web` → `intergrationOfBC` | 병합 (2026-11-17) | 가우시안 웹 연결과 리뷰 반영 |
| [#4](https://github.com/jjert9070/PACS/pull/4) | `image-processing` → `develop` | 병합 (2026-11-19) | 가우시안·감마·임계값·ROI 통합 |
| #7, #9, #10 | 알고리즘별 브랜치 | 닫힘 | 내용은 #4, #22로 반영 |
| [#26](https://github.com/jjert9070/PACS/pull/26) | `B-dicom-input` → `develop` | 리뷰 중 | DICOM 입력 (Refs #23) |
| [#29](https://github.com/jjert9070/PACS/pull/29) | `B-algorithm-experiments` → `develop` | 리뷰 중 | 감마·임계값 비교 실험 (Closes #28) |
| [#31](https://github.com/jjert9070/PACS/pull/31) | `processing-compat` → `develop` | 리뷰 중 | P03 처리 기록 호환성 (Closes #30) |

### Issue

| Issue | 작성 | 상태 | 내용 |
|---|---|---|---|
| #12–#16 | 박지성 | 닫힘 | 1단계 B 작업 항목 |
| [#23](https://github.com/jjert9070/PACS/issues/23) | 팀장 | 진행 중 | DICOM 입력과 영상 처리 연결. B 항목 완료, A 뷰어 연결 대기 |
| [#28](https://github.com/jjert9070/PACS/issues/28) | 박지성 | 진행 중 | 감마·임계값 비교 실험 |
| [#30](https://github.com/jjert9070/PACS/issues/30) | 박지성 | 진행 중 | P03 처리 기록 호환성 |

### 커밋

- 박지성이 작성한 커밋은 24개이고, 그중 18개가 `develop`에 있다(2026-11-20 기준).
- 팀장이 B 파일을 직접 수정한 커밋도 있다. `07059fb`(BC 저장·복원 연결), `fbd02e1`, `e274440`(DICOM 윈도우 경계 수정)이다. 보고서에서는 이 부분을 B 단독 기여로 적지 않는다.

## 5. 검증

| 항목 | 결과 |
|---|---|
| 자동 테스트 (`develop` `d55b347`) | `uv run --locked python -m unittest discover -v`, 162개 통과 |
| 자동 테스트 (PR #31 브랜치) | 176개 통과 (기존 162개 + 신규 14개) |
| 브라우저 확인 | Edge(headless, Playwright)로 실제 `app.main`을 조작. 세 알고리즘, ROI, 원본/결과 정렬, 파일 업로드 확인. 화면은 `docs/image-processing/*-checks/` |
| BC32 (WSL Ubuntu) | 잠금 파일 확인, 281개 중 280개 통과·1개 건너뜀, 로컬 저장 → 재시작 → 다시 열기 확인 |

모의 로그인 하네스로 확인한 항목은 실제 Firebase 로그인, 권한, Firestore 저장을 포함하지 않는다.

## 6. 계획과 실제의 차이

- 설계서의 B 범위는 영상처리와 ROI였다. 실제로는 가우시안, 감마, 임계값 세 알고리즘을 모두 B가 구현했다(팀 합의).
- 초기 뷰어 시작 코드(PR #2)도 B가 작성했다. 이후 뷰어(`viewer.py`)는 A 담당이다.
- 2단계에서 DICOM 입력(#23)의 읽기와 표시 변환, C 저장 연결 흐름을 B가 맡았다. 뷰어의 `.dcm` 연결은 A가 진행한다.
- 마우스 ROI(P02)는 A의 좌표 역변환이 필요해 아직 하지 않았다.

## 7. 남은 일

- A의 `.dcm` 뷰어 연결 후 #23의 BC28–BC30 확인
- 팀장의 Firestore 규칙 배포 후 BC31 확인
- C와 합의: 실제 비식별 DICOM의 IssuerOfPatientID 정책, 처리 기록의 `environment` 필드(#30)
- PR #26, #29 병합 후 README의 B 절 갱신

## 8. AI 도구 사용

- 코드 작성, 테스트 작성, 문서 초안, 브라우저 자동 검증에 Claude Code(Claude Opus 5.5)를 사용했다.
- AI가 함께 작성한 커밋에는 `Co-Authored-By: Claude Opus 5.5` 줄이 있다.
- 구현 범위와 판단 기준은 담당자가 정하고, 결과는 자동 테스트와 브라우저 확인으로 검증했다.
