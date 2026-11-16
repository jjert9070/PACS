# P02: ROI 선택과 통계

로그인한 사용자가 기존 웹 뷰어에서 사각형을 숫자로 지정하고 원본/처리 결과의
같은 영역을 비교한다. `roi.py`는 화면 없는 검증/계산, `roi_ui.py`는 입력과 표시를 맡는다.
가우시안 실행, 보기 조작과 저장 기능은 각 담당 코드의 기존 계약을 유지한다.

## 좌표와 계산 기준

- 방향 보정된 원본의 정수 픽셀 좌표 `(left, top, right, bottom)`를 사용한다.
  원점은 좌상단이며 오른쪽/아래 끝은 제외한다.
- `0 ≤ left < right ≤ width`, `0 ≤ top < bottom ≤ height`를 검사한다.
  빈 영역, 역전, 음수, 범위 밖, 실수/불리언 좌표는 거절한다. 자동 자르기는 하지 않는다.
- 원본과 결과를 각각 Pillow `convert("L")`로 변환하고 NumPy `float64` 배열로 계산한다.
  평균은 `np.mean`, 모집단 분산은 `np.var(ddof=0)`이다. RGB 채널별 분산이나 표본 분산이 아니다.
- 현재 계약은 비어 있지 않은 RGB PIL 이미지다. 결과가 있으면 원본과 같은 크기여야 한다.
  현재 지원하는 8비트 표시 영상의 L 밝기이며 DICOM 원본값, HU, 물리 길이로 해석하지 않는다.
- 검은색 2개와 흰색 2개인 2×2 영역: 픽셀 수 4, 평균 127.5, 모집단 분산 16256.25.
  한 픽셀 영역의 분산은 0이다.

## 함수와 화면

`validate_roi(bounds, image.size)`는 검증된 Python 정수 튜플을 반환한다.
`brightness_statistics(image, bounds)`는 픽셀 수/평균/모집단 분산을 반환한다.
`compare_roi(original, result_or_none, bounds)`는 두 영상의 같은 영역 통계를 반환한다.
선택 안 함은 화면 안내로 구분하고, 결과 없음은 `result: None`으로 표현한다. 0으로 채우지 않는다.

적용한 좌표만 화면과 통계에 사용한다. 숫자를 편집한 뒤 **ROI 적용**을 눌러야 갱신된다.
잘못된 좌표를 적용하면 이전 선택과 통계를 제거하고 올바른 범위를 알린다.
**ROI 해제**, 다른 원본(이름이 같아도 내용 해시가 다른 파일 포함), 로그아웃 때 선택을 정리한다.
가우시안 재적용 시 현재 결과로 통계를 다시 계산하며, 처리 실패 시 원본 통계만 남긴다.

`viewer.py`에는 ROI 입력/요약 콜백을 추가했다. 테두리는 표시용 복사본에만 그린 뒤
기존 `viewport_box`와 같은 자르기/축소를 두 창에 적용한다. 원본과 처리 결과 픽셀은 바꾸지 않는다.
확대/이동/보기 초기화는 기존 좌표 변환을 그대로 쓰며 통계는 화면 자르기가 아닌 원본 ROI에서 계산한다.
작은 1픽셀 영역은 확대해서 확인할 수 있다. ROI가 화면 밖이면 테두리도 화면 밖에 있다.
마우스 드래그 도구는 이번 범위가 아니다.

## C 담당에게 전달할 형식

현재 C의 `case_records.ProcessingRecord`와 호환되는 전달 예시다.
실제 저장 호출이나 자동 저장은 이번 PR에 연결하지 않는다.

```python
statistics = compare_roi(source.image, result_image, bounds)
statistics["source_id"] = source.source_id
record = ProcessingRecord(
    source_id=source.source_id,
    algorithm_id=applied["algorithm_id"],
    algorithm_version=applied["algorithm_version"],
    parameters={"radius": applied["radius"]},
    result=result_image.copy(),
    roi=validate_roi(bounds, source.image.size),
    statistics=statistics,
)
```

`applied`는 현재 입력창이 아닌 실제 적용 시 고정한 설정이다. `applied["source_id"]`가
현재 원본 ID와 같은지 먼저 확인해야 한다. 결과는 같은 크기의 RGB PIL 이미지이며
표시용 테두리를 포함하지 않는다. `radius`는 원본 픽셀 단위 표준편차다.
결과가 없는 경우 ProcessingRecord를 만들지 않는다. 원본 통계만 보여 줄 수 있다.
저장된 과거 결과를 화면에 열었을 때도 통계를 계산할 수 있지만 새 알고리즘 정보로 바꿔 쓰면 안 된다.

통계 필드 구조:

```json
{
  "bounds": [0, 0, 2, 2], "width": 2, "height": 2,
  "coordinate_system": "exif_transposed_original_pixels_v1",
  "brightness_method": "pillow_L_v1",
  "calculation": {
    "numpy": "2.2.6", "pillow": "12.3.0", "brightness_method": "pillow_L_v1",
    "variance_definition": "population", "ddof": 0, "calculation_dtype": "float64"
  },
  "original": {"pixel_count": 4, "mean": 127.5, "population_variance": 16256.25},
  "result": null
}
```

유효한 결과가 있으면 `result`에도 같은 세 통계 필드가 들어간다.
좌표 기준 ID는 C의 `coordinate_space` 값과 같다. 모든 값은 JSON 저장 가능한 기본 자료형이다.
`calculation`은 실행 시 사용 중인 버전을 자동으로 기록한다. 위 버전은 예시이며 고정값이 아니다.
화면 콜백은 현재 원본의 `source_id`도 통계에 넣는다. 계산 함수 자체는 바이트 ID를 알지 못하므로
화면 없이 호출하는 C도 원본 ID를 함께 보관해야 한다. 원본 너비/높이와 파일 식별자는
ViewerSource 및 C의 케이스 메타데이터에서 전달하며, 현재 C의 결과 인코딩은 RGB PNG다.

## 고정 ROI 비교 실험과 해석

실행: `uv run --locked python experiments/roi_experiment.py`

[48개 조건의 수치](results/roi-gaussian/metrics.csv), [조건과 결과 해시](results/roi-gaussian/manifest.json),
[요약](results/roi-gaussian/summary.md), [비교 이미지](results/roi-gaussian/comparison.png)를 함께 보관한다.
외부 파일 없이 직접 생성한 256×256 합성 영상에서 같은 좌표를 모든 조건에 사용한다.

| 영역 | 좌표 (left,top,right,bottom) | 크기 | 목적 |
|---|---|---|---|
| 평탄부 | (16,64,48,192) | 32×128 | 잡음 흔들림의 감소 |
| 경계 | (112,64,144,192) | 32×128 | 경계 평활화와 정보 손실 |
| 얇은 선 | (52,64,70,192) | 18×128 | 작은 구조가 흐려지는 조건 |

잡음 표준편차 0/5/20/40, 가우시안 σ=0/1/2/4를 비교한다. PCG64와 시드를 고정하고
각 잡음 강도마다 같은 표준정규 패턴을 사용한다. RGB 잡음을 추가한 뒤 모두 같은 Pillow L로
통계를 계산하므로, 설정한 RGB 잡음 σ를 L 밝기의 실제 표준편차라고 해석하지 않는다.
잡음은 float64로 더하고 `np.rint`와 0~255 제한 후 uint8로 변환한다. 이 규칙은 현재 8비트 실험용이다.
OpenCV가 가우시안 처리·비교판 크기 조절·PNG 저장을, NumPy가 잡음·통계·RMSE를 맡는다.

평탄부에서는 분산 감소가 잡음 완화의 근거가 될 수 있다. 반면 경계와 얇은 선의 분산에는
원래 구조 차이가 포함된다. 분산이 작아져도 구조가 사라진 결과일 수 있다.
같은 ROI의 깨끗한 기준 영상과 L RMSE를 함께 기록해서 단순 분산 감소와 구별한다.
잡음 없는 경계에 필터를 적용하면 원본에 없던 흐림이 생긴다. 강도 0에서는 입력/결과 통계가 같다.
결과 수치의 강도별 해석은 요약 표와 함께 확인하며, 합성 도형 결과를 Lenna나 임상 성능으로 일반화하지 않는다.

이번 잡음 20 조건에서 평탄부 분산은 σ=0의 179.839462에서 σ=4의 0.919212로 줄었다.
하지만 얇은 선의 L RMSE는 13.269787에서 41.387723으로 커졌다. 잡음이 줄어드는 것과
얇은 구조를 잘 보존하는 것은 다른 판단이다. 경계 ROI의 L RMSE는 σ=1에서 8.301773,
σ=4에서 16.335637이었다. 시험한 조건 안의 결과이며 모든 영상의 최적 강도라는 뜻은 아니다.

## P02 완료 범위와 통합 경계

숫자 ROI 입력, 범위 검증, 동일 영역 테두리, L 평균/모집단 분산, 결과 없음 구분,
영상 교체/로그아웃 정리, 확대/이동/보기 초기화 검증까지가 현재 완료 범위다.
입력 파일의 EXIF 회전·반전 7가지에 대해서도 방향 보정 후 원본 좌표로 계산되는지 자동 검사한다.
이는 **뷰어에서 사용자가 회전·반전 버튼을 조작한 검증과 다르다.**

현재 A 브랜치에도 사용자 회전·반전 UI가 없다. 해당 표시 변환이 연결되면 같은 원본 ROI의
테두리가 양쪽 창에서 대응하고 통계가 유지되는지 A와 통합 검증해야 한다.
C의 실제 저장·새 세션 재열기는 P03/통합 단계다. 이번 PR은 계산 정보와 전달 형식을 보완하며
Firestore 규칙, 저장 화면, 재열기 동작을 변경하지 않는다. 마우스 드래그와 히스토그램은 추가하지 않는다.
안내문의 ROI 요구에는 선택한 평균·분산 통계를 사용하며, 알고리즘 3종 전체 완료를 주장하지 않는다.

## 검증

`uv run --locked python -m unittest discover -v`로 전체 회귀 테스트를 실행한다.
좌표/색상 변환/2×2 기준값/1픽셀/결과 없음/결과 크기 검증과
Streamlit UI의 적용/해제/잘못된 영역/확대·이동/원본 유지/영상 교체/처리 실패/로그아웃을 검사한다.
실제 앱 로그아웃 테스트에도 ROI 상태 정리를 포함한다.

2026-11-16 Windows / Python 3.12.14 / NumPy 2.2.6 / Pillow 12.3.0 /
OpenCV 4.12.0 / Streamlit 1.55.0에서 전체 **81개 테스트 통과**.
Edge에서 실제 `app.main`과 ROI 화면을 직접 조작했다. Firebase 모의 로그인과
검증 전용 원본 선택/실패 주입을 제공하는 Git 무시 경로의 로컬 스크립트를 사용했다.
운영 앱의 인증 코드는 변경하지 않았으며 실제 Firebase 인증·권한·저장은 검증하지 않았다.
확장 프로그램 파일 접근 제한으로 브라우저 업로드 대신 동일한 `load_image`로 읽은
2×2 검증 PNG와 앱의 합성 예시를 사용했다.

| 직접 조작 | 확인 결과 | 화면 |
|---|---|---|
| 2×2 흑백 원본, 전체 ROI, 가우시안 σ=0 | 양쪽 픽셀 수 4, 평균 127.5, 모집단 분산 16256.25 | [01](roi-checks/01-checker-zero.jpg) |
| right=0으로 빈 영역 적용 | 범위 오류, 기존 ROI/통계 제거 | [02](roi-checks/02-invalid.jpg) |
| 640×480 합성 예시로 교체 | ROI 미선택 안내, 이전 결과 제거, 새 좌표 기본값 | [03](roi-checks/03-source-changed.jpg) |
| ROI=(100,80,400,300), σ=2 적용 | 같은 테두리, 양쪽 66,000픽셀과 통계 비교 | [04](roi-checks/04-normal-statistics.jpg) |
| 확대 1.25배, 가로/세로 이동 후 초기화 | ROI 통계 유지, 두 창 동일 변환 | [05](roi-checks/05-view-transform.jpg) |
| 처리 예외 주입 후 재적용 | 오류, 원본 통계 유지, 결과 통계 없음 | [06](roi-checks/06-processing-failure.jpg) |
| 로그아웃 | 로그인 화면 복귀; ROI 상태 제거는 실제 앱 자동 테스트에서도 확인 | [07](roi-checks/07-logout.jpg) |

이 PR은 P01 `feature/gaussian-web`을 기반으로 한 별도 변경이다. P01 병합 후 대상 브랜치를
팀 통합 브랜치로 조정할 수 있다. 직접 병합하지 않고 팀원 확인을 받는다.

### ROI 보완 검증 (2026-11-16)

초기 P02의 81개에 6개 테스트를 추가하여 전체 **87개 통과**.
추가 검증은 네 가장자리·끝 픽셀과 원본/결과 보존, EXIF 방향 보정 7가지,
C 기록 계약의 JSON 직렬화와 다른 원본 거절, 같은 이름의 다른 내용 입력 교체,
잘못된 크기 결과의 이전 통계 차단, 48개 ROI 실험 조건이다.
C 계약 테스트는 실제 저장이나 새 세션 재열기 검증을 대신하지 않는다.
통계의 `source_id`는 현재 화면에서 추가하며, NumPy/Pillow 버전과 계산 정의는
`compare_roi` 반환값에 자동 포함한다. 이전 화면 캡처의 숫자 계산 기준은 그대로 유지한다.

