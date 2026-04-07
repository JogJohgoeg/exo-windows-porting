"""
Backend Utilities for Exo Windows Porting.

This module provides utility functions for backend operations, hardware detection,
and system information gathering.

Author: Exo Windows Porting Team
License: MIT
"""

"""
Backend Utilities for Exo Windows Porting.

Key design principle: GPU *hardware* presence and the GPU *software stack*
availability are two separate questions and must be checked independently.

  has_amd_gpu   – an AMD GPU exists in the system (detected via dxdiag/rocm-smi)
  rocm_ready    – the ROCm software stack is installed and functional
                  (verified by running rocm-smi --showproductname)

Only when both are True should the ROCm backend be selected.
"""

import logging
import subprocess
import platform
from typing import Dict, Optional, List, Any

logger = logging.getLogger(__name__)


class SystemInfo:
    """System information collector."""

    def __init__(self):
        self.os = platform.system()
        self.arch = platform.machine()
        self.python_version = platform.python_version()

        try:
            import psutil
            self.cpu_count = psutil.cpu_count(logical=True)
            self.memory_total_gb = psutil.virtual_memory().total / (1024 ** 3)
        except ImportError:
            self.cpu_count = None
            self.memory_total_gb = None

        # GPU hardware presence
        self.has_amd_gpu: bool = False
        self.has_nvidia_gpu: bool = False
        self.amd_devices: List[Dict[str, str]] = []
        self.nvidia_devices: List[Dict[str, Any]] = []

        # Software stack availability (populated by detect_hardware)
        self.rocm_ready: bool = False
        self.cuda_ready: bool = False


def detect_hardware() -> SystemInfo:
    """
    Detect available GPU hardware AND software stacks.

    NVIDIA: nvidia-smi is used for both hardware detection and CUDA readiness.
    AMD: hardware is detected via rocm-smi (preferred) or dxdiag (fallback).
         rocm_ready is True only when rocm-smi responds successfully — meaning
         the ROCm driver stack is actually installed, not just that an AMD GPU
         exists in the system.

    Returns:
        SystemInfo with populated hardware and stack-availability fields.
    """
    info = SystemInfo()

    # ── NVIDIA detection ──────────────────────────────────────────────
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode == 0 and result.stdout.strip():
            info.has_nvidia_gpu = True
            info.cuda_ready = True  # nvidia-smi present ⟹ CUDA driver installed

            for line in result.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    try:
                        mem_str = parts[2].replace(" MiB", "").replace(" MB", "").strip()
                        info.nvidia_devices.append({
                            "id": int(parts[0]),
                            "name": parts[1],
                            "memory_total_mb": int(mem_str) if mem_str.isdigit() else 0,
                        })
                    except (ValueError, IndexError):
                        logger.warning("Skipping malformed nvidia-smi line: %r", line)

    except subprocess.TimeoutExpired:
        logger.warning("NVIDIA GPU detection timed out (nvidia-smi took >5 s)")
    except FileNotFoundError:
        logger.debug("nvidia-smi not found — no NVIDIA GPU or driver not installed")
    except Exception as exc:
        logger.warning("NVIDIA GPU detection error: %s", exc)

    # ── AMD / ROCm detection ──────────────────────────────────────────
    #
    # Strategy (in priority order):
    #   1. rocm-smi --showproductname  → proves ROCm stack is installed
    #   2. dxdiag /T (Windows fallback) → proves AMD GPU exists but NOT ROCm
    #
    # has_amd_gpu is set by either method.
    # rocm_ready is set ONLY when rocm-smi succeeds.

    try:
        rocm_result = subprocess.run(
            ["rocm-smi", "--showproductname"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if rocm_result.returncode == 0:
            info.has_amd_gpu = True
            info.rocm_ready = True

            for line in rocm_result.stdout.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("=") and stripped != "GPU":
                    info.amd_devices.append({"name": stripped})

            logger.info("ROCm stack detected: %d device(s)", len(info.amd_devices))
        else:
            logger.debug("rocm-smi exited with code %d; ROCm not ready", rocm_result.returncode)

    except FileNotFoundError:
        logger.debug("rocm-smi not found — ROCm not installed; will try dxdiag fallback")
        # Fallback: detect AMD GPU hardware via dxdiag (Windows only)
        if platform.system() == "Windows":
            _detect_amd_via_dxdiag(info)
    except subprocess.TimeoutExpired:
        logger.warning("rocm-smi timed out (>5 s)")
    except Exception as exc:
        logger.warning("ROCm detection error: %s", exc)

    return info


def _detect_amd_via_dxdiag(info: SystemInfo) -> None:
    """
    Fallback AMD GPU hardware detection via dxdiag on Windows.

    This only sets has_amd_gpu; it does NOT set rocm_ready because dxdiag
    says nothing about the ROCm software stack.
    """
    try:
        result = subprocess.run(
            ["dxdiag", "/T", "-"],   # write output to stdout
            capture_output=True,
            text=True,
            timeout=15,
        )
        output_upper = result.stdout.upper()
        if result.returncode == 0 and ("AMD" in output_upper or "RADEON" in output_upper):
            info.has_amd_gpu = True
            for line in result.stdout.splitlines():
                if any(kw in line.upper() for kw in ("AMD", "RADEON")):
                    info.amd_devices.append({"name": line.strip()})
            logger.info(
                "AMD GPU(s) found via dxdiag, but ROCm stack is NOT installed. "
                "CPU fallback will be used."
            )
    except subprocess.TimeoutExpired:
        logger.warning("dxdiag timed out (>15 s)")
    except Exception as exc:
        logger.warning("dxdiag AMD detection error: %s", exc)


def check_rocm_availability() -> Dict[str, Any]:
    """
    Check whether the ROCm software stack is installed and functional.

    Returns a dict with keys: available (bool), version (str|None), error (str|None).
    """
    result: Dict[str, Any] = {"available": False, "version": None, "error": None}

    try:
        proc = subprocess.run(
            ["rocm-smi", "--showproductname"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            result["available"] = True
        else:
            result["error"] = f"rocm-smi exited with code {proc.returncode}: {proc.stderr.strip()}"
    except FileNotFoundError:
        result["error"] = "rocm-smi not found — ROCm driver not installed."
    except subprocess.TimeoutExpired:
        result["error"] = "ROCm query timed out."
    except Exception as exc:
        result["error"] = str(exc)

    return result


def check_cuda_availability() -> Dict[str, Any]:
    """Check if CUDA is available and properly installed."""
    
    result = {
        "available": False,
        "version": None,
        "device_count": 0,
        "devices": []
    }
    
    try:
        # Use nvidia-smi to query GPU information
        import json
        
        result["available"] = True
        
        # Get device count
        nvidia_smi_result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total,driver_version,cuda_version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if nvidia_smi_result.returncode == 0:
            lines = nvidia_smi_result.stdout.strip().split("\n")
            
            for line in lines[1:]:  # Skip header
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 5:
                    result["device_count"] += 1
                    
                    result["devices"].append({
                        "id": int(parts[0]),
                        "name": parts[1],
                        "memory_total_mb": int(parts[2].replace(" MiB", "")),
                        "driver_version": parts[3],
                        "cuda_version": parts[4] if len(parts) > 4 else None
                    })
    
    except (subprocess.TimeoutExpired, FileNotFoundError):
        result["error"] = "nvidia-smi not found or CUDA drivers not installed."
    
    return result


def get_model_info(model_path: str) -> Dict[str, Any]:
    """Get information about a GGUF model file."""
    
    import os
    
    if not os.path.exists(model_path):
        return {
            "exists": False,
            "error": f"Model file not found: {model_path}"
        }
    
    stats = os.stat(model_path)
    
    return {
        "exists": True,
        "path": model_path,
        "size_mb": round(stats.st_size / (1024 ** 2), 2),
        "modified_time": stats.st_mtime,
        "created_time": stats.st_ctime
    }


def format_speed(toks_per_sec: float) -> str:
    """Format tokens per second for display."""
    
    if toks_per_sec < 1:
        return f"{toks_per_sec:.2f} tok/s"
    elif toks_per_sec < 10:
        return f"{toks_per_sec:.1f} tok/s"
    else:
        return f"{toks_per_sec:.0f} tok/s"


def format_duration(seconds: float) -> str:
    """Format duration for display."""
    
    if seconds < 60:
        return f"{seconds:.2f}s"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        remaining = seconds % 60
        return f"{minutes}m {remaining:.1f}s"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h {minutes}m"
