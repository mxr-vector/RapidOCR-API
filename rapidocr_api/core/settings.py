import os
from pathlib import Path


def _read_int_env(name: str, default: int) -> int:
    """读取正整数类型的环境变量，未设置时返回默认值；非整数或非正值会抛错。"""
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
    """读取非负整数类型的环境变量，允许 0 值，用于表达“关闭/不启用”语义。"""
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
    """读取路径类型的环境变量，空字符串视为非法配置。"""
    value = os.getenv(name)
    if value is None:
        return Path(default)
    if not value.strip():
        raise RuntimeError(f"{name} must not be empty.")
    return Path(value)


def _read_str_env(name: str, default: str) -> str:
    """读取字符串类型的环境变量，空字符串视为非法配置。"""
    value = os.getenv(name)
    if value is None:
        return default
    if not value.strip():
        raise RuntimeError(f"{name} must not be empty.")
    return value


def posix_path(path: str | Path) -> str:
    """将路径统一转换为 POSIX 风格字符串，便于跨平台写入索引和结果文件。"""
    return Path(path).as_posix()


# 项目和模型根目录配置：允许通过环境变量覆盖，便于容器化部署时挂载外部模型目录。
PROJECT_ROOT = _read_path_env("RAPIDOCR_PROJECT_ROOT", Path(__file__).resolve().parents[2])
MODEL_ROOT = _read_path_env("RAPIDOCR_MODEL_ROOT", PROJECT_ROOT / "models")
RAPIDOCR_MODEL_ROOT = _read_path_env("RAPIDOCR_MODEL_RAPIDOCR_ROOT", MODEL_ROOT / "RapidOCR")
RAPIDDOC_MODEL_ROOT = _read_path_env("RAPIDOCR_MODEL_RAPIDDOC_ROOT", MODEL_ROOT / "RapidDoc")

# 上传限制：图片、PDF 和 base64 解码后的二进制共用同一大小上限。
MAX_UPLOAD_FILE_SIZE = _read_int_env("RAPIDOCR_MAX_UPLOAD_FILE_SIZE", 20 * 1024 * 1024)

# PDF 渲染配置：按目标 DPI 渲染，超出像素预算时降到最小 DPI。
PDF_RENDER_DPI = _read_int_env("RAPIDOCR_PDF_RENDER_DPI", 150)
PDF_MIN_RENDER_DPI = _read_int_env("RAPIDOCR_PDF_MIN_RENDER_DPI", 72)
PDF_MAX_RENDER_PIXELS = _read_int_env("RAPIDOCR_PDF_MAX_RENDER_PIXELS", 12_000_000)

# PDF 后台任务配置：超时为 0 表示不启用内部处理超时。
PDF_REQUEST_TIMEOUT_SECONDS = _read_non_negative_int_env("RAPIDOCR_PDF_REQUEST_TIMEOUT_SECONDS", 0)
PDF_MAX_CONCURRENT_REQUESTS = _read_int_env("RAPIDOCR_PDF_MAX_CONCURRENT_REQUESTS", 1)
PDF_PAGE_WORKERS = _read_int_env("RAPIDOCR_PDF_PAGE_WORKERS", 1)

# 存储目录配置：PDF 原文件、结果文件和索引文件都写入该目录下。
KNOWLEDGE_MAX_LENGTH = _read_int_env("RAPIDOCR_KNOWLEDGE_MAX_LENGTH", 128)
STORAGE_DIR = _read_path_env("RAPIDOCR_STORAGE_DIR", PROJECT_ROOT / "storage")
PDF_STORAGE_INDEX = STORAGE_DIR / "index.json"

# 公开静态资源配置：PDF Markdown 图片默认写入 public/pdf-images 并通过 /public 访问。
PUBLIC_DIR = _read_path_env("RAPIDOCR_PUBLIC_DIR", PROJECT_ROOT / "public")
PUBLIC_ROUTE_PREFIX = _read_str_env("RAPIDOCR_PUBLIC_ROUTE_PREFIX", "/public")
PDF_MARKDOWN_IMAGE_DIR = _read_path_env("RAPIDOCR_PDF_MARKDOWN_IMAGE_DIR", PUBLIC_DIR / "pdf-images")
PDF_MARKDOWN_IMAGE_URL_BASE = _read_str_env(
    "RAPIDOCR_PDF_MARKDOWN_IMAGE_URL_BASE",
    f"{PUBLIC_ROUTE_PREFIX.rstrip('/')}/pdf-images",
)

# OCR 模型路径配置：用于 RapidOCR 的检测、方向分类和文本行识别模型。
MODEL_OCR_DET = _read_path_env(
    "RAPIDOCR_MODEL_OCR_DET",
    RAPIDOCR_MODEL_ROOT / "onnx" / "PP-OCRv5" / "det" / "ch_PP-OCRv5_det_mobile.onnx",
)
MODEL_OCR_CLS = _read_path_env(
    "RAPIDOCR_MODEL_OCR_CLS",
    RAPIDOCR_MODEL_ROOT / "onnx" / "PP-OCRv5" / "cls" / "ch_PP-LCNet_x0_25_textline_ori_cls_mobile.onnx",
)
MODEL_OCR_REC = _read_path_env(
    "RAPIDOCR_MODEL_OCR_REC",
    RAPIDOCR_MODEL_ROOT / "onnx" / "PP-OCRv5" / "rec" / "ch_PP-OCRv5_rec_mobile.onnx",
)

# 文档理解模型路径配置：供版面识别和公式识别能力复用。
MODEL_PAGE_LAYOUT = _read_path_env(
    "RAPIDOCR_MODEL_PAGE_LAYOUT",
    RAPIDDOC_MODEL_ROOT / "layout" / "PP-DocLayoutV2" / "pp_doclayoutv2.onnx",
)
MODEL_FORMULA_RECOGNITION = _read_path_env(
    "RAPIDOCR_MODEL_FORMULA_RECOGNITION",
    RAPIDDOC_MODEL_ROOT / "formula" / "PP-FormulaNet_plus-M" / "pp_formulanet_plus_m.onnx",
)
MODEL_TABLE_WIRED = _read_path_env(
    "RAPIDOCR_MODEL_TABLE_WIRED",
    RAPIDDOC_MODEL_ROOT / "table" / "SLANeXt_wired" / "slanext_wired.onnx",
)
MODEL_TABLE_WIRELESS = _read_path_env(
    "RAPIDOCR_MODEL_TABLE_WIRELESS",
    RAPIDDOC_MODEL_ROOT / "table" / "SLANeXt_wireless" / "slanext_wireless.onnx",
)

if PDF_MIN_RENDER_DPI > PDF_RENDER_DPI:
    raise RuntimeError("RAPIDOCR_PDF_MIN_RENDER_DPI must not exceed RAPIDOCR_PDF_RENDER_DPI.")
if PDF_MIN_RENDER_DPI <= 0 or PDF_RENDER_DPI <= 0:
    raise RuntimeError("PDF render DPI settings must be greater than 0.")
if PDF_MAX_RENDER_PIXELS < 1:
    raise RuntimeError("RAPIDOCR_PDF_MAX_RENDER_PIXELS must be greater than 0.")
# 防御性检查：避免将存储目录误配置为文件系统根目录导致误操作。
if STORAGE_DIR == STORAGE_DIR.parent:
    raise RuntimeError("RAPIDOCR_STORAGE_DIR must not be a filesystem root.")
if PUBLIC_DIR == PUBLIC_DIR.parent:
    raise RuntimeError("RAPIDOCR_PUBLIC_DIR must not be a filesystem root.")
if PDF_MARKDOWN_IMAGE_DIR == PDF_MARKDOWN_IMAGE_DIR.parent:
    raise RuntimeError("RAPIDOCR_PDF_MARKDOWN_IMAGE_DIR must not be a filesystem root.")
if not PUBLIC_ROUTE_PREFIX.startswith("/"):
    raise RuntimeError("RAPIDOCR_PUBLIC_ROUTE_PREFIX must start with '/'.")
if not PDF_MARKDOWN_IMAGE_URL_BASE.startswith(("/", "http://", "https://")):
    raise RuntimeError("RAPIDOCR_PDF_MARKDOWN_IMAGE_URL_BASE must be a URL or an absolute route path.")
