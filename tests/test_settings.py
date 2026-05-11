import importlib
import os
import sys
import unittest
from unittest.mock import patch


SETTING_ENV_VARS = [
    "RAPIDOCR_MAX_UPLOAD_FILE_SIZE",
    "RAPIDOCR_PDF_RENDER_DPI",
    "RAPIDOCR_PDF_MIN_RENDER_DPI",
    "RAPIDOCR_PDF_MAX_RENDER_PIXELS",
    "RAPIDOCR_PDF_REQUEST_TIMEOUT_SECONDS",
    "RAPIDOCR_PDF_MAX_CONCURRENT_REQUESTS",
    "RAPIDOCR_KNOWLEDGE_MAX_LENGTH",
    "RAPIDOCR_STORAGE_DIR",
    "RAPIDOCR_MODEL_OCR_DET",
    "RAPIDOCR_MODEL_OCR_CLS",
    "RAPIDOCR_MODEL_OCR_REC",
    "RAPIDOCR_MODEL_PAGE_LAYOUT",
    "RAPIDOCR_MODEL_FORMULA_RECOGNITION",
]


def import_settings(env: dict[str, str] | None = None):
    clean_env = {key: value for key, value in os.environ.items() if key not in SETTING_ENV_VARS}
    clean_env.update(env or {})
    sys.modules.pop("core.settings", None)
    with patch.dict(os.environ, clean_env, clear=True):
        return importlib.import_module("core.settings")


class SettingsTest(unittest.TestCase):
    def tearDown(self) -> None:
        sys.modules.pop("core.settings", None)

    def test_default_values(self) -> None:
        settings = import_settings()

        self.assertEqual(settings.MAX_UPLOAD_FILE_SIZE, 20 * 1024 * 1024)
        self.assertEqual(settings.PDF_RENDER_DPI, 150)
        self.assertEqual(settings.PDF_MIN_RENDER_DPI, 72)
        self.assertEqual(settings.PDF_MAX_RENDER_PIXELS, 12_000_000)
        self.assertEqual(settings.PDF_REQUEST_TIMEOUT_SECONDS, 600)
        self.assertEqual(settings.PDF_MAX_CONCURRENT_REQUESTS, 1)
        self.assertEqual(settings.KNOWLEDGE_MAX_LENGTH, 128)
        self.assertEqual(settings.PDF_STORAGE_INDEX, settings.STORAGE_DIR / "index.json")
        self.assertEqual(
            settings.MODEL_OCR_DET.as_posix(),
            "models/RapidOCR/onnx/PP-OCRv4/det/multi_PP-OCRv3_det_mobile.onnx",
        )
        self.assertEqual(
            settings.MODEL_OCR_CLS.as_posix(),
            "models/RapidOCR/onnx/PP-OCRv4/cls/ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        )
        self.assertEqual(
            settings.MODEL_OCR_REC.as_posix(),
            "models/RapidOCR/onnx/PP-OCRv5/rec/ch_PP-OCRv5_rec_mobile.onnx",
        )

    def test_positive_integer_env_rejects_invalid_value(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError, "RAPIDOCR_MAX_UPLOAD_FILE_SIZE must be greater than 0"
        ):
            import_settings({"RAPIDOCR_MAX_UPLOAD_FILE_SIZE": "0"})

    def test_non_negative_integer_env_rejects_invalid_value(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "RAPIDOCR_PDF_REQUEST_TIMEOUT_SECONDS must be greater than or equal to 0",
        ):
            import_settings({"RAPIDOCR_PDF_REQUEST_TIMEOUT_SECONDS": "-1"})

    def test_pdf_min_dpi_must_not_exceed_render_dpi(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "RAPIDOCR_PDF_MIN_RENDER_DPI must not exceed RAPIDOCR_PDF_RENDER_DPI",
        ):
            import_settings(
                {
                    "RAPIDOCR_PDF_RENDER_DPI": "100",
                    "RAPIDOCR_PDF_MIN_RENDER_DPI": "101",
                }
            )

    def test_env_overrides_paths_and_limits(self) -> None:
        settings = import_settings(
            {
                "RAPIDOCR_MAX_UPLOAD_FILE_SIZE": "1048576",
                "RAPIDOCR_STORAGE_DIR": "/var/lib/rapidocr",
                "RAPIDOCR_MODEL_OCR_REC": "/models/rec.onnx",
            }
        )

        self.assertEqual(settings.MAX_UPLOAD_FILE_SIZE, 1_048_576)
        self.assertEqual(settings.STORAGE_DIR.as_posix(), "/var/lib/rapidocr")
        self.assertEqual(settings.PDF_STORAGE_INDEX.as_posix(), "/var/lib/rapidocr/index.json")
        self.assertEqual(settings.MODEL_OCR_REC.as_posix(), "/models/rec.onnx")


if __name__ == "__main__":
    unittest.main()
