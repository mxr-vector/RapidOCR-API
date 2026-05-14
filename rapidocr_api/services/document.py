import base64
import binascii
import re
from collections.abc import Mapping
from typing import Any

from PIL import Image
from pydantic import BaseModel, ConfigDict

from rapidocr_api.core.settings import PDF_MARKDOWN_IMAGE_DIR, PDF_MARKDOWN_IMAGE_URL_BASE
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


_IMAGE_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
_MARKDOWN_IMAGE_PATTERN = re.compile(r"(!\[[^\]]*\]\()([^)]*)(\))")
_HTML_IMAGE_SRC_PATTERN = re.compile(r"(<img\b[^>]*\bsrc\s*=\s*[\"'])([^\"']+)([\"'])", re.IGNORECASE)
_REFERENCE_FIELD_NAMES = {"img_path", "path", "src", "image_path"}


def _resource_extension(mime_type: str | None, data: bytes) -> str:
    detected_mime_type = mime_type or _bytes_mime_type(data)
    return _IMAGE_EXTENSIONS.get(detected_mime_type or "", ".bin")


def _safe_asset_stem(resource_id: Any) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", str(resource_id)).strip("-")
    return stem[:80] or "image"


def _decode_resource_data(resource: dict[str, Any]) -> tuple[bytes, str | None] | None:
    data = resource.get("data")
    if not isinstance(data, str) or not data:
        return None
    mime_type = resource.get("mime_type") if isinstance(resource.get("mime_type"), str) else None
    try:
        if data.startswith("data:"):
            header, encoded = data.split(",", 1)
            mime_type = _resource_mime_type(header) or mime_type
            if ";base64" not in header:
                return None
            return base64.b64decode(encoded, validate=True), mime_type
        return base64.b64decode(data, validate=True), mime_type
    except (ValueError, binascii.Error):
        return None


def _asset_url(task_id: str, filename: str) -> str:
    return f"{PDF_MARKDOWN_IMAGE_URL_BASE.rstrip('/')}/{task_id}/{filename}"


def _persist_image_resource(
    resource: dict[str, Any],
    task_id: str,
    page_no: int,
) -> tuple[dict[str, Any], dict[str, str]]:
    updated = dict(resource)
    replacement: dict[str, str] = {}
    existing_path = updated.get("path")
    if isinstance(existing_path, str) and existing_path:
        for key in (updated.get("resource_id"), updated.get("source_path"), existing_path):
            if key:
                replacement[str(key)] = existing_path
        return updated, replacement

    decoded = _decode_resource_data(updated)
    if decoded is None:
        return updated, replacement

    data, mime_type = decoded
    resource_id = updated.get("resource_id") or updated.get("source_path") or "image"
    filename = f"p{page_no}-{_safe_asset_stem(resource_id)}{_resource_extension(mime_type, data)}"
    target_dir = PDF_MARKDOWN_IMAGE_DIR / task_id
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / filename).write_bytes(data)

    url = _asset_url(task_id, filename)
    for key in (updated.get("resource_id"), updated.get("source_path"), updated.get("data")):
        if key:
            replacement[str(key)] = url
    updated["data_type"] = "url"
    updated["mime_type"] = mime_type or _bytes_mime_type(data)
    updated["data"] = None
    updated["path"] = url
    updated["size_bytes"] = len(data)
    return updated, replacement


def _replace_image_references(value: str, replacements: dict[str, str]) -> str:
    if not replacements:
        return value

    def replace_markdown(match: re.Match[str]) -> str:
        target = match.group(2)
        return f"{match.group(1)}{replacements.get(target, target)}{match.group(3)}"

    def replace_html(match: re.Match[str]) -> str:
        target = match.group(2)
        return f"{match.group(1)}{replacements.get(target, target)}{match.group(3)}"

    updated = _MARKDOWN_IMAGE_PATTERN.sub(replace_markdown, value)
    updated = _HTML_IMAGE_SRC_PATTERN.sub(replace_html, updated)
    for old, new in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        if old.startswith("data:image/"):
            updated = updated.replace(old, new)
    return updated


def _replace_structured_references(value: Any, replacements: dict[str, str], key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {item_key: _replace_structured_references(item_value, replacements, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_replace_structured_references(item, replacements, key) for item in value]
    if isinstance(value, str):
        if key in _REFERENCE_FIELD_NAMES and value in replacements:
            return replacements[value]
        return _replace_image_references(value, replacements)
    return value


def finalize_pdf_markdown_page_assets(page: dict[str, Any], task_id: str | None, page_no: int) -> dict[str, Any]:
    """将 PDF Markdown 页内图片资源落盘，并把结果中的图片引用改为 URL。"""
    if task_id is None:
        return page

    replacements: dict[str, str] = {}
    resources: list[dict[str, Any]] = []
    for resource in page.get("resources", []):
        if not isinstance(resource, dict) or resource.get("resource_type") != "image":
            resources.append(resource)
            continue
        updated_resource, resource_replacements = _persist_image_resource(resource, task_id, page_no)
        resources.append(updated_resource)
        replacements.update(resource_replacements)

    updated_page = dict(page)
    updated_page["resources"] = resources
    markdown = updated_page.get("markdown")
    if isinstance(markdown, str):
        updated_page["markdown"] = _replace_image_references(markdown, replacements)
    updated_page["blocks"] = _replace_structured_references(updated_page.get("blocks", []), replacements)
    updated_page["layout"] = _replace_structured_references(updated_page.get("layout"), replacements)
    return updated_page


def format_image_document(image: Image.Image, page_no: int | None = None) -> dict[str, Any]:
    """调用文档格式化器并返回 Markdown、版面和资源信息。"""
    formatted = formatter.format_image(image)
    resources = normalize_document_resources(formatted.images, page_no)
    blocks = extract_document_blocks(formatted.content, page_no, resources)
    return {
        "markdown": formatted.markdown,
        "blocks": blocks,
        "layout": formatted.layout,
        "resources": resources,
    }
