"""PNG/JPEG viewer UI and a small, explicit processing integration point."""

from dataclasses import dataclass, field
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Callable
import warnings

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError
import streamlit as st


MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 20_000_000
TONE_MIN = 50
TONE_MAX = 150
TONE_DEFAULT = 100
VIEW_DEFAULTS = {
    "viewer_zoom": 1.0,
    "viewer_x": 50,
    "viewer_y": 50,
    "viewer_turns": 0,
    "viewer_flip_horizontal": False,
    "viewer_flip_vertical": False,
    "viewer_brightness": TONE_DEFAULT,
    "viewer_contrast": TONE_DEFAULT,
}

# Pillow names rotation constants by their counterclockwise angles.
CLOCKWISE_ROTATION = (
    None, Image.Transpose.ROTATE_270, Image.Transpose.ROTATE_180, Image.Transpose.ROTATE_90
)


class ViewerInputError(ValueError):
    """A file cannot be safely displayed within this starter's support limits."""


class ProcessingPending(Exception):
    """A processor has no applied result yet; display guidance, not an error."""


@dataclass(frozen=True)
class ViewerSource:
    source_id: str
    name: str
    file_format: str
    byte_count: int
    image: Image.Image
    original_bytes: bytes = field(default=b"", repr=False, kw_only=True)


@dataclass(frozen=True)
class ViewTransform:
    """Crop in source coordinates, then rotate and flip along the display axes."""

    box: tuple[int, int, int, int]
    turns: int = 0
    flip_horizontal: bool = False
    flip_vertical: bool = False

    def apply(self, image: Image.Image) -> Image.Image:
        preview = image.crop(self.box)
        rotation = CLOCKWISE_ROTATION[self.turns % 4]
        if rotation is not None:
            preview = preview.transpose(rotation)
        if self.flip_horizontal:
            preview = preview.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if self.flip_vertical:
            preview = preview.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        return preview


def adjust_display_tone(preview: Image.Image, brightness: int, contrast: int) -> Image.Image:
    """Apply the same 8-bit mapping to both panes, using a fixed contrast midpoint."""
    if brightness == contrast == TONE_DEFAULT:
        return preview

    brightness_factor = brightness / 100
    contrast_factor = contrast / 100
    midpoint = 127.5
    table = [
        max(0, min(255, round((value * brightness_factor - midpoint) * contrast_factor + midpoint)))
        for value in range(256)
    ]
    if preview.has_transparency_data:
        return preview.convert("RGBA").point(table * 3 + list(range(256)))
    if preview.mode == "L":
        return preview.point(table)
    return preview.convert("RGB").point(table * 3)


def load_image(name: str, data: bytes) -> ViewerSource:
    """Validate the actual file, decode fully, and preserve the original bytes."""
    if not isinstance(name, str) or not isinstance(data, bytes):
        raise ViewerInputError("파일명은 문자열, 원본 내용은 바이트여야 합니다.")
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
    return ViewerSource(sha256(data).hexdigest(), name, expected, len(data), image, original_bytes=data)


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
    st.session_state.update(VIEW_DEFAULTS)


def rotate_view() -> None:
    st.session_state.viewer_turns = (st.session_state.viewer_turns + 1) % 4


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
    stored_result_source_id: str | None = None,
    processing_controls: Callable[[ViewerSource], None] | None = None,
    roi_controls: Callable[[ViewerSource], tuple[int, int, int, int] | None] | None = None,
    roi_summary: Callable | None = None,
    processing_record=None,
    stored_processing=None,
    allow_source_change: Callable[[], bool] | None = None,
) -> ViewerSource | None:
    """Processor receives a COPY of the RGB source and must return the same size.

    This view does not write storage; the app submits its displayed record explicitly.
    The callback is run against the current image on each Streamlit rerun.
    """
    st.session_state.pop("viewer_displayed_processing", None)
    st.session_state.pop("viewer_displayed_result", None)
    st.session_state.pop("viewer_processing_contract_error", None)
    st.subheader("PACS 뷰어")
    st.caption("교육용 영상 검토 · 원본을 보존하고 같은 위치의 처리 결과를 비교합니다.")
    selection, canvas, details = st.columns([1.15, 3.3, 1.15], gap="medium")
    with selection:
        st.markdown("#### 영상 선택")
        st.caption("PNG · JPEG / 20 MB · 2,000만 픽셀 이하")
        mode = st.radio("영상 가져오기", ["파일 가져오기", "합성 예시", "로컬 저장 영상"], key="viewer_mode")
    source = None
    input_failed = False
    try:
        if mode == "합성 예시":
            source = load_image("synthetic-sample.png", sample_image_bytes())
            with selection:
                st.caption("명암과 사각형으로 만든 합성 예시입니다. 의료영상이 아닙니다.")
        elif mode == "로컬 저장 영상":
            if stored_source is not None:
                source = load_image(stored_source.name, stored_source.original_bytes)
                if source.source_id != stored_source.source_id:
                    raise ViewerInputError("저장 원본의 참조와 파일 해시가 일치하지 않습니다.")
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
        source = None
        input_failed = True
        with canvas:
            st.error(str(error))

    identity = source.source_id if source else None
    previous = st.session_state.get("viewer_active_source")
    if (identity != st.session_state.get("viewer_identity") and previous is not None
            and not input_failed and allow_source_change is not None and not allow_source_change()):
        source = previous
        identity = source.source_id
        mode = st.session_state.get("viewer_active_mode", mode)
        with canvas:
            st.warning("저장하지 않은 변경이 있습니다. 케이스 내용을 저장하거나 C의 폐기 확인을 선택한 뒤 영상을 전환하세요.")
    st.session_state["viewer_active_source"] = source
    st.session_state["viewer_active_mode"] = mode
    if st.session_state.get("viewer_identity") != identity:
        reset_view()
        for key in list(st.session_state):
            if key.startswith(("viewer_processing_", "viewer_roi_")):
                del st.session_state[key]
        st.session_state.pop("viewer_current_processing", None)
        processing_record = None
        st.session_state["viewer_identity"] = identity
    if source is None:
        with canvas:
            st.info("영상을 선택하면 원본을 표시합니다. 파일 없이 확인하려면 합성 예시를 선택하세요.")
        with details:
            st.markdown("#### 영상 정보")
            st.caption("선택된 영상이 없습니다.")
        return None

    from case_processing import validate_processing, show_processing

    record = processing_record
    saved = record is None and mode == "로컬 저장 영상"
    if saved:
        record = stored_processing
    if record is not None:
        try:
            record = validate_processing(source, record)
        except ValueError as error:
            message = str(error)
            st.session_state["viewer_processing_contract_error"] = message
            with canvas:
                st.error(message)
            record = None
            stored_result = None
    width, height = source.image.size
    if processing_controls is not None:
        with selection:
            processing_controls(source)
    roi_bounds = None
    if roi_controls is not None:
        with selection:
            roi_bounds = roi_controls(source)
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
        st.button("보기 초기화", key="viewer_reset", on_click=reset_view)
        controls = st.columns(3)
    with controls[0]:
        zoom = st.slider("확대 배율", 1.0, 4.0, value=None, step=0.25, key="viewer_zoom", format="%.2fx")
    with controls[1]:
        x = st.slider("가로 위치", 0, 100, value=None, key="viewer_x", disabled=zoom == 1)
    with controls[2]:
        y = st.slider("세로 위치", 0, 100, value=None, key="viewer_y", disabled=zoom == 1)
    box = viewport_box(source.image.size, zoom, x, y)
    with canvas:
        rotate, horizontal, vertical = st.columns(3)
        with rotate:
            st.button("시계 방향 90° 회전", key="viewer_rotate", on_click=rotate_view)
        with horizontal:
            flip_horizontal = st.toggle("좌우 반전", key="viewer_flip_horizontal")
        with vertical:
            flip_vertical = st.toggle("상하 반전", key="viewer_flip_vertical")
        turns = st.session_state.viewer_turns
        transform = ViewTransform(box, turns, flip_horizontal, flip_vertical)
        st.caption(f"표시 밝기·대비 · 기본 {TONE_DEFAULT}% · 범위 {TONE_MIN}~{TONE_MAX}%")
        brightness_column, contrast_column = st.columns(2)
        with brightness_column:
            brightness = st.slider("표시 밝기", TONE_MIN, TONE_MAX, value=None, step=5,
                                   key="viewer_brightness", format="%d%%")
        with contrast_column:
            contrast = st.slider("표시 대비", TONE_MIN, TONE_MAX, value=None, step=5,
                                 key="viewer_contrast", format="%d%%")
        st.caption(f"원본 표시 영역: x=[{box[0]}, {box[2]}), y=[{box[1]}, {box[3]}) px")
        horizontal_status = "켜짐" if flip_horizontal else "꺼짐"
        vertical_status = "켜짐" if flip_vertical else "꺼짐"
        st.caption(f"두 창 공통 보기 · 회전 {turns * 90}° · 좌우 반전 {horizontal_status} · 상하 반전 {vertical_status}")
        original, processed = st.columns(2)
    active_bounds = roi_bounds if roi_controls is not None else (record.roi if record else None)
    if active_bounds is not None:
        from roi import validate_roi
        try:
            active_bounds = validate_roi(active_bounds, source.image.size)
        except (TypeError, ValueError) as error:
            with canvas:
                st.error(str(error))
            active_bounds = None
            roi_bounds = None
    # Limit only the display raster, never the processor's original-size input.
    def display(image: Image.Image) -> None:
        bounds = active_bounds
        marked = image
        if bounds is not None:
            # PIL endpoints are inclusive; stored and active ROI ends are exclusive.
            left, top, right, bottom = bounds
            marked = image.copy()
            ImageDraw.Draw(marked).rectangle(
                (left, top, right - 1, bottom - 1), outline=(255, 210, 0), width=1,
            )
        preview = transform.apply(marked)
        preview.thumbnail((900, 900))
        st.image(adjust_display_tone(preview, brightness, contrast), width="stretch")

    displayed_result = None
    with original:
        st.markdown("**원본 영상**")
        display(source.image)
    with processed:
        st.markdown("**처리 결과**")
        # Reserve the image slot first: processor captions/notices then render
        # below the result, so both images start at the same height.
        image_slot = st.container()
        if st.session_state.get("viewer_processing_contract_error"):
            st.info("현재 원본과 일치하는 처리 결과가 없습니다.")
        elif record is not None and (processor is None or processing_record is not None):
            with image_slot:
                display(record.result)
            displayed_result = record.result
            show_processing(record, saved=saved)
            st.session_state["viewer_displayed_processing"] = record
        elif processor is None:
            if (mode == "로컬 저장 영상" and stored_result is not None
                    and stored_result_source_id == source.source_id
                    and stored_result.size == source.image.size):
                with image_slot:
                    display(stored_result)
                displayed_result = stored_result
                st.caption("이 케이스에 저장된 처리 결과입니다.")
            else:
                st.info("처리 결과가 없습니다. 영상처리 기능을 연결하면 같은 영상의 결과를 여기에 표시합니다.")
        else:
            try:
                result = processor(source.image.copy())
                applied = st.session_state.get("viewer_processing_applied")
                if applied is not None and applied.get("source_id") != source.source_id:
                    raise ValueError("처리 결과가 현재 원본을 참조하지 않습니다.")
                if not isinstance(result, Image.Image) or result.size != source.image.size:
                    raise ValueError("처리 결과는 원본과 같은 크기의 PIL 이미지여야 합니다.")
                with image_slot:
                    display(result)
                displayed_result = result
            except ProcessingPending as pending:
                if record is not None:
                    with image_slot:
                        display(record.result)
                    displayed_result = record.result
                    show_processing(record, saved=saved)
                    st.session_state["viewer_displayed_processing"] = record
                elif (mode == "로컬 저장 영상" and stored_result is not None
                      and stored_result_source_id == source.source_id
                      and stored_result.size == source.image.size):
                    with image_slot:
                        display(stored_result)
                    displayed_result = stored_result
                    st.caption("이 케이스에 저장된 처리 결과입니다. 새로 적용한 처리 결과와 구분하세요.")
                st.info(str(pending))
            except Exception:
                message = "영상 처리에 실패했습니다. 원본은 유지됩니다. 처리 입력과 결과 크기를 확인하세요."
                st.session_state["viewer_processing_contract_error"] = message
                st.error(message)
    if active_bounds is not None:
        left, top, right, bottom = active_bounds
        if right <= box[0] or bottom <= box[1] or left >= box[2] or top >= box[3]:
            with canvas:
                st.info("선택한 ROI는 현재 보기에 표시되지 않음 · 원본 좌표의 선택은 유지됩니다.")
    if roi_summary is not None:
        with canvas:
            roi_summary(source, displayed_result, roi_bounds)
    st.session_state["viewer_displayed_result"] = displayed_result
    st.caption("현재 보기는 세션 상태입니다. 원본 파일 저장과 케이스 연결 상태는 아래에서 별도로 확인합니다.")
    return source
