# P03 처리 기록 호환성 검사 (담당 B)

#4에서 요청받은 처리 기록 보완 중 B 범위를 정리한다. C의 저장·조회 코드(`case_*.py`)는 바꾸지 않았다. 저장 필드 연결은 C와 합의한 뒤 진행한다.

## 1. 하는 일

[processing_compat.py](../../processing_compat.py)는 저장된 처리 기록을 현재 B 코드로 **설정 복원**하거나 **다시 실행**할 수 있는지 판단한다. 알고리즘을 실행하지 않고, 저장 기록을 바꾸지도 않는다.

| 함수 | 역할 |
|---|---|
| `processing_environment(algorithm_id)` | 실제 적용한 결과와 함께 남길 실행 환경. 알고리즘이 사용하는 라이브러리만 기록한다(가우시안은 NumPy·Pillow·OpenCV, 감마와 임계값은 NumPy·Pillow). |
| `check_compatibility(id, version, parameters, environment)` | 지원 여부와 이유, 실행 환경 비교 결과를 담은 `Compatibility`를 반환한다. |

현재 지원하는 조합은 다음과 같다.

| 알고리즘 ID | 버전 | 설정 | 허용 범위 |
|---|---|---|---|
| `gaussian_blur` | 2.0.0 | `radius` (실수) | 0~20 |
| `gamma_correction` | 1.0.0 | `gamma` (실수) | 0.1~10 |
| `threshold_segmentation` | 1.0.0 | `threshold` (정수) | 0~255 |

## 2. 판정 결과

| `status` | 의미 | 설정 자동 복원과 재실행 |
|---|---|---|
| `supported` | 지원하는 ID·버전이고 설정이 유효하다 | 가능. 입력란만 채우고 실행은 사용자가 적용 버튼으로 한다 |
| `unknown_algorithm` | 현재 없는 알고리즘이다 | 불가. 이유를 표시한다 |
| `unsupported_version` | 같은 ID이지만 버전이 다르다. 예: Pillow 기반 가우시안 1.0.0 | 불가. 현재 알고리즘으로 **대신 실행하지 않는다** |
| `invalid_parameters` | 설정 항목이나 형식, 범위가 맞지 않는다 | 불가. 이유를 표시한다 |

실행 환경(`environment_status`)은 다음과 같이 나눈다.

- `same`: 저장 당시와 현재 라이브러리 버전이 같다. `same_conditions`가 참이 된다.
- `changed`: 버전이 다르거나 일부 버전 기록이 없다. "재현 조건이 달라졌습니다"와 함께 라이브러리별 차이를 안내한다. 설정 복원은 가능하다.
- `missing`: 환경 정보가 없는 기존 기록이다. 「환경 정보 없음」으로 구분하고, 기록 읽기와 설정 복원은 그대로 허용한다.
- `not_checked`: 알고리즘이나 버전을 지원하지 않아서 비교하지 않았다.

어느 경우에도 **저장 당시 결과는 다시 계산하지 않고 그대로 표시**한다. C의 `show_processing`이 그 역할을 한다.

## 3. 화면 연결 (`gaussian_ui.py`)

- 결과를 적용할 때 `viewer_processing_applied`에 `environment`를 함께 기록한다. 이 정보는 ROI가 아니라 적용 결과에 붙으므로, ROI가 없거나 해제돼도 사라지지 않는다.
- C가 저장 기록을 다시 열면(`viewer_restore_controls`) 호환성 검사를 거친 뒤에만 입력란을 채운다.
  - 버전은 복원 정보에 `algorithm_version`이 있으면 그것을 쓰고, 없으면 C가 둔 `viewer_stored_processing`에서 읽는다.
  - 지원하지 않으면 입력란을 채우지 않고 이유를 경고로 보여준다.
  - 실행 환경이 다르면 정보 안내, 환경 정보가 없으면 캡션으로 표시한다.
- 새 결과를 적용하면 저장 기록에 대한 안내는 사라진다.

## 4. C와 합의할 내용

현재 C의 `ProcessingRecord`와 저장 JSON에는 실행 환경을 담는 필드가 없다. 그래서 지금은 모든 저장 기록이 「환경 정보 없음」으로 표시된다. B의 제안은 다음과 같다.

- `ProcessingRecord.environment: dict | None = None`을 추가하고, 처리 기록 JSON에 `"environment"`로 저장한다.
- 값은 `applied["environment"]`를 그대로 사용한다. 예: `{"schema": "b_processing_environment_v1", "libraries": {"numpy": "2.2.6", "pillow": "12.3.0", "opencv": "4.12.0"}}`
- 필드가 없는 기존 기록은 `None`으로 읽는다. B는 이를 「환경 정보 없음」으로 처리한다.
- 복원 정보(`viewer_restore_controls`)에 `algorithm_version`과 `environment`를 넣어 주면 B가 그것을 우선 사용한다. 넣지 않아도 `viewer_stored_processing`에서 읽는다.

## 5. 검증

- 자동 테스트: `uv run --locked python -m unittest discover -v` 결과 **162개 통과**(기존 148개 + 신규 14개, `test_processing_compat.py`). 미지원 알고리즘과 버전, 잘못된 설정 14가지, 환경 정보 누락 5가지, 라이브러리 버전 변경, ROI 없이 적용한 결과의 환경 보존, 저장 기록 복원 화면(미지원 버전, 정상 복원, 환경 정보 없음, 환경 변경 후 새 적용)을 다룬다.
- 브라우저: 로컬 `app.main`에서 세 알고리즘, ROI, 원본/결과 정렬, Lenna 업로드가 기존처럼 동작함을 확인했다.
- 미검증: 실제 Firestore에 저장했다가 다시 열 때의 화면. 모의 로그인 환경에서는 Firestore를 쓸 수 없어서 AppTest의 복원 입력으로 대신 확인했다.
