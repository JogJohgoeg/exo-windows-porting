"""
Backend Utilities for Exo Windows Porting.

This module provides utility functions for backend operations, hardware detection,
and system information gathering.

Author: Exo Windows Porting Team
License: MIT
"""

import subprocess
import platform
from typing import Dict, Optional, List, Any


class SystemInfo:
    """System information collector."""
    
    def __init__(self):
        self.os = platform.system()
        self.arch = platform.machine()
        self.python_version = platform.python_version()
        
        # CPU info
        try:
            import psutil
            
            self.cpu_count = psutil.cpu_count(logical=True)
            self.memory_total_gb = psutil.virtual_memory().total / (1024 ** 3)
            
        except ImportError:
            self.cpu_count = None
            self.memory_total_gb = None
        
        # GPU info (populated separately)
        self.has_amd_gpu = False
        self.has_nvidia_gpu = False
        self.amd_devices: List[Dict[str, str]] = []
        self.nvidia_devices: List[Dict[str, Any]] = []


def detect_hardware() -> SystemInfo:
    """Detect available GPU hardware on the system.
    
    Returns:
        SystemInfo object with detected hardware information
        
    Notes:
        - NVIDIA detection via nvidia-smi (Windows/Linux/macOS)
        - AMD detection via dxdiag on Windows only
        - Gracefully handles missing or malformed output
    """
    
    info = SystemInfo()
    
    # Check for NVIDIA GPU
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0 and result.stdout.strip():
            info.has_nvidia_gpu = True
            
            for line in result.stdout.strip().split("\n"):
                parts = [p.strip() for p in line.split(",")]
                
                # Validate we have enough fields before parsing
                if len(parts) >= 3:
                    try:
                        device_id = int(parts[0])
                        name = parts[1]
                        memory_mb_str = parts[2].replace(" MiB", "").replace(" MB", "")
                        
                        # Handle cases where memory field might be empty or invalid
                        if memory_mb_str and memory_mb_str.isdigit():
                            memory_mb = int(memory_mb_str)
                            
                            info.nvidia_devices.append({
                                "id": device_id,
                                "name": name,
                                "memory_total_mb": memory_mb
                            })
                    except (ValueError, IndexError):
                        # Skip malformed lines
                        print(f"⚠️ Skipping malformed nvidia-smi line: {line}")
                        continue
                elif len(parts) == 2 and parts[0].strip():
                    # Handle case where memory.total is not available
                    try:
                        device_id = int(parts[0])
                        name = parts[1]
                        
                        info.nvidia_devices.append({
                            "id": device_id,
                            "name": name,
                            "memory_total_mb": 0  # Unknown
                        })
                    except ValueError:
                        pass
                        
    except subprocess.TimeoutExpired:
        print("⚠️ NVIDIA GPU detection timed out (nvidia-smi took >5s)")
    except FileNotFoundError:
        print("ℹ️ nvidia-smi not found - no NVIDIA GPUs detected")
    except Exception as e:
        print(f"⚠️ Error detecting NVIDIA GPUs: {e}")
    
    # Check for AMD GPU (Windows only)
    if info.os == "Windows":
        try:
            result = subprocess.run(
                ["dxdiag", "/T"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0 and ("AMD" in result.stdout.upper() or "RADEON" in result.stdout.upper()):
                info.has_amd_gpu = True
                
                # Parse AMD GPU information from dxdiag output
                for line in result.stdout.split("\n"):
                    if "Display Device" in line:
                        continue  # Skip header lines
                    
                    stripped = line.strip()
                    if any(keyword in stripped.upper() for keyword in ["AMD", "RADEON"]):
                        info.amd_devices.append({"name": stripped})
                        
        except subprocess.TimeoutExpired:
            print("⚠️ AMD GPU detection timed out (dxdiag took >10s)")
        except Exception as e:
            print(f"⚠️ Error detecting AMD GPUs: {e}")
    else:
        # On non-Windows systems, skip AMD detection but don't error
        pass
    
    return info


def check_rocm_availability() -> Dict[str, Any]:
    """Check if ROCm is available and properly installed."""
    
    result = {
        "available": False,
        "version": None,
        "error": None
    }
    
    try:
        # Check for rocm-smi command (ROCm system management interface)
        subprocess.run(
            ["rocm-smi", "--showproductname"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        result["available"] = True
        
    except FileNotFoundError:
        result["error"] = "rocm-smi command not found. ROCm may not be installed."
    
    except subprocess.TimeoutExpired:
        result["error"] = "ROCm query timed out. System may be under heavy load."
    
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
