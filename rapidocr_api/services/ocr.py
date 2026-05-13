from typing import Any, Optional

import numpy as np
from fastapi import HTTPException
from PIL import Image, ImageOps
from rapidocr import RapidOCR

from rapidocr_api.core.constants import DB_POSTPROCESS_DEFAULT_BOX_TYPE, ERROR_OCR_FAILED
from rapidocr_api.schemas.ocr import OcrResult
from rapidocr_api.services.document import format_image_document
from rapidocr_api.services.formatter import OCR_CONFIG
from rapidocr_api.services.utils import load_image


def ensure_db_postprocess_box_type(ocr_engine: Any) -> None:
    """RapidDoc 动态 patch 后，已存在的检测器实例仍需补齐 box_type。"""
    text_detector = getattr(ocr_engine, "text_det", None)
    postprocess_op = getattr(text_detector, "postprocess_op", None)
    if postprocess_op is not None and not hasattr(postprocess_op, "box_type"):
        postprocess_op.box_type = DB_POSTPROCESS_DEFAULT_BOX_TYPE


class OCRAPIUtils:
    """封装 RapidOCR 引擎，统一图像预处理与结果对象转换。"""

    def __init__(self) -> None:
        self.ocr = RapidOCR(params=OCR_CONFIG)
        ensure_db_postprocess_box_type(self.ocr)

    def to_rapidocr_result(
        self,
        ori_img: Image.Image,
        use_det: Optional[bool] = None,
        use_cls: Optional[bool] = None,
        use_rec: Optional[bool] = None,
        **kwargs: Any,
    ) -> Any:
        """执行 RapidOCR 推理，调用前修正 EXIF 与 RGB 通道约束。"""
        ensure_db_postprocess_box_type(self.ocr)
        img = np.array(ImageOps.exif_transpose(ori_img).convert("RGB"))
        try:
            return self.ocr(img, use_det=use_det, use_cls=use_cls, use_rec=use_rec, **kwargs)
        finally:
            del img

    def __call__(
        self,
        ori_img: Image.Image,
        use_det: Optional[bool] = None,
        use_cls: Optional[bool] = None,
        use_rec: Optional[bool] = None,
        **kwargs: Any,
    ) -> OcrResult:
        """把 RapidOCR 原始结果转换成现有 JSON 对象形态。"""
        ocr_res = self.to_rapidocr_result(
            ori_img, use_det=use_det, use_cls=use_cls, use_rec=use_rec, **kwargs
        )
        if ocr_res.boxes is None or ocr_res.txts is None or ocr_res.scores is None:
            return OcrResult()

        result_data: dict[str, Any] = {}
        for index, (boxes, txt, score) in enumerate(zip(ocr_res.boxes, ocr_res.txts, ocr_res.scores)):
            result_data[str(index)] = {
                "rec_txt": txt,
                "dt_boxes": boxes.tolist(),
                "score": float(score),
            }
        return OcrResult.model_validate(result_data)


processor = OCRAPIUtils()


def process_image_bytes(
    image_data: bytes,
    use_det: Optional[bool],
    use_cls: Optional[bool],
    use_rec: Optional[bool],
    ocr_kwargs: dict[str, Any],
    is_markdown: bool = False,
) -> OcrResult:
    """对图像字节执行 OCR，Markdown 模式叠加文档格式化字段。"""
    try:
        img = load_image(image_data)
        try:
            result = processor(img, use_det=use_det, use_cls=use_cls, use_rec=use_rec, **ocr_kwargs)
            if is_markdown:
                result = OcrResult.model_validate({**result.model_dump(), **format_image_document(img)})
            return result
        finally:
            img.close()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=ERROR_OCR_FAILED) from exc
