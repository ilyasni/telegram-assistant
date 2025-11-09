import base64
import os
from typing import Any, Dict, Optional

import structlog
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from .service import PaddleOCRService, get_default_service

logger = structlog.get_logger(__name__)

REQUEST_COUNTER = Counter(
    "local_ocr_requests_total",
    "Total OCR requests handled",
    ["outcome"],
)

LATENCY_HISTOGRAM = Histogram(
    "local_ocr_request_duration_seconds",
    "Latency of OCR requests",
    buckets=(0.05, 0.1, 0.2, 0.5, 1, 2, 5),
)

QUEUE_GAUGE = Gauge(
    "local_ocr_processing_active",
    "Number of active OCR operations",
)


class OCRPayload(BaseModel):
    image_base64: str = Field(..., description="Base64-кодированное изображение")
    languages: Optional[list[str]] = Field(
        default=None, description="Список языков (subset config.languages)"
    )
    return_image: bool = Field(
        default=False, description="Возвращать ли изображение в ответе (диагностика)"
    )


def get_service() -> PaddleOCRService:
    return get_default_service()


app = FastAPI(
    title="Local PaddleOCR Service",
    version="0.1.0",
    description="CPU-only PaddleOCR wrapper for Phase 0 benchmarking.",
)


@app.on_event("startup")
async def startup_event() -> None:
    preload = os.getenv("LOCAL_OCR_PRELOAD", "false").lower() in {"1", "true", "yes"}
    if preload:
        logger.info("Preloading PaddleOCR models on startup")
        service = get_service()
        service._ensure_loaded()  # type: ignore[attr-defined]  # internal preload
    logger.info("Local OCR service started", preload=preload)


@app.get("/healthz", summary="Проверка готовности")
async def healthz() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics", summary="Prometheus metrics")
async def metrics() -> Response:
    content = generate_latest()
    return Response(content=content, media_type=CONTENT_TYPE_LATEST)


@app.post("/v1/ocr", summary="Выполнить OCR", response_model=Dict[str, Any])
async def run_ocr_json(
    payload: OCRPayload, service: PaddleOCRService = Depends(get_service)
) -> JSONResponse:
    try:
        image_bytes = base64.b64decode(payload.image_base64)
    except Exception as exc:  # pragma: no cover
        logger.warning("Failed to decode base64 image", error=str(exc))
        raise HTTPException(status_code=400, detail="Invalid base64 payload") from exc

    return await _process_request(
        image_bytes=image_bytes,
        languages=payload.languages,
        return_image=payload.return_image,
        service=service,
    )


@app.post("/v1/ocr/upload", summary="OCR изображение (multipart/form-data)")
async def run_ocr_upload(
    file: UploadFile = File(...),
    service: PaddleOCRService = Depends(get_service),
) -> JSONResponse:
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Unsupported content type")
    image_bytes = await file.read()
    return await _process_request(image_bytes=image_bytes, languages=None, return_image=False, service=service)


async def _process_request(
    image_bytes: bytes,
    languages: Optional[list[str]],
    return_image: bool,
    service: PaddleOCRService,
) -> JSONResponse:
    with LATENCY_HISTOGRAM.time(), QUEUE_GAUGE.track_inprogress():
        try:
            response = service.run_ocr(
                image_bytes=image_bytes,
                languages=languages,
                return_image=return_image,
            )
        except Exception as exc:  # pragma: no cover
            REQUEST_COUNTER.labels(outcome="error").inc()
            logger.exception("OCR processing failed", error=str(exc))
            raise HTTPException(status_code=500, detail="OCR failed") from exc
    REQUEST_COUNTER.labels(outcome="success").inc()
    return JSONResponse(content=response)


@app.get("/", include_in_schema=False)
async def root() -> PlainTextResponse:
    return PlainTextResponse(
        "Local PaddleOCR service. POST /v1/ocr with base64 payload or /v1/ocr/upload with file."
    )

