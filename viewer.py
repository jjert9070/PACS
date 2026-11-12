"""PNG/JPEG viewer UI and a small, explicit processing integration point."""

from dataclasses import dataclass, field
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Callable
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError
import streamlit as st


MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 20_000_000
VIEW_KEYS = ("viewer_zoom", "viewer_x", "viewer_y")


class ViewerInputError(ValueError):
    """A file cannot be safely displayed within this starter's support limits."""


@dataclass(frozen=True)
class ViewerSource:
    source_id: str
    name: str
    file_format: str
    byte_count: int
    image: Image.Image
    original_bytes: bytes = field(default=b"", repr=False)


def load_image(name: str, data: bytes) -> ViewerSource:
    """Validate the actual file, decode fully, and preserve the original bytes."""
    suffix = Path(name).suffix.lower()
    expected = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG"}.get(suffix)
    if expected is None:
        raise ViewerInputError("PNG 또는 JPEG 파일만 열 수 있습니다. DICOM은 아직 지원하지 않습니다.")
    if not data or len(data) > MAX_FILE_BYTES:
        raise ViewerInputError("비어 있는 파일이거나 파일 크기가 20 MB를 초과했습니다.")
    if expected == "PNG":
        # PNG signature + first chunk length/type, followed by 13 IHDR bytes
        # and its CRC. Bit depth follows the 4-byte width and 4-byte height.
        # Read before Pillow, which exposes 16-bit color PNG as RGB/RGBA.
        if len(data) < 33 or data[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR":
            raise ViewerInputError("PNG 헤더가 올바르지 않거나 파일이 손상되었습니다.")
        bit_depth = data[24]
        if bit_depth > 8:
            raise ViewerInputError(f"{bit_depth}비트 PNG는 지원하지 않습니다. 8비트 이하 PNG를 선택하세요.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as opened:
                if opened.format != expected:
                    raise ViewerInputError("파일 확장자와 실제 영상 형식이 다릅니다.")
                if opened.width * opened.height > MAX_PIXELS:
                    raise ViewerInputError("영상 크기는 2,000만 픽셀 이하여야 합니다.")
                if getattr(opened, "n_frames", 1) != 1:
                    raise ViewerInputError("여러 프레임 영상은 지원하지 않습니다. 단일 영상으로 선택하세요.")
                if opened.mode not in {"1", "L", "P", "RGB", "RGBA", "CMYK"}:
                    raise ViewerInputError("8비트 표시용 영상을 선택하세요. 고비트 영상은 아직 지원하지 않습니다.")
                opened.load()
                image = ImageOps.exif_transpose(opened).convert("RGB")
    except ViewerInputError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
        raise ViewerInputError("영상 파일을 읽을 수 없습니다. 손상 여부와 파일 형식을 확인하세요.") from error
    return ViewerSource(sha256(data).hexdigest(), name, expected, len(data), image, data)


def sample_image_bytes() -> bytes:
    """Generate a deterministic grayscale test image without patient data."""
    width, height = 640, 480
    pixels = bytes(
        235 if 220 <= x < 420 and 140 <= y < 340 else x * 255 // (width - 1)
        for y in range(height) for x in range(width)
    )
    stream = BytesIO()
    Image.frombytes("L", (width, height), pixels).save(stream, format="PNG")
    return stream.getvalue()


def viewport_box(size: tuple[int, int], zoom: float, x: int, y: int) -> tuple[int, int, int, int]:
    """Return original-image pixel bounds; x/y select a position in available travel."""
    if not 1 <= zoom <= 4 or not 0 <= x <= 100 or not 0 <= y <= 100:
        raise ValueError("Invalid viewing position or zoom")
    width, height = size
    view_width, view_height = max(1, round(width / zoom)), max(1, round(height / zoom))
    left = round((width - view_width) * x / 100)
    top = round((height - view_height) * y / 100)
    return left, top, left + view_width, top + view_height


def reset_view() -> None:
    for key, value in zip(VIEW_KEYS, (1.0, 50, 50)):
        st.session_state[key] = value


def clear_viewer() -> None:
    """Drop image and uploader state on logout or session expiry."""
    for key in list(st.session_state):
        if key.startswith("viewer_"):
            del st.session_state[key]


def render_viewer(
    processor: Callable[[Image.Image], Image.Image] | None = None,
    *,
    stored_source: ViewerSource | None = None,
    stored_result: Image.Image | None = None,
) -> ViewerSource | None:
    """Processor receives a COPY of the RGB source and must return the same size.

    No processing result or image is persisted to Firebase in this starter.
    The callback is run against the current image on each Streamlit rerun.
    """
    st.subheader("PACS 뷰어")
    st.caption("교육용 영상 검토 · 원본을 보존하고 같은 위치의 처리 결과를 비교합니다.")
    selection, canvas, details = st.columns([1.15, 3.3, 1.15], gap="medium")
    with selection:
        st.markdown("#### 영상 선택")
        st.caption("PNG · JPEG / 20 MB · 2,000만 픽셀 이하")
        mode = st.radio("영상 가져오기", ["파일 가져오기", "합성 예시", "로컬 저장 영상"], key="viewer_mode")
    source = None
    try:
        if mode == "합성 예시":
            source = load_image("synthetic-sample.png", sample_image_bytes())
            with selection:
                st.caption("명암과 사각형으로 만든 합성 예시입니다. 의료영상이 아닙니다.")
        elif mode == "로컬 저장 영상":
            source = stored_source
            with selection:
                st.caption("아래 개발용 로컬 원본 저장과 재열기 영역에서 저장한 원본을 선택하세요.")
        else:
            with selection:
                uploaded = st.file_uploader("PNG/JPEG 영상 열기", type=["png", "jpg", "jpeg"],
                                            max_upload_size=20, key="viewer_upload")
                st.caption("공개 또는 합성 영상을 사용하세요.")
            if uploaded is not None:
                source = load_image(uploaded.name, uploaded.getvalue())
    except ViewerInputError as error:
        with canvas:
            st.error(str(error))

    identity = (mode, source.source_id, source.name) if source else None
    if st.session_state.get("viewer_identity") != identity:
        reset_view()
        st.session_state["viewer_identity"] = identity
    if source is None:
        with canvas:
            st.info("영상을 선택하면 원본을 표시합니다. 파일 없이 확인하려면 합성 예시를 선택하세요.")
        with details:
            st.markdown("#### 영상 정보")
            st.caption("선택된 영상이 없습니다.")
        return None

    width, height = source.image.size
    with details:
        st.markdown("#### 영상 정보")
        st.text(source.name)
        st.caption(f"{source.file_format} · {width} × {height} px")
        st.caption(f"파일 크기: {source.byte_count:,} bytes")
        st.caption(f"영상 ID: {source.source_id[:12]}")
        st.divider()
        st.markdown("#### 표시 상태")
        st.success("영상 표시 준비 완료")
        st.caption("파일 저장 여부는 아래 저장 영역에서 확인하세요.")
        st.caption("픽셀 단위 표시 · 실제 길이 측정 미지원")
    with canvas:
        st.button("보기 초기화", on_click=reset_view)
        controls = st.columns(3)
    with controls[0]:
        zoom = st.slider("확대 배율", 1.0, 4.0, value=None, step=0.25, key="viewer_zoom", format="%.2fx")
    with controls[1]:
        x = st.slider("가로 위치", 0, 100, value=None, key="viewer_x", disabled=zoom == 1)
    with controls[2]:
        y = st.slider("세로 위치", 0, 100, value=None, key="viewer_y", disabled=zoom == 1)
    box = viewport_box(source.image.size, zoom, x, y)
    with canvas:
        st.caption(f"표시 영역: x=[{box[0]}, {box[2]}), y=[{box[1]}, {box[3]}) px · 두 창은 같은 위치와 배율을 사용합니다.")
        original, processed = st.columns(2)
    # Limit only the display raster, never the processor's original-size input.
    def display(image: Image.Image) -> None:
        preview = image.crop(box)
        preview.thumbnail((900, 900))
        st.image(preview, width="stretch")

    with original:
        st.markdown("**원본 영상**")
        display(source.image)
    with processed:
        st.markdown("**처리 결과**")
        if processor is None:
            if mode == "로컬 저장 영상" and stored_result is not None and stored_result.size == source.image.size:
                display(stored_result)
                st.caption("이 케이스에 저장된 처리 결과입니다.")
            else:
                st.info("처리 결과가 없습니다. 영상처리 기능을 연결하면 같은 영상의 결과를 여기에 표시합니다.")
        else:
            try:
                result = processor(source.image.copy())
                if not isinstance(result, Image.Image) or result.size != source.image.size:
                    raise ValueError("처리 결과는 원본과 같은 크기의 PIL 이미지여야 합니다.")
                display(result)
            except Exception:
                st.error("영상 처리에 실패했습니다. 원본은 유지됩니다. 처리 입력과 결과 크기를 확인하세요.")
    st.caption("현재 보기는 세션 상태입니다. 원본 파일 저장과 케이스 연결 상태는 아래에서 별도로 확인합니다.")
    return source
