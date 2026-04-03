"""
FastAPI REST API Server for Exo Windows Porting.

This module provides a RESTful HTTP interface to the Exo Windows Porting system,
enabling external clients to interact with the distributed LLM inference cluster.

Author: Exo Windows Porting Team
License: MIT
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import asyncio
import time
import uuid


# Pydantic Models for Request/Response
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


# FastAPI Application
app = FastAPI(
    title="Exo Windows Porting API",
    description="REST API for distributed LLM inference on Windows with ROCm/CUDA support",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Enable CORS for cross-origin requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Global state (in production, use proper database/cache)
_inference_queue: List[Dict[str, Any]] = []
_model_cache: Dict[str, ModelInfo] = {}
_cluster_status = ClusterStatus()


@app.get("/")
async def root():
    """Root endpoint - API health check."""
    
    return {
        "status": "healthy",
        "title": app.title,
        "version": app.version
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    
    return {
        "status": "ok",
        "timestamp": time.time(),
        "uptime_seconds": _cluster_status.uptime_seconds
    }


@app.post("/v1/inference", response_model=InferenceResponse)
async def run_inference(
    request: InferenceRequest,
    background_tasks: BackgroundTasks
):
    """Run inference on the distributed cluster."""
    
    try:
        # Validate model path
        if not request.model_path:
            raise HTTPException(status_code=400, detail="Model path is required")
        
        # Check if model is cached/available
        if request.model_path not in _model_cache:
            raise HTTPException(
                status_code=404, 
                detail=f"Model not found: {request.model_path}"
            )
        
        # Create unique request ID
        request_id = str(uuid.uuid4())
        
        # Add to processing queue (in production, use proper task queue)
        task_info = {
            "id": request_id,
            "status": "queued",
            "request": request.dict(),
            "created_at": time.time()
        }
        
        _inference_queue.append(task_info)
        
        # Process asynchronously (simplified - in production use proper async handling)
        result = await process_inference_task(request, request_id)
        
        return InferenceResponse(
            success=result["success"],
            text=result.get("text", ""),
            tokens_generated=result.get("tokens_generated", 0),
            time_ms=result.get("time_ms", 0.0),
            throughput_tok_s=result.get("throughput_tok_s", 0.0),
            error_message=result.get("error_message"),
            request_id=request_id
        )
        
    except HTTPException:
        raise
    except Exception as e:
        return InferenceResponse(
            success=False,
            error_message=str(e),
            request_id=str(uuid.uuid4())
        )


async def process_inference_task(request: InferenceRequest, request_id: str):
    """Process an inference task (simplified implementation)."""
    
    from exo_windows_porting.backend.factory import get_backend_factory
    
    try:
        # Get backend factory
        factory = get_backend_factory()
        
        # Create backend instance with force_cpu if GPU not available when required
        if request.gpu_required and (not factory.hardware.has_nvidia_gpu and not factory.hardware.has_amd_gpu):
            raise HTTPException(
                status_code=400, 
                detail="GPU required but no GPU available on this system"
            )
        
        backend = factory.create_backend(
            model_path=request.model_path,
            force_cpu=(not request.gpu_required)  # Force CPU if GPU not requested
        )
        
        # Execute inference
        start_time = time.time()
        result_text = await backend.generate(
            prompt=request.prompt,
            max_tokens=request.max_tokens
        )
        elapsed_ms = (time.time() - start_time) * 1000
        
        # Calculate metrics
        tokens_generated = len(result_text.split()) if result_text else 0
        throughput = tokens_generated / (elapsed_ms / 1000) if elapsed_ms > 0 else 0
        
        return {
            "success": True,
            "text": result_text or "",
            "tokens_generated": tokens_generated,
            "time_ms": round(elapsed_ms, 2),
            "throughput_tok_s": round(throughput, 2)
        }
        
    except HTTPException:
        # Re-raise HTTP exceptions to be handled by FastAPI
        raise
    except Exception as e:
        import traceback
        
        # Log full traceback for debugging (in production, use proper logging)
        print(f"Error processing inference task {request_id}: {e}")
        traceback.print_exc()
        
        return {
            "success": False,
            "error_message": str(e),
            "tokens_generated": 0,
            "time_ms": 0.0,
            "throughput_tok_s": 0.0
        }


@app.get("/v1/models", response_model=List[ModelInfo])
async def list_models():
    """List available models in the cache."""
    
    return list(_model_cache.values())


@app.post("/v1/models/upload")
async def upload_model(
    model_path: str = Query(..., description="Path to GGUF model file"),
    size_mb: float = Query(..., description="Model size in MB")
):
    """Register a new model in the cache."""
    
    import os
    
    if not os.path.exists(model_path):
        raise HTTPException(status_code=404, detail=f"Model file not found: {model_path}")
    
    import time as time_module
    
    stat = os.stat(model_path)
    
    model_info = ModelInfo(
        path=model_path,
        size_mb=size_mb,
        modified_time=stat.st_mtime,
        available=True
    )
    
    _model_cache[model_path] = model_info
    
    return model_info


@app.get("/v1/cluster/status", response_model=ClusterStatus)
async def get_cluster_status():
    """Get current cluster status."""
    
    # Update uptime
    _cluster_status.uptime_seconds += 1
    
    return _cluster_status


@app.delete("/v1/models/{model_path}")
async def delete_model(model_path: str):
    """Remove a model from the cache."""
    
    if model_path not in _model_cache:
        raise HTTPException(status_code=404, detail=f"Model not found: {model_path}")
    
    del _model_cache[model_path]
    
    return {"status": "deleted", "path": model_path}


@app.get("/v1/models/{model_path}/info", response_model=ModelInfo)
async def get_model_info(model_path: str):
    """Get information about a specific model."""
    
    if model_path not in _model_cache:
        raise HTTPException(status_code=404, detail=f"Model not found: {model_path}")
    
    return _model_cache[model_path]


@app.get("/v1/health/backends")
async def check_backends():
    """Check status of all available backends."""
    
    from exo_windows_porting.backend.factory import get_backend_factory
    
    factory = get_backend_factory()
    info = factory.get_backend_info()
    
    return {
        "hardware": info["hardware"],
        "available_backends": info["available_backends"],
        "selected_backend": info["selected_backend"]
    }


# Startup event
@app.on_event("startup")
async def startup_event():
    """Initialize application on startup."""
    
    global _cluster_status
    
    import time as time_module
    
    _cluster_status.uptime_seconds = 0.0
    print(f"🚀 Exo Windows Porting API started at {time.strftime('%H:%M:%S')}")


# Shutdown event
@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown."""
    
    print(f"🛑 Exo Windows Porting API stopped at {time.strftime('%H:%M:%S')}")
