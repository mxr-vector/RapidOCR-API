import argparse
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn
from fastapi import FastAPI
from starlette.formparsers import MultiPartParser

from rapidocr_api.core.settings import MAX_UPLOAD_FILE_SIZE
from rapidocr_api.api.routes import router
from rapidocr_api.core.constants import (
    APP_IMPORT_PATH,
    APP_TITLE,
    APP_VERSION,
    DEFAULT_API_HOST,
    DEFAULT_API_PORT,
    DEFAULT_API_WORKERS,
    UVICORN_LOG_FORMAT,
)

MultiPartParser.max_part_size = MAX_UPLOAD_FILE_SIZE
MultiPartParser.max_file_size = MAX_UPLOAD_FILE_SIZE

app = FastAPI(title=APP_TITLE, version=APP_VERSION)
app.include_router(router)


def main() -> None:
    """命令行入口：解析参数并启动 uvicorn 服务。"""
    parser = argparse.ArgumentParser("rapidocr_api")
    parser.add_argument("-ip", "--ip", type=str, default=DEFAULT_API_HOST, help="IP Address")
    parser.add_argument("-p", "--port", type=int, default=DEFAULT_API_PORT, help="IP port")
    parser.add_argument(
        "-workers", "--workers", type=int, default=DEFAULT_API_WORKERS, help="number of worker process"
    )
    args = parser.parse_args()

    log_config: dict[str, Any] = uvicorn.config.LOGGING_CONFIG
    log_config["formatters"]["access"]["fmt"] = UVICORN_LOG_FORMAT
    log_config["formatters"]["default"]["fmt"] = UVICORN_LOG_FORMAT

    uvicorn.run(
        APP_IMPORT_PATH,
        host=args.ip,
        port=args.port,
        reload=False,
        workers=args.workers,
        log_config=log_config,
    )


if __name__ == "__main__":
    main()
