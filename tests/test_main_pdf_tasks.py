import importlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from PIL import Image


def import_main():
    sys.modules.pop("rapidocr_api.main", None)
    with patch("rapidocr.RapidOCR", return_value=Mock()):
        return importlib.import_module("rapidocr_api.main")


class MainPdfTaskTest(unittest.TestCase):
    def setUp(self) -> None:
        self.main = import_main()

    def tearDown(self) -> None:
        sys.modules.pop("rapidocr_api.main", None)

    def test_pdf_task_model_exposes_progress_fields(self) -> None:
        record = self.main.PdfStorageRecord.model_validate(
            {
                "task_id": "task123",
                "knowledge": "default",
                "original_filename": "demo.pdf",
                "filename": "demo.pdf",
                "original_file_path": "storage/task123.pdf",
                "result_file_path": "storage/task123.json",
                "file_size": 1,
                "created_at": "2026-05-12T00:00:00+00:00",
                "status": "running",
                "result_type": "ocr",
                "page_count": 10,
                "processed_pages": 3,
                "current_page": 3,
                "started_at": None,
                "finished_at": None,
                "error": None,
            }
        )

        task = self.main._task_from_record(record)

        self.assertEqual(task.result_type, "ocr")
        self.assertEqual(task.page_count, 10)
        self.assertEqual(task.processed_pages, 3)
        self.assertEqual(task.current_page, 3)

    def test_create_pdf_task_initializes_progress_fields(self) -> None:
        upload = SimpleNamespace(filename="demo.pdf")
        runner = Mock()
        executor = Mock()
        self.main.pdf_task_executor = executor

        with patch.object(self.main, "uuid4", return_value=SimpleNamespace(hex="task123")), patch.object(
            self.main,
            "store_pdf_upload",
            return_value={
                "task_id": "task123",
                "knowledge": "default",
                "original_filename": "demo.pdf",
                "filename": "demo.pdf",
                "original_file_path": "storage/task123.pdf",
                "result_file_path": "storage/task123.json",
                "file_size": 1,
                "created_at": "2026-05-12T00:00:00+00:00",
                "status": "pending",
                "started_at": None,
                "finished_at": None,
                "error": None,
            },
        ), patch.object(self.main, "_update_pdf_task") as update:
            self.main._create_pdf_task(upload, "default", None, None, None, {}, task_runner=runner)

        update.assert_called_once_with(
            "task123",
            result_type="ocr",
            page_count=None,
            processed_pages=0,
            current_page=None,
        )

    def test_create_pdf_ocr_task_uses_markdown_runner_when_requested(self) -> None:
        upload = SimpleNamespace(filename="demo.pdf")
        created = self.main.PdfTaskCreated(task_id="task123", status="pending", result_type="markdown")

        with patch.object(self.main, "is_pdf_upload_file", return_value=True), patch.object(
            self.main, "_create_pdf_task", return_value=created
        ) as create:
            result = self.main.create_pdf_ocr_task(upload, "default", text_score=0.5, is_markdown=True)

        self.assertEqual(result.result_type, "markdown")
        args = create.call_args.args
        kwargs = create.call_args.kwargs
        self.assertTrue(args[5]["return_word_box"])
        self.assertTrue(args[5]["return_single_char_box"])
        self.assertIs(kwargs["task_runner"], self.main._run_pdf_markdown_task)
        self.assertEqual(kwargs["result_type"], "markdown")

    def test_create_pdf_ocr_task_uses_ocr_runner_by_default(self) -> None:
        upload = SimpleNamespace(filename="demo.pdf")
        created = self.main.PdfTaskCreated(task_id="task123", status="pending", result_type="ocr")

        with patch.object(self.main, "is_pdf_upload_file", return_value=True), patch.object(
            self.main, "_create_pdf_task", return_value=created
        ) as create:
            result = self.main.create_pdf_ocr_task(upload, "default")

        self.assertEqual(result.result_type, "ocr")
        self.assertEqual(create.call_args.kwargs, {})

    def test_pdf_task_loads_markdown_result(self) -> None:
        record = self.main.PdfStorageRecord.model_validate(
            {
                "task_id": "task123",
                "knowledge": "default",
                "original_filename": "demo.pdf",
                "filename": "demo.pdf",
                "original_file_path": "storage/task123.pdf",
                "result_file_path": "storage/task123.json",
                "file_size": 1,
                "created_at": "2026-05-12T00:00:00+00:00",
                "status": "succeeded",
                "result_type": "markdown",
                "page_count": 1,
                "processed_pages": 1,
                "current_page": 1,
                "started_at": None,
                "finished_at": None,
                "error": None,
            }
        )

        with patch.object(
            self.main,
            "read_pdf_result",
            return_value={
                "page_count": 1,
                "markdown": "# page",
                "pages": [{"page_no": 1, "markdown": "# page", "blocks": [{"type": "text"}]}],
                "blocks": [{"type": "text"}],
            },
        ):
            task = self.main._task_from_record(record, include_result=True)

        self.assertEqual(task.result_type, "markdown")
        self.assertEqual(task.result.markdown, "# page")
        self.assertEqual(task.result.pages[0].blocks, [{"type": "text"}])

    def test_pdf2md_routes_are_not_registered(self) -> None:
        paths = {route.path for route in self.main.app.routes}

        self.assertNotIn("/ocr/pdf2md", paths)
        self.assertNotIn("/ocr/pdf2md/tasks/{task_id}", paths)

    def test_check_pdf_timeout_disabled_by_default(self) -> None:
        with patch.object(self.main, "PDF_REQUEST_TIMEOUT_SECONDS", 0):
            self.main._check_pdf_timeout(0, 0)

    def test_process_pdf_updates_page_progress(self) -> None:
        images = [Image.new("RGB", (1, 1), "white"), Image.new("RGB", (1, 1), "white")]
        rendered_pages = [
            SimpleNamespace(page_no=1, image=images[0], dpi=72, width=1, height=1),
            SimpleNamespace(page_no=2, image=images[1], dpi=72, width=1, height=1),
        ]
        pdf = MagicMock(page_count=2)
        pdf.__enter__.return_value = pdf
        pdf.__exit__.return_value = False
        self.main.processor = Mock(
            return_value=self.main.OcrResult.model_validate({"0": {"rec_txt": "a"}})
        )

        with patch.object(self.main, "PDF_PAGE_WORKERS", 1), patch.object(
            self.main, "open_pdf", return_value=pdf
        ), patch.object(self.main, "render_pdf_pages", return_value=rendered_pages), patch.object(
            self.main, "_acquire_pdf_slot"
        ), patch.object(
            self.main.pdf_request_semaphore, "release"
        ), patch.object(self.main, "_update_pdf_task") as update:
            result = self.main.process_pdf("demo.pdf", None, None, None, {}, task_id="task123")

        self.assertEqual(result.page_count, 2)
        update.assert_any_call("task123", processed_pages=1, current_page=1)
        update.assert_any_call("task123", processed_pages=2, current_page=2)
        self.assertTrue(all(image.fp is None for image in images if hasattr(image, "fp")))

    def test_process_pdf_markdown_updates_page_progress(self) -> None:
        images = [Image.new("RGB", (1, 1), "white")]
        rendered_pages = [SimpleNamespace(page_no=1, image=images[0], dpi=72, width=1, height=1)]
        pdf = MagicMock(page_count=1)
        pdf.__enter__.return_value = pdf
        pdf.__exit__.return_value = False
        with patch.object(self.main, "PDF_PAGE_WORKERS", 1), patch.object(
            self.main, "open_pdf", return_value=pdf
        ), patch.object(self.main, "render_pdf_pages", return_value=rendered_pages), patch.object(
            self.main, "_acquire_pdf_slot"
        ), patch.object(
            self.main.pdf_request_semaphore, "release"
        ), patch.object(self.main, "_update_pdf_task") as update:
            self.main.formatter = Mock(
                format_image=Mock(
                    return_value=SimpleNamespace(
                        markdown="# page",
                        layout={"layout": []},
                        content=[{"type": "text", "text": "page"}],
                    )
                )
            )
            result = self.main.process_pdf_markdown("demo.pdf", None, None, None, {}, task_id="task123")

        self.assertEqual(result.markdown, "# page")
        self.assertEqual(result.pages[0].layout, {"layout": []})
        self.assertEqual(result.pages[0].content, [{"type": "text", "text": "page"}])
        self.assertEqual(result.pages[0].blocks, [{"type": "text", "text": "page", "page_no": 1}])
        self.assertEqual(result.blocks, [{"type": "text", "text": "page", "page_no": 1}])
        update.assert_any_call("task123", page_count=1, processed_pages=0)
        update.assert_any_call("task123", processed_pages=1, current_page=1)


if __name__ == "__main__":
    unittest.main()
