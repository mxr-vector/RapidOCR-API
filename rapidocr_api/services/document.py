from typing import Any

from PIL import Image
from pydantic import BaseModel, ConfigDict

from rapidocr_api.services.formatter import formatter


class DocumentBlock(BaseModel):
    """前端消费的精简文档块结构。"""

    model_config = ConfigDict(extra="allow")

    type: str | None = None
    page_no: int | None = None
    content: Any | None = None
    bbox: list[int] | None = None
    text_level: int | None = None
    img_path: str | None = None


BLOCK_TYPE_ALIASES = {
    "doc_title": "title",
    "paragraph_title": "title",
    "interline_equation": "equation",
    "inline_equation": "equation",
}

BLOCK_EXTRA_FIELDS = (
    "bbox",
    "text_level",
    "img_path",
    "text_format",
    "image_caption",
    "image_footnote",
    "table_caption",
    "table_body",
    "table_footnote",
)


def _document_block_type(block: dict[str, Any]) -> str | None:
    block_type = block.get("type") or block.get("block_type") or block.get("category")
    if block.get("text_level") is not None:
        return "title"
    if block_type is None:
        return None
    return BLOCK_TYPE_ALIASES.get(str(block_type), str(block_type))


def _document_block_page_no(block: dict[str, Any], page_no: int | None) -> int | None:
    if page_no is not None:
        return page_no
    page_idx = block.get("page_idx")
    if isinstance(page_idx, int):
        return page_idx + 1
    return None


def _document_block_content(block: dict[str, Any]) -> Any | None:
    for key in ("text", "table_body", "html", "content"):
        value = block.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def extract_document_blocks(content: list[Any] | None, page_no: int | None = None) -> list[dict[str, Any]]:
    """从 RapidDoc content list 规整出稳定块列表。"""
    if not content:
        return []
    blocks: list[dict[str, Any]] = []
    for item in content:
        source = dict(item) if isinstance(item, dict) else {"content": item}
        block: dict[str, Any] = {}
        block_type = _document_block_type(source)
        if block_type is not None:
            block["type"] = block_type
        normalized_page_no = _document_block_page_no(source, page_no)
        if normalized_page_no is not None:
            block["page_no"] = normalized_page_no
        block_content = _document_block_content(source)
        if block_content is not None:
            block["content"] = block_content
        for key in BLOCK_EXTRA_FIELDS:
            value = source.get(key)
            if value not in (None, "", [], {}):
                block[key] = value
        blocks.append(DocumentBlock.model_validate(block).model_dump(exclude_none=True))
    return blocks


def format_image_document(image: Image.Image, page_no: int | None = None) -> dict[str, Any]:
    """调用文档格式化器并返回 Markdown 与精简块列表。"""
    formatted = formatter.format_image(image)
    return {"formatted_markdown": formatted.markdown, "blocks": extract_document_blocks(formatted.content, page_no)}
