"""
FastAPI REST API Server for Exo Windows Porting.

This module provides a RESTful HTTP interface to the Exo Windows Porting system,
enabling external clients to interact with the distributed LLM inference cluster.

Author: Exo Windows Porting Team
License: MIT
"""

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, Query, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security.api_key import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

_EXO_API_KEY: Optional[str] = os.getenv("EXO_API_KEY")

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: Optional[str] = Security(_api_key_header)) -> None:
    """FastAPI dependency — pass through if EXO_API_KEY not set (dev mode)."""
    if _EXO_API_KEY is None:
        return  # auth disabled
    if api_key != _EXO_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------


class InferenceRequest(BaseModel):
    """Inference request model."""

    prompt: str = Field(..., description="Input text for generation")
    model_path: Optional[str] = Field(None, description="Path to GGUF model file")
    max_tokens: int = Field(512, ge=1, le=8192, description="Maximum tokens to generate")
    temperature: float = Field(0.7, ge=0.0, le=2.0, description="Sampling temperature")
    top_p: float = Field(0.9, ge=0.0, le=1.0, description="Top-p sampling parameter")
    stop_sequences: List[str] = Field(default_factory=list, description="Stop sequences")
    gpu_required: bool = Field(False, description="Require GPU acceleration")


class InferenceResponse(BaseModel):
    """Inference response model."""

    success: bool
    text: str = ""
    tokens_generated: int = 0
    time_ms: float = 0.0
    throughput_tok_s: float = 0.0
    error_message: Optional[str] = None
    request_id: str


class ModelInfo(BaseModel):
    """Model information model."""

    path: str
    size_mb: float
    modified_time: float
    available: bool


class ClusterStatus(BaseModel):
    """Cluster status model."""

    total_nodes: int = 0
    active_nodes: int = 0
    total_gpu_memory_gb: float = 0.0
    average_load_percent: float = 0.0
    uptime_seconds: float = 0.0


# ---------------------------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Exo Windows Porting API",
    description="REST API for distributed LLM inference on Windows with ROCm/CUDA support",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static dashboard UI at /ui (index.html)
_static_dir = Path(__file__).parent / "static"
if _static_dir.is_dir():
    app.mount("/ui", StaticFiles(directory=str(_static_dir), html=True), name="ui")

# ---------------------------------------------------------------------------
# Global State
# ---------------------------------------------------------------------------

_inference_queue: List[Dict[str, Any]] = []
_model_cache: Dict[str, ModelInfo] = {}
_cluster_status = ClusterStatus()
_server_start_time: float = time.time()

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _process_inference_task(
    request: InferenceRequest,
    request_id: str,
) -> Dict[str, Any]:
    """Execute an inference request and return a result dict."""
    from exo_windows_porting.backend.factory import get_backend_factory

    try:
        factory = get_backend_factory()

        if request.gpu_required and (
            not factory.hardware.has_nvidia_gpu and not factory.hardware.has_amd_gpu
        ):
            raise HTTPException(
                status_code=400,
                detail="GPU required but no GPU available on this system",
            )

        backend = factory.create_backend(
            model_path=request.model_path,
            force_cpu=False,
        )

        start = time.time()
        result_text = await backend.generate(
            prompt=request.prompt,
            max_tokens=request.max_tokens,
        )
        elapsed_ms = (time.time() - start) * 1000

        tokens_generated = len(result_text.split()) if result_text else 0
        throughput = tokens_generated / (elapsed_ms / 1000) if elapsed_ms > 0 else 0.0

        return {
            "success": True,
            "text": result_text or "",
            "tokens_generated": tokens_generated,
            "time_ms": round(elapsed_ms, 2),
            "throughput_tok_s": round(throughput, 2),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error processing inference task %s", request_id)
        return {
            "success": False,
            "error_message": str(e),
            "tokens_generated": 0,
            "time_ms": 0.0,
            "throughput_tok_s": 0.0,
        }


async def _stream_inference(request: InferenceRequest) -> AsyncGenerator[str, None]:
    """Async generator that yields SSE-formatted lines for a streaming request."""
    from exo_windows_porting.backend.factory import get_backend_factory

    try:
        factory = get_backend_factory()

        if request.gpu_required and (
            not factory.hardware.has_nvidia_gpu and not factory.hardware.has_amd_gpu
        ):
            yield f"data: {json.dumps({'error': 'GPU required but not available'})}\n\n"
            yield "data: [DONE]\n\n"
            return

        backend = factory.create_backend(
            model_path=request.model_path,
            force_cpu=False,
        )

        # Use generate_stream if available, otherwise fall back to generate()
        if hasattr(backend, "generate_stream"):
            async for token in backend.generate_stream(
                request.prompt, max_tokens=request.max_tokens
            ):
                payload = json.dumps({"token": token})
                yield f"data: {payload}\n\n"
                await asyncio.sleep(0)   # yield control to event loop
        else:
            # Fallback: run generate() and emit the whole text as one chunk
            text = await backend.generate(
                prompt=request.prompt,
                max_tokens=request.max_tokens,
            )
            yield f"data: {json.dumps({'token': text})}\n\n"

    except Exception as e:
        logger.exception("Streaming inference error")
        yield f"data: {json.dumps({'error': str(e)})}\n\n"

    yield "data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Routes — public (no auth)
# ---------------------------------------------------------------------------


@app.get("/")
async def root():
    """Root endpoint — redirects browsers to /ui, returns JSON for API clients."""
    return {
        "status": "healthy",
        "title": app.title,
        "version": app.version,
        "dashboard": "/ui",
    }


@app.get("/health")
async def health_check():
    """Health check endpoint (unauthenticated)."""
    return {
        "status": "ok",
        "timestamp": time.time(),
        "uptime_seconds": round(time.time() - _server_start_time, 1),
    }


# ---------------------------------------------------------------------------
# Routes — protected (require API key when EXO_API_KEY is set)
# ---------------------------------------------------------------------------


@app.post(
    "/v1/inference",
    response_model=InferenceResponse,
    dependencies=[Depends(verify_api_key)],
)
async def run_inference(
    request: InferenceRequest,
    background_tasks: BackgroundTasks,
):
    """Run inference on the distributed cluster (blocking, returns full text)."""
    if not request.model_path:
        raise HTTPException(status_code=400, detail="model_path is required")

    if request.model_path not in _model_cache:
        raise HTTPException(
            status_code=404,
            detail=f"Model not found: {request.model_path}",
        )

    request_id = str(uuid.uuid4())
    _inference_queue.append(
        {"id": request_id, "status": "queued", "created_at": time.time()}
    )

    try:
        result = await _process_inference_task(request, request_id)
        return InferenceResponse(
            success=result["success"],
            text=result.get("text", ""),
            tokens_generated=result.get("tokens_generated", 0),
            time_ms=result.get("time_ms", 0.0),
            throughput_tok_s=result.get("throughput_tok_s", 0.0),
            error_message=result.get("error_message"),
            request_id=request_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        return InferenceResponse(
            success=False,
            error_message=str(e),
            request_id=request_id,
        )


@app.post(
    "/v1/completions/stream",
    dependencies=[Depends(verify_api_key)],
    summary="Streaming inference (SSE)",
    response_description="Server-Sent Events stream of token objects",
)
async def run_inference_stream(request: InferenceRequest):
    """
    Stream inference results as Server-Sent Events.

    Each event has the form::

        data: {"token": "<text>"}

    The stream ends with::

        data: [DONE]

    Errors are reported as::

        data: {"error": "<message>"}
    """
    if request.model_path and request.model_path not in _model_cache:
        raise HTTPException(
            status_code=404,
            detail=f"Model not found: {request.model_path}",
        )

    return StreamingResponse(
        _stream_inference(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx buffering
        },
    )


@app.get(
    "/v1/models",
    response_model=List[ModelInfo],
    dependencies=[Depends(verify_api_key)],
)
async def list_models():
    """List models registered in the cache."""
    return list(_model_cache.values())


@app.post(
    "/v1/models/upload",
    dependencies=[Depends(verify_api_key)],
)
async def upload_model(
    model_path: str = Query(..., description="Path to GGUF model file"),
    size_mb: float = Query(..., description="Model size in MB"),
):
    """Register a model in the cache by filesystem path."""
    if not os.path.exists(model_path):
        raise HTTPException(status_code=404, detail=f"Model file not found: {model_path}")

    stat = os.stat(model_path)
    model_info = ModelInfo(
        path=model_path,
        size_mb=size_mb,
        modified_time=stat.st_mtime,
        available=True,
    )
    _model_cache[model_path] = model_info
    return model_info


@app.get(
    "/v1/cluster/status",
    response_model=ClusterStatus,
    dependencies=[Depends(verify_api_key)],
)
async def get_cluster_status():
    """Get current cluster status."""
    _cluster_status.uptime_seconds = round(time.time() - _server_start_time, 1)
    return _cluster_status


@app.delete(
    "/v1/models/{model_path:path}",
    dependencies=[Depends(verify_api_key)],
)
async def delete_model(model_path: str):
    """Remove a model from the cache."""
    if model_path not in _model_cache:
        raise HTTPException(status_code=404, detail=f"Model not found: {model_path}")
    del _model_cache[model_path]
    return {"status": "deleted", "path": model_path}


@app.get(
    "/v1/models/{model_path:path}/info",
    response_model=ModelInfo,
    dependencies=[Depends(verify_api_key)],
)
async def get_model_info(model_path: str):
    """Get information about a specific model."""
    if model_path not in _model_cache:
        raise HTTPException(status_code=404, detail=f"Model not found: {model_path}")
    return _model_cache[model_path]


@app.get(
    "/v1/health/backends",
    dependencies=[Depends(verify_api_key)],
)
async def check_backends():
    """Check status of all available backends."""
    from exo_windows_porting.backend.factory import get_backend_factory

    factory = get_backend_factory()
    info = factory.get_backend_info()
    return {
        "hardware": info["hardware"],
        "available_backends": info["available_backends"],
        "selected_backend": info["selected_backend"],
    }


# ---------------------------------------------------------------------------
# Lifecycle events
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def startup_event():
    global _server_start_time
    _server_start_time = time.time()
    logger.info("Exo Windows Porting API started")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Exo Windows Porting API stopped")
