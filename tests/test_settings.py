import importlib
import os
import sys
import unittest
from unittest.mock import patch


SETTING_ENV_VARS = [
    "RAPIDOCR_PROJECT_ROOT",
    "RAPIDOCR_MODEL_ROOT",
    "RAPIDOCR_MODEL_RAPIDOCR_ROOT",
    "RAPIDOCR_MODEL_RAPIDDOC_ROOT",
    "RAPIDOCR_MAX_UPLOAD_FILE_SIZE",
    "RAPIDOCR_PDF_RENDER_DPI",
    "RAPIDOCR_PDF_MIN_RENDER_DPI",
    "RAPIDOCR_PDF_MAX_RENDER_PIXELS",
    "RAPIDOCR_PDF_REQUEST_TIMEOUT_SECONDS",
    "RAPIDOCR_PDF_MAX_CONCURRENT_REQUESTS",
    "RAPIDOCR_PDF_PAGE_WORKERS",
    "RAPIDOCR_KNOWLEDGE_MAX_LENGTH",
    "RAPIDOCR_STORAGE_DIR",
    "RAPIDOCR_MODEL_OCR_DET",
    "RAPIDOCR_MODEL_OCR_CLS",
    "RAPIDOCR_MODEL_OCR_REC",
    "RAPIDOCR_MODEL_PAGE_LAYOUT",
    "RAPIDOCR_MODEL_FORMULA_RECOGNITION",
    "RAPIDOCR_MODEL_TABLE_WIRED",
    "RAPIDOCR_MODEL_TABLE_WIRELESS",
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
        self.assertEqual(settings.PDF_REQUEST_TIMEOUT_SECONDS, 0)
        self.assertEqual(settings.PDF_MAX_CONCURRENT_REQUESTS, 1)
        self.assertEqual(settings.PDF_PAGE_WORKERS, 1)
        self.assertEqual(settings.KNOWLEDGE_MAX_LENGTH, 128)
        self.assertEqual(settings.MODEL_ROOT, settings.PROJECT_ROOT / "models")
        self.assertEqual(settings.RAPIDOCR_MODEL_ROOT, settings.MODEL_ROOT / "RapidOCR")
        self.assertEqual(settings.RAPIDDOC_MODEL_ROOT, settings.MODEL_ROOT / "RapidDoc")
        self.assertEqual(settings.PDF_STORAGE_INDEX, settings.STORAGE_DIR / "index.json")
        self.assertEqual(
            settings.MODEL_OCR_DET.relative_to(settings.PROJECT_ROOT).as_posix(),
            "models/RapidOCR/onnx/PP-OCRv5/det/ch_PP-OCRv5_det_mobile.onnx",
        )
        self.assertEqual(
            settings.MODEL_OCR_CLS.relative_to(settings.PROJECT_ROOT).as_posix(),
            "models/RapidOCR/onnx/PP-OCRv5/cls/ch_PP-LCNet_x0_25_textline_ori_cls_mobile.onnx",
        )
        self.assertEqual(
            settings.MODEL_OCR_REC.relative_to(settings.PROJECT_ROOT).as_posix(),
            "models/RapidOCR/onnx/PP-OCRv5/rec/ch_PP-OCRv5_rec_mobile.onnx",
        )
        self.assertEqual(
            settings.MODEL_PAGE_LAYOUT,
            settings.RAPIDDOC_MODEL_ROOT / "layout" / "PP-DocLayoutV2" / "pp_doclayoutv2.onnx",
        )
        self.assertEqual(
            settings.MODEL_FORMULA_RECOGNITION,
            settings.RAPIDDOC_MODEL_ROOT
            / "formula"
            / "PP-FormulaNet_plus-M"
            / "pp_formulanet_plus_m.onnx",
        )
        self.assertEqual(
            settings.MODEL_TABLE_WIRED,
            settings.RAPIDDOC_MODEL_ROOT / "table" / "SLANeXt_wired" / "slanext_wired.onnx",
        )
        self.assertEqual(
            settings.MODEL_TABLE_WIRELESS,
            settings.RAPIDDOC_MODEL_ROOT
            / "table"
            / "SLANeXt_wireless"
            / "slanext_wireless.onnx",
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

    def test_pdf_page_workers_rejects_non_positive_value(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "RAPIDOCR_PDF_PAGE_WORKERS must be greater than 0",
        ):
            import_settings({"RAPIDOCR_PDF_PAGE_WORKERS": "0"})

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

    def test_model_root_override_updates_default_model_paths(self) -> None:
        settings = import_settings({"RAPIDOCR_MODEL_ROOT": "/opt/models"})

        self.assertEqual(settings.MODEL_ROOT.as_posix(), "/opt/models")
        self.assertEqual(
            settings.MODEL_OCR_DET.as_posix(),
            "/opt/models/RapidOCR/onnx/PP-OCRv5/det/ch_PP-OCRv5_det_mobile.onnx",
        )
        self.assertEqual(
            settings.MODEL_PAGE_LAYOUT.as_posix(),
            "/opt/models/RapidDoc/layout/PP-DocLayoutV2/pp_doclayoutv2.onnx",
        )

    def test_model_family_root_override_updates_default_model_paths(self) -> None:
        settings = import_settings(
            {
                "RAPIDOCR_MODEL_RAPIDOCR_ROOT": "/opt/rapidocr",
                "RAPIDOCR_MODEL_RAPIDDOC_ROOT": "/opt/rapiddoc",
            }
        )

        self.assertEqual(
            settings.MODEL_OCR_CLS.as_posix(),
            "/opt/rapidocr/onnx/PP-OCRv5/cls/ch_PP-LCNet_x0_25_textline_ori_cls_mobile.onnx",
        )
        self.assertEqual(
            settings.MODEL_FORMULA_RECOGNITION.as_posix(),
            "/opt/rapiddoc/formula/PP-FormulaNet_plus-M/pp_formulanet_plus_m.onnx",
        )
        self.assertEqual(
            settings.MODEL_TABLE_WIRELESS.as_posix(),
            "/opt/rapiddoc/table/SLANeXt_wireless/slanext_wireless.onnx",
        )

    def test_individual_model_path_override_wins(self) -> None:
        settings = import_settings(
            {
                "RAPIDOCR_MODEL_ROOT": "/opt/models",
                "RAPIDOCR_MODEL_OCR_REC": "/custom/rec.onnx",
                "RAPIDOCR_MODEL_TABLE_WIRED": "/custom/table-wired.onnx",
            }
        )

        self.assertEqual(settings.MODEL_OCR_REC.as_posix(), "/custom/rec.onnx")
        self.assertEqual(settings.MODEL_TABLE_WIRED.as_posix(), "/custom/table-wired.onnx")

    def test_empty_path_env_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "RAPIDOCR_MODEL_ROOT must not be empty"):
            import_settings({"RAPIDOCR_MODEL_ROOT": " "})

    def test_storage_dir_must_not_be_filesystem_root(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "RAPIDOCR_STORAGE_DIR must not be a filesystem root",
        ):
            import_settings({"RAPIDOCR_STORAGE_DIR": "/"})

    def test_posix_path_serializes_with_forward_slashes(self) -> None:
        settings = import_settings()

        self.assertEqual(
            settings.posix_path("storage\\default\\file.pdf"),
            "storage/default/file.pdf",
        )


if __name__ == "__main__":
    unittest.main()
