<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://github.com/RapidAI/RapidOCRAPI/releases/download/v0.2.0/rapidocr_api_logo_v2_dark.png" width="60%" height="60%">
    <source media="(prefers-color-scheme: light)" srcset="https://github.com/RapidAI/RapidOCRAPI/releases/download/v0.2.0/rapidocr_api_logov2_white.png" width="60%" height="60%">
    <img alt="Shows an illustrated sun in light mode and a moon with stars in dark mode." src="https://github.com/RapidAI/RapidOCRAPI/releases/download/v0.2.0/rapidocr_api_logov2_white.png">
  </picture>

 <br/>
  <a href=""><img src="https://img.shields.io/badge/Python->=3.12-aff.svg"></a>
  <a href=""><img src="https://img.shields.io/badge/OS-Linux%2C%20Win%2C%20Mac-pink.svg"></a>
  <a href="https://github.com/RapidAI/RapidOCRAPI/graphs/contributors"><img src="https://img.shields.io/github/contributors/RapidAI/RapidOCRAPI?color=9ea"></a>
  <a href="https://github.com/RapidAI/RapidOCRAPI/stargazers"><img src="https://img.shields.io/github/stars/RapidAI/RapidOCRAPI?color=ccf" ></a>
  <a href="https://pypistats.org/packages/rapidocr_api"><img src="https://img.shields.io/pypi/dm/rapidocr_api?style=flat&label=rapidocr_api"></a>
  <a href="https://pypi.org/project/rapidocr_api/"><img alt="PyPI" src="https://img.shields.io/pypi/v/rapidocr_api"></a>
  <a href="https://choosealicense.com/licenses/apache-2.0/"><img src="https://img.shields.io/badge/License-Apache%202-dfd.svg"></a>
  <a href="https://semver.org/"><img alt="SemVer2.0" src="https://img.shields.io/badge/SemVer-2.0-brightgreen"></a>
  <a href="https://github.com/psf/black"><img src="https://img.shields.io/badge/code%20style-black-000000.svg"></a>

</div>

### 📖 简介

- 该包是将[rapidocr](./rapidocr/install.md)库做了API封装，采用[FastAPI](https://fastapi.tiangolo.com/) + [uvicorn](https://www.uvicorn.org/)实现。
- 定位是一个快速调用`rapidocr`的API接口，没有考虑多进程处理并发请求，如果有这需求的小伙伴，可以看看[gunicorn](https://gunicorn.org/)等。

### 📌 版本依赖关系

|`rapidocr_api`|`rapidocr`|
|:---|:---|
|`v0.2.x`|`rapidocr>1.0.0,<3.0.0`|
|`v0.1.x`|`rapidocr_onnxruntime`|

### 🛠️ 安装

```bash linenums="1"
uv sync
```

### 模型下载

从 [rapidocr-models](https://modelscope.ai/models/RapidAI/RapidOCR/files) 下载 RapidOCR 模型文件，默认放入 `models/RapidOCR` 目录。
如需使用 `is_markdown=true` 的 Markdown 与结构化排版恢复能力，还需要从 [rapiddoc-models](https://www.modelscope.cn/models/RapidAI/RapidDoc/files) 下载 RapidDoc 模型文件，默认放入 `models/RapidDoc` 目录。

### 🚀 使用

#### ▶️ 启动服务

```bash
# 默认参数启动
uv run rapidocr_api/main.py
# 指定参数：端口与进程数量；
rapidocr_api -ip 0.0.0.0 -p 9005 -workers 2
```

#### 📞 调用服务

服务启动后，可以访问 `http://localhost:9003/docs` 查看 FastAPI 自动生成的交互式接口文档。

接口概览：

| 方法 | 路径 | 说明 |
|:---|:---|:---|
| `GET` | `/` | 服务健康检查，返回欢迎信息 |
| `POST` | `/ocr` | 统一 OCR 入口，支持图片文件、图片 base64，也支持 PDF 文件创建异步任务 |
| `POST` | `/ocr/pdf` | PDF 专用入口，根据 `is_markdown` 创建 OCR 或 Markdown/排版恢复异步任务 |
| `GET` | `/ocr/pdf/tasks/{task_id}` | 查询 PDF 任务状态和结果 |

##### 图片 OCR

通过 multipart/form-data 上传图片文件到统一入口 `/ocr`：

```bash
curl -F image_file=@1.png http://localhost:9003/ocr
```

也可以通过 `image_data` 传入图片 base64 字符串，支持普通 base64 和 data URI：

```bash
curl -X POST http://localhost:9003/ocr \
  -F "image_data=$(base64 -w 0 1.png)"
```

> `image_file` 和 `image_data` 只能二选一；同时传入会返回 `400`。

🐍 Python 脚本使用：

```python
import requests

url = "http://localhost:9003/ocr"
img_path = "1.png"

with open(img_path, "rb") as f:
    files = {"image_file": (img_path, f, "image/png")}
    response = requests.post(url, files=files, timeout=60)

print(response.json())
```

图片 OCR 成功后返回 `OcrResult`，数字键为逐行识别结果：

```json
{
  "0": {
    "rec_txt": "识别出的文本行",
    "dt_boxes": [[0, 0], [100, 0], [100, 30], [0, 30]],
    "score": 0.99
  }
}
```

如果 OCR 引擎没有返回文本框、文本或置信度，接口会返回空结果：

```json
{}
```

传入 `is_markdown=true` 时，接口会保留逐行识别结果，并额外返回 Markdown 和结构化排版字段，例如 `formatted_markdown`、`layout`、`content` 和 `blocks`：

```bash
curl -F image_file=@1.png \
  -F is_markdown=true \
  http://localhost:9003/ocr
```

如需同时获取 RapidDoc 版面、公式和表格模型生成的结构化字段，请使用 `/ocr` 并传入 `is_markdown=true`。

##### PDF OCR 与 Markdown/排版恢复

PDF 识别耗时较长，接口会创建后台任务并立即返回 `task_id`，HTTP 状态码为 `202`。默认创建普通 OCR 任务：

```bash
curl -F pdf_file=@demo.pdf \
  -F knowledge=default \
  http://localhost:9003/ocr/pdf
```

也可以把 PDF 作为 `image_file` 上传到统一入口 `/ocr`，服务会自动识别 PDF 并创建异步任务：

```bash
curl -F image_file=@demo.pdf \
  -F knowledge=default \
  http://localhost:9003/ocr
```

创建任务返回示例：

```json
{
  "task_id": "f3c8d2d2a3d94a22a1e2f1d6b0f0a9c1",
  "status": "pending",
  "result_type": "ocr"
}
```

PDF 上传时必须传入 `knowledge`，服务会把 PDF 原文件和识别结果 JSON 保存到 `storage/{knowledge}/YYYYMMDD` 目录下，并在 `storage/index.json` 中记录任务索引。`knowledge` 会作为目录名使用，不能为空，长度不能超过 128，不能包含 `/`、`\\`、`:`、控制字符或 `..`。

传入 `is_markdown=true` 时，`/ocr/pdf` 会创建 Markdown/排版恢复任务，使用 RapidDoc 生成 Markdown，并返回 `layout`、`content` 和归一化 `blocks`：

```bash
curl -F pdf_file=@demo.pdf \
  -F knowledge=default \
  -F is_markdown=true \
  http://localhost:9003/ocr/pdf
```

通过任务 ID 查询处理状态：

```bash
curl http://localhost:9003/ocr/pdf/tasks/f3c8d2d2a3d94a22a1e2f1d6b0f0a9c1
```

任务状态包括：`pending`、`running`、`succeeded`、`failed`。

查询返回示例：

```json
{
  "task_id": "f3c8d2d2a3d94a22a1e2f1d6b0f0a9c1",
  "status": "succeeded",
  "result_type": "ocr",
  "created_at": "2026-05-08T10:00:00+00:00",
  "started_at": "2026-05-08T10:00:01+00:00",
  "finished_at": "2026-05-08T10:00:10+00:00",
  "file": {
    "knowledge": "default",
    "original_filename": "demo.pdf",
    "filename": "demo.pdf",
    "original_file_path": "storage/default/20260508/f3c8d2d2a3d94a22a1e2f1d6b0f0a9c1.pdf",
    "result_file_path": "storage/default/20260508/f3c8d2d2a3d94a22a1e2f1d6b0f0a9c1.json",
    "file_size": 123456,
    "created_at": "2026-05-08T10:00:00+00:00"
  },
  "result_file_path": "storage/default/20260508/f3c8d2d2a3d94a22a1e2f1d6b0f0a9c1.json",
  "result": {
    "page_count": 1,
    "pages": [
      {
        "page_no": 1,
        "result": {}
      }
    ]
  },
  "error": null
}
```

任务失败时，`status` 为 `failed`，`error` 中包含 `status_code` 和 `detail`；任务不存在时查询接口返回 `404`。

Markdown/排版恢复任务成功结果示例：

```json
{
  "task_id": "f3c8d2d2a3d94a22a1e2f1d6b0f0a9c1",
  "status": "succeeded",
  "result_type": "markdown",
  "result": {
    "page_count": 1,
    "markdown": "第一页 Markdown 内容",
    "pages": [
      {
        "page_no": 1,
        "markdown": "第一页 Markdown 内容",
        "layout": {"pdf_info": []},
        "content": [{"type": "text", "text": "第一页 Markdown 内容"}],
        "blocks": [{"page_no": 1, "type": "text", "text": "第一页 Markdown 内容"}]
      }
    ],
    "blocks": [{"page_no": 1, "type": "text", "text": "第一页 Markdown 内容"}]
  },
  "error": null
}
```

`content` 和 `layout` 为 RapidDoc 原始结构化结果，`blocks` 是面向前端恢复段落、标题、表格、公式等块类型的归一化列表。

##### OCR 可选参数

`/ocr` 和 `/ocr/pdf` 支持以下表单参数：

| 参数 | 类型 | 适用接口 | 说明 |
|:---|:---|:---|:---|
| `image_file` | `UploadFile` | `/ocr` | 图片文件；`/ocr` 也支持 PDF 文件创建异步任务 |
| `image_data` | `str` | `/ocr` | 图片 base64 字符串，支持 data URI |
| `pdf_file` | `UploadFile` | `/ocr/pdf` | PDF 文件 |
| `knowledge` | `str` | `/ocr`、`/ocr/pdf` | PDF 上传必填；用于保存到 `storage/{knowledge}/YYYYMMDD`，图片 OCR 不需要 |
| `use_det` | `bool` | 全部 OCR 接口 | 是否启用文本检测 |
| `use_cls` | `bool` | 全部 OCR 接口 | 是否启用方向分类 |
| `use_rec` | `bool` | 全部 OCR 接口 | 是否启用文本识别 |
| `text_score` | `float` | 全部 OCR 接口 | 文本置信度阈值，范围 `0` 到 `1` |
| `return_word_box` | `bool` | `/ocr`、`/ocr/pdf` | 透传给 RapidOCR，是否返回词级文本框；Markdown 模式会自动启用 |
| `return_single_char_box` | `bool` | `/ocr`、`/ocr/pdf` | 透传给 RapidOCR，是否返回单字符文本框；Markdown 模式会自动启用 |
| `is_markdown` | `bool` | `/ocr`、`/ocr/pdf` | 是否返回 Markdown 与结构化排版信息，默认 `false` |

示例：

```bash
curl -F image_file=@1.png \
  -F text_score=0.5 \
  -F return_word_box=true \
  http://localhost:9003/ocr
```

##### 常见错误

| 状态码 | 场景 |
|:---|:---|
| `400` | 未传入 `image_file` 或 `image_data`、同时传入两者、上传空文件、base64 为空或非法、图片/PDF 格式不支持、PDF 缺少或使用非法 `knowledge`、`text_score` 越界 |
| `404` | 查询的 PDF 任务不存在 |
| `413` | 上传文件、base64 解码后二进制或 PDF 单页渲染像素数超过限制 |
| `503` | PDF OCR 并发达到上限，或 PDF 处理超过配置的超时时间 |
| `500` | OCR 处理过程中发生未预期错误 |

##### 运行时配置

可通过环境变量调整上传、PDF 渲染处理、存储和模型路径配置。数值配置会在启动阶段校验；路径配置建议在 Linux 容器中使用 `/path/to/...` 形式，服务内部会转换为平台路径，任务索引和 API 响应中的路径字段统一使用 `/` 分隔符。

| 环境变量 | 默认值 | 说明 |
|:---|:---|:---|
| `RAPIDOCR_PROJECT_ROOT` | 项目根目录 | 默认路径派生基准 |
| `RAPIDOCR_MODEL_ROOT` | `{RAPIDOCR_PROJECT_ROOT}/models` | 模型根目录 |
| `RAPIDOCR_MODEL_RAPIDOCR_ROOT` | `{RAPIDOCR_MODEL_ROOT}/RapidOCR` | RapidOCR 模型族根目录 |
| `RAPIDOCR_MODEL_RAPIDDOC_ROOT` | `{RAPIDOCR_MODEL_ROOT}/RapidDoc` | RapidDoc 模型族根目录 |
| `RAPIDOCR_STORAGE_DIR` | `{RAPIDOCR_PROJECT_ROOT}/storage` | PDF 原文件、结果文件和任务索引存储目录，不能配置为文件系统根目录 |
| `RAPIDOCR_MAX_UPLOAD_FILE_SIZE` | `20971520` | 上传文件、multipart 表单字段或 base64 解码后二进制的最大字节数，默认 20MB |
| `RAPIDOCR_PDF_RENDER_DPI` | `150` | PDF 页面渲染目标 DPI |
| `RAPIDOCR_PDF_MIN_RENDER_DPI` | `72` | PDF 页面过大时允许降低到的最小 DPI，不能大于 `RAPIDOCR_PDF_RENDER_DPI` |
| `RAPIDOCR_PDF_MAX_RENDER_PIXELS` | `12000000` | 单页 PDF 渲染后的最大像素数 |
| `RAPIDOCR_PDF_REQUEST_TIMEOUT_SECONDS` | `0` | PDF 后台任务处理硬超时时间；设置为 `0` 表示不启用内部超时，避免大 PDF 异步任务被固定时长中断 |
| `RAPIDOCR_PDF_MAX_CONCURRENT_REQUESTS` | `1` | PDF OCR 最大并发处理数，也是后台 PDF 任务线程池大小 |
| `RAPIDOCR_PDF_PAGE_WORKERS` | `1` | 单个 PDF 内部页级 OCR worker 数；默认串行，增大可加速多页 PDF 但会增加 CPU/内存占用 |
| `RAPIDOCR_KNOWLEDGE_MAX_LENGTH` | `128` | PDF 存储目录中 `knowledge` 段的最大长度 |
| `RAPIDOCR_MODEL_OCR_DET` | `{RAPIDOCR_MODEL_RAPIDOCR_ROOT}/onnx/PP-OCRv5/det/multi_PP-ch_PP-OCRv5_det_mobile.onnx` | OCR 检测模型路径 |
| `RAPIDOCR_MODEL_OCR_CLS` | `{RAPIDOCR_MODEL_RAPIDOCR_ROOT}/onnx/PP-OCRv5/cls/ch_PP-LCNet_x0_25_textline_ori_cls_mobile.onnx` | OCR 方向分类模型路径 |
| `RAPIDOCR_MODEL_OCR_REC` | `{RAPIDOCR_MODEL_RAPIDOCR_ROOT}/onnx/PP-OCRv5/rec/ch_PP-OCRv5_rec_mobile.onnx` | OCR 文本识别模型路径 |
| `RAPIDOCR_MODEL_PAGE_LAYOUT` | `{RAPIDOCR_MODEL_RAPIDDOC_ROOT}/layout/PP-DocLayoutV2/pp_doclayoutv2.onnx` | RapidDoc 版面识别模型路径 |
| `RAPIDOCR_MODEL_FORMULA_RECOGNITION` | `{RAPIDOCR_MODEL_RAPIDDOC_ROOT}/formula/PP-FormulaNet_plus-M/pp_formulanet_plus_m.onnx` | RapidDoc 公式识别模型路径 |
| `RAPIDOCR_MODEL_TABLE_WIRED` | `{RAPIDOCR_MODEL_RAPIDDOC_ROOT}/table/SLANeXt_wired/slanext_wired.onnx` | RapidDoc 有线表格模型路径 |
| `RAPIDOCR_MODEL_TABLE_WIRELESS` | `{RAPIDOCR_MODEL_RAPIDDOC_ROOT}/table/SLANeXt_wireless/slanext_wireless.onnx` | RapidDoc 无线表格模型路径 |

### 📚 文档

完整文档请移步：[docs](https://rapidai.github.io/RapidOCRDocs/main/install_usage/rapidocr_api/usage/)
