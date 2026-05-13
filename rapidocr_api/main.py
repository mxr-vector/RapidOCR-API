# -*- encoding: utf-8 -*-
# @Author: YuanJie
# @Contact: wangjh2001@qq.com
import argparse
import logging
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path as FilePath
from typing import Annotated, Any, Callable, Literal, Optional
from uuid import uuid4

sys.path.append(str(FilePath(__file__).resolve().parent.parent))

import numpy as np
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Path, Response, UploadFile
from pydantic import BaseModel, ConfigDict, field_validator
from PIL import Image, ImageOps
from rapidocr import RapidOCR
from starlette.formparsers import MultiPartParser

from core.settings import (
    MAX_UPLOAD_FILE_SIZE,
    PDF_MAX_CONCURRENT_REQUESTS,
    PDF_PAGE_WORKERS,
    PDF_REQUEST_TIMEOUT_SECONDS,
)
from rapidocr_api.formatter import OCR_CONFIG, formatter
from rapidocr_api.utils import (
    build_ocr_kwargs,
    decode_base64_payload,
    get_pdf_storage_record,
    is_pdf_upload_file,
    load_image,
    open_pdf,
    read_pdf_result,
    read_upload_file,
    render_pdf_pages,
    store_pdf_upload,
    update_pdf_storage_record,
    write_pdf_result,
)

# Starlette 会先按单个 multipart part 限制读取表单字段，再按文件限制读取上传内容。
# 两个限制都使用同一个总上传配置，避免 PDF、图片和表单字段各有一套大小口径。
MultiPartParser.max_part_size = MAX_UPLOAD_FILE_SIZE
MultiPartParser.max_file_size = MAX_UPLOAD_FILE_SIZE


class OcrResult(BaseModel):
    """OCR 单张图片的识别结果容器，允许任意附加字段以兼容格式化扩展字段。"""

    model_config = ConfigDict(extra="allow")


class PdfPageResult(BaseModel):
    """PDF 单页 OCR 结果：包含页码和该页对应的 OCR 结果对象。"""

    page_no: int
    result: OcrResult


class PdfResult(BaseModel):
    """PDF 文件整体 OCR 结果：页数与按顺序排列的每页结果。"""

    page_count: int
    pages: list[PdfPageResult]


class PdfMarkdownPageResult(BaseModel):
    """PDF 单页 Markdown 结果，包含 RapidDoc 结构化排版信息。"""

    page_no: int
    markdown: str
    layout: Any | None = None
    content: list[Any] | None = None
    blocks: list[dict[str, Any]] | None = None


class PdfMarkdownResult(BaseModel):
    """PDF 整体 Markdown 结果：整份 Markdown、分页结果与块列表。"""

    page_count: int
    markdown: str
    pages: list[PdfMarkdownPageResult]
    blocks: list[dict[str, Any]] | None = None


class PdfRenderStat(BaseModel):
    """PDF 渲染统计，主要用于日志记录。"""

    page_no: int
    dpi: int
    width: int
    height: int


class PdfTaskCreated(BaseModel):
    """异步 PDF 任务创建成功后的响应。"""

    task_id: str
    status: Literal["pending"]
    result_type: Literal["ocr", "markdown"] = "ocr"


class PdfTaskError(BaseModel):
    """后台任务失败时记录的错误信息，兼容 HTTPException 的 status_code/detail。"""

    status_code: int
    detail: Any


class PdfStoredFile(BaseModel):
    """PDF 存储索引中与文件元数据相关的字段。"""

    knowledge: str
    original_filename: str
    filename: str
    original_file_path: str
    result_file_path: str
    file_size: int
    created_at: str


class PdfStorageRecord(PdfStoredFile):
    """完整的 PDF 存储记录：在文件信息基础上附加任务状态和耗时。"""

    task_id: str
    status: Literal["pending", "running", "succeeded", "failed"]
    result_type: Literal["ocr", "markdown"] = "ocr"
    page_count: int | None = None
    processed_pages: int = 0
    current_page: int | None = None
    started_at: float | None = None
    finished_at: float | None = None
    error: PdfTaskError | None = None

    @field_validator("started_at", "finished_at", mode="before")
    @classmethod
    def parse_timestamp(cls, value: Any) -> float | None:
        """允许时间戳以 ISO 字符串形式持久化，读出时自动转换为浮点秒。"""
        if value is None or isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value).timestamp()
        return value

    @property
    def file(self) -> PdfStoredFile:
        """按文件视角裁剪出 PdfStoredFile 子模型，用于返回给调用方。"""
        return PdfStoredFile.model_validate(self.model_dump())


class PdfTask(BaseModel):
    """PDF 任务的完整视图，含文件元信息和可选的识别结果。"""

    task_id: str
    status: Literal["pending", "running", "succeeded", "failed"]
    result_type: Literal["ocr", "markdown"] = "ocr"
    created_at: str
    started_at: float | None = None
    finished_at: float | None = None
    page_count: int | None = None
    processed_pages: int = 0
    current_page: int | None = None
    file: PdfStoredFile | None = None
    result_file_path: str | None = None
    result: PdfResult | PdfMarkdownResult | None = None
    error: PdfTaskError | None = None


class RootResponse(BaseModel):
    """根路径欢迎信息响应模型。"""

    message: str


# 表单字段的类型别名：集中声明，便于在多个接口中复用相同的描述与约束。
ImageFileForm = Annotated[UploadFile | None, File(description="上传图片或 PDF 文件")]
MarkdownImageFileForm = Annotated[UploadFile | None, File(description="上传图片文件")]
PdfFileForm = Annotated[UploadFile, File(description="上传 PDF 文件")]
ImageDataForm = Annotated[str | None, Form(description="图片 base64 字符串，支持 data URI")]
PdfKnowledgeForm = Annotated[str, Form(description="知识库标识，用于 PDF 存储目录")]
OptionalKnowledgeForm = Annotated[str | None, Form(description="知识库标识，用于 PDF 存储目录")]
UseDetForm = Annotated[bool | None, Form(description="是否启用文本检测")]
UseClsForm = Annotated[bool | None, Form(description="是否启用方向分类")]
UseRecForm = Annotated[bool | None, Form(description="是否启用文本识别")]
TextScoreForm = Annotated[
    float | None, Form(ge=0, le=1, description="文本置信度阈值，范围 0 到 1")
]
ReturnWordBoxForm = Annotated[bool | None, Form(description="是否返回词级文本框")]
ReturnSingleCharBoxForm = Annotated[bool | None, Form(description="是否返回单字符文本框")]
IsMarkdownForm = Annotated[bool | None, Form(description="是否返回 Markdown 与结构化排版信息")]
TaskIdPath = Annotated[str, Path(description="PDF OCR 任务 ID")]


logger = logging.getLogger(__name__)
# 限制同时在后台运行的 PDF 任务数，防止单进程内存/CPU 被占满。
pdf_request_semaphore = threading.BoundedSemaphore(PDF_MAX_CONCURRENT_REQUESTS)
# 共享线程池用于执行 PDF 渲染和识别；容量与信号量一致，保证并发受控。
pdf_task_executor = ThreadPoolExecutor(max_workers=PDF_MAX_CONCURRENT_REQUESTS)


# PDF OCR 通常耗时明显长于图片 OCR，因此接口只负责创建任务并立即返回 task_id。
# 实际渲染和识别在受限线程池中执行，避免 HTTP 连接长时间占用并防止并发 PDF 请求拖垮服务。
def _now_timestamp() -> float:
    """返回当前 Unix 时间戳，方便被测试打桩。"""
    return time.time()


def _ensure_db_postprocess_box_type(ocr_engine: Any) -> None:
    """RapidDoc 会动态 patch DBPostProcess.__call__，已存在实例需补默认 box_type。"""
    text_detector = getattr(ocr_engine, "text_det", None)
    postprocess_op = getattr(text_detector, "postprocess_op", None)
    if postprocess_op is not None and not hasattr(postprocess_op, "box_type"):
        postprocess_op.box_type = "quad"


class OCRAPIUtils:
    """封装 RapidOCR 引擎，提供图像到 OCR 结果对象的统一入口。"""

    def __init__(self) -> None:
        self.ocr = RapidOCR(params=OCR_CONFIG)
        _ensure_db_postprocess_box_type(self.ocr)

    def to_rapidocr_result(
        self,
        ori_img: Image.Image,
        use_det: Optional[bool] = None,
        use_cls: Optional[bool] = None,
        use_rec: Optional[bool] = None,
        **kwargs: Any,
    ) -> Any:
        """执行 RapidOCR 推理并返回其原生结果对象。

        会先修正 EXIF 方向并转换为 RGB，避免底层模型对通道与方向敏感。
        """
        _ensure_db_postprocess_box_type(self.ocr)
        img = np.array(ImageOps.exif_transpose(ori_img).convert("RGB"))
        try:
            return self.ocr(img, use_det=use_det, use_cls=use_cls, use_rec=use_rec, **kwargs)
        finally:
            # 显式释放大数组引用，有助于及时回收内存。
            del img

    def __call__(
        self,
        ori_img: Image.Image,
        use_det: Optional[bool] = None,
        use_cls: Optional[bool] = None,
        use_rec: Optional[bool] = None,
        **kwargs: Any,
    ) -> OcrResult:
        """把 RapidOCR 原始结果转换成 API 对外暴露的 OcrResult 模型。"""
        ocr_res = self.to_rapidocr_result(
            ori_img, use_det=use_det, use_cls=use_cls, use_rec=use_rec, **kwargs
        )

        # 没有任何检测/识别结果时返回空对象，保持响应结构一致。
        if ocr_res.boxes is None or ocr_res.txts is None or ocr_res.scores is None:
            return OcrResult()

        result_data: dict[str, Any] = {}
        for i, (boxes, txt, score) in enumerate(
            zip(ocr_res.boxes, ocr_res.txts, ocr_res.scores)
        ):
            # 使用字符串下标作为键，便于直接转为 JSON 对象返回。
            result_data[str(i)] = {
                "rec_txt": txt,
                "dt_boxes": boxes.tolist(),
                "score": float(score),
            }
        return OcrResult.model_validate(result_data)


app = FastAPI(title="RapidOCR API", version="0.1.0")
# 全局 OCR 处理器：一次加载模型，整个进程共享。
processor = OCRAPIUtils()


@app.get("/")
def root() -> RootResponse:
    """根路径健康检查：返回欢迎信息。"""
    return RootResponse(message="Welcome to RapidOCR API Server!")


def _task_from_record(record: PdfStorageRecord, include_result: bool = False) -> PdfTask:
    """把存储记录装配为 PdfTask；成功任务可按结果类型加载结果文件。"""
    result = None
    if include_result and record.status == "succeeded":
        raw_result = read_pdf_result(record.result_file_path)
        if record.result_type == "markdown":
            result = PdfMarkdownResult.model_validate(raw_result)
        else:
            result = PdfResult.model_validate(raw_result)
    return PdfTask(
        task_id=record.task_id,
        status=record.status,
        result_type=record.result_type,
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
        page_count=record.page_count,
        processed_pages=record.processed_pages,
        current_page=record.current_page,
        file=record.file,
        result_file_path=record.result_file_path,
        result=result,
        error=record.error,
    )


def _load_pdf_storage_record(task_id: str) -> PdfStorageRecord | None:
    """基于 task_id 加载存储记录并校验为 Pydantic 模型。"""
    record = get_pdf_storage_record(task_id)
    if record is None:
        return None
    return PdfStorageRecord.model_validate(record)


def _create_pdf_task(
    upload_file: UploadFile,
    knowledge: str,
    use_det: Optional[bool],
    use_cls: Optional[bool],
    use_rec: Optional[bool],
    ocr_kwargs: dict[str, Any],
    is_markdown: bool | None = False,
    task_runner: Any = None,
    result_type: Literal["ocr", "markdown"] = "ocr",
) -> PdfTaskCreated:
    """统一的 PDF 任务创建入口：落盘、登记索引并把执行函数提交到线程池。"""
    task_id = uuid4().hex
    record = PdfStorageRecord.model_validate(store_pdf_upload(upload_file, task_id, knowledge))
    # 登记结果类型与进度字段，便于后续按类型检索、渲染响应和轮询进度。
    _update_pdf_task(
        task_id,
        result_type=result_type,
        page_count=None,
        processed_pages=0,
        current_page=None,
    )
    record.result_type = result_type
    runner = task_runner or _run_pdf_task

    pdf_task_executor.submit(
        runner,
        task_id,
        record.original_file_path,
        record.result_file_path,
        use_det,
        use_cls,
        use_rec,
        ocr_kwargs,
        bool(is_markdown),
    )
    return PdfTaskCreated(task_id=task_id, status="pending", result_type=result_type)


@app.post("/ocr/pdf", status_code=202, response_model=PdfTaskCreated)
def create_pdf_ocr_task(
    pdf_file: PdfFileForm,
    knowledge: PdfKnowledgeForm,
    use_det: UseDetForm = None,
    use_cls: UseClsForm = None,
    use_rec: UseRecForm = None,
    text_score: TextScoreForm = None,
    return_word_box: ReturnWordBoxForm = None,
    return_single_char_box: ReturnSingleCharBoxForm = None,
    is_markdown: IsMarkdownForm = None,
) -> PdfTaskCreated:
    """创建 PDF 异步任务；is_markdown=true 时返回 Markdown 与排版结构。"""
    if not is_pdf_upload_file(pdf_file):
        raise HTTPException(status_code=400, detail="Input is not a supported PDF.")
    if is_markdown:
        return _create_pdf_task(
            pdf_file,
            knowledge,
            use_det,
            use_cls,
            use_rec,
            _build_markdown_ocr_kwargs(text_score),
            is_markdown=True,
            task_runner=_run_pdf_markdown_task,
            result_type="markdown",
        )
    ocr_kwargs = build_ocr_kwargs(text_score, return_word_box, return_single_char_box)
    return _create_pdf_task(pdf_file, knowledge, use_det, use_cls, use_rec, ocr_kwargs)


@app.get("/ocr/pdf/tasks/{task_id}", response_model=PdfTask)
def get_pdf_ocr_task(task_id: TaskIdPath) -> PdfTask:
    """查询 PDF OCR 任务状态；成功时一并返回结果 JSON。"""
    record = _load_pdf_storage_record(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="PDF OCR task not found.")
    return _task_from_record(record, include_result=True)


@app.post("/ocr", response_model=OcrResult | PdfTaskCreated)
def ocr(
    response: Response,
    image_file: ImageFileForm = None,
    image_data: ImageDataForm = None,
    knowledge: OptionalKnowledgeForm = None,
    use_det: UseDetForm = None,
    use_cls: UseClsForm = None,
    use_rec: UseRecForm = None,
    text_score: TextScoreForm = None,
    return_word_box: ReturnWordBoxForm = None,
    return_single_char_box: ReturnSingleCharBoxForm = None,
    is_markdown: IsMarkdownForm = None,
) -> OcrResult | PdfTaskCreated:
    """OCR 统一入口：同时兼容 multipart 上传与 base64 数据，自动识别 PDF 转异步。"""
    # 统一入口只允许一种输入来源，避免同一次请求产生不确定的识别对象。
    if image_file and image_data:
        raise HTTPException(
            status_code=400, detail="Only one of image_file or image_data can be provided."
        )

    ocr_kwargs = build_ocr_kwargs(text_score, return_word_box, return_single_char_box)
    if image_file:
        if is_pdf_upload_file(image_file):
            if knowledge is None or not knowledge.strip():
                raise HTTPException(status_code=400, detail="knowledge is required for PDF uploads.")
            response.status_code = 202
            if is_markdown:
                return _create_pdf_task(
                    image_file,
                    knowledge,
                    use_det,
                    use_cls,
                    use_rec,
                    _build_markdown_ocr_kwargs(text_score),
                    is_markdown=True,
                    task_runner=_run_pdf_markdown_task,
                    result_type="markdown",
                )
            return _create_pdf_task(image_file, knowledge, use_det, use_cls, use_rec, ocr_kwargs)
        data, _ = read_upload_file(image_file)
        return process_image_bytes(data, use_det, use_cls, use_rec, ocr_kwargs, bool(is_markdown))

    if image_data:
        return process_image_bytes(
            decode_base64_payload(image_data), use_det, use_cls, use_rec, ocr_kwargs, bool(is_markdown)
        )

    raise HTTPException(
        status_code=400,
        detail="When sending a POST request, image_file or image_data must have a value.",
    )


def _check_pdf_timeout(started_at: float, processed_pages: int) -> None:
    """检查 PDF 任务是否超出配置的软超时，超时则抛出 503。"""
    # 异步任务默认不做内部超时；需要保护后台资源时再通过环境变量设置正整数秒数。
    if PDF_REQUEST_TIMEOUT_SECONDS == 0:
        return

    elapsed = time.monotonic() - started_at
    if elapsed > PDF_REQUEST_TIMEOUT_SECONDS:
        logger.warning(
            "PDF OCR request timed out after %.2fs; processed_pages=%s limit_seconds=%s",
            elapsed,
            processed_pages,
            PDF_REQUEST_TIMEOUT_SECONDS,
        )
        raise HTTPException(status_code=503, detail="PDF OCR processing timed out.")


def _update_pdf_task(task_id: str, **updates: Any) -> None:
    """任务状态写入索引的薄封装，便于测试时统一打桩。"""
    update_pdf_storage_record(task_id, **updates)


def _update_pdf_progress(
    task_id: str | None,
    *,
    page_count: int | None = None,
    processed_pages: int | None = None,
    current_page: int | None = None,
) -> None:
    """按任务 ID 持久化 PDF 处理进度；同步调用时无 task_id 则跳过。"""
    if task_id is None:
        return
    updates: dict[str, Any] = {}
    if page_count is not None:
        updates["page_count"] = page_count
    if processed_pages is not None:
        updates["processed_pages"] = processed_pages
    if current_page is not None:
        updates["current_page"] = current_page
    if updates:
        _update_pdf_task(task_id, **updates)


def _build_markdown_ocr_kwargs(text_score: Optional[float]) -> dict[str, Any]:
    """Markdown 场景的 OCR 参数：强制开启词级与单字符框以便生成细粒度排版。"""
    ocr_kwargs = build_ocr_kwargs(text_score, True, True)
    ocr_kwargs["return_word_box"] = True
    ocr_kwargs["return_single_char_box"] = True
    return ocr_kwargs


def _extract_document_blocks(
    content: list[Any] | None, page_no: int | None = None
) -> list[dict[str, Any]]:
    """从 RapidDoc content list 提取前端可稳定消费的块列表。"""
    if not content:
        return []
    blocks: list[dict[str, Any]] = []
    for item in content:
        block = dict(item) if isinstance(item, dict) else {"content": item}
        block_type = block.get("type") or block.get("block_type") or block.get("category")
        if block_type is not None:
            block["type"] = block_type
        if page_no is not None:
            block["page_no"] = page_no
        blocks.append(block)
    return blocks


def _format_image_document(image: Image.Image, page_no: int | None = None) -> dict[str, Any]:
    """调用 DocumentFormatter 对图像进行版面/公式/表格格式化，返回附加字段。"""
    formatted = formatter.format_image(image)
    blocks = _extract_document_blocks(formatted.content, page_no)
    result: dict[str, Any] = {"formatted_markdown": formatted.markdown, "blocks": blocks}
    if formatted.layout is not None:
        result["layout"] = formatted.layout
    if formatted.content is not None:
        result["content"] = formatted.content
    return result


# 后台线程不能直接把异常抛给客户端，因此把 HTTPException 转成可轮询的 failed 状态。
def _run_pdf_task(
    task_id: str,
    pdf_path: str,
    result_path: str,
    use_det: Optional[bool],
    use_cls: Optional[bool],
    use_rec: Optional[bool],
    ocr_kwargs: dict[str, Any],
    is_markdown: bool = False,
) -> None:
    """PDF OCR 任务在线程池中的执行体：全程更新任务状态、捕获异常并写入结果。"""
    _update_pdf_task(task_id, status="running", started_at=_now_timestamp())
    try:
        result = process_pdf(pdf_path, use_det, use_cls, use_rec, ocr_kwargs, is_markdown, task_id)
        write_pdf_result(result_path, result.model_dump())
    except HTTPException as exc:
        # 业务型错误（如文件损坏/超限）原样保留 status_code 与 detail 供客户端查询。
        _update_pdf_task(
            task_id,
            status="failed",
            finished_at=_now_timestamp(),
            error={"status_code": exc.status_code, "detail": exc.detail},
        )
    except Exception:
        # 未知异常统一转为 500，避免泄漏内部栈信息给调用方。
        logger.exception("PDF OCR task failed unexpectedly; task_id=%s", task_id)
        _update_pdf_task(
            task_id,
            status="failed",
            finished_at=_now_timestamp(),
            error={"status_code": 500, "detail": "OCR processing failed."},
        )
    else:
        _update_pdf_task(
            task_id,
            status="succeeded",
            finished_at=_now_timestamp(),
            result_file_path=result_path,
            error=None,
        )


def _run_pdf_markdown_task(
    task_id: str,
    pdf_path: str,
    result_path: str,
    use_det: Optional[bool],
    use_cls: Optional[bool],
    use_rec: Optional[bool],
    ocr_kwargs: dict[str, Any],
    is_markdown: bool = True,
) -> None:
    """PDF 转 Markdown 任务的线程池执行体，逻辑与 _run_pdf_task 对称。"""
    _update_pdf_task(task_id, status="running", started_at=_now_timestamp())
    try:
        result = process_pdf_markdown(pdf_path, use_det, use_cls, use_rec, ocr_kwargs, is_markdown, task_id)
        write_pdf_result(result_path, result.model_dump())
    except HTTPException as exc:
        _update_pdf_task(
            task_id,
            status="failed",
            finished_at=_now_timestamp(),
            error={"status_code": exc.status_code, "detail": exc.detail},
        )
    except Exception:
        logger.exception("PDF markdown task failed unexpectedly; task_id=%s", task_id)
        _update_pdf_task(
            task_id,
            status="failed",
            finished_at=_now_timestamp(),
            error={"status_code": 500, "detail": "OCR processing failed."},
        )
    else:
        _update_pdf_task(
            task_id,
            status="succeeded",
            finished_at=_now_timestamp(),
            result_file_path=result_path,
            error=None,
        )


def _acquire_pdf_slot() -> None:
    """尝试获取 PDF 并发槽位，繁忙时以 503 拒绝请求而非排队。"""
    if not pdf_request_semaphore.acquire(blocking=False):
        logger.warning(
            "PDF OCR request rejected: concurrency limit reached; limit=%s",
            PDF_MAX_CONCURRENT_REQUESTS,
        )
        raise HTTPException(status_code=503, detail="PDF OCR service is busy.")


def process_image_bytes(
    image_data: bytes,
    use_det: Optional[bool],
    use_cls: Optional[bool],
    use_rec: Optional[bool],
    ocr_kwargs: dict[str, Any],
    is_markdown: bool = False,
) -> OcrResult:
    """对图像字节流执行 OCR，可选叠加 Markdown 与结构化排版结果。"""
    try:
        img = load_image(image_data)
        try:
            result = processor(img, use_det=use_det, use_cls=use_cls, use_rec=use_rec, **ocr_kwargs)
            if is_markdown:
                # 合并 OCR 与格式化字段，保持向下兼容同时提供富文本信息。
                result = OcrResult.model_validate(
                    {**result.model_dump(), **_format_image_document(img)}
                )
            return result
        finally:
            img.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("OCR image request failed")
        raise HTTPException(status_code=500, detail="OCR processing failed.") from exc


def _process_rendered_pages(
    pdf_path: str,
    task_id: str | None,
    log_prefix: str,
    page_processor: Callable[[Image.Image], Any],
) -> tuple[list[Any], list[PdfRenderStat], int, int, float]:
    """按页渲染并处理 PDF，必要时使用页级 worker 并按页序返回结果。"""
    _acquire_pdf_slot()
    started_at = time.monotonic()
    results: dict[int, Any] = {}
    render_stats: list[PdfRenderStat] = []
    processed_pages = 0
    pdf_page_count = 0
    page_workers = max(1, PDF_PAGE_WORKERS)

    def process_page(image: Image.Image) -> Any:
        try:
            return page_processor(image)
        finally:
            image.close()

    def collect_done(done: set[Future[Any]]) -> None:
        nonlocal processed_pages
        for future in done:
            page_no, stat = pending.pop(future)
            results[page_no] = future.result()
            processed_pages += 1
            render_stats.append(stat)
            _update_pdf_progress(
                task_id, processed_pages=processed_pages, current_page=page_no
            )
            _check_pdf_timeout(started_at, processed_pages)

    pending: dict[Future[Any], tuple[int, PdfRenderStat]] = {}
    try:
        with open_pdf(pdf_path) as pdf:
            pdf_page_count = pdf.page_count
            _update_pdf_progress(task_id, page_count=pdf_page_count, processed_pages=0)
            if page_workers == 1:
                for rendered_page in render_pdf_pages(pdf):
                    _check_pdf_timeout(started_at, processed_pages)
                    stat = PdfRenderStat(
                        page_no=rendered_page.page_no,
                        dpi=rendered_page.dpi,
                        width=rendered_page.width,
                        height=rendered_page.height,
                    )
                    results[rendered_page.page_no] = process_page(rendered_page.image)
                    processed_pages += 1
                    render_stats.append(stat)
                    _update_pdf_progress(
                        task_id,
                        processed_pages=processed_pages,
                        current_page=rendered_page.page_no,
                    )
                    _check_pdf_timeout(started_at, processed_pages)
            else:
                with ThreadPoolExecutor(max_workers=page_workers) as page_executor:
                    for rendered_page in render_pdf_pages(pdf):
                        _check_pdf_timeout(started_at, processed_pages)
                        while len(pending) >= page_workers:
                            done, _ = wait(pending, return_when=FIRST_COMPLETED)
                            collect_done(done)
                        stat = PdfRenderStat(
                            page_no=rendered_page.page_no,
                            dpi=rendered_page.dpi,
                            width=rendered_page.width,
                            height=rendered_page.height,
                        )
                        future = page_executor.submit(process_page, rendered_page.image)
                        pending[future] = (rendered_page.page_no, stat)
                    while pending:
                        done, _ = wait(pending, return_when=FIRST_COMPLETED)
                        collect_done(done)
    except HTTPException as exc:
        logger.warning(
            "%s request rejected: status_code=%s detail=%s page_count=%s processed_pages=%s",
            log_prefix,
            exc.status_code,
            exc.detail,
            pdf_page_count,
            processed_pages,
        )
        raise
    except Exception as exc:
        logger.exception("%s request failed", log_prefix)
        raise HTTPException(status_code=500, detail="OCR processing failed.") from exc
    finally:
        pdf_request_semaphore.release()

    elapsed = time.monotonic() - started_at
    ordered_results = [results[page_no] for page_no in sorted(results)]
    return ordered_results, render_stats, pdf_page_count, processed_pages, elapsed


def process_pdf(
    pdf_path: str,
    use_det: Optional[bool],
    use_cls: Optional[bool],
    use_rec: Optional[bool],
    ocr_kwargs: dict[str, Any],
    is_markdown: bool = False,
    task_id: str | None = None,
) -> PdfResult:
    """逐页渲染并识别 PDF，返回每页结果的有序集合。"""

    def process_page(image: Image.Image) -> OcrResult:
        result = processor(image, use_det=use_det, use_cls=use_cls, use_rec=use_rec, **ocr_kwargs)
        if is_markdown:
            result = OcrResult.model_validate(
                {**result.model_dump(), **_format_image_document(image, page_no=None)}
            )
        return result

    page_results, render_stats, pdf_page_count, processed_pages, elapsed = _process_rendered_pages(
        pdf_path, task_id, "PDF OCR", process_page
    )
    pages = [
        PdfPageResult(page_no=page_no, result=result)
        for page_no, result in enumerate(page_results, start=1)
    ]
    logger.info(
        "PDF OCR request completed: page_count=%s processed_pages=%s elapsed_seconds=%.2f page_workers=%s render_stats=%s",
        pdf_page_count,
        processed_pages,
        elapsed,
        PDF_PAGE_WORKERS,
        render_stats,
    )
    return PdfResult(page_count=len(pages), pages=pages)


def process_pdf_markdown(
    pdf_path: str,
    use_det: Optional[bool],
    use_cls: Optional[bool],
    use_rec: Optional[bool],
    ocr_kwargs: dict[str, Any],
    is_markdown: bool = True,
    task_id: str | None = None,
) -> PdfMarkdownResult:
    """逐页生成 PDF 的 Markdown 表达，整体 Markdown 以空行分页拼接。"""

    def process_page(image: Image.Image) -> dict[str, Any]:
        formatted = _format_image_document(image)
        return formatted

    markdown_pages, render_stats, pdf_page_count, processed_pages, elapsed = _process_rendered_pages(
        pdf_path, task_id, "PDF markdown", process_page
    )
    pages = [
        PdfMarkdownPageResult(
            page_no=page_no,
            markdown=page["formatted_markdown"],
            layout=page.get("layout"),
            content=page.get("content"),
            blocks=_extract_document_blocks(page.get("content"), page_no),
        )
        for page_no, page in enumerate(markdown_pages, start=1)
    ]
    logger.info(
        "PDF markdown request completed: page_count=%s processed_pages=%s elapsed_seconds=%.2f page_workers=%s render_stats=%s",
        pdf_page_count,
        processed_pages,
        elapsed,
        PDF_PAGE_WORKERS,
        render_stats,
    )
    blocks = [block for page in pages for block in (page.blocks or [])]
    return PdfMarkdownResult(
        page_count=len(pages),
        markdown="\n\n".join(page.markdown for page in pages if page.markdown),
        pages=pages,
        blocks=blocks,
    )


def main() -> None:
    """命令行入口：解析参数并启动 uvicorn 服务。"""
    parser = argparse.ArgumentParser("rapidocr_api")
    parser.add_argument("-ip", "--ip", type=str, default="0.0.0.0", help="IP Address")
    parser.add_argument("-p", "--port", type=int, default=9003, help="IP port")
    parser.add_argument(
        "-workers", "--workers", type=int, default=1, help="number of worker process"
    )
    args = parser.parse_args()

    # 统一 uvicorn 日志格式，便于与业务日志对齐时间戳。
    log_config = uvicorn.config.LOGGING_CONFIG
    log_config["formatters"]["access"]["fmt"] = "%(asctime)s %(levelname)s %(message)s"
    log_config["formatters"]["default"]["fmt"] = "%(asctime)s %(levelname)s %(message)s"

    uvicorn.run(
        "rapidocr_api.main:app",
        host=args.ip,
        port=args.port,
        reload=False,
        workers=args.workers,
        log_config=log_config,
    )


if __name__ == "__main__":
    main()
