import base64
import binascii
import io
import math
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

import fitz
from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError


def _read_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc
    if parsed <= 0:
        raise RuntimeError(f"{name} must be greater than 0.")
    return parsed


def _read_non_negative_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc
    if parsed < 0:
        raise RuntimeError(f"{name} must be greater than or equal to 0.")
    return parsed


# 只保留一个上传文件大小上限，图片、PDF 和 base64 解码后的二进制都使用同一口径。
MAX_UPLOAD_FILE_SIZE = _read_int_env("RAPIDOCR_MAX_UPLOAD_FILE_SIZE", 20 * 1024 * 1024)
PDF_RENDER_DPI = _read_int_env("RAPIDOCR_PDF_RENDER_DPI", 150)
PDF_MIN_RENDER_DPI = _read_int_env("RAPIDOCR_PDF_MIN_RENDER_DPI", 72)
PDF_MAX_RENDER_PIXELS = _read_int_env("RAPIDOCR_PDF_MAX_RENDER_PIXELS", 12_000_000)
# 0 表示不启用 PDF 内部处理超时，异步任务默认等待后台处理完成。
PDF_REQUEST_TIMEOUT_SECONDS = _read_non_negative_int_env("RAPIDOCR_PDF_REQUEST_TIMEOUT_SECONDS", 600)
PDF_MAX_CONCURRENT_REQUESTS = _read_int_env("RAPIDOCR_PDF_MAX_CONCURRENT_REQUESTS", 1)
PDF_MAGIC = b"%PDF"

if PDF_MIN_RENDER_DPI > PDF_RENDER_DPI:
    raise RuntimeError("RAPIDOCR_PDF_MIN_RENDER_DPI must not exceed RAPIDOCR_PDF_RENDER_DPI.")


@dataclass(frozen=True)
class PdfRenderPlan:
    page_no: int
    dpi: int
    width: int
    height: int

    @property
    def pixels(self) -> int:
        return self.width * self.height


@dataclass(frozen=True)
class RenderedPdfPage:
    page_no: int
    image: Image.Image
    dpi: int
    width: int
    height: int


def read_upload_file(upload_file: UploadFile) -> tuple[bytes, str]:
    data = upload_file.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(data) > MAX_UPLOAD_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Uploaded file exceeds {MAX_UPLOAD_FILE_SIZE // 1024 // 1024}MB.",
        )
    return data, upload_file.filename or ""


def decode_base64_payload(image_data: str) -> bytes:
    payload = image_data.strip()
    if not payload:
        raise HTTPException(status_code=400, detail="image_data is empty.")

    # 兼容浏览器常见的 data URI 格式，只取逗号后的真实 base64 内容。
    if "," in payload and payload.split(",", 1)[0].lower().startswith("data:"):
        payload = payload.split(",", 1)[1]

    try:
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="image_data is not valid base64.") from exc

    if not data:
        raise HTTPException(status_code=400, detail="Decoded image_data is empty.")
    if len(data) > MAX_UPLOAD_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Decoded image_data exceeds {MAX_UPLOAD_FILE_SIZE // 1024 // 1024}MB.",
        )
    return data


def build_ocr_kwargs(
    text_score: Optional[float],
    return_word_box: Optional[bool],
    return_single_char_box: Optional[bool],
) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {}
    if text_score is not None:
        # RapidOCR 的置信度阈值按 0~1 透传，越界值在进入引擎前拒绝。
        if not 0 <= text_score <= 1:
            raise HTTPException(status_code=400, detail="text_score must be between 0 and 1.")
        kwargs["text_score"] = text_score
    if return_word_box is not None:
        kwargs["return_word_box"] = return_word_box
    if return_single_char_box is not None:
        kwargs["return_single_char_box"] = return_single_char_box
    return kwargs


def is_pdf_input(data: bytes, source_name: str) -> bool:
    # 同时检查魔数和文件名，兼容无扩展名上传与浏览器保留原文件名的场景。
    return data.lstrip().startswith(PDF_MAGIC) or source_name.lower().endswith(".pdf")


def load_image(data: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(data)) as img:
            return img.copy()
    except UnidentifiedImageError as exc:
        raise HTTPException(status_code=400, detail="Input is not a supported image or PDF.") from exc


def open_pdf(pdf_data: bytes) -> fitz.Document:
    try:
        return fitz.open(stream=pdf_data, filetype="pdf")
    except fitz.FileDataError as exc:
        raise HTTPException(status_code=400, detail="Input PDF is invalid or corrupted.") from exc


def reject_pdf_limit(detail: str) -> None:
    raise HTTPException(status_code=413, detail=detail)


def build_pdf_render_plan(page: fitz.Page, page_no: int) -> PdfRenderPlan:
    rect = page.rect
    max_width = rect.width * PDF_RENDER_DPI / 72
    max_height = rect.height * PDF_RENDER_DPI / 72
    max_pixels = max_width * max_height
    dpi = PDF_RENDER_DPI

    # 先按目标 DPI 估算页面像素数，超过预算时等比例降低 DPI。
    if max_pixels > PDF_MAX_RENDER_PIXELS:
        scale = math.sqrt(PDF_MAX_RENDER_PIXELS / max_pixels)
        dpi = max(PDF_MIN_RENDER_DPI, math.floor(PDF_RENDER_DPI * scale))

    width = max(1, math.ceil(rect.width * dpi / 72))
    height = max(1, math.ceil(rect.height * dpi / 72))
    pixels = width * height
    # 降低到最小 DPI 后仍然超出像素预算，说明该页面尺寸过大，继续渲染会带来过高内存占用。
    if pixels > PDF_MAX_RENDER_PIXELS:
        reject_pdf_limit(
            f"PDF page {page_no} render size {pixels} pixels exceeds limit {PDF_MAX_RENDER_PIXELS}."
        )
    return PdfRenderPlan(page_no=page_no, dpi=dpi, width=width, height=height)


def render_pdf_pages(pdf: fitz.Document) -> Iterable[RenderedPdfPage]:
    for page_index in range(pdf.page_count):
        page_no = page_index + 1
        page = pdf.load_page(page_index)
        plan = build_pdf_render_plan(page, page_no)
        matrix = fitz.Matrix(plan.dpi / 72, plan.dpi / 72)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        try:
            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        finally:
            pix = None
        yield RenderedPdfPage(
            page_no=page_no,
            image=image,
            dpi=plan.dpi,
            width=plan.width,
            height=plan.height,
        )
