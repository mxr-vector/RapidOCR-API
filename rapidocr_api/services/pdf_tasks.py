import logging
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Any, Callable, Optional
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from PIL import Image
from pydantic import BaseModel, ConfigDict

from rapidocr_api.core.settings import PDF_MAX_CONCURRENT_REQUESTS, PDF_PAGE_WORKERS, PDF_REQUEST_TIMEOUT_SECONDS
from rapidocr_api.core.constants import (
    ERROR_OCR_FAILED,
    ERROR_PDF_SERVICE_BUSY,
    ERROR_PDF_TIMEOUT,
    OCR_KWARG_RETURN_SINGLE_CHAR_BOX,
    OCR_KWARG_RETURN_WORD_BOX,
    PDF_TIMEOUT_DISABLED_SECONDS,
    PdfResultType,
    PdfTaskStatus,
)
from rapidocr_api.schemas.ocr import (
    OcrResult,
    PdfMarkdownPageResult,
    PdfMarkdownResult,
    PdfPageResult,
    PdfRenderStat,
    PdfResult,
    PdfStorageRecord,
    PdfTask,
    PdfTaskCreated,
)
from rapidocr_api.services.document import extract_document_blocks, format_image_document
from rapidocr_api.services.ocr import processor
from rapidocr_api.services.utils import (
    build_ocr_kwargs,
    get_pdf_storage_record,
    open_pdf,
    read_pdf_result,
    render_pdf_pages,
    store_pdf_upload,
    update_pdf_storage_record,
    write_pdf_result,
)

logger = logging.getLogger(__name__)

pdf_request_semaphore = threading.BoundedSemaphore(PDF_MAX_CONCURRENT_REQUESTS)
pdf_task_executor = ThreadPoolExecutor(max_workers=PDF_MAX_CONCURRENT_REQUESTS)


class PdfTaskInitialProgress(BaseModel):
    """新建任务写入索引的进度初始化字段。"""

    result_type: PdfResultType = PdfResultType.OCR
    page_count: int | None = None
    processed_pages: int = 0
    current_page: int | None = None


class PdfProgressUpdate(BaseModel):
    """分页处理时允许写入索引的进度字段。"""

    page_count: int | None = None
    processed_pages: int | None = None
    current_page: int | None = None

    def to_updates(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class PdfTaskFailure(BaseModel):
    """后台异常转换为持久化 failed 状态时使用的字段集合。"""

    model_config = ConfigDict(use_enum_values=True)

    status: PdfTaskStatus = PdfTaskStatus.FAILED
    finished_at: float
    error: dict[str, Any]


class RenderedPagesResult(BaseModel):
    """PDF 分页处理后的有序结果与统计信息。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    page_results: list[Any]
    render_stats: list[PdfRenderStat]
    pdf_page_count: int
    processed_pages: int
    elapsed: float


def now_timestamp() -> float:
    """返回当前 Unix 时间戳，便于测试替换任务时间来源。"""
    return time.time()


def task_from_record(record: PdfStorageRecord, include_result: bool = False) -> PdfTask:
    """把存储记录装配为查询响应，成功任务按结果类型加载结果文件。"""
    result = None
    if include_result and record.status == PdfTaskStatus.SUCCEEDED:
        raw_result = read_pdf_result(record.result_file_path)
        if record.result_type == PdfResultType.MARKDOWN:
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


def load_pdf_storage_record(task_id: str) -> PdfStorageRecord | None:
    """从索引加载并校验 PDF 任务记录。"""
    record = get_pdf_storage_record(task_id)
    if record is None:
        return None
    return PdfStorageRecord.model_validate(record)


def update_pdf_task(task_id: str, **updates: Any) -> None:
    """任务状态写入索引的薄封装，保留单一打桩入口。"""
    update_pdf_storage_record(task_id, **updates)


def update_pdf_progress(
    task_id: str | None,
    *,
    page_count: int | None = None,
    processed_pages: int | None = None,
    current_page: int | None = None,
) -> None:
    """同步调用没有 task_id，只有异步任务需要持久化分页进度。"""
    if task_id is None:
        return
    updates = PdfProgressUpdate(
        page_count=page_count,
        processed_pages=processed_pages,
        current_page=current_page,
    ).to_updates()
    if updates:
        update_pdf_task(task_id, **updates)


def build_markdown_ocr_kwargs(text_score: Optional[float]) -> dict[str, Any]:
    """Markdown 输出需要细粒度文本框以支撑版面结构。"""
    ocr_kwargs = build_ocr_kwargs(text_score, True, True)
    ocr_kwargs[OCR_KWARG_RETURN_WORD_BOX] = True
    ocr_kwargs[OCR_KWARG_RETURN_SINGLE_CHAR_BOX] = True
    return ocr_kwargs


def create_pdf_task(
    upload_file: UploadFile,
    knowledge: str,
    use_det: Optional[bool],
    use_cls: Optional[bool],
    use_rec: Optional[bool],
    ocr_kwargs: dict[str, Any],
    is_markdown: bool | None = False,
    task_runner: Any = None,
    result_type: PdfResultType = PdfResultType.OCR,
) -> PdfTaskCreated:
    """落盘 PDF、初始化索引字段，并把实际处理提交到受限线程池。"""
    task_id = uuid4().hex
    record = PdfStorageRecord.model_validate(store_pdf_upload(upload_file, task_id, knowledge))
    initial_progress = PdfTaskInitialProgress(result_type=result_type)
    update_pdf_task(task_id, **initial_progress.model_dump())
    record.result_type = result_type
    runner = task_runner or run_pdf_task

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
    return PdfTaskCreated(task_id=task_id, status=PdfTaskStatus.PENDING, result_type=result_type)


def check_pdf_timeout(started_at: float, processed_pages: int) -> None:
    """PDF 后台任务超出软超时时转成客户端可轮询的 503 错误。"""
    if PDF_REQUEST_TIMEOUT_SECONDS == PDF_TIMEOUT_DISABLED_SECONDS:
        return

    elapsed = time.monotonic() - started_at
    if elapsed > PDF_REQUEST_TIMEOUT_SECONDS:
        logger.warning(
            "PDF OCR request timed out after %.2fs; processed_pages=%s limit_seconds=%s",
            elapsed,
            processed_pages,
            PDF_REQUEST_TIMEOUT_SECONDS,
        )
        raise HTTPException(status_code=503, detail=ERROR_PDF_TIMEOUT)


def _failure_update(status_code: int, detail: Any) -> dict[str, Any]:
    return PdfTaskFailure(
        finished_at=now_timestamp(),
        error={"status_code": status_code, "detail": detail},
    ).model_dump()


def run_pdf_task(
    task_id: str,
    pdf_path: str,
    result_path: str,
    use_det: Optional[bool],
    use_cls: Optional[bool],
    use_rec: Optional[bool],
    ocr_kwargs: dict[str, Any],
    is_markdown: bool = False,
) -> None:
    """PDF OCR 后台执行体负责把异常转换为任务状态。"""
    update_pdf_task(task_id, status=PdfTaskStatus.RUNNING, started_at=now_timestamp())
    try:
        result = process_pdf(pdf_path, use_det, use_cls, use_rec, ocr_kwargs, is_markdown, task_id)
        write_pdf_result(result_path, result.model_dump())
    except HTTPException as exc:
        update_pdf_task(task_id, **_failure_update(exc.status_code, exc.detail))
    except Exception:
        logger.exception("PDF OCR task failed unexpectedly; task_id=%s", task_id)
        update_pdf_task(task_id, **_failure_update(500, ERROR_OCR_FAILED))
    else:
        update_pdf_task(
            task_id,
            status=PdfTaskStatus.SUCCEEDED,
            finished_at=now_timestamp(),
            result_file_path=result_path,
            error=None,
        )


def run_pdf_markdown_task(
    task_id: str,
    pdf_path: str,
    result_path: str,
    use_det: Optional[bool],
    use_cls: Optional[bool],
    use_rec: Optional[bool],
    ocr_kwargs: dict[str, Any],
    is_markdown: bool = True,
) -> None:
    """PDF Markdown 后台执行体与 OCR 任务共享状态转换规则。"""
    update_pdf_task(task_id, status=PdfTaskStatus.RUNNING, started_at=now_timestamp())
    try:
        result = process_pdf_markdown(pdf_path, use_det, use_cls, use_rec, ocr_kwargs, is_markdown, task_id)
        write_pdf_result(result_path, result.model_dump())
    except HTTPException as exc:
        update_pdf_task(task_id, **_failure_update(exc.status_code, exc.detail))
    except Exception:
        logger.exception("PDF markdown task failed unexpectedly; task_id=%s", task_id)
        update_pdf_task(task_id, **_failure_update(500, ERROR_OCR_FAILED))
    else:
        update_pdf_task(
            task_id,
            status=PdfTaskStatus.SUCCEEDED,
            finished_at=now_timestamp(),
            result_file_path=result_path,
            error=None,
        )


def acquire_pdf_slot() -> None:
    """PDF 渲染是内存密集型操作，繁忙时拒绝而不是无限排队。"""
    if not pdf_request_semaphore.acquire(blocking=False):
        logger.warning(
            "PDF OCR request rejected: concurrency limit reached; limit=%s",
            PDF_MAX_CONCURRENT_REQUESTS,
        )
        raise HTTPException(status_code=503, detail=ERROR_PDF_SERVICE_BUSY)


def process_rendered_pages(
    pdf_path: str,
    task_id: str | None,
    log_prefix: str,
    page_processor: Callable[[Image.Image], Any],
) -> RenderedPagesResult:
    """按页渲染并处理 PDF，页级并发开启时仍按页码排序返回。"""
    acquire_pdf_slot()
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
            update_pdf_progress(task_id, processed_pages=processed_pages, current_page=page_no)
            check_pdf_timeout(started_at, processed_pages)

    pending: dict[Future[Any], tuple[int, PdfRenderStat]] = {}
    try:
        with open_pdf(pdf_path) as pdf:
            pdf_page_count = pdf.page_count
            update_pdf_progress(task_id, page_count=pdf_page_count, processed_pages=0)
            if page_workers == 1:
                for rendered_page in render_pdf_pages(pdf):
                    check_pdf_timeout(started_at, processed_pages)
                    stat = PdfRenderStat(
                        page_no=rendered_page.page_no,
                        dpi=rendered_page.dpi,
                        width=rendered_page.width,
                        height=rendered_page.height,
                    )
                    results[rendered_page.page_no] = process_page(rendered_page.image)
                    processed_pages += 1
                    render_stats.append(stat)
                    update_pdf_progress(
                        task_id,
                        processed_pages=processed_pages,
                        current_page=rendered_page.page_no,
                    )
                    check_pdf_timeout(started_at, processed_pages)
            else:
                with ThreadPoolExecutor(max_workers=page_workers) as page_executor:
                    for rendered_page in render_pdf_pages(pdf):
                        check_pdf_timeout(started_at, processed_pages)
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
        raise HTTPException(status_code=500, detail=ERROR_OCR_FAILED) from exc
    finally:
        pdf_request_semaphore.release()

    elapsed = time.monotonic() - started_at
    ordered_results = [results[page_no] for page_no in sorted(results)]
    return RenderedPagesResult(
        page_results=ordered_results,
        render_stats=render_stats,
        pdf_page_count=pdf_page_count,
        processed_pages=processed_pages,
        elapsed=elapsed,
    )


def process_pdf(
    pdf_path: str,
    use_det: Optional[bool],
    use_cls: Optional[bool],
    use_rec: Optional[bool],
    ocr_kwargs: dict[str, Any],
    is_markdown: bool = False,
    task_id: str | None = None,
) -> PdfResult:
    """逐页渲染并识别 PDF，返回与旧接口一致的分页 OCR 结果。"""

    def process_page(image: Image.Image) -> OcrResult:
        result = processor(image, use_det=use_det, use_cls=use_cls, use_rec=use_rec, **ocr_kwargs)
        if is_markdown:
            result = OcrResult.model_validate({**result.model_dump(), **format_image_document(image, page_no=None)})
        return result

    rendered = process_rendered_pages(pdf_path, task_id, "PDF OCR", process_page)
    pages = [
        PdfPageResult(page_no=page_no, result=result)
        for page_no, result in enumerate(rendered.page_results, start=1)
    ]
    logger.info(
        "PDF OCR request completed: page_count=%s processed_pages=%s elapsed_seconds=%.2f page_workers=%s render_stats=%s",
        rendered.pdf_page_count,
        rendered.processed_pages,
        rendered.elapsed,
        PDF_PAGE_WORKERS,
        rendered.render_stats,
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
    """逐页生成 PDF Markdown，整份 Markdown 以空行分页拼接。"""

    def process_page(image: Image.Image) -> dict[str, Any]:
        return format_image_document(image)

    rendered = process_rendered_pages(pdf_path, task_id, "PDF markdown", process_page)
    pages = [
        PdfMarkdownPageResult(
            page_no=page_no,
            markdown=page["formatted_markdown"],
            layout=page.get("layout"),
            content=page.get("content"),
            blocks=extract_document_blocks(page.get("content"), page_no),
        )
        for page_no, page in enumerate(rendered.page_results, start=1)
    ]
    logger.info(
        "PDF markdown request completed: page_count=%s processed_pages=%s elapsed_seconds=%.2f page_workers=%s render_stats=%s",
        rendered.pdf_page_count,
        rendered.processed_pages,
        rendered.elapsed,
        PDF_PAGE_WORKERS,
        rendered.render_stats,
    )
    blocks = [block for page in pages for block in (page.blocks or [])]
    return PdfMarkdownResult(
        page_count=len(pages),
        markdown="\n\n".join(page.markdown for page in pages if page.markdown),
        pages=pages,
        blocks=blocks,
    )
