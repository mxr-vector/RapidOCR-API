import unittest
from types import SimpleNamespace

from PIL import Image
from rapidocr import OCRVersion

from rapidocr_api.formatter import DocumentFormatter


class FormatterTest(unittest.TestCase):
    def test_constructor_does_not_initialize_rapid_doc(self) -> None:
        class FailingRapidDoc:
            def __init__(self, **kwargs):
                raise AssertionError("RapidDoc should be lazy-loaded")

        DocumentFormatter(FailingRapidDoc)

    def test_format_image_invokes_rapid_doc_with_model_configs(self) -> None:
        calls = []

        class FakeRapidDoc:
            def __init__(self, **kwargs):
                calls.append(("init", kwargs))

            def __call__(self, data: bytes):
                calls.append(("call", data))
                return SimpleNamespace(
                    markdown="# formatted",
                    middle_json={"layout": []},
                    content_list_json=[{"type": "text"}],
                    images={"images/0.png": b"image"},
                )

        formatter = DocumentFormatter(FakeRapidDoc)
        result = formatter.format_image(Image.new("RGB", (2, 2), "white"))

        self.assertEqual(result.markdown, "# formatted")
        self.assertEqual(result.layout, {"layout": []})
        self.assertEqual(result.content, [{"type": "text"}])
        self.assertEqual(result.images, {"images/0.png": b"image"})
        self.assertEqual(calls[0][0], "init")
        self.assertEqual(calls[0][1]["ocr_config"]["Det.ocr_version"], OCRVersion.PPOCRV5)
        self.assertEqual(calls[0][1]["ocr_config"]["Cls.ocr_version"], OCRVersion.PPOCRV5)
        self.assertEqual(calls[0][1]["ocr_config"]["Rec.ocr_version"], OCRVersion.PPOCRV5)
        self.assertIn("model_dir_or_path", calls[0][1]["layout_config"])
        self.assertIn("model_dir_or_path", calls[0][1]["formula_config"])
        self.assertIn("unet.model_dir_or_path", calls[0][1]["table_config"])
        self.assertIn("slanet_plus.model_dir_or_path", calls[0][1]["table_config"])
        self.assertEqual(calls[1][0], "call")
        self.assertIsInstance(calls[1][1], bytes)


if __name__ == "__main__":
    unittest.main()
