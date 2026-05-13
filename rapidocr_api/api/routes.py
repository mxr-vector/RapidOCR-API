from fastapi import File, Form, Path, UploadFile, APIRouter, HTTPException, Response


from rapidocr_api.core.constants import (
    ERROR_KNOWLEDGE_REQUIRED,
    ERROR_MISSING_IMAGE_SOURCE,
    ERROR_ONLY_ONE_IMAGE_SOURCE,
    ERROR_PDF_REQUIRED,
    ERROR_PDF_TASK_NOT_FOUND,
    ROOT_MESSAGE,
    PdfResultType,
)
from rapidocr_api.schemas.ocr import OcrResult, PdfTask, PdfTaskCreated, RootResponse
from rapidocr_api.services.ocr import process_image_bytes
from rapidocr_api.services.pdf_tasks import (
    build_markdown_ocr_kwargs,
    create_pdf_task,
    load_pdf_storage_record,
    run_pdf_markdown_task,
    task_from_record,
)
from rapidocr_api.services.utils import build_ocr_kwargs, decode_base64_payload, is_pdf_upload_file, read_upload_file
from typing import Annotated

ImageFileForm = Annotated[UploadFile | None, File(description="上传图片或 PDF 文件")]
MarkdownImageFileForm = Annotated[UploadFile | None, File(description="上传图片文件")]
PdfFileForm = Annotated[UploadFile, File(description="上传 PDF 文件")]
ImageDataForm = Annotated[str | None, Form(description="图片 base64 字符串，支持 data URI")]
PdfKnowledgeForm = Annotated[str, Form(description="知识库标识，用于 PDF 存储目录")]
OptionalKnowledgeForm = Annotated[str | None, Form(description="知识库标识，用于 PDF 存储目录")]
UseDetForm = Annotated[bool | None, Form(description="是否启用文本检测")]
UseClsForm = Annotated[bool | None, Form(description="是否启用方向分类")]
UseRecForm = Annotated[bool | None, Form(description="是否启用文本识别")]
TextScoreForm = Annotated[float | None, Form(ge=0, le=1, description="文本置信度阈值，范围 0 到 1")]
ReturnWordBoxForm = Annotated[bool | None, Form(description="是否返回词级文本框")]
ReturnSingleCharBoxForm = Annotated[bool | None, Form(description="是否返回单字符文本框")]
IsMarkdownForm = Annotated[bool | None, Form(description="是否返回 Markdown 与结构化排版信息")]
TaskIdPath = Annotated[str, Path(description="PDF OCR 任务 ID")]
router = APIRouter()


@router.get("/")
def root() -> RootResponse:
    """根路径健康检查：返回欢迎信息。"""
    return RootResponse(message=ROOT_MESSAGE)


@router.post("/ocr/pdf", status_code=202, response_model=PdfTaskCreated)
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
        raise HTTPException(status_code=400, detail=ERROR_PDF_REQUIRED)
    if is_markdown:
        return create_pdf_task(
            pdf_file,
            knowledge,
            use_det,
            use_cls,
            use_rec,
            build_markdown_ocr_kwargs(text_score),
            is_markdown=True,
            task_runner=run_pdf_markdown_task,
            result_type=PdfResultType.MARKDOWN,
        )
    ocr_kwargs = build_ocr_kwargs(text_score, return_word_box, return_single_char_box)
    return create_pdf_task(pdf_file, knowledge, use_det, use_cls, use_rec, ocr_kwargs)


@router.get("/ocr/pdf/tasks/{task_id}", response_model=PdfTask)
def get_pdf_ocr_task(task_id: TaskIdPath) -> PdfTask:
    """查询 PDF OCR 任务状态；成功时一并返回结果 JSON。"""
    record = load_pdf_storage_record(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail=ERROR_PDF_TASK_NOT_FOUND)
    return task_from_record(record, include_result=True)


@router.post("/ocr", response_model=OcrResult | PdfTaskCreated)
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
    """OCR 统一入口：图片同步返回结果，PDF 创建异步任务。"""
    if image_file and image_data:
        raise HTTPException(status_code=400, detail=ERROR_ONLY_ONE_IMAGE_SOURCE)

    ocr_kwargs = build_ocr_kwargs(text_score, return_word_box, return_single_char_box)
    if image_file:
        if is_pdf_upload_file(image_file):
            if knowledge is None or not knowledge.strip():
                raise HTTPException(status_code=400, detail=ERROR_KNOWLEDGE_REQUIRED)
            response.status_code = 202
            if is_markdown:
                return create_pdf_task(
                    image_file,
                    knowledge,
                    use_det,
                    use_cls,
                    use_rec,
                    build_markdown_ocr_kwargs(text_score),
                    is_markdown=True,
                    task_runner=run_pdf_markdown_task,
                    result_type=PdfResultType.MARKDOWN,
                )
            return create_pdf_task(image_file, knowledge, use_det, use_cls, use_rec, ocr_kwargs)
        data, _ = read_upload_file(image_file)
        return process_image_bytes(data, use_det, use_cls, use_rec, ocr_kwargs, bool(is_markdown))

    if image_data:
        return process_image_bytes(
            decode_base64_payload(image_data), use_det, use_cls, use_rec, ocr_kwargs, bool(is_markdown)
        )

    raise HTTPException(status_code=400, detail=ERROR_MISSING_IMAGE_SOURCE)
