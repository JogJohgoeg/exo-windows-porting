"""
Exo Windows Porting - API Compatibility Layer

This package provides compatibility with the original Exo API protocol.
"""

from .compat_layer import (
    ExoMessage,
    InferenceRequest,
    InferenceResponse,
    NodeInfo,
    ExoProtocolHandler,
    ExoAPIServer,
    create_exo_server,
    run_inference
)

__all__ = [
    "ExoMessage",
    "InferenceRequest",
    "InferenceResponse", 
    "NodeInfo",
    "ExoProtocolHandler",
    "ExoAPIServer",
    "create_exo_server",
    "run_inference"
]

__version__ = "0.1.0-alpha"
__author__ = "Exo Windows Porting Team"
