# -*- encoding: utf-8 -*-
# @Author: SWHL
# @Contact: liekkaskono@163.com
import argparse
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path as FilePath
from typing import Annotated, Any, Dict, Literal, Optional
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
    is_pdf_input,
    load_image,
    open_pdf,
    read_upload_file,
    render_pdf_pages,
)

# Starlette 会先按单个 multipart part 限制读取表单字段，再按文件限制读取上传内容。
# 两个限制都使用同一个总上传配置，避免 PDF、图片和表单字段各有一套大小口径。
MultiPartParser.max_part_size = MAX_UPLOAD_FILE_SIZE
MultiPartParser.max_file_size = MAX_UPLOAD_FILE_SIZE


class OcrResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    rec_txt_all: str = ""


class PdfTaskCreated(BaseModel):
    task_id: str
    status: Literal["pending"]


class PdfTaskError(BaseModel):
    status_code: int
    detail: Any


class PdfTask(BaseModel):
    task_id: str
    status: Literal["pending", "running", "succeeded", "failed"]
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    result: Dict[str, Any] | None = None
    error: PdfTaskError | None = None


class RootResponse(BaseModel):
    message: str


ImageFileForm = Annotated[UploadFile | None, File(description="上传图片或 PDF 文件")]
PdfFileForm = Annotated[UploadFile, File(description="上传 PDF 文件")]
ImageDataForm = Annotated[str | None, Form(description="图片 base64 字符串，支持 data URI")]
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
pdf_tasks_lock = threading.Lock()
# 任务结果保存在当前进程内存中；多 worker 部署时，每个 worker 只知道自己创建的任务。
pdf_tasks: Dict[str, Dict[str, Any]] = {}


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

    def __call__(
        self,
        ori_img: Image.Image,
        use_det: Optional[bool] = None,
        use_cls: Optional[bool] = None,
        use_rec: Optional[bool] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        img = np.array(ImageOps.exif_transpose(ori_img).convert("RGB"))
        try:
            ocr_res = self.ocr(
                img, use_det=use_det, use_cls=use_cls, use_rec=use_rec, **kwargs
            )
        finally:
            del img

        if ocr_res.boxes is None or ocr_res.txts is None or ocr_res.scores is None:
            return {}

        out_dict: Dict[str, Any] = {"rec_txt_all": " ".join(ocr_res.txts)}
        for i, (boxes, txt, score) in enumerate(
            zip(ocr_res.boxes, ocr_res.txts, ocr_res.scores)
        ):
            out_dict[str(i)] = {
                "rec_txt": txt,
                "dt_boxes": boxes.tolist(),
                "score": float(score),
            }
        return out_dict


app = FastAPI(title="RapidOCR API", version="0.1.0")
processor = OCRAPIUtils()


@app.get("/")
def root() -> RootResponse:
    return RootResponse(message="Welcome to RapidOCR API Server!")


def _create_pdf_task(
    pdf_data: bytes,
    use_det: Optional[bool],
    use_cls: Optional[bool],
    use_rec: Optional[bool],
    ocr_kwargs: Dict[str, Any],
) -> Dict[str, str]:
    task_id = uuid4().hex
    task = {
        "task_id": task_id,
        "status": "pending",
        "created_at": _utc_now_iso(),
        "started_at": None,
        "finished_at": None,
        "result": None,
        "error": None,
    }
    with pdf_tasks_lock:
        pdf_tasks[task_id] = task

    pdf_task_executor.submit(
        _run_pdf_task, task_id, pdf_data, use_det, use_cls, use_rec, ocr_kwargs
    )
    return {"task_id": task_id, "status": "pending"}


@app.post("/ocr/pdf", status_code=202, response_model=PdfTaskCreated)
def create_pdf_ocr_task(
    pdf_file: PdfFileForm,
    use_det: UseDetForm = None,
    use_cls: UseClsForm = None,
    use_rec: UseRecForm = None,
    text_score: TextScoreForm = None,
    return_word_box: ReturnWordBoxForm = None,
    return_single_char_box: ReturnSingleCharBoxForm = None,
) -> Dict[str, str]:
    pdf_data, _ = read_upload_file(pdf_file)
    ocr_kwargs = build_ocr_kwargs(text_score, return_word_box, return_single_char_box)
    return _create_pdf_task(pdf_data, use_det, use_cls, use_rec, ocr_kwargs)


@app.get("/ocr/pdf/tasks/{task_id}", response_model=PdfTask)
def get_pdf_ocr_task(task_id: TaskIdPath) -> Dict[str, Any]:
    with pdf_tasks_lock:
        task = pdf_tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="PDF OCR task not found.")
        return dict(task)


@app.post("/ocr", response_model=OcrResult | PdfTaskCreated)
def ocr(
    response: Response,
    image_file: ImageFileForm = None,
    image_data: ImageDataForm = None,
    use_det: UseDetForm = None,
    use_cls: UseClsForm = None,
    use_rec: UseRecForm = None,
    text_score: TextScoreForm = None,
    return_word_box: ReturnWordBoxForm = None,
    return_single_char_box: ReturnSingleCharBoxForm = None,
) -> Dict[str, Any] | Dict[str, str]:
    # 统一入口只允许一种输入来源，避免同一次请求产生不确定的识别对象。
    if image_file and image_data:
        raise HTTPException(
            status_code=400, detail="Only one of image_file or image_data can be provided."
        )

    ocr_kwargs = build_ocr_kwargs(text_score, return_word_box, return_single_char_box)
    if image_file:
        data, source_name = read_upload_file(image_file)
        # PDF 是长任务，即使从统一 /ocr 入口上传也改为返回 task_id，避免同一类文件出现两种响应语义。
        if is_pdf_input(data, source_name):
            response.status_code = 202
            return _create_pdf_task(data, use_det, use_cls, use_rec, ocr_kwargs)
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


# 任务状态只在这个函数中成组更新，保证查询接口不会读到半写入的状态。
def _update_pdf_task(task_id: str, **updates: Any) -> None:
    with pdf_tasks_lock:
        task = pdf_tasks.get(task_id)
        if task is not None:
            task.update(updates)


# 后台线程不能直接把异常抛给客户端，因此把 HTTPException 转成可轮询的 failed 状态。
def _run_pdf_task(
    task_id: str,
    pdf_data: bytes,
    use_det: Optional[bool],
    use_cls: Optional[bool],
    use_rec: Optional[bool],
    ocr_kwargs: Dict[str, Any],
) -> None:
    _update_pdf_task(task_id, status="running", started_at=_utc_now_iso())
    try:
        result = process_pdf(pdf_data, use_det, use_cls, use_rec, ocr_kwargs)
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
            result=result,
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
    ocr_kwargs: Dict[str, Any],
) -> Dict[str, Any]:
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


def process_pdf(
    pdf_data: bytes,
    use_det: Optional[bool],
    use_cls: Optional[bool],
    use_rec: Optional[bool],
    ocr_kwargs: Dict[str, Any],
) -> Dict[str, Any]:
    _acquire_pdf_slot()
    started_at = time.monotonic()
    pages = []
    render_stats = []
    processed_pages = 0
    pdf_page_count = 0

    try:
        with open_pdf(pdf_data) as pdf:
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
                        {
                            "page_no": rendered_page.page_no,
                            "rec_txt_all": result.get("rec_txt_all", ""),
                            "result": result,
                        }
                    )
                    render_stats.append(
                        {
                            "page_no": rendered_page.page_no,
                            "dpi": rendered_page.dpi,
                            "width": rendered_page.width,
                            "height": rendered_page.height,
                        }
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
    return {
        "page_count": len(pages),
        "rec_txt_all": "\n".join(page["rec_txt_all"] for page in pages if page["rec_txt_all"]),
        "pages": pages,
    }


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
