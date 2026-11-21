# 선·사각형 측정과 보정 (담당 B, Issue #38)

기준 브랜치는 `develop` `865fd4e`에서 만든 `feature/B-measurement-tools`이고, 공통 개발 계약 R2.1을 따른다.

이 문서의 범위는 다음과 같다.
- **포함:** 원본 픽셀 좌표의 선 길이와 사각형 면적, 그리고 근거가 확인된 경우의 mm 값
- **제외:** 3D 거리·부피, 다중 프레임, 수동 보정, 임상 정확도 보증

## 1. 파일과 함수

| 파일 | 내용 |
|---|---|
| [measurement_tools.py](../../measurement_tools.py) | 계산 함수. Streamlit, Firebase, UI, 저장소를 import하지 않는다 |
| [dicom_input.py](../../dicom_input.py) | `DicomImport.pixel_geometry` 추가(B09). 끝에 기본값 `None`인 필드로 붙였고 기존 인수와 `provenance()`는 그대로다 |
| [test_measurement_tools.py](../../test_measurement_tools.py) | 측정·보정 시험 30개 |
| [experiments/measurement_examples.py](../../experiments/measurement_examples.py) | A와 C용 고정 예시 생성기 |
| [docs/handoff/b-measurement-examples.json](../handoff/b-measurement-examples.json) | 고정 예시(B13). `test_measurement_examples.py`가 코드와 일치하는지 검사한다 |

| 함수 | 사용자 | 설명 |
|---|---|---|
| `measure_annotation(source_ref, geometry, calibration=None)` | A, C | measurement 사전을 반환한다. 잘못된 입력은 `MeasurementInputError` |
| `calibration_from_metadata(source_ref, metadata)` | A, C | B가 원본에서 읽은 `pixel_geometry`(또는 `None`)로 보정 사전을 만든다 |
| `calibration_matches(source_ref, calibration, metadata)` | C | 제출된 보정이 신뢰된 원본에서 B가 다시 만든 값과 같은지 판정한다. 합성 보정은 실제 원본에서 거짓 |
| `measurement_matches(source_ref, geometry, calibration, measurement)` | C | B로 다시 계산해 비교한다. 수치는 절대·상대 허용오차 1e-9 |
| `synthetic_calibration(source_ref, row_mm, column_mm)` | 시험 | `synthetic_fixture` 근거의 verified 보정. 실제 원본의 근거로는 쓰지 않는다 |

**오류 코드**는 `MeasurementInputError.code`에 들어가고, 계약의 세 가지만 쓴다.

| code | 경우 |
|---|---|
| `invalid_geometry` | 좌표·형식 오류: 같은 두 점, 소수·논리형·NaN·무한대 좌표, 범위 밖, 빈 사각형·역전 경계, 선에 bounds 포함, 보기 상태 같은 추가 키 |
| `source_mismatch` | 원본 참조 형식 오류, 해시·크기·좌표계 불일치, 다른 원본의 보정, 위조되거나 자기모순인 보정(예: 근거 없는 verified, 0·음수·비유한 간격) |
| `unsupported_frame` | `frame_index`가 0이 아님, 메타데이터의 프레임 수가 1이 아님 |

메시지에는 환자 정보를 넣지 않는다.

## 2. 계산식

점 $(x_1,y_1)$, $(x_2,y_2)$는 원본 픽셀 중심의 정수 인덱스이고, 사각형 $(l,t,r,b)$는 오른쪽과 아래 끝을 제외한다. 행(세로) 간격은 $s_r$, 열(가로) 간격은 $s_c$이며 단위는 mm다.

$$
d_{\mathrm{px}}=\sqrt{\Delta x^2+\Delta y^2},\qquad A_{\mathrm{px}^2}=(r-l)(b-t)
$$

$$
d_{\mathrm{mm}}=\sqrt{(\Delta x\,s_c)^2+(\Delta y\,s_r)^2},\qquad A_{\mathrm{mm}^2}=A_{\mathrm{px}^2}\,s_c\,s_r
$$

- **축 순서 주의:** DICOM `PixelSpacing`의 첫 값은 행 간격 $s_r$(세로), 둘째 값은 열 간격 $s_c$(가로)다. 가로 차이 $\Delta x$에는 둘째 값을 곱한다. 시험에서 행 0.5, 열 0.8일 때 가로 10px은 8mm, 세로 10px은 5mm인지 확인한다.
- 픽셀 길이는 `float`, 픽셀 면적은 `int`다.
- 실제 값을 계산할 수 없으면 `physical_value`와 `physical_unit`이 모두 `null`이다. 0이나 빈 문자열로 대신하지 않는다.
- 사각형은 기존 `roi.validate_roi`로 검사한다(B05). 밝기 통계(`roi.compare_roi`)와는 별개의 계산이다.

## 3. 실제 단위 지원 조건 (B10)

**verified는 아래 표의 첫 행 조건을 모두 만족할 때만 준다.** 태그가 있다는 이유만으로 verified를 주지 않는다.

| 원본 | 조건 | status | reason | 이유 |
|---|---|---|---|---|
| DICOM CT·MR Image Storage | 단일 프레임, 메타데이터의 행·열 = 원본 참조의 height·width, 해시 일치, `PixelSpacing` 2개 값이 모두 유한한 양수 | `verified` | `dicom_image_plane_pixel_spacing` | Image Plane 모듈(PS3.3 C.7.6.2)의 `PixelSpacing`은 환자 영상 평면의 행·열 간격이다. B의 표시 영상은 원본 행·열 격자를 그대로 쓴다(리샘플링 없음) |
| DICOM CT·MR | `PixelSpacing` 없음 | `unverified` | `pixel_spacing_absent` | 근거 없음 |
| DICOM CT·MR | `PixelSpacing`이 0, 음수, 1개나 3개 값, NaN·무한대, 숫자가 아님 | `unverified` | `pixel_spacing_invalid` | 표준의 단일 행·열 0 간격 예외는 구현하지 않음 |
| DICOM CR·DX | `PixelSpacing`이나 `ImagerPixelSpacing`이 있어도 | `unverified` | `projection_radiograph_spacing_not_verified` | 투영 촬영은 검출기 기준 간격이거나 추정값이고, 환자 위치의 확대율을 이 단계에서 확인하지 않는다 |
| DICOM Secondary Capture, 그 밖의 SOP Class | 항상 | `unsupported` | `sop_class_without_physical_geometry` | 영상 평면 정보가 없음 |
| PNG, JPEG | 항상 | `unsupported` | `raster_has_no_physical_geometry` | DPI나 화면 크기를 mm 근거로 쓰지 않는다 |
| 메타데이터 없음 | DICOM 참조인데 `metadata=None` | `unverified` | `no_calibration_metadata` | |
| 보정 없음 | `calibration=None`으로 측정 | `unverified` | `no_calibration` | |

- **보정 사전의 필드:** `status`, `reason`, `row_spacing_mm`, `column_spacing_mm`, `source_id`, `evidence: {kind, attributes}`, `method_version: "calibration_v1"`
  - verified DICOM의 근거는 `evidence.kind = "dicom_supported_geometry"`이다.
  - `attributes`에는 SOP Class, Modality, 행·열, `PixelSpacing` 원문 문자열을 보존한다.
- **실제 영상 증빙:** 실제 영상에서 mm 값이 맞다는 것은 아직 증빙하지 않았다(#38 B26). 위 표는 표준의 의미와 합성 자료로 확인한 조건이다.
- **2D 한계:** 측정값은 영상 평면 위의 2D 길이와 면적이다. 신체의 3D 거리나 부피로 설명하지 않는다.

## 4. A와 C에 넘기는 내용

- **A**
  - 마우스 좌표를 원본 픽셀로 역변환하고 드래그 방향을 정규화한 뒤 `geometry`를 만든다(공통 계약 6.2).
  - 보정은 `calibration_from_metadata(source_ref, imported.pixel_geometry)`로만 만든다. 체크박스나 사용자 입력으로 verified를 만들 수 없다.
  - `physical_value`가 null이면 픽셀 값만 표시하고 `calibration_reason`을 안내한다.
- **C**
  - 저장할 때 신뢰된 원본을 B로 다시 읽는다.
  - `calibration_matches`로 보정을, `measurement_matches`로 측정값을 재검증한다.
  - 원본 참조가 인증 사용자의 것인지는 B가 판정하지 않으며 C가 확인한다.
- 정상 값, 실제 값 null, 각 오류 코드의 예시는 [b-measurement-examples.json](../handoff/b-measurement-examples.json)에 있다.

## 5. 검증

- `uv run --locked python -m unittest test_measurement_tools test_measurement_examples test_processing_compat -v`: **45개 통과**(측정 30, 예시 2, P03 13)
- 전체 `uv run --locked python -m unittest discover`(Windows, Python 3.12.14): **399개 통과, 건너뜀 0개**
- **아직 하지 않은 것** (#38 B24~B26)
  - A의 실제 마우스 좌표와의 연결
  - C 저장 후 재열기
  - 실제 DICOM 영상의 mm 값 증빙
