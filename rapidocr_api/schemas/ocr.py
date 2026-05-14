from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class PdfMarkdownBlock(BaseModel):
    """PDF Markdown 结构化块，保留稳定字段并兼容额外字段。"""

    model_config = ConfigDict(extra="allow")

    type: str | None = None
    page_no: int | None = None
    content: Any | None = None
    bbox: list[int] | None = None
    text_level: int | None = None
    img_path: str | None = None
    resource_id: str | None = None
    resource_type: str | None = None
    data_type: str | None = None
    mime_type: str | None = None


class PdfDocumentResource(BaseModel):
    """PDF Markdown 中引用的图片或文档资源。"""

    model_config = ConfigDict(extra="allow")

    resource_id: str
    page_no: int | None = None
    resource_type: str = "image"
    data_type: str | None = None
    mime_type: str | None = None
    data: str | None = None
    path: str | None = None
    source_path: str | None = None
    size_bytes: int | None = None


class PdfMarkdownPageResult(BaseModel):
    """PDF 单页 Markdown 与结构化结果。"""

    page_no: int
    markdown: str = ""
    blocks: list[PdfMarkdownBlock] = Field(default_factory=list)
    layout: dict[str, Any] | None = None
    resources: list[PdfDocumentResource] = Field(default_factory=list)

    @field_validator("markdown", mode="before")
    @classmethod
    def default_markdown(cls, value: Any) -> str:
        return "" if value is None else value

    @field_validator("blocks", "resources", mode="before")
    @classmethod
    def default_lists(cls, value: Any) -> list[Any]:
        return [] if value is None else value


class PdfMarkdownResult(BaseModel):
    """PDF 整体 Markdown 结果。"""

    page_count: int
    markdown: str = ""
    pages: list[PdfMarkdownPageResult]

    @field_validator("markdown", mode="before")
    @classmethod
    def default_markdown(cls, value: Any) -> str:
        return "" if value is None else value


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
    result_file_exists: bool = False
    result_available: bool = False
    result: PdfResult | PdfMarkdownResult | None = None
    error: PdfTaskError | None = None


class RootResponse(BaseModel):
    """根路径欢迎信息响应模型。"""

    message: str
