"""
Exo Windows Porting - GPU Backend Module

This package provides unified interfaces for different GPU backends (ROCm, CUDA, CPU).
"""

from .factory import (
    BackendConfig,
    BackendRegistry,
    HardwareDetector,
    BackendFactory,
    get_backend_factory,
    detect_hardware,
    get_available_backends
)

from .backend_utils import (
    SystemInfo,
    check_rocm_availability,
    check_cuda_availability,
    get_model_info,
    format_speed,
    format_duration
)

__all__ = [
    # Factory and Configuration
    "BackendConfig",
    "BackendRegistry",
    "HardwareDetector",
    "BackendFactory",
    "get_backend_factory",
    
    # Utilities
    "detect_hardware",
    "get_available_backends",
    "SystemInfo",
    "check_rocm_availability",
    "check_cuda_availability",
    "get_model_info",
    "format_speed",
    "format_duration"
]

__version__ = "0.1.0-alpha"
__author__ = "Exo Windows Porting Team"
