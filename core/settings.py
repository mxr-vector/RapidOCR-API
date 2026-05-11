import os
from pathlib import Path


def _read_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc
    if parsed <= 0:
        raise RuntimeError(f"{name} must be greater than 0.")
    return parsed


def _read_non_negative_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc
    if parsed < 0:
        raise RuntimeError(f"{name} must be greater than or equal to 0.")
    return parsed


def _read_path_env(name: str, default: str | Path) -> Path:
    return Path(os.getenv(name, str(default)))


# 上传限制：图片、PDF 和 base64 解码后的二进制共用同一大小上限。
MAX_UPLOAD_FILE_SIZE = _read_int_env("RAPIDOCR_MAX_UPLOAD_FILE_SIZE", 20 * 1024 * 1024)

# PDF 渲染配置：按目标 DPI 渲染，超出像素预算时降到最小 DPI。
PDF_RENDER_DPI = _read_int_env("RAPIDOCR_PDF_RENDER_DPI", 150)
PDF_MIN_RENDER_DPI = _read_int_env("RAPIDOCR_PDF_MIN_RENDER_DPI", 72)
PDF_MAX_RENDER_PIXELS = _read_int_env("RAPIDOCR_PDF_MAX_RENDER_PIXELS", 12_000_000)

# PDF 后台任务配置：超时为 0 表示不启用内部处理超时。
PDF_REQUEST_TIMEOUT_SECONDS = _read_non_negative_int_env("RAPIDOCR_PDF_REQUEST_TIMEOUT_SECONDS", 600)
PDF_MAX_CONCURRENT_REQUESTS = _read_int_env("RAPIDOCR_PDF_MAX_CONCURRENT_REQUESTS", 1)

# 存储目录配置：PDF 原文件、结果文件和索引文件都写入该目录下。
KNOWLEDGE_MAX_LENGTH = _read_int_env("RAPIDOCR_KNOWLEDGE_MAX_LENGTH", 128)
STORAGE_DIR = _read_path_env("RAPIDOCR_STORAGE_DIR", "storage")
PDF_STORAGE_INDEX = STORAGE_DIR / "index.json"

# OCR 模型路径配置：用于 RapidOCR 的检测、方向分类和文本行识别模型。
MODEL_OCR_DET = _read_path_env(
    "RAPIDOCR_MODEL_OCR_DET",
    "models/RapidOCR/onnx/PP-OCRv4/det/multi_PP-OCRv3_det_mobile.onnx",
)
MODEL_OCR_CLS = _read_path_env(
    "RAPIDOCR_MODEL_OCR_CLS",
    "models/RapidOCR/onnx/PP-OCRv4/cls/ch_ppocr_mobile_v2.0_cls_mobile.onnx",
)
MODEL_OCR_REC = _read_path_env(
    "RAPIDOCR_MODEL_OCR_REC",
    "models/RapidOCR/onnx/PP-OCRv5/rec/ch_PP-OCRv5_rec_mobile.onnx",
)

# 文档理解模型路径配置：供版面识别和公式识别能力复用。
MODEL_PAGE_LAYOUT = _read_path_env("RAPIDOCR_MODEL_PAGE_LAYOUT", "models/PP-DocLayoutV2")
MODEL_FORMULA_RECOGNITION = _read_path_env(
    "RAPIDOCR_MODEL_FORMULA_RECOGNITION", "models/PP-FormulaNet_plus-M"
)

if PDF_MIN_RENDER_DPI > PDF_RENDER_DPI:
    raise RuntimeError("RAPIDOCR_PDF_MIN_RENDER_DPI must not exceed RAPIDOCR_PDF_RENDER_DPI.")
