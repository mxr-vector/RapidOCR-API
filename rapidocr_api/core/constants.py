from enum import StrEnum


class PdfTaskStatus(StrEnum):
    """PDF 异步任务状态的持久化字符串集合。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class PdfResultType(StrEnum):
    """PDF 任务结果类型的持久化字符串集合。"""

    OCR = "ocr"
    MARKDOWN = "markdown"


APP_TITLE = "RapidOCR API"
APP_VERSION = "0.1.0"
APP_IMPORT_PATH = "rapidocr_api.main:app"
ROOT_MESSAGE = "Welcome to RapidOCR API Server!"

DEFAULT_API_HOST = "0.0.0.0"
DEFAULT_API_PORT = 9003
DEFAULT_API_WORKERS = 1
UVICORN_LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"

DB_POSTPROCESS_DEFAULT_BOX_TYPE = "quad"
PDF_TIMEOUT_DISABLED_SECONDS = 0

ERROR_ONLY_ONE_IMAGE_SOURCE = "Only one of image_file or image_data can be provided."
ERROR_MISSING_IMAGE_SOURCE = "When sending a POST request, image_file or image_data must have a value."
ERROR_PDF_REQUIRED = "Input is not a supported PDF."
ERROR_PDF_TASK_NOT_FOUND = "PDF OCR task not found."
ERROR_KNOWLEDGE_REQUIRED = "knowledge is required for PDF uploads."
ERROR_PDF_SERVICE_BUSY = "PDF OCR service is busy."
ERROR_PDF_TIMEOUT = "PDF OCR processing timed out."
ERROR_OCR_FAILED = "OCR processing failed."

OCR_KWARG_RETURN_WORD_BOX = "return_word_box"
OCR_KWARG_RETURN_SINGLE_CHAR_BOX = "return_single_char_box"
