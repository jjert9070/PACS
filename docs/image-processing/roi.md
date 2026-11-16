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

`applied`는현재 입력창이 아닌 실제 적용 시 고정한 설정이다. `applied["source_id"]`가
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
  "original": {"pixel_count": 4, "mean": 127.5, "population_variance": 16256.25},
  "result": null
}
```

유효한 결과가 있으면 `result`에도 같은 세 통계 필드가 들어간다.
좌표 기준 ID는 C의 `coordinate_space` 값과 같다. 모든 값은 JSON 저장 가능한 기본 자료형이다.

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

