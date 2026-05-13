import importlib
import io
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image


def import_main():
    sys.modules.pop("rapidocr_api.main", None)
    with patch("rapidocr.RapidOCR", return_value=Mock()):
        return importlib.import_module("rapidocr_api.main")


class MainFormattingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.main = import_main()

    def tearDown(self) -> None:
        sys.modules.pop("rapidocr_api.main", None)

    def test_process_image_bytes_default_does_not_add_markdown(self) -> None:
        image = Image.new("RGB", (2, 2), "white")
        self.main.processor = Mock(return_value=self.main.OcrResult.model_validate({"0": {"rec_txt": "a"}}))
        self.main.formatter = Mock()

        with patch.object(self.main, "load_image", return_value=image):
            result = self.main.process_image_bytes(b"image", None, None, None, {}, False)

        self.assertEqual(result.model_dump(), {"0": {"rec_txt": "a"}})
        self.main.formatter.format_image.assert_not_called()

    def test_ensure_db_postprocess_box_type_adds_default_to_existing_detector(self) -> None:
        postprocess_op = SimpleNamespace()
        ocr_engine = SimpleNamespace(text_det=SimpleNamespace(postprocess_op=postprocess_op))

        self.main._ensure_db_postprocess_box_type(ocr_engine)

        self.assertEqual(postprocess_op.box_type, "quad")

    def test_process_image_bytes_markdown_adds_fields(self) -> None:
        image = Image.new("RGB", (2, 2), "white")
        self.main.processor = Mock(return_value=self.main.OcrResult.model_validate({"0": {"rec_txt": "a"}}))
        self.main.formatter = Mock(
            format_image=Mock(
                return_value=SimpleNamespace(
                    markdown="# formatted",
                    layout={"blocks": []},
                    content=[{"type": "text"}],
                )
            )
        )

        with patch.object(self.main, "load_image", return_value=image):
            result = self.main.process_image_bytes(b"image", None, None, None, {}, True)

        self.assertEqual(result.model_dump()["0"], {"rec_txt": "a"})
        self.assertEqual(result.model_dump()["formatted_markdown"], "# formatted")
        self.assertEqual(result.model_dump()["layout"], {"blocks": []})
        self.assertEqual(result.model_dump()["content"], [{"type": "text"}])
        self.assertEqual(result.model_dump()["blocks"], [{"type": "text"}])
        self.main.formatter.format_image.assert_called_once_with(image)

    def test_markdown_route_is_not_registered(self) -> None:
        paths = {route.path for route in self.main.app.routes}

        self.assertNotIn("/ocr/markdown", paths)

    def test_pdf_task_creation_passes_is_markdown_to_runner(self) -> None:
        upload = SimpleNamespace(filename="demo.pdf")
        runner = Mock()
        executor = Mock()
        self.main.pdf_task_executor = executor

        with patch.object(
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
        ), patch.object(self.main, "_update_pdf_task"):
            self.main._create_pdf_task(
                upload, "default", None, None, None, {}, is_markdown=True, task_runner=runner
            )

        args = executor.submit.call_args.args
        self.assertIs(args[0], runner)
        self.assertIs(args[-1], True)

    def test_ocr_endpoint_forwards_is_markdown_for_image_upload(self) -> None:
        png = io.BytesIO()
        Image.new("RGB", (1, 1), "white").save(png, format="PNG")
        upload = SimpleNamespace(file=io.BytesIO(png.getvalue()), filename="demo.png")
        response = SimpleNamespace(status_code=200)

        with patch.object(self.main, "process_image_bytes", return_value=self.main.OcrResult()) as process:
            self.main.ocr(response, image_file=upload, is_markdown=True)

        self.assertIs(process.call_args.args[-1], True)


if __name__ == "__main__":
    unittest.main()
