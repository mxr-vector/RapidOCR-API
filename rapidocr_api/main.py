# -*- encoding: utf-8 -*-
# @Author: YuanJie
# @Contact: wangjh2001@qq.com
import argparse
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path as FilePath
from typing import Annotated, Any, Literal, Optional
from uuid import uuid4

sys.path.append(str(FilePath(__file__).resolve().parent.parent))

import numpy as np
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Path, Response, UploadFile
from pydantic import BaseModel, ConfigDict
from PIL import Image, ImageOps
from rapidocr import RapidOCR
from starlette.formparsers import MultiPartParser

from rapidocr_api.utils import (
    MAX_UPLOAD_FILE_SIZE,
    PDF_MAX_CONCURRENT_REQUESTS,
    PDF_REQUEST_TIMEOUT_SECONDS,
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
    model_config = ConfigDict(extra="allow")


class PdfPageResult(BaseModel):
    page_no: int
    result: OcrResult


class PdfResult(BaseModel):
    page_count: int
    pages: list[PdfPageResult]


class MarkdownResult(BaseModel):
    markdown: str


class PdfMarkdownPageResult(BaseModel):
    page_no: int
    markdown: str


class PdfMarkdownResult(BaseModel):
    page_count: int
    markdown: str
    pages: list[PdfMarkdownPageResult]


class PdfRenderStat(BaseModel):
    page_no: int
    dpi: int
    width: int
    height: int


class PdfTaskCreated(BaseModel):
    task_id: str
    status: Literal["pending"]


class PdfTaskError(BaseModel):
    status_code: int
    detail: Any


class PdfStoredFile(BaseModel):
    uuid: str
    knowledge: str
    original_filename: str
    filename: str
    stored_pdf_filename: str
    result_filename: str
    original_file_path: str
    result_file_path: str
    file_size: int
    created_at: str


class PdfStorageRecord(PdfStoredFile):
    task_id: str
    status: Literal["pending", "running", "succeeded", "failed"]
    result_type: Literal["ocr", "markdown"] = "ocr"
    started_at: str | None = None
    finished_at: str | None = None
    error: PdfTaskError | None = None

    @property
    def file(self) -> PdfStoredFile:
        return PdfStoredFile.model_validate(self.model_dump())


class PdfTask(BaseModel):
    task_id: str
    status: Literal["pending", "running", "succeeded", "failed"]
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    file: PdfStoredFile | None = None
    result_file_path: str | None = None
    result: PdfResult | None = None
    error: PdfTaskError | None = None


class PdfMarkdownTask(BaseModel):
    task_id: str
    status: Literal["pending", "running", "succeeded", "failed"]
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    file: PdfStoredFile | None = None
    result_file_path: str | None = None
    result: PdfMarkdownResult | None = None
    error: PdfTaskError | None = None


class RootResponse(BaseModel):
    message: str


ImageFileForm = Annotated[UploadFile | None, File(description="上传图片或 PDF 文件")]
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
TaskIdPath = Annotated[str, Path(description="PDF OCR 任务 ID")]


logger = logging.getLogger(__name__)
pdf_request_semaphore = threading.BoundedSemaphore(PDF_MAX_CONCURRENT_REQUESTS)
pdf_task_executor = ThreadPoolExecutor(max_workers=PDF_MAX_CONCURRENT_REQUESTS)


# PDF OCR 通常耗时明显长于图片 OCR，因此接口只负责创建任务并立即返回 task_id。
# 实际渲染和识别在受限线程池中执行，避免 HTTP 连接长时间占用并防止并发 PDF 请求拖垮服务。
def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class OCRAPIUtils:
    def __init__(self) -> None:
        det_model_path = "models/RapidOCR/onnx/PP-OCRv4/det/multi_PP-OCRv3_det_mobile.onnx"
        cls_model_path = "models/RapidOCR/onnx/PP-OCRv4/cls/ch_ppocr_mobile_v2.0_cls_mobile.onnx"
        rec_model_path = "models/RapidOCR/onnx/PP-OCRv5/rec/ch_PP-OCRv5_rec_mobile.onnx"

        self.ocr = RapidOCR(
            params={
                "Det.model_path": det_model_path,
                "Cls.model_path": cls_model_path,
                "Rec.model_path": rec_model_path,
            }
        )

    def to_rapidocr_result(
        self,
        ori_img: Image.Image,
        use_det: Optional[bool] = None,
        use_cls: Optional[bool] = None,
        use_rec: Optional[bool] = None,
        **kwargs: Any,
    ) -> Any:
        img = np.array(ImageOps.exif_transpose(ori_img).convert("RGB"))
        try:
            return self.ocr(img, use_det=use_det, use_cls=use_cls, use_rec=use_rec, **kwargs)
        finally:
            del img

    def __call__(
        self,
        ori_img: Image.Image,
        use_det: Optional[bool] = None,
        use_cls: Optional[bool] = None,
        use_rec: Optional[bool] = None,
        **kwargs: Any,
    ) -> OcrResult:
        ocr_res = self.to_rapidocr_result(
            ori_img, use_det=use_det, use_cls=use_cls, use_rec=use_rec, **kwargs
        )

        if ocr_res.boxes is None or ocr_res.txts is None or ocr_res.scores is None:
            return OcrResult()

        result_data: dict[str, Any] = {}
        for i, (boxes, txt, score) in enumerate(
            zip(ocr_res.boxes, ocr_res.txts, ocr_res.scores)
        ):
            result_data[str(i)] = {
                "rec_txt": txt,
                "dt_boxes": boxes.tolist(),
                "score": float(score),
            }
        return OcrResult.model_validate(result_data)


app = FastAPI(title="RapidOCR API", version="0.1.0")
processor = OCRAPIUtils()


@app.get("/")
def root() -> RootResponse:
    return RootResponse(message="Welcome to RapidOCR API Server!")


def _task_from_record(record: PdfStorageRecord, include_result: bool = False) -> PdfTask:
    if record.result_type != "ocr":
        raise HTTPException(status_code=404, detail="PDF OCR task not found.")
    result = None
    if include_result and record.status == "succeeded":
        result = PdfResult.model_validate(read_pdf_result(record.result_file_path))
    return PdfTask(
        task_id=record.task_id,
        status=record.status,
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
        file=record.file,
        result_file_path=record.result_file_path,
        result=result,
        error=record.error,
    )


def _markdown_task_from_record(
    record: PdfStorageRecord, include_result: bool = False
) -> PdfMarkdownTask:
    if record.result_type != "markdown":
        raise HTTPException(status_code=404, detail="PDF markdown task not found.")
    result = None
    if include_result and record.status == "succeeded":
        result = PdfMarkdownResult.model_validate(read_pdf_result(record.result_file_path))
    return PdfMarkdownTask(
        task_id=record.task_id,
        status=record.status,
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
        file=record.file,
        result_file_path=record.result_file_path,
        result=result,
        error=record.error,
    )


def _load_pdf_storage_record(task_id: str) -> PdfStorageRecord | None:
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
    task_runner: Any = None,
    result_type: Literal["ocr", "markdown"] = "ocr",
) -> PdfTaskCreated:
    task_id = uuid4().hex
    record = PdfStorageRecord.model_validate(store_pdf_upload(upload_file, task_id, knowledge))
    _update_pdf_task(task_id, result_type=result_type)
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
    )
    return PdfTaskCreated(task_id=task_id, status="pending")


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
) -> PdfTaskCreated:
    if not is_pdf_upload_file(pdf_file):
        raise HTTPException(status_code=400, detail="Input is not a supported PDF.")
    ocr_kwargs = build_ocr_kwargs(text_score, return_word_box, return_single_char_box)
    return _create_pdf_task(pdf_file, knowledge, use_det, use_cls, use_rec, ocr_kwargs)


@app.get("/ocr/pdf/tasks/{task_id}", response_model=PdfTask)
def get_pdf_ocr_task(task_id: TaskIdPath) -> PdfTask:
    record = _load_pdf_storage_record(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="PDF OCR task not found.")
    return _task_from_record(record, include_result=True)


@app.post("/ocr/pdf2md", status_code=202, response_model=PdfTaskCreated)
def create_pdf_markdown_task(
    pdf_file: PdfFileForm,
    knowledge: PdfKnowledgeForm,
    use_det: UseDetForm = None,
    use_cls: UseClsForm = None,
    use_rec: UseRecForm = None,
    text_score: TextScoreForm = None,
) -> PdfTaskCreated:
    if not is_pdf_upload_file(pdf_file):
        raise HTTPException(status_code=400, detail="Input is not a supported PDF.")
    ocr_kwargs = _build_markdown_ocr_kwargs(text_score)
    return _create_pdf_task(
        pdf_file,
        knowledge,
        use_det,
        use_cls,
        use_rec,
        ocr_kwargs,
        task_runner=_run_pdf_markdown_task,
        result_type="markdown",
    )


@app.get("/ocr/pdf2md/tasks/{task_id}", response_model=PdfMarkdownTask)
def get_pdf_markdown_task(task_id: TaskIdPath) -> PdfMarkdownTask:
    record = _load_pdf_storage_record(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="PDF markdown task not found.")
    return _markdown_task_from_record(record, include_result=True)


@app.post("/ocr/markdown", response_model=MarkdownResult | PdfTaskCreated)
def ocr_markdown(
    response: Response,
    image_file: ImageFileForm = None,
    image_data: ImageDataForm = None,
    knowledge: OptionalKnowledgeForm = None,
    use_det: UseDetForm = None,
    use_cls: UseClsForm = None,
    use_rec: UseRecForm = None,
    text_score: TextScoreForm = None,
) -> MarkdownResult | PdfTaskCreated:
    if image_file and image_data:
        raise HTTPException(
            status_code=400, detail="Only one of image_file or image_data can be provided."
        )

    ocr_kwargs = _build_markdown_ocr_kwargs(text_score)
    if image_file:
        if is_pdf_upload_file(image_file):
            if knowledge is None or not knowledge.strip():
                raise HTTPException(status_code=400, detail="knowledge is required for PDF uploads.")
            response.status_code = 202
            return _create_pdf_task(
                image_file,
                knowledge,
                use_det,
                use_cls,
                use_rec,
                ocr_kwargs,
                task_runner=_run_pdf_markdown_task,
                result_type="markdown",
            )
        data, _ = read_upload_file(image_file)
        return process_image_markdown_bytes(data, use_det, use_cls, use_rec, ocr_kwargs)

    if image_data:
        return process_image_markdown_bytes(
            decode_base64_payload(image_data), use_det, use_cls, use_rec, ocr_kwargs
        )

    raise HTTPException(
        status_code=400,
        detail="When sending a POST request, image_file or image_data must have a value.",
    )


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
) -> OcrResult | PdfTaskCreated:
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
            return _create_pdf_task(image_file, knowledge, use_det, use_cls, use_rec, ocr_kwargs)
        data, _ = read_upload_file(image_file)
        return process_image_bytes(data, use_det, use_cls, use_rec, ocr_kwargs)

    if image_data:
        return process_image_bytes(
            decode_base64_payload(image_data), use_det, use_cls, use_rec, ocr_kwargs
        )

    raise HTTPException(
        status_code=400,
        detail="When sending a POST request, image_file or image_data must have a value.",
    )


def _check_pdf_timeout(started_at: float, processed_pages: int) -> None:
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
    update_pdf_storage_record(task_id, **updates)


def _build_markdown_ocr_kwargs(text_score: Optional[float]) -> dict[str, Any]:
    ocr_kwargs = build_ocr_kwargs(text_score, True, True)
    ocr_kwargs["return_word_box"] = True
    ocr_kwargs["return_single_char_box"] = True
    return ocr_kwargs


# 后台线程不能直接把异常抛给客户端，因此把 HTTPException 转成可轮询的 failed 状态。
def _run_pdf_task(
    task_id: str,
    pdf_path: str,
    result_path: str,
    use_det: Optional[bool],
    use_cls: Optional[bool],
    use_rec: Optional[bool],
    ocr_kwargs: dict[str, Any],
) -> None:
    _update_pdf_task(task_id, status="running", started_at=_utc_now_iso())
    try:
        result = process_pdf(pdf_path, use_det, use_cls, use_rec, ocr_kwargs)
        write_pdf_result(result_path, result.model_dump())
    except HTTPException as exc:
        _update_pdf_task(
            task_id,
            status="failed",
            finished_at=_utc_now_iso(),
            error={"status_code": exc.status_code, "detail": exc.detail},
        )
    except Exception:
        logger.exception("PDF OCR task failed unexpectedly; task_id=%s", task_id)
        _update_pdf_task(
            task_id,
            status="failed",
            finished_at=_utc_now_iso(),
            error={"status_code": 500, "detail": "OCR processing failed."},
        )
    else:
        _update_pdf_task(
            task_id,
            status="succeeded",
            finished_at=_utc_now_iso(),
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
) -> None:
    _update_pdf_task(task_id, status="running", started_at=_utc_now_iso())
    try:
        result = process_pdf_markdown(pdf_path, use_det, use_cls, use_rec, ocr_kwargs)
        write_pdf_result(result_path, result.model_dump())
    except HTTPException as exc:
        _update_pdf_task(
            task_id,
            status="failed",
            finished_at=_utc_now_iso(),
            error={"status_code": exc.status_code, "detail": exc.detail},
        )
    except Exception:
        logger.exception("PDF markdown task failed unexpectedly; task_id=%s", task_id)
        _update_pdf_task(
            task_id,
            status="failed",
            finished_at=_utc_now_iso(),
            error={"status_code": 500, "detail": "OCR processing failed."},
        )
    else:
        _update_pdf_task(
            task_id,
            status="succeeded",
            finished_at=_utc_now_iso(),
            result_file_path=result_path,
            error=None,
        )


def _acquire_pdf_slot() -> None:
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
) -> OcrResult:
    try:
        img = load_image(image_data)
        try:
            return processor(img, use_det=use_det, use_cls=use_cls, use_rec=use_rec, **ocr_kwargs)
        finally:
            img.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("OCR image request failed")
        raise HTTPException(status_code=500, detail="OCR processing failed.") from exc


def process_image_markdown_bytes(
    image_data: bytes,
    use_det: Optional[bool],
    use_cls: Optional[bool],
    use_rec: Optional[bool],
    ocr_kwargs: dict[str, Any],
) -> MarkdownResult:
    try:
        img = load_image(image_data)
        try:
            result = processor.to_rapidocr_result(
                img, use_det=use_det, use_cls=use_cls, use_rec=use_rec, **ocr_kwargs
            )
            return MarkdownResult(
                markdown=result.to_markdown(),
            )
        finally:
            img.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("OCR markdown image request failed")
        raise HTTPException(status_code=500, detail="OCR processing failed.") from exc


def process_pdf(
    pdf_path: str,
    use_det: Optional[bool],
    use_cls: Optional[bool],
    use_rec: Optional[bool],
    ocr_kwargs: dict[str, Any],
) -> PdfResult:
    _acquire_pdf_slot()
    started_at = time.monotonic()
    pages: list[PdfPageResult] = []
    render_stats: list[PdfRenderStat] = []
    processed_pages = 0
    pdf_page_count = 0

    try:
        with open_pdf(pdf_path) as pdf:
            pdf_page_count = pdf.page_count
            for rendered_page in render_pdf_pages(pdf):
                _check_pdf_timeout(started_at, processed_pages)
                image = rendered_page.image
                try:
                    result = processor(
                        image, use_det=use_det, use_cls=use_cls, use_rec=use_rec, **ocr_kwargs
                    )
                    processed_pages += 1
                    pages.append(
                        PdfPageResult(
                            page_no=rendered_page.page_no,
                            result=result,
                        )
                    )
                    render_stats.append(
                        PdfRenderStat(
                            page_no=rendered_page.page_no,
                            dpi=rendered_page.dpi,
                            width=rendered_page.width,
                            height=rendered_page.height,
                        )
                    )
                    _check_pdf_timeout(started_at, processed_pages)
                finally:
                    image.close()
    except HTTPException as exc:
        logger.warning(
            "PDF OCR request rejected: status_code=%s detail=%s page_count=%s processed_pages=%s",
            exc.status_code,
            exc.detail,
            pdf_page_count,
            processed_pages,
        )
        raise
    except Exception as exc:
        logger.exception("OCR PDF request failed")
        raise HTTPException(status_code=500, detail="OCR processing failed.") from exc
    finally:
        pdf_request_semaphore.release()

    elapsed = time.monotonic() - started_at
    logger.info(
        "PDF OCR request completed: page_count=%s processed_pages=%s elapsed_seconds=%.2f render_stats=%s",
        pdf_page_count,
        processed_pages,
        elapsed,
        render_stats,
    )
    return PdfResult(
        page_count=len(pages),
        pages=pages,
    )


def process_pdf_markdown(
    pdf_path: str,
    use_det: Optional[bool],
    use_cls: Optional[bool],
    use_rec: Optional[bool],
    ocr_kwargs: dict[str, Any],
) -> PdfMarkdownResult:
    _acquire_pdf_slot()
    started_at = time.monotonic()
    pages: list[PdfMarkdownPageResult] = []
    render_stats: list[PdfRenderStat] = []
    processed_pages = 0
    pdf_page_count = 0

    try:
        with open_pdf(pdf_path) as pdf:
            pdf_page_count = pdf.page_count
            for rendered_page in render_pdf_pages(pdf):
                _check_pdf_timeout(started_at, processed_pages)
                image = rendered_page.image
                try:
                    result = processor.to_rapidocr_result(
                        image, use_det=use_det, use_cls=use_cls, use_rec=use_rec, **ocr_kwargs
                    )
                    processed_pages += 1
                    pages.append(
                        PdfMarkdownPageResult(
                            page_no=rendered_page.page_no,
                            markdown=result.to_markdown(),
                        )
                    )
                    render_stats.append(
                        PdfRenderStat(
                            page_no=rendered_page.page_no,
                            dpi=rendered_page.dpi,
                            width=rendered_page.width,
                            height=rendered_page.height,
                        )
                    )
                    _check_pdf_timeout(started_at, processed_pages)
                finally:
                    image.close()
    except HTTPException as exc:
        logger.warning(
            "PDF markdown request rejected: status_code=%s detail=%s page_count=%s processed_pages=%s",
            exc.status_code,
            exc.detail,
            pdf_page_count,
            processed_pages,
        )
        raise
    except Exception as exc:
        logger.exception("OCR PDF markdown request failed")
        raise HTTPException(status_code=500, detail="OCR processing failed.") from exc
    finally:
        pdf_request_semaphore.release()

    elapsed = time.monotonic() - started_at
    logger.info(
        "PDF markdown request completed: page_count=%s processed_pages=%s elapsed_seconds=%.2f render_stats=%s",
        pdf_page_count,
        processed_pages,
        elapsed,
        render_stats,
    )
    return PdfMarkdownResult(
        page_count=len(pages),
        markdown="\n\n".join(page.markdown for page in pages if page.markdown),
        pages=pages,
    )


def main() -> None:
    parser = argparse.ArgumentParser("rapidocr_api")
    parser.add_argument("-ip", "--ip", type=str, default="0.0.0.0", help="IP Address")
    parser.add_argument("-p", "--port", type=int, default=9003, help="IP port")
    parser.add_argument(
        "-workers", "--workers", type=int, default=1, help="number of worker process"
    )
    args = parser.parse_args()

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
