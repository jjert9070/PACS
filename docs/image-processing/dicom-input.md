# DICOM 입력과 영상 처리 연결 (Issue #23, 담당 B)

기준 브랜치는 `feature/B-dicom-input`(`develop` `d55b347`에서 시작)이다. 이 문서에 적은 범위만 확인했으며 **DICOM 전체 지원을 뜻하지 않는다.** 근거로 삼은 문서는 통합 설계서 8.5(영상 종류별 조건), 9.2(등록과 해시), 10.2(DICOMInstance), 13.3(DICOM 지원 범위)과 C의 `docs/patient-study-contract.md`, `docs/patient-storage-handoff.md`다.

## 1. 지원 범위와 샘플

### 이번 단계 지원 범위

| 항목 | 지원 | 그 밖의 경우 |
|---|---|---|
| 파일 | DICOM Part 10. 128바이트 머리말 뒤 `DICM` 표지와 File Meta, 20 MB 이하 | 표지·구조 없음 → 손상(`corrupt`) |
| 전송 구문 | Implicit VR Little Endian, Explicit VR Little Endian (비압축) | JPEG, JPEG 2000, RLE 등 압축 → 미지원 |
| SOP Class | CT, MR, CR, DX(For Presentation), Secondary Capture **영상** | 표시 상태(PR) 등 비영상과 그 밖의 SOP Class → 미지원 |
| 프레임 | 단일 프레임 | 다중 프레임 → 미지원 (프레임 수를 안내) |
| 픽셀 | 채널 1개, MONOCHROME1/2, 할당 8 또는 16비트, 부호 있음·없음 | RGB 등 컬러, 32비트, 부동소수 → 미지원 |
| 크기 | 1픽셀 이상, 2,000만 픽셀 이하 (PNG/JPEG와 같은 한도) | 미지원 |

### 샘플: 직접 만든 합성 DICOM (환자정보 없음)

공개 데이터 대신 [`experiments/dicom_samples.py`](../../experiments/dicom_samples.py)로 픽셀과 식별자를 모두 직접 생성했다. **실제 환자 자료가 없어서 외부 사용 허락이 필요 없다.** 다시 실행하면 같은 바이트가 나오고, 테스트가 [`samples/dicom/manifest.json`](../../samples/dicom/manifest.json)의 SHA-256과 대조한다. 실행 명령은 `uv run --locked python experiments/dicom_samples.py`다.

| 파일 | 기대 결과 | 내용 |
|---|---|---|
| `ct_phantom.dcm` | 지원 | CT 256×256, 12비트, Rescale −1024(HU), Window 40/400. 공기·물·뼈·지방 원형 팬텀 |
| `mr_phantom.dcm` | 지원 | MR 160행×192열(직사각형), 12비트, Window 없음(최소–최대), Implicit VR |
| `cr_monochrome1.dcm` | 지원 | CR 96×128, 8비트 MONOCHROME1(반전 표시). 이름은 같고 환자 ID가 다른 사례 |
| `unsupported_jpeg_baseline.dcm` | 미지원 | JPEG Baseline 전송 구문 |
| `unsupported_multiframe.dcm` | 미지원 | 3프레임 |
| `unsupported_rgb.dcm` | 미지원 | Secondary Capture RGB |
| `missing_issuer.dcm` | 필수 정보 누락 | `IssuerOfPatientID` 없음 |
| `truncated.dcm` | 손상 | CT 샘플의 앞 60%만 남김 |

공통 식별 정보는 발급 주체 `PACS-EDU-SYNTH`, 환자 `SYN-0001`(CT·MR, 검사 2건)과 `SYN-0002`(CR)다. 두 환자는 이름이 `Synthetic^Phantom`으로 같다. UID는 라벨의 해시로 만든 `2.25.…` 형식이다. A·B·C 공통 샘플 후보로 제안한다.

### 라이브러리와 실행 환경

- `pydicom==3.0.1`(MIT 라이선스)을 `pyproject.toml`과 `uv.lock`에 추가했다. 비압축 픽셀 해석은 NumPy로 처리하므로 별도 디코더 패키지는 넣지 않았다.
- 확인한 환경은 Windows, Python 3.12.14, NumPy 2.2.6, Pillow 12.3.0, Streamlit 1.55.0이다.

## 2. DICOM 입력 (`dicom_input.py`)

| 함수 | 결과 |
|---|---|
| `read_dicom(name, data)` | `DicomImport`. 식별자, 픽셀 정보, 원본 저장값(원래 자료형, 읽기 전용), 표시 영상과 변환 기록 |
| `load_dicom_source(name, data)` | 뷰어용 `ViewerSource`(`file_format="DICOM"`, RGB 표시 영상, 원본 바이트 그대로) |
| `storage_metadata(data)` | C의 `register_dicom_original`에 넘길 **Decoder**. C의 `DicomMetadata`를 반환 |
| `DicomImport.provenance()` | 전송 구문, 픽셀 형식과 표시 변환을 담은 JSON 기록. 환자 식별자는 넣지 않는다 |

### 검사 순서와 오류 구분

1. 크기, `DICM` 표지, File Meta를 확인한다.
2. 전송 구문, SOP Class(File Meta와 데이터셋의 값이 같은지 포함)를 확인한다.
3. UID 형식(PS3.5 9.1, 64자 이하)과 UID끼리 서로 다른지 확인한다.
4. `PatientID`, `IssuerOfPatientID`를 확인한다.
5. 프레임 수, 채널, Photometric, 비트 수, 크기를 확인한다.
6. 픽셀 데이터 길이를 확인하고 해석한다.

| 오류 클래스 | `code` | 예 |
|---|---|---|
| `DicomCorruptError` | `corrupt` | DICOM이 아님, 잘린 파일, 픽셀 길이 부족 |
| `DicomMissingTagError` | `missing_tag` | `PatientID`, `IssuerOfPatientID`, `StudyInstanceUID`, `Rows`, `PixelData` 없음 |
| `DicomInvalidValueError` | `invalid_value` | UID 형식 오류, 검사와 시리즈 UID가 같음, `RescaleSlope` 0 |
| `DicomUnsupportedError` | `unsupported` | 압축 전송 구문, 다중 프레임, 컬러, 비영상 SOP Class, 20 MB 초과 |

모든 오류는 뷰어의 `ViewerInputError`를 상속한다. 그래서 A의 기존 입력 오류 경로가 **영상 대신 오류 안내**를 보여준다. 동시에 `ValueError`이기도 하므로 C의 해석 실패 처리와도 맞는다. 오류 문구에는 환자 ID나 이름을 넣지 않는다. 확장자나 `DICM` 표지만 보고 정상이라고 판단하지 않는다.

### 원본 보존

- 원본 바이트를 수정하지 않는다. 읽기 전후의 SHA-256을 비교하고, 이 해시를 `source_id`로 쓴다.
- C가 쓰는 원본 키 `<sha256>.dcm`과 같은 해시다.
- 원본 저장값은 원래 자료형(예: `uint16`)으로 보관하고 읽기 전용으로 둔다.

## 3. 표시와 처리 연결

### 표시 변환 (명시적으로 기록하는 변환)

뷰어와 B 알고리즘은 8비트 RGB를 받는다. DICOM 저장값을 그대로 넘기지 않고, **표시용 영상을 따로 만든다.**

1. Modality 값 = 저장값 × `RescaleSlope` + `RescaleIntercept`
2. `WindowCenter`/`WindowWidth`가 있으면 DICOM VOI LUT LINEAR(PS3.3 C.11.2.1.2.1)를 적용한다. 값이 여러 개면 첫 값을 쓴다. 없거나 폭이 1 미만이면 최소–최대로 늘린다.
3. MONOCHROME1이면 변환 뒤에 반전한다.
4. 0~255로 반올림(`np.rint`)한 회색 값을 RGB 세 채널에 복제한다.

변환 방식, center, width, 반전 여부와 변환 버전(`1.0.0`)은 `DicomImport.display`와 `provenance()`에 기록된다. 그래서 **B 알고리즘과 ROI 통계는 이 표시값을 기준으로 계산된다.** CT HU나 원본 저장값이 아니다. ROI 통계 화면에도 DICOM일 때 이 점을 함께 안내한다(`roi_ui.py`). 지원 범위 밖의 픽셀 형식은 변환하지 않고 거절한다.

### 처리 결과와 ROI의 원본 참조

DICOM 표시 영상에 가우시안·감마·임계값과 ROI를 적용했을 때 다음을 확인했다.

- 적용 기록의 `source_id`와 ROI 통계의 `source_id`가 DICOM 원본 바이트의 SHA-256과 같다.
- 결과 크기가 원본과 같다.

## 4. A와 C에 넘길 연결

### A (#24) — 뷰어 입력 분기 필요

현재 `viewer.load_image`는 PNG/JPEG만 받는다. 저장 원본도 이 함수로 다시 읽기 때문에, DICOM을 표시하려면 A의 `viewer.py`에 아래 분기가 필요하다. B는 A 담당 파일을 수정하지 않았다.

```python
from dicom_input import load_dicom_source
if Path(name).suffix.lower() == ".dcm":
    return load_dicom_source(name, data)   # 오류는 ViewerInputError로 전달됨
```

파일 업로더의 `type`에 `"dcm"`을 추가하는 것도 A가 결정할 부분이다. 영상 정보 영역에는 `file_format`이 `"DICOM"`으로 표시된다. 검사 날짜, 영상 종류, 표시 변환을 화면에 보여줄지도 A와 정한다.

### C (#25) — Decoder 계약 충족

`storage_metadata`가 C의 `Decoder = Callable[[bytes], DicomMetadata]` 계약을 따른다. 매핑은 다음과 같다.

| DicomMetadata | DICOM 태그 |
|---|---|
| `patient.issuer` / `patient.patient_id` | `IssuerOfPatientID` / `PatientID` (이름은 쓰지 않음) |
| `study_uid` | `StudyInstanceUID` |
| `series_uid` | `SeriesInstanceUID` |
| `sop_uid` | `SOPInstanceUID` |
| `sop_class_uid` | `SOPClassUID` |
| `frame_count` | `NumberOfFrames` (없으면 1). 현재 단일 프레임 영상만 통과 |
| `modality` / `body_region` | `Modality` / `BodyPartExamined` (없으면 빈 값) |

C 브랜치(`feature/C-patient-study-storage` `8a9518e`) 사본에서 실제 샘플로 `register_dicom_original`을 실행해 다음을 확인했다(`test_dicom_c_handoff.py`, Firestore는 C 테스트와 같은 모의 구현).

- 원본 키의 해시가 B의 `source_id`와 같다.
- 같은 파일을 다시 등록하면 기존 원본을 재사용한다.
- 미지원·손상 파일 5종은 원본을 저장하지 않는다.
- 다른 환자를 선택하면 충돌로 차단된다.

이 테스트는 C 모듈이 없는 브랜치에서는 자동으로 건너뛴다.

## 5. 검증

- 자동 테스트: `uv run --locked python -m unittest discover -v` 결과 **182개 통과, 1개 건너뜀**(C 모듈이 필요한 연결 시험). 신규 `test_dicom_input.py` 20개, `test_dicom_c_handoff.py` 3개(C 브랜치에서 실행해 통과).
- 확인한 항목: 샘플 재생성 일치와 해시, CT 식별자·저장값·윈도우 값(물 HU 0 → 102), MR 직사각형 방향과 최소–최대, MONOCHROME1 반전, 읽기 전후 바이트와 해시 유지, 오류 4종 분류와 문구의 개인정보 비포함, 뷰어 입력 오류 경로, 세 알고리즘과 ROI의 원본 참조.
- 브라우저: 실제 A 뷰어와 B 처리·ROI에 DICOM 샘플을 넘기는 검증용 페이지(저장소 밖)로 확인했다. A의 `.dcm` 분기 대신 테스트와 같은 방식으로 B 로더를 연결했다.

| 조작 | 결과 | 화면 |
|---|---|---|
| CT, 임계값 100, 전체 ROI | 표시·처리·ROI 정상, 원본/결과 같은 높이 | [CT](dicom-checks/ct-display.jpg) |
| MR, 감마 0.5 | 직사각형 방향 유지, ROI 평균 81.916 → 131.166 | [MR](dicom-checks/mr-display.jpg) |
| CR MONOCHROME1, 감마 0.5 | 반전 표시, ROI 평균 127.5 → 169.570 | [CR](dicom-checks/cr-display.jpg) |
| 다중 프레임, JPEG, 발급 주체 누락, 잘린 파일 | 오류 안내만 표시, 영상과 처리 결과 없음 | [다중 프레임](dicom-checks/unsupported-multiframe.jpg) |

## 6. 미지원 항목과 남은 결정

- 압축 전송 구문, 다중 프레임과 CT 시리즈(여러 슬라이스), 컬러 DICOM, 비영상 객체, 20 MB 초과 파일
- 원본 저장값(HU 등) 기준의 처리와 통계. 현재는 표시값 기준이다. 윈도우를 사용자가 바꾸는 기능도 없다.
- `PixelSpacing`을 사용한 물리 길이, 방향(Image Orientation)과 위치 정보
- `IssuerOfPatientID`가 없는 실제 공개 샘플을 어떻게 다룰지(현재는 필수 정보 누락으로 거절). C의 식별 규칙과 함께 팀 결정이 필요하다.
- 실제 공개 DICOM 샘플(예: TCIA)은 사용 조건을 확인한 뒤 추가할 후보로 남긴다.
- A의 뷰어 분기와 업로드, C의 화면 연결, 실제 Firebase에서의 등록은 각 담당의 연결 작업이다.
