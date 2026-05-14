from pathlib import Path

from PIL import Image

from rapidocr_api.core.constants import PdfResultType, PdfTaskStatus
from rapidocr_api.schemas.ocr import PdfMarkdownResult, PdfStorageRecord
from rapidocr_api.services import document, pdf_tasks
from rapidocr_api.services.formatter import FormattedDocument


def test_pdf_markdown_schema_fills_missing_fields() -> None:
    result = PdfMarkdownResult.model_validate(
        {
            "page_count": 1,
            "markdown": None,
            "pages": [{"page_no": 1, "markdown": None, "blocks": None}],
            "blocks": None,
        }
    )

    dumped = result.model_dump()

    assert dumped["markdown"] == ""
    assert dumped["blocks"] == []
    assert dumped["resources"] == []
    assert dumped["images"] == []
    assert dumped["layout"] is None
    assert dumped["pages"][0]["markdown"] == ""
    assert dumped["pages"][0]["blocks"] == []
    assert dumped["pages"][0]["resources"] == []
    assert dumped["pages"][0]["images"] == []
    assert dumped["pages"][0]["layout"] is None


def test_pdf_markdown_block_keeps_stable_empty_fields() -> None:
    result = PdfMarkdownResult.model_validate(
        {
            "page_count": 1,
            "pages": [{"page_no": 1, "blocks": [{"type": "text", "content": "hello"}]}],
        }
    )

    block = result.model_dump()["pages"][0]["blocks"][0]

    assert block["type"] == "text"
    assert block["content"] == "hello"
    assert block["resource_id"] is None
    assert block["resource_type"] is None
    assert block["data_type"] is None
    assert block["mime_type"] is None


def test_format_image_document_returns_layout_and_resources(monkeypatch) -> None:
    image = Image.new("RGB", (1, 1))

    def fake_format_image(_image: Image.Image) -> FormattedDocument:
        return FormattedDocument(
            markdown="![img](img-1)",
            layout={"blocks": 1},
            content=[{"type": "image", "img_path": "img-1"}],
            images={"img-1": "data:image/png;base64,AAAA"},
        )

    monkeypatch.setattr(document.formatter, "format_image", fake_format_image)

    formatted = document.format_image_document(image, page_no=2)

    assert formatted["formatted_markdown"] == "![img](img-1)"
    assert formatted["layout"] == {"blocks": 1}
    assert formatted["resources"] == [
        {
            "resource_id": "img-1",
            "page_no": 2,
            "resource_type": "image",
            "data_type": "data_uri",
            "mime_type": "image/png",
            "data": "data:image/png;base64,AAAA",
            "path": None,
            "source_path": "img-1",
            "size_bytes": None,
        }
    ]
    assert formatted["images"] == formatted["resources"]
    assert formatted["blocks"][0]["resource_id"] == "img-1"
    assert formatted["blocks"][0]["data_type"] == "data_uri"
    assert formatted["blocks"][0]["mime_type"] == "image/png"


def test_normalize_document_resources_converts_bytes() -> None:
    resources = document.normalize_document_resources({"raw": b"\x89PNG\r\n\x1a\nabc"}, page_no=1)

    assert resources[0]["resource_id"] == "raw"
    assert resources[0]["data_type"] == "data_uri"
    assert resources[0]["mime_type"] == "image/png"
    assert resources[0]["data"].startswith("data:image/png;base64,")
    assert resources[0]["size_bytes"] == 11


def test_process_pdf_markdown_aggregates_page_fields(monkeypatch) -> None:
    rendered = pdf_tasks.RenderedPagesResult(
        page_results=[
            {
                "formatted_markdown": "page 1",
                "blocks": [{"type": "text", "content": "a"}],
                "layout": {"page": 1},
                "resources": [{"resource_id": "img-1", "resource_type": "image", "data_type": "path", "path": "img-1.png"}],
                "images": [{"resource_id": "img-1", "resource_type": "image", "data_type": "path", "path": "img-1.png"}],
            },
            {"formatted_markdown": "page 2", "blocks": [], "layout": None, "resources": [], "images": []},
        ],
        render_stats=[],
        pdf_page_count=2,
        processed_pages=2,
        elapsed=0.1,
    )

    monkeypatch.setattr(pdf_tasks, "process_rendered_pages", lambda *args, **kwargs: rendered)

    result = pdf_tasks.process_pdf_markdown("demo.pdf", None, None, None, {}, True, None)
    dumped = result.model_dump()

    assert dumped["markdown"] == "page 1\n\npage 2"
    assert dumped["layout"] == {"pages": [{"page_no": 1, "layout": {"page": 1}}]}
    assert dumped["blocks"][0]["page_no"] == 1
    assert dumped["resources"][0]["page_no"] == 1
    assert dumped["images"][0]["page_no"] == 1
    assert dumped["pages"][1]["resources"] == []
    assert dumped["pages"][1]["images"] == []


def test_task_from_record_reports_result_file_state(tmp_path: Path) -> None:
    result_file = tmp_path / "result.json"
    record = PdfStorageRecord(
        task_id="task-1",
        knowledge="kb",
        original_filename="demo.pdf",
        filename="demo.pdf",
        original_file_path=str(tmp_path / "demo.pdf"),
        result_file_path=str(result_file),
        file_size=1,
        created_at="2026-05-14T00:00:00",
        status=PdfTaskStatus.PENDING,
        result_type=PdfResultType.MARKDOWN,
    )

    pending = pdf_tasks.task_from_record(record, include_result=True)

    assert pending.result is None
    assert pending.result_file_exists is False
    assert pending.result_available is False

    result_file.write_text('{"page_count": 0, "pages": []}', encoding="utf-8")
    record.status = PdfTaskStatus.SUCCEEDED
    succeeded = pdf_tasks.task_from_record(record, include_result=True)

    assert succeeded.result_file_exists is True
    assert succeeded.result_available is True
    assert isinstance(succeeded.result, PdfMarkdownResult)
    assert succeeded.result.resources == []
