import base64
from collections.abc import Mapping
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
    resource_id: str | None = None
    resource_type: str | None = None
    data_type: str | None = None
    mime_type: str | None = None


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
    "resource_id",
    "resource_type",
    "data_type",
    "mime_type",
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


def _resource_mime_type(data_uri: str) -> str | None:
    if not data_uri.startswith("data:") or ";" not in data_uri:
        return None
    return data_uri[5 : data_uri.find(";")] or None


def _bytes_mime_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def _normalize_document_resource(resource_id: str, value: Any, page_no: int | None) -> dict[str, Any]:
    resource: dict[str, Any] = {
        "resource_id": str(resource_id),
        "page_no": page_no,
        "resource_type": "image",
        "data_type": "unknown",
        "mime_type": None,
        "data": None,
        "path": None,
        "source_path": str(resource_id),
        "size_bytes": None,
    }
    if isinstance(value, str):
        if value.startswith("data:"):
            resource["data_type"] = "data_uri"
            resource["mime_type"] = _resource_mime_type(value)
            resource["data"] = value
        else:
            resource["data_type"] = "url" if _is_url(value) else "path"
            resource["path"] = value
            resource["source_path"] = value
    elif isinstance(value, bytes):
        mime_type = _bytes_mime_type(value)
        encoded = base64.b64encode(value).decode("ascii")
        resource["data_type"] = "data_uri" if mime_type else "base64"
        resource["mime_type"] = mime_type
        resource["data"] = f"data:{mime_type};base64,{encoded}" if mime_type else encoded
        resource["size_bytes"] = len(value)
    return resource


def normalize_document_resources(images: dict[str, Any] | None, page_no: int | None = None) -> list[dict[str, Any]]:
    if not isinstance(images, Mapping):
        return []
    return [_normalize_document_resource(resource_id, value, page_no) for resource_id, value in images.items()]


def _resource_lookup(resources: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for resource in resources:
        for key in (resource.get("resource_id"), resource.get("source_path"), resource.get("path")):
            if key:
                lookup[str(key)] = resource
    return lookup


def extract_document_blocks(
    content: list[Any] | None,
    page_no: int | None = None,
    resources: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """从 RapidDoc content list 规整出稳定块列表。"""
    if not content:
        return []
    resource_by_key = _resource_lookup(resources or [])
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
        img_path = block.get("img_path")
        resource = resource_by_key.get(str(img_path)) if img_path else None
        if resource:
            block.setdefault("resource_id", resource.get("resource_id"))
            block.setdefault("resource_type", resource.get("resource_type"))
            block.setdefault("data_type", resource.get("data_type"))
            block.setdefault("mime_type", resource.get("mime_type"))
        blocks.append(DocumentBlock.model_validate(block).model_dump())
    return blocks


def format_image_document(image: Image.Image, page_no: int | None = None) -> dict[str, Any]:
    """调用文档格式化器并返回 Markdown、版面和资源信息。"""
    formatted = formatter.format_image(image)
    resources = normalize_document_resources(formatted.images, page_no)
    blocks = extract_document_blocks(formatted.content, page_no, resources)
    return {
        "formatted_markdown": formatted.markdown,
        "blocks": blocks,
        "layout": formatted.layout,
        "resources": resources,
        "images": [resource for resource in resources if resource.get("resource_type") == "image"],
    }
