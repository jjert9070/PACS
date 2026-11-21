# P03 처리 기록 호환성 검사 (담당 B, #38 B01·B02·B11·B12·B21)

1차의 #30과 PR #31에서 만든 P03 기능을 2차 기준 `develop` `865fd4e`로 옮겼다. 2차 계약에서 `gaussian_ui.py`는 A의 파일이다. 그래서 **순수 판정 모듈은 B 브랜치에 넣고, 화면 연결은 A에게 패치로 넘긴다.** C의 `case_records.py`와 `firestore.rules`는 수정하지 않는다.

## 1. 재사용 대조 결과 (B01)

| PR #31 파일 | 2차 처리 | 근거 |
|---|---|---|
| `processing_compat.py` | B 브랜치에 반영하고 schema 검사를 보완(2절) | B 소유 순수 모듈 |
| `test_processing_compat.py` 중 판정 시험 | B 브랜치에 반영하고 시험을 추가 | UI 없이 실행 |
| `gaussian_ui.py` 변경 | [docs/handoff/b-p03-gaussian-ui.patch](../handoff/b-p03-gaussian-ui.patch)로 A에게 전달 | A 소유 화면 파일 |
| `test_processing_compat.py` 중 화면 시험 5개 | 같은 패치의 `test_processing_compat_ui.py`로 전달 | `gaussian_ui.py`가 필요함 |

추가로 확인한 사실은 두 가지다.
- PR #31은 `develop` `865fd4e`에 충돌 없이 합쳐진다. 임시 사본에서 PR #31 전체를 얹으면 전체 368개 시험이 통과했다.
- 위 패치는 `develop`과 B 브랜치 모두에 `git apply --check`로 적용된다. 패치를 적용한 사본에서 화면 시험 5개와 전체 시험이 통과했다.

## 2. 함수와 판정

[processing_compat.py](../../processing_compat.py)는 저장된 처리 기록을 현재 B 코드로 **설정 복원**하거나 **다시 실행**할 수 있는지 판단한다. 알고리즘을 실행하지 않고, 저장 기록을 바꾸지 않는다.

| 함수 | 역할 |
|---|---|
| `processing_environment(algorithm_id)` | 실제 적용한 결과와 함께 남길 실행 환경. 알고리즘이 사용하는 라이브러리만 기록한다(가우시안은 NumPy·Pillow·OpenCV, 감마와 임계값은 NumPy·Pillow). ROI 인수가 없으므로 ROI가 없거나 해제돼도 환경 정보와 무관하다. |
| `check_compatibility(algorithm_id, algorithm_version, parameters, environment=None)` | 지원 여부와 이유, 실행 환경 비교 결과를 담은 `Compatibility`를 반환한다. |

| 알고리즘 ID | 버전 | 설정 | 허용 범위 |
|---|---|---|---|
| `gaussian_blur` | 2.0.0 | `radius` (실수) | 0~20 |
| `gamma_correction` | 1.0.0 | `gamma` (실수) | 0.1~10 |
| `threshold_segmentation` | 1.0.0 | `threshold` (정수) | 0~255 |

논리형, NaN, 무한대, 문자열, 추가 항목은 모두 `invalid_parameters`로 판정한다.

`status`의 값은 네 가지다.

| `status` | 의미 | `can_restore_settings` / `can_rerun` |
|---|---|---|
| `supported` | 지원하는 ID·버전이고 설정이 유효함 | 참 / 참 |
| `unknown_algorithm` | 현재 없는 알고리즘 | 거짓 / 거짓 |
| `unsupported_version` | 같은 ID, 다른 버전(예: Pillow 기반 가우시안 1.0.0). **현재 알고리즘으로 대신 실행하지 않음** | 거짓 / 거짓 |
| `invalid_parameters` | 설정 항목·형식·범위가 맞지 않음 | 거짓 / 거짓 |

`environment_status`는 재현 조건을 나타내고 `status`와 따로 판단한다. `same_conditions`는 `status`가 `supported`이고 `environment_status`가 `same`일 때만 참이다.

| `environment_status` | 조건 |
|---|---|
| `same` | schema가 `b_processing_environment_v1`이고, 알고리즘이 쓰는 라이브러리 버전이 모두 같다 |
| `changed` | 버전이 다르거나 일부 버전 기록이 없다. "재현 조건이 달라졌습니다"와 라이브러리별 차이를 안내한다 |
| `missing` | 환경 정보가 없는 기존 기록(`None`, 빈 사전, 라이브러리 없음). 「환경 정보 없음」. 읽기와 설정 복원은 허용한다 |
| `not_checked` | 알고리즘이나 버전을 지원하지 않거나, **schema가 없거나 알 수 없는 환경 정보**. 라이브러리 이름과 버전이 같아도 같은 조건이라고 하지 않는다 |

예를 들어 설정은 지원하지만 환경이 `changed`이면 `can_rerun`은 참이고 `same_conditions`는 거짓이다. 이 둘을 지원 실패 하나로 합치지 않는다.

**2차 보완 사항:** 1차 코드는 schema를 보지 않았다. 그래서 `{"libraries": {...}}`처럼 schema가 없는 기록도 버전이 같으면 `same`이 될 수 있었다. #38 6절에 따라 이런 기록은 `not_checked`로 바꿨다.

## 3. C와 A에 넘기는 내용

**C에 저장할 환경 JSON** (공통 계약 9.3)
- 처리 기록 JSON의 최상위 `"environment"`에 `processing_environment()`의 반환값을 그대로 보존한다.
- 런타임 `ProcessingRecord.environment`(키워드 전용, 기본값 `None`)는 C가 추가한다.
- 과거 기록에 이 필드가 없거나 null이면 `None`으로 읽는다. B는 이를 `missing`(「환경 정보 없음」)으로 판정한다. 현재 환경을 과거 값처럼 채우지 않는다.

```json
{"schema": "b_processing_environment_v1",
 "libraries": {"numpy": "2.2.6", "pillow": "12.3.0", "opencv": "4.12.0"}}
```

**A의 화면 연결** ([패치](../handoff/b-p03-gaussian-ui.patch))
- 결과를 적용할 때 `viewer_processing_applied["environment"] = processing_environment(algorithm_id)`를 기록한다. 이 값은 ROI가 아니라 적용 결과에 붙는다.
- 저장 기록을 다시 열 때(`viewer_restore_controls`) `check_compatibility`를 거친 뒤에만 입력란을 채운다.
  - 버전은 복원 정보의 `algorithm_version`을 쓰고, 없으면 `viewer_stored_processing`에서 읽는다.
  - `can_restore_settings`가 거짓이면 입력란을 채우지 않고 `reasons`를 경고로 보여준다.
  - `changed`는 정보 안내로, `missing`과 `not_checked`는 캡션으로 표시한다.
- 다시 열기만으로 재실행하거나 새 버전을 저장하지 않는다. 저장 당시 결과는 그대로 표시한다.
- 새 결과를 적용하면 저장 기록에 대한 안내를 지운다.

## 4. 검증

- B 브랜치: `uv run --locked python -m unittest test_processing_compat -v` 결과 **13개 통과**. 다룬 내용은 다음과 같다.
  - 지원 조합 4가지
  - 미지원 버전과 알고리즘
  - 잘못된 설정 15가지
  - 환경 누락 4가지
  - schema 없음·미지원 6가지
  - 환경 변경과 쓰지 않는 라이브러리의 차이
  - 상태 필드의 의미 분리
  - 입력 사전을 바꾸지 않음
- A 패치를 적용한 임시 사본: `test_processing_compat_ui` 5개 통과(ROI 없이 적용한 결과의 환경 보존, 미지원 버전 복원 거절, 정상 복원, 환경 정보 없음, 환경 변경 후 새 적용)와 전체 시험 통과.
- 실제 Firestore에 환경을 저장한 뒤 다시 여는 화면은 C의 필드 추가 이후 확인한다.
