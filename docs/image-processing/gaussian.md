# 가우시안 블러 2.0.0: 구현과 비교 실험

이 문서는 담당 B가 구현한 가우시안 처리 함수의 계약과 실험 방법을 설명한다. 실행부터 따라 하려면 [최상단 README](../../README.md)의 담당 B 안내를, 저장된 그림과 CSV를 읽으려면 [결과 폴더 README](results/README.md)를 먼저 본다.

## 1. 구현한 범위

- NumPy로 픽셀 배열과 잡음, 지표 계산을 다루고 OpenCV로 가우시안 처리와 실험 이미지 입출력을 수행한다. 처리 단계를 함수별로 나눠 읽을 수 있도록 구성했다.
- 외부 입출력은 기존 계약을 유지한다. RGB PIL 이미지 전체를 받아 같은 크기의 새 RGB PIL 이미지를 반환하며 원본은 바꾸지 않는다.
- 합성 도형과 외부 8비트 입력을 대상으로 잡음 강도와 처리 강도를 바꾸어 비교한다.
- 후속 P01에서 [웹 강도 조절과 적용](gaussian-web.md)을 연결했다. 브라우저 수동 검증 상태는 해당 문서를 따른다. 처리 결과의 케이스 저장, 사용자 ROI와 DICOM 읽기는 아직 포함되지 않는다.
- 가우시안 하나를 구현했다고 해서 과제의 알고리즘 3개와 ROI 요구를 모두 충족한 것은 아니다.

## 2. 가우시안의 목적과 강도

가우시안 블러는 주변 픽셀을 섞어 작은 밝기 변화를 완만하게 만든다. 중심에 가까운 픽셀에 더 큰 가중치를 주므로 잡음이 줄어들 수 있다. 경계와 얇은 구조도 함께 섞이므로 강도를 높일수록 항상 좋은 결과가 나오는 것은 아니다.

이 함수에서 `radius`는 커널의 반지름 자체가 아니라 가우시안 표준편차인 $\sigma$를 뜻한다. 단위는 원본 영상의 픽셀이다. 확대된 화면의 픽셀이나 실제 길이 단위가 아니다.

가중치의 상대적인 크기는 다음과 같다. $x$와 $y$는 중심에서 떨어진 픽셀 거리이며, 실제 이산 커널에서는 가중치의 합이 1이 되도록 정규화한다.

$$
G(x,y)\propto\exp\left(-\frac{x^2+y^2}{2\sigma^2}\right),\qquad \sigma>0
$$

현재 구현의 커널 한 변 길이 $k$는 다음과 같다. 올림 기호는 소수 부분이 있으면 다음 정수로 올린다는 뜻이다.

$$
k=2\left\lceil 3\sigma\right\rceil+1
$$

$\sigma$가 1, 2, 4일 때 커널은 각각 7×7, 13×13, 25×25다. `radius=0`은 이 계산과 OpenCV 호출을 건너뛰고 독립 복사본을 반환한다.

## 3. 처리 함수의 계약

구현은 [image_processing.py](../../image_processing.py)에 있다.

- 호출: `gaussian_blur(image, radius=1.0)`.
- 입력: 기존 영상 로더에서 검증하고 방향을 보정한 비어 있지 않은 RGB PIL 이미지 전체.
- 출력: 입력과 같은 크기의 새 RGB PIL 이미지. 입력 이미지의 픽셀은 유지한다.
- 허용 강도: 유한한 0~20의 값. Python의 `int`, `float`와 NumPy 정수 및 실수 스칼라를 받는다.
- 거절하는 입력: 참과 거짓, 문자열, 배열, 복소수, NaN과 무한대, 범위 밖 강도, RGB가 아닌 이미지. 예를 들어 문자열 `"1"`을 숫자 1로 자동 해석하지 않는다.
- 처리: `cv2.GaussianBlur`에 같은 가로와 세로 표준편차를 전달한다.
- 경계: `cv2.BORDER_REFLECT_101`로 경계 바깥을 반사해 확장한다. 끝 픽셀을 중복하는 방식과 구분한다.
- 알고리즘 ID: 문자열 `"gaussian_blur"`.
- 알고리즘 버전: 문자열 `"2.0.0"`.

기본값 1과 상한 20은 현재 구현의 선택이며 과제가 지정한 수치가 아니다. 내부에서 RGB 배열을 복사하고 각 채널을 독립 처리하므로 블러 함수 안에서는 BGR로 바꾸지 않는다. OpenCV 파일 코덱을 사용하는 실험의 읽기와 저장 단계에서는 BGR과 RGB를 변환한다.

유한값 검사에는 `np.isfinite`, 커널 크기 계산에는 `np.ceil`, 난수 생성에는 NumPy의 PCG64를 사용한다. 함수와 실험에서 별도의 Python `random`이나 `math`를 사용하지 않는다.

현재 고정 의존성은 NumPy 2.2.6, opencv-python-headless 4.12.0.88, Pillow 12.3.0이다. OpenCV 실행 버전은 결과 명세에 4.12.0으로 기록된다. headless 패키지를 사용하므로 별도의 OpenCV GUI 창을 띄우지 않는다.

## 4. 실행 방법

모든 명령은 PACS 최상단 폴더에서 실행한다. 먼저 `uv sync --locked`로 잠금 파일에 맞춰 환경을 준비한다. 이 독립 실험에는 Firebase 계정, `.env`와 Streamlit 서버가 필요 없다.

1. 합성 도형: `uv run --locked python experiments/gaussian_experiment.py`.
2. Lenna 컬러: `uv run --locked python experiments/gaussian_experiment.py --input "./experiments/Lenna.png" --color-mode color`.
3. 같은 입력의 흑백: `uv run --locked python experiments/gaussian_experiment.py --input "./experiments/Lenna.png" --color-mode grayscale`.
4. 옵션 확인: `uv run --locked python experiments/gaussian_experiment.py --help`.
5. 전체 자동 테스트: `uv run --locked python -m unittest discover -v`.

프로젝트 환경을 이미 활성화했다면 위의 `uv run --locked python`을 `python`으로 바꿔 실행할 수 있다. `--input`을 생략하면 합성 도형을 사용한다. 외부 입력의 기본 색상 모드는 문자열 `"color"`다. Lenna 파일은 명령에 적힌 위치에 실제로 있어야 한다.

기본 출력 위치는 다음과 같다.

- 합성 도형: `docs/image-processing/results/gaussian-opencv/synthetic`.
- 외부 컬러: `docs/image-processing/results/gaussian-opencv/external-color`.
- 외부 흑백: `docs/image-processing/results/gaussian-opencv/external-grayscale`.

외부 결과 폴더는 입력 파일 이름별로 나뉘지 않는다. 다른 영상을 실행하거나 같은 실험을 다시 실행하면 같은 이름의 파일이 덮어써진다. 기록을 따로 보관하려면 명령 끝에 `--output ".local-data/gaussian-lenna-test"`와 같이 새 경로를 붙인다.

## 5. 입력과 잡음 생성

실험 코드는 [gaussian_experiment.py](../../experiments/gaussian_experiment.py)에 있다. 실행 흐름은 입력 읽기, 잡음 생성, 독립 필터 적용, 지표 계산, 비교 이미지 생성, 결과 저장 순서다.

합성 기준 영상은 왼쪽 밝기 80, 오른쪽 밝기 176과 밝기 224인 폭 2픽셀 선으로 구성된다. 외부 입력은 OpenCV로 읽고 `INTER_AREA` 방식으로 256×256으로 조정한다. 정사각형이 아닌 입력은 가로세로 비율이 달라질 수 있다. `clean.png`는 이 크기 및 색상 처리를 마친 기준 영상이며 입력 파일의 바이트 사본이 아니다.

컬러 실험은 기준 영상의 RGB를 유지하고 각 채널에 독립 잡음을 더한다. 흑백 실험은 기준부터 Pillow L 밝기로 바꾼 다음 밝기 잡음 하나를 세 채널에 동일하게 복제한다. 합성 도형은 흑백 방식으로 실행된다. 고비트 입력은 자동으로 8비트로 낮추지 않고 거절한다.

잡음 표준편차는 0, 5, 20, 40이다. 평균은 0이며 단위는 8비트 밝기값이다. 이는 필터를 시험하기 위한 인공 잡음으로, 실제 촬영 장치의 모든 잡음 특성을 재현하거나 AI 학습을 실행하는 기능을 뜻하지 않는다.

각 조건마다 시드 20261115의 `Generator(PCG64)`를 초기화해 같은 표준 잡음 패턴의 크기를 바꾼다. 먼저 `float64`에서 더하고 `np.rint`로 반올림한 뒤 0~255로 제한하고 `uint8`로 변환한다. 정확히 중간인 값은 짝수 쪽으로 반올림한다. 이 정책은 현재 8비트 PNG 실험에 한정한다.

같은 잡음 입력에 처리 강도 0, 1, 2, 4를 각각 독립 적용한다. 강도 2 결과에 다시 강도 4를 누적 적용하지 않는다. 잡음 4조건과 처리 4조건으로 각 실행에서 처리 결과 16개를 만든다.

## 6. 지표 해석

- `noise_sigma`: 인공 잡음의 표준편차. 필터의 강도와 다른 항목이다.
- `radius_px`: 가우시안 처리에 전달한 표준편차. 원본 픽셀 단위다.
- `rmse`: 기준 영상과 결과 사이의 제곱평균제곱근오차(Root Mean Square Error, RMSE). RGB 전체 채널을 실수형으로 비교한다. 0이면 비교한 픽셀이 모두 같고, 작을수록 해당 기준 영상과의 차이가 작다.
- `flat_variance`: 합성 영상의 평탄한 영역에서 측정한 밝기의 모집단 분산. 잡음으로 인한 흔들림을 관찰한다. 단위는 밝기값의 제곱이다.
- `step_contrast`: 합성 영상의 밝기 경계 양쪽 차이. 기준 영상의 값은 96이며 블러로 경계가 완만해지면 낮아질 수 있다.
- `thin_line_contrast`: 합성 영상의 얇은 선과 배경의 밝기 차이. 기준 영상의 값은 144다. 선이 흐려지는 정도를 함께 본다.
- `pillow_difference_mae`: 동일한 입력과 강도를 Pillow와 OpenCV에 적용한 두 결과의 평균 절대 차이.
- `pillow_difference_max`: 같은 두 결과의 최대 절대 차이.

평탄부는 가로 좌표 16 이상 48 미만, 세로 좌표 64 이상 192 미만이다. 경계 대비는 같은 세로 범위에서 가로 128과 127의 차이를 평균한다. 선 대비는 가로 60과 61의 평균에서 가로 52의 배경을 뺀 뒤 같은 세로 범위에서 평균한다. 좌표는 왼쪽 위가 원점이고 오른쪽과 아래로 증가한다.

이 세 구조 지표는 합성 도형의 위치를 아는 상태에서만 의미가 있으므로 외부 영상에는 계산하지 않는다. 실험의 고정 영역은 사용자 ROI 기능의 구현을 뜻하지 않는다. RMSE가 낮다는 사실만으로 임상적으로 더 적절하거나 작은 구조를 더 잘 보존한다고 판단하지 않는다.

Pillow 차이 항목은 구현을 바꾸면서 생긴 픽셀 차이를 설명한다. 기준 원본과의 오차가 아니므로 이 값만으로 어느 구현의 영상 품질이 더 좋은지 결정할 수 없다.

## 7. 현재 저장된 결과

현재 저장된 두 OpenCV 결과 명세는 Python 3.12.16, NumPy 2.2.6, OpenCV 4.12.0, Pillow 12.3.0을 기록한다. 실제 실행 환경은 각 폴더의 `manifest.json`을 기준으로 확인한다.

### 합성 도형

[비교 이미지](results/gaussian-opencv/synthetic/comparison.png), [요약](results/gaussian-opencv/synthetic/summary.md), [CSV](results/gaussian-opencv/synthetic/metrics.csv), [실험 명세](results/gaussian-opencv/synthetic/manifest.json).

잡음 20에서 처리 강도를 0에서 1로 바꾸면 RMSE는 19.951에서 7.972로, 평탄부 분산은 400.556에서 31.900으로 줄었다. 동시에 얇은 선 대비도 142.035에서 92.219로 낮아졌다. 강도 4에서는 선 대비가 26.320으로 낮아지고 RMSE가 10.871로 증가했다. 잡음 감소와 구조 보존 사이의 변화를 함께 읽어야 한다.

### Lenna 컬러

[비교 이미지](results/gaussian-opencv/external-color/comparison.png), [요약](results/gaussian-opencv/external-color/summary.md), [CSV](results/gaussian-opencv/external-color/metrics.csv), [실험 명세](results/gaussian-opencv/external-color/manifest.json).

이 폴더의 명세에는 입력 파일 이름이 문자열 `"Lenna.png"`, 색상 모드가 문자열 `"color"`로 기록되어 있다. 잡음 20에서 처리 강도 0, 1, 2, 4의 RMSE는 각각 19.725, 9.702, 13.162, 18.893이다. 시험한 네 강도 중에서는 1이 가장 작았다는 뜻이며, 모든 영상과 모든 잡음에 대한 최적값을 뜻하지 않는다. 흑백 외부 입력 결과는 해당 실행 명령을 수행한 뒤 확인한다.

### 변경 전 기록과 비교할 때

[gaussian 폴더](results/gaussian/)는 Pillow 1.0.0 결과를 보관하는 기존 위치다. 현재 비교판은 Lenna 컬러 기준과 흑백 처리 결과가 섞여 있으므로 새 컬러 실험과 수치를 바로 비교하지 않는다. 과거 고정 좌표의 구조 지표도 Lenna의 품질 지표로 해석하지 않는다.

버전 변경 과정에서 난수 생성기, 색상 처리와 크기 조정 및 오차 계산 기준이 달라졌다. 같은 시드 숫자를 사용해도 잡음 픽셀이 같다고 볼 수 없다. 구현 차이만 확인하려면 현재 CSV의 Pillow 차이 항목처럼 같은 입력을 두 구현에 전달해 비교한다.

## 8. 재현 기록

`manifest.json`에는 알고리즘 버전, 실행 환경, 난수 생성기와 시드, 색상 모드, 입력 해시, 크기 조정, 반올림, 커널, 경계 처리와 PNG 결과 해시가 기록된다. `metrics.csv`는 계산 정밀도를 유지하고 `summary.md`는 소수 셋째 자리까지 표시한다.

현재 텍스트 출력은 BOM 없는 UTF-8이다. 요약과 명세는 LF, CSV는 CRLF 줄바꿈을 사용한다. 입력과 라이브러리 환경이 바뀌면 결과를 다시 기록한다. 픽셀이 같더라도 PNG 압축이나 메타데이터, 라이브러리 빌드가 달라지면 파일 바이트의 SHA-256은 달라질 수 있다. 모든 환경에서 파일 해시가 일치한다고 보장하지 않는다.

## 9. 테스트와 A, B, C 통합

확인한 통합 브랜치의 전체 자동 테스트는 63개가 통과했다. 테스트 개수는 이후 커밋에서 바뀔 수 있으므로 현재 상태는 위의 전체 테스트 명령으로 확인한다. 모의 응답과 AppTest를 포함한 결과이며 실제 Firebase 규칙 적용이나 JCloud 배포를 검증한 것은 아니다.

자동 테스트는 원본 보존, 강도 0의 독립 복사, 크기와 RGB 채널 유지, 작은 영상과 잘못된 입력, 뷰어 콜백, 커널과 반사 경계, 실수형 오차 계산, 시드 재현과 범위 제한, 컬러 PNG 입출력, 흑백 기준 변환과 16비트 거절 등을 다룬다.

- A의 `feature/viewer-controls`: 원본 크기의 RGB PIL 이미지 복사본과 설정한 처리 강도를 B 함수에 전달하는 화면 연결을 담당한다. 표시용 확대와 이동을 처리 입력에 적용하지 않는다.
- B의 `feature/image-processing`: 독립 계산 함수와 비교 실험을 제공한다. 다음 ROI 작업은 명세 P02의 원본 좌표 검증과 통계 계산을 화면 및 Firebase 없이 시험할 수 있도록 분리한다.
- C의 `feature/case-management`: 원본 `source_id`, 알고리즘 ID와 버전, 강도와 결과 및 필요한 ROI 정보를 케이스 기록에 연결한다. 계산 함수가 UID나 저장 권한을 결정하지 않는다.

A와 C는 브랜치 이름만을 뜻하지 않는다. 각각 화면 조작과 케이스 저장을 맡은 담당 범위이며 위 경계에서 함수를 연결한다. 현재 `app.py`는 `gaussian_blur`를 `render_viewer`의 `processor`로 전달하지 않는다. 가우시안 버튼, 처리 이력 저장과 ROI 화면을 완성된 기능으로 안내하지 않는다.

## 10. 남은 범위

현재 함수와 실험은 8비트 표시 영상용이다. DICOM의 원본 자료형, CT 값과 물리 좌표를 보존하는 처리는 별도 설계가 필요하다. pydicom, CT 값 변환, 물리 길이 측정과 AI 판정은 추가하지 않았다. 실제 임상 영상의 유용성도 이 실험으로 검증하지 않았다.

외부 시험 영상은 프로젝트에서 사용을 허용받은 자료를 준비한다. 특정 파일로 실험했다는 사실은 그 파일의 자유 배포 권한을 뜻하지 않는다.

## 기존에 참고한 공식 문서

- [OpenCV GaussianBlur](https://docs.opencv.org/4.12.0/d4/d86/group__imgproc__filter.html).
- [NumPy Generator](https://numpy.org/doc/2.2/reference/random/generator.html).
- [Pillow GaussianBlur](https://pillow.readthedocs.io/en/stable/reference/ImageFilter.html#PIL.ImageFilter.GaussianBlur).
