<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://github.com/RapidAI/RapidOCRAPI/releases/download/v0.2.0/rapidocr_api_logo_v2_dark.png" width="60%" height="60%">
    <source media="(prefers-color-scheme: light)" srcset="https://github.com/RapidAI/RapidOCRAPI/releases/download/v0.2.0/rapidocr_api_logov2_white.png" width="60%" height="60%">
    <img alt="Shows an illustrated sun in light mode and a moon with stars in dark mode." src="https://github.com/RapidAI/RapidOCRAPI/releases/download/v0.2.0/rapidocr_api_logov2_white.png">
  </picture>

 <br/>
  <a href=""><img src="https://img.shields.io/badge/Python->=3.6-aff.svg"></a>
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

[rapidocr-models](https://modelscope.ai/models/RapidAI/RapidOCR/files) 下载的模型文件放入 models目录下

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
| `POST` | `/ocr/pdf` | PDF OCR 专用入口，创建异步任务 |
| `GET` | `/ocr/pdf/tasks/{task_id}` | 查询 PDF OCR 任务状态和结果 |

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

图片 OCR 成功后返回 `OcrResult`，其中 `rec_txt_all` 是所有识别文本按空格拼接后的结果，数字键为逐行识别结果：

```json
{
  "rec_txt_all": "识别出的完整文本",
  "0": {
    "rec_txt": "识别出的文本行",
    "dt_boxes": [[0, 0], [100, 0], [100, 30], [0, 30]],
    "score": 0.99
  }
}
```

如果 OCR 引擎没有返回文本框、文本或置信度，接口会返回空结果：

```json
{
  "rec_txt_all": ""
}
```

##### PDF OCR

PDF 识别耗时较长，接口会创建后台任务并立即返回 `task_id`，HTTP 状态码为 `202`。可以直接调用专用 PDF 接口：

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
  "status": "pending"
}
```

PDF 上传时必须传入 `knowledge`，服务会把 PDF 原文件和识别结果 JSON 保存到 `storage/{knowledge}/YYYYMMDD` 目录下，并在 `storage/index.json` 中记录任务索引。`knowledge` 会作为目录名使用，不能为空，长度不能超过 128，不能包含 `/`、`\\`、`:`、控制字符或 `..`。

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
  "created_at": "2026-05-08T10:00:00+00:00",
  "started_at": "2026-05-08T10:00:01+00:00",
  "finished_at": "2026-05-08T10:00:10+00:00",
  "file": {
    "uuid": "f3c8d2d2a3d94a22a1e2f1d6b0f0a9c1",
    "knowledge": "default",
    "original_filename": "demo.pdf",
    "filename": "demo.pdf",
    "stored_pdf_filename": "f3c8d2d2a3d94a22a1e2f1d6b0f0a9c1.pdf",
    "result_filename": "f3c8d2d2a3d94a22a1e2f1d6b0f0a9c1.json",
    "original_file_path": "storage/default/20260508/f3c8d2d2a3d94a22a1e2f1d6b0f0a9c1.pdf",
    "result_file_path": "storage/default/20260508/f3c8d2d2a3d94a22a1e2f1d6b0f0a9c1.json",
    "file_size": 123456,
    "created_at": "2026-05-08T10:00:00+00:00"
  },
  "result_file_path": "storage/default/20260508/f3c8d2d2a3d94a22a1e2f1d6b0f0a9c1.json",
  "result": {
    "page_count": 1,
    "rec_txt_all": "第一页识别文本",
    "pages": [
      {
        "page_no": 1,
        "rec_txt_all": "第一页识别文本",
        "result": {
          "rec_txt_all": "第一页识别文本"
        }
      }
    ]
  },
  "error": null
}
```

任务失败时，`status` 为 `failed`，`error` 中包含 `status_code` 和 `detail`；任务不存在时查询接口返回 `404`。

##### OCR 可选参数

`/ocr` 和 `/ocr/pdf` 支持以下表单参数：

| 参数 | 类型 | 适用接口 | 说明 |
|:---|:---|:---|:---|
| `image_file` | `UploadFile` | `/ocr` | 图片或 PDF 文件；PDF 会创建异步任务 |
| `image_data` | `str` | `/ocr` | 图片 base64 字符串，支持 data URI |
| `pdf_file` | `UploadFile` | `/ocr/pdf` | PDF 文件 |
| `knowledge` | `str` | `/ocr`、`/ocr/pdf` | PDF 上传必填；用于保存到 `storage/{knowledge}/YYYYMMDD`，图片 OCR 不需要 |
| `use_det` | `bool` | `/ocr`、`/ocr/pdf` | 是否启用文本检测 |
| `use_cls` | `bool` | `/ocr`、`/ocr/pdf` | 是否启用方向分类 |
| `use_rec` | `bool` | `/ocr`、`/ocr/pdf` | 是否启用文本识别 |
| `text_score` | `float` | `/ocr`、`/ocr/pdf` | 文本置信度阈值，范围 `0` 到 `1` |
| `return_word_box` | `bool` | `/ocr`、`/ocr/pdf` | 透传给 RapidOCR，是否返回词级文本框 |
| `return_single_char_box` | `bool` | `/ocr`、`/ocr/pdf` | 透传给 RapidOCR，是否返回单字符文本框 |

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
| `404` | 查询的 PDF OCR 任务不存在 |
| `413` | 上传文件、base64 解码后二进制或 PDF 单页渲染像素数超过限制 |
| `503` | PDF OCR 并发达到上限，或 PDF 处理超过配置的超时时间 |
| `500` | OCR 处理过程中发生未预期错误 |

##### 上传与 PDF 处理限制

可通过环境变量调整上传与 PDF 渲染处理限制：

| 环境变量 | 默认值 | 说明 |
|:---|:---|:---|
| `RAPIDOCR_MAX_UPLOAD_FILE_SIZE` | `20971520` | 上传文件、multipart 表单字段或 base64 解码后二进制的最大字节数，默认 20MB |
| `RAPIDOCR_PDF_RENDER_DPI` | `150` | PDF 页面渲染目标 DPI |
| `RAPIDOCR_PDF_MIN_RENDER_DPI` | `72` | PDF 页面过大时允许降低到的最小 DPI |
| `RAPIDOCR_PDF_MAX_RENDER_PIXELS` | `12000000` | 单页 PDF 渲染后的最大像素数 |
| `RAPIDOCR_PDF_REQUEST_TIMEOUT_SECONDS` | `600` | PDF 后台任务处理超时时间；设置为 `0` 表示不启用内部超时 |
| `RAPIDOCR_PDF_MAX_CONCURRENT_REQUESTS` | `1` | PDF OCR 最大并发处理数，也是后台 PDF 任务线程池大小 |

### 📚 文档

完整文档请移步：[docs](https://rapidai.github.io/RapidOCRDocs/main/install_usage/rapidocr_api/usage/)
