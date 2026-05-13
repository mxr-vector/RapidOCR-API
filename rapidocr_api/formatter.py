from dataclasses import dataclass
from functools import cached_property
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image
from rapidocr import OCRVersion

from core.settings import (
    MODEL_FORMULA_RECOGNITION,
    MODEL_OCR_CLS,
    MODEL_OCR_DET,
    MODEL_OCR_REC,
    MODEL_PAGE_LAYOUT,
    MODEL_TABLE_WIRED,
    MODEL_TABLE_WIRELESS,
)

OCR_CONFIG = {
    "Det.model_path": str(MODEL_OCR_DET),
    "Det.ocr_version": OCRVersion.PPOCRV5,
    "Cls.model_path": str(MODEL_OCR_CLS),
    "Cls.ocr_version": OCRVersion.PPOCRV5,
    "Rec.model_path": str(MODEL_OCR_REC),
    "Rec.ocr_version": OCRVersion.PPOCRV5,
}


@dataclass(frozen=True)
class FormattedDocument:
    """文档格式化结果的不可变载体。

    - markdown：文档 Markdown 文本，用于直接返回给上层调用方。
    - layout：版面识别中间结构（如 middle_json），可选。
    - content：结构化内容列表（如 content_list_json），可选。
    - images：可选的图像字节流（如公式截图、表格截图），键为引用标识。
    """

    markdown: str
    layout: dict[str, Any] | None = None
    content: list[Any] | None = None
    images: dict[str, bytes] | None = None


def _path_config(path: str | Path) -> dict[str, str]:
    """将单一模型路径包装成 RapidDoc 所需的配置字典格式。"""
    return {"model_dir_or_path": str(path)}


class DocumentFormatter:
    """RapidDoc 文档理解引擎的惰性封装。

    引擎加载较重，这里通过 ``cached_property`` 延迟初始化，只有首次调用格式化
    接口时才真正创建底层对象，便于在测试中注入 ``rapid_doc_class``。
    """

    def __init__(self, rapid_doc_class: type[Any] | None = None) -> None:
        # 测试可通过传入自定义类来替换真实 RapidDoc，实现依赖解耦。
        self._rapid_doc_class = rapid_doc_class

    @cached_property
    def engine(self) -> Any:
        """按需构造 RapidDoc 引擎，整合版面、OCR、公式和表格所需的模型路径。"""
        rapid_doc_class = self._rapid_doc_class
        if rapid_doc_class is None:
            from rapid_doc import RapidDoc

            rapid_doc_class = RapidDoc
        return rapid_doc_class(
            layout_config=_path_config(MODEL_PAGE_LAYOUT),
            ocr_config=OCR_CONFIG,
            formula_config=_path_config(MODEL_FORMULA_RECOGNITION),
            table_config={
                "unet.model_dir_or_path": str(MODEL_TABLE_WIRED),
                "slanet_plus.model_dir_or_path": str(MODEL_TABLE_WIRELESS),
            },
            formula_enable=True,
            table_enable=True,
            image_output_mode="data_uri",
            preload_model=False,
        )

    def format_image(self, image: Image.Image) -> FormattedDocument:
        """对 PIL 图像进行格式化识别：先编码为 PNG 字节再走字节流入口。"""
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return self.format_bytes(buffer.getvalue())

    def format_bytes(self, data: bytes) -> FormattedDocument:
        """直接对原始字节调用引擎，并把引擎输出映射成 FormattedDocument。"""
        output = self.engine(data)
        return FormattedDocument(
            markdown=getattr(output, "markdown", "") or "",
            layout=getattr(output, "middle_json", None),
            content=getattr(output, "content_list_json", None),
            images=getattr(output, "images", None),
        )


# 模块级单例：应用运行期共享同一个格式化器，避免重复加载模型。
formatter = DocumentFormatter()
