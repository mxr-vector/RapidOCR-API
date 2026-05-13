import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rapidocr_api import utils


class UtilsTest(unittest.TestCase):
    def test_build_pdf_storage_record_uses_posix_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            storage_dir = Path(tmp_dir) / "storage"
            with patch.object(utils, "STORAGE_DIR", storage_dir):
                record = utils.build_pdf_storage_record("demo.pdf", "task123", "default")

        self.assertNotIn("\\", record["original_file_path"])
        self.assertNotIn("\\", record["result_file_path"])
        day = record["created_at"][:10].replace("-", "")
        self.assertTrue(record["original_file_path"].endswith(f"/default/{day}/task123.pdf"))
        self.assertTrue(record["result_file_path"].endswith(f"/default/{day}/task123.json"))


if __name__ == "__main__":
    unittest.main()
