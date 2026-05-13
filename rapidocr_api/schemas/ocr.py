from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from rapidocr_api.core.constants import PdfResultType, PdfTaskStatus


class OcrResult(BaseModel):
    """OCR 单张图片的识别结果容器，允许格式化流程附加扩展字段。"""

    model_config = ConfigDict(extra="allow")


class PdfPageResult(BaseModel):
    """PDF 单页 OCR 结果。"""

    page_no: int
    result: OcrResult


class PdfResult(BaseModel):
    """PDF 文件整体 OCR 结果。"""

    page_count: int
    pages: list[PdfPageResult]


class PdfMarkdownPageResult(BaseModel):
    """PDF 单页 Markdown 与精简结构化块结果。"""

    page_no: int
    markdown: str
    blocks: list[dict[str, Any]] | None = None


class PdfMarkdownResult(BaseModel):
    """PDF 整体 Markdown 结果。"""

    page_count: int
    markdown: str
    pages: list[PdfMarkdownPageResult]
    blocks: list[dict[str, Any]] | None = None


class PdfRenderStat(BaseModel):
    """PDF 渲染统计，主要用于日志记录和任务诊断。"""

    page_no: int
    dpi: int
    width: int
    height: int


class PdfTaskCreated(BaseModel):
    """异步 PDF 任务创建成功后的响应。"""

    task_id: str
    status: PdfTaskStatus = PdfTaskStatus.PENDING
    result_type: PdfResultType = PdfResultType.OCR


class PdfTaskError(BaseModel):
    """后台任务失败时记录的可轮询错误信息。"""

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
    """完整 PDF 存储记录，任务状态字段需兼容索引 JSON。"""

    task_id: str
    status: PdfTaskStatus
    result_type: PdfResultType = PdfResultType.OCR
    page_count: int | None = None
    processed_pages: int = 0
    current_page: int | None = None
    started_at: float | None = None
    finished_at: float | None = None
    error: PdfTaskError | None = None

    @field_validator("started_at", "finished_at", mode="before")
    @classmethod
    def parse_timestamp(cls, value: Any) -> float | None:
        """兼容历史索引中可能存在的 ISO 时间戳字符串。"""
        if value is None or isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value).timestamp()
        return value

    @property
    def file(self) -> PdfStoredFile:
        """按文件视角裁剪存储字段，避免把任务状态混入文件信息。"""
        return PdfStoredFile.model_validate(self.model_dump())


class PdfTask(BaseModel):
    """PDF 任务的完整查询视图，成功时可附带识别结果。"""

    task_id: str
    status: PdfTaskStatus
    result_type: PdfResultType = PdfResultType.OCR
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
