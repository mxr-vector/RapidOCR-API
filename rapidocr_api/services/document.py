from typing import Any

from PIL import Image
from pydantic import BaseModel, ConfigDict

from rapidocr_api.services.formatter import formatter


class DocumentBlock(BaseModel):
    """前端消费的文档块结构，保留 RapidDoc 的额外字段。"""

    model_config = ConfigDict(extra="allow")

    type: Any | None = None
    page_no: int | None = None
    content: Any | None = None


def extract_document_blocks(content: list[Any] | None, page_no: int | None = None) -> list[dict[str, Any]]:
    """从 RapidDoc content list 规整出稳定块列表。"""
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
        blocks.append(DocumentBlock.model_validate(block).model_dump(exclude_none=True))
    return blocks


def format_image_document(image: Image.Image, page_no: int | None = None) -> dict[str, Any]:
    """调用文档格式化器并只暴露当前 API 已承诺的字段。"""
    formatted = formatter.format_image(image)
    blocks = extract_document_blocks(formatted.content, page_no)
    result: dict[str, Any] = {"formatted_markdown": formatted.markdown, "blocks": blocks}
    if formatted.layout is not None:
        result["layout"] = formatted.layout
    if formatted.content is not None:
        result["content"] = formatted.content
    return result
