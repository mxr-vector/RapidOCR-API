import base64
import binascii
import io
import json
import math
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import fitz
from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from core.settings import (
    KNOWLEDGE_MAX_LENGTH,
    MAX_UPLOAD_FILE_SIZE,
    PDF_MAX_RENDER_PIXELS,
    PDF_MIN_RENDER_DPI,
    PDF_RENDER_DPI,
    PDF_STORAGE_INDEX,
    STORAGE_DIR,
    posix_path,
)


PDF_MAGIC = b"%PDF"
# 索引文件是多线程共享资源，读写均需在同一把锁下执行以避免竞态。
pdf_storage_index_lock = threading.Lock()


@dataclass(frozen=True)
class PdfRenderPlan:
    """单页 PDF 渲染方案：包含目标 DPI 与对应的像素尺寸。"""

    page_no: int
    dpi: int
    width: int
    height: int

    @property
    def pixels(self) -> int:
        """渲染后总像素数，用于评估内存占用。"""
        return self.width * self.height


@dataclass(frozen=True)
class RenderedPdfPage:
    """已经被栅格化为 PIL 图像的 PDF 单页结果。"""

    page_no: int
    image: Image.Image
    dpi: int
    width: int
    height: int


def read_upload_file(upload_file: UploadFile) -> tuple[bytes, str]:
    """一次性读取上传文件并校验大小，返回二进制内容和原始文件名。"""
    data = upload_file.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(data) > MAX_UPLOAD_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Uploaded file exceeds {MAX_UPLOAD_FILE_SIZE // 1024 // 1024}MB.",
        )
    return data, upload_file.filename or ""


def normalize_knowledge_segment(knowledge: str) -> str:
    """校验并规范化 knowledge 目录片段，防止路径穿越等注入风险。"""
    value = knowledge.strip()
    if not value or len(value) > KNOWLEDGE_MAX_LENGTH:
        raise HTTPException(status_code=400, detail="knowledge is invalid.")
    # 显式拒绝 "." / ".." 以及包含 ".." 的片段，避免构造出父目录路径。
    if value in {".", ".."} or ".." in value:
        raise HTTPException(status_code=400, detail="knowledge is invalid.")
    # 禁止路径分隔符与盘符冒号，强制 knowledge 只能是单层目录名。
    if any(char in value for char in ("/", "\\", ":")):
        raise HTTPException(status_code=400, detail="knowledge is invalid.")
    # 控制字符可能破坏文件名或日志，直接拒绝。
    if any(ord(char) < 32 for char in value):
        raise HTTPException(status_code=400, detail="knowledge is invalid.")
    return value


def build_pdf_storage_record(upload_filename: str, file_uuid: str, knowledge: str) -> Dict[str, Any]:
    """根据上传信息构造一条初始 PDF 存储记录，带完整路径与时间戳。"""
    now = datetime.now(timezone.utc)
    safe_knowledge = normalize_knowledge_segment(knowledge)
    storage_root = STORAGE_DIR.resolve(strict=False)
    day_dir = STORAGE_DIR / safe_knowledge / now.strftime("%Y%m%d")
    resolved_day_dir = day_dir.resolve(strict=False)
    # 再次校验解析后的目录仍落在存储根之内，防止 knowledge 被软链接等方式绕过。
    if storage_root != resolved_day_dir and storage_root not in resolved_day_dir.parents:
        raise HTTPException(status_code=400, detail="knowledge is invalid.")
    original_filename = Path(upload_filename).name
    return {
        "task_id": file_uuid,
        "knowledge": safe_knowledge,
        "original_filename": original_filename,
        "filename": original_filename,
        "original_file_path": posix_path(day_dir / f"{file_uuid}.pdf"),
        "result_file_path": posix_path(day_dir / f"{file_uuid}.json"),
        "file_size": 0,
        "status": "pending",
        "created_at": now.isoformat(),
        "started_at": None,
        "finished_at": None,
        "error": None,
    }


def is_pdf_upload_file(upload_file: UploadFile) -> bool:
    """读取上传文件开头若干字节判断是否为 PDF，读取完毕后恢复指针位置。"""
    upload_file.file.seek(0)
    header = upload_file.file.read(4096)
    upload_file.file.seek(0)
    return is_pdf_input(header, upload_file.filename or "")


def _read_pdf_storage_index_unlocked() -> list[Dict[str, Any]]:
    """不加锁地读取索引文件，调用方必须已持有 ``pdf_storage_index_lock``。"""
    if not PDF_STORAGE_INDEX.exists() or PDF_STORAGE_INDEX.stat().st_size == 0:
        return []
    with PDF_STORAGE_INDEX.open("r", encoding="utf-8") as f:
        try:
            index_data = json.load(f)
        except json.JSONDecodeError as exc:
            raise RuntimeError("storage/index.json must contain valid JSON.") from exc
    if not isinstance(index_data, list):
        raise RuntimeError("storage/index.json must contain a JSON array.")
    return index_data


def _write_json_file(path: Path, data: Any) -> None:
    """以“先写临时文件再原子替换”的方式落盘 JSON，降低写入中途崩溃导致的损坏概率。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp_path.replace(path)


def _write_pdf_storage_index_unlocked(index_data: list[Dict[str, Any]]) -> None:
    """不加锁地写入索引文件，调用方必须已持有 ``pdf_storage_index_lock``。"""
    _write_json_file(PDF_STORAGE_INDEX, index_data)


def append_pdf_storage_index(record: Dict[str, Any]) -> None:
    """线程安全地追加一条 PDF 存储记录到索引。"""
    with pdf_storage_index_lock:
        index_data = _read_pdf_storage_index_unlocked()
        index_data.append(record)
        _write_pdf_storage_index_unlocked(index_data)


def get_pdf_storage_record(task_id: str) -> Dict[str, Any] | None:
    """根据任务 ID 检索索引记录，返回记录副本以避免外部修改。"""
    with pdf_storage_index_lock:
        for record in _read_pdf_storage_index_unlocked():
            if record.get("task_id") == task_id:
                return dict(record)
    return None


def update_pdf_storage_record(task_id: str, **updates: Any) -> Dict[str, Any] | None:
    """在锁保护下就地更新指定任务记录，并返回更新后的副本。"""
    with pdf_storage_index_lock:
        index_data = _read_pdf_storage_index_unlocked()
        for record in index_data:
            if record.get("task_id") == task_id:
                record.update(updates)
                _write_pdf_storage_index_unlocked(index_data)
                return dict(record)
    return None


def store_pdf_upload(upload_file: UploadFile, file_uuid: str, knowledge: str) -> Dict[str, Any]:
    """将上传的 PDF 以分块写入落盘，并在索引中登记对应记录。"""
    record = build_pdf_storage_record(upload_file.filename or "", file_uuid, knowledge)
    pdf_path = Path(record["original_file_path"])
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    size = 0

    upload_file.file.seek(0)
    try:
        with pdf_path.open("wb") as f:
            while True:
                # 分块读取避免一次性把大文件读入内存。
                chunk = upload_file.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_FILE_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Uploaded file exceeds {MAX_UPLOAD_FILE_SIZE // 1024 // 1024}MB.",
                    )
                f.write(chunk)
    except HTTPException:
        # 出现大小超限等异常时清理掉已写入的残缺文件，避免占用磁盘。
        if pdf_path.exists():
            pdf_path.unlink()
        raise

    if size == 0:
        if pdf_path.exists():
            pdf_path.unlink()
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    record["file_size"] = size
    append_pdf_storage_index(record)
    return record


def write_pdf_result(result_path: str, result: Dict[str, Any]) -> None:
    """将 OCR 结果以原子方式写入 PDF 任务结果文件。"""
    _write_json_file(Path(result_path), result)


def read_pdf_result(result_path: str) -> Dict[str, Any]:
    """读取 PDF 任务结果文件，必须是 JSON 对象结构。"""
    with Path(result_path).open("r", encoding="utf-8") as f:
        result = json.load(f)
    if not isinstance(result, dict):
        raise RuntimeError("PDF OCR result file must contain a JSON object.")
    return result


def decode_base64_payload(image_data: str) -> bytes:
    """解码客户端传入的 base64 图像数据，兼容 data URI 前缀并进行大小校验。"""
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
    """把可选的 OCR 参数整合为传给 RapidOCR 的关键字参数字典。"""
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


def is_pdf_input(data: bytes, upload_filename: str) -> bool:
    """综合魔数与文件扩展名判定输入是否为 PDF。"""
    # 同时检查魔数和文件名，兼容无扩展名上传与浏览器保留原文件名的场景。
    return data.lstrip().startswith(PDF_MAGIC) or upload_filename.lower().endswith(".pdf")


def load_image(data: bytes) -> Image.Image:
    """将字节流加载为 PIL 图像，对不可识别的输入返回 400。"""
    try:
        with Image.open(io.BytesIO(data)) as img:
            # 使用 copy() 让返回的对象独立于 with 上下文，调用方可以延后处理。
            return img.copy()
    except UnidentifiedImageError as exc:
        raise HTTPException(status_code=400, detail="Input is not a supported image or PDF.") from exc


def open_pdf(pdf_path: str | Path) -> fitz.Document:
    """用 PyMuPDF 打开 PDF，捕获损坏文件并转换为 400 错误。"""
    try:
        return fitz.open(str(pdf_path))
    except (fitz.FileDataError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail="Input PDF is invalid or corrupted.") from exc


def reject_pdf_limit(detail: str) -> None:
    """PDF 渲染过程中触发资源限制时统一抛出 413。"""
    raise HTTPException(status_code=413, detail=detail)


def build_pdf_render_plan(page: fitz.Page, page_no: int) -> PdfRenderPlan:
    """根据页面实际尺寸与像素预算计算安全的渲染 DPI 与目标像素尺寸。"""
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
    """按页面顺序把 PDF 栅格化为 RGB 图像，采用生成器以便逐页处理后释放内存。"""
    for page_index in range(pdf.page_count):
        page_no = page_index + 1
        page = pdf.load_page(page_index)
        plan = build_pdf_render_plan(page, page_no)
        matrix = fitz.Matrix(plan.dpi / 72, plan.dpi / 72)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        try:
            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        finally:
            # 主动置空释放 PyMuPDF 的底层像素缓冲区。
            pix = None
        yield RenderedPdfPage(
            page_no=page_no,
            image=image,
            dpi=plan.dpi,
            width=plan.width,
            height=plan.height,
        )
