"""
Exo Windows Porting - Web Dashboard Module

This package provides web-based dashboard and REST API functionality.
"""

from .server import (
    app,
    InferenceRequest,
    InferenceResponse,
    ModelInfo,
    ClusterStatus
)

__all__ = [
    "app",
    "InferenceRequest",
    "InferenceResponse", 
    "ModelInfo",
    "ClusterStatus",
    "run_inference"
]

__version__ = "0.1.0-alpha"
__author__ = "Exo Windows Porting Team"
