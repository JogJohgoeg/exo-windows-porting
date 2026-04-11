"""
GPU Backend Factory for Exo Windows Porting.

This module provides a unified interface for selecting and creating GPU backends
(ROCm, CUDA, CPU) based on available hardware and user configuration.

Author: Exo Windows Porting Team
License: MIT
"""

import logging
import threading
from typing import Optional, Dict, Any, Type
from dataclasses import dataclass
import os

logger = logging.getLogger(__name__)

_VALID_BACKENDS = frozenset({"rocm", "cuda", "cpu"})


@dataclass
class BackendConfig:
    """Configuration for GPU backend selection."""

    # User preferences
    preferred_backend: Optional[str] = None  # "rocm", "cuda", or "cpu"
    force_cpu: bool = False

    # Hardware constraints
    min_gpu_memory_mb: int = 4096

    # Performance tuning
    max_tokens: int = 512
    n_ctx: int = 8192

    # Debug settings
    verbose: bool = False

    def __post_init__(self) -> None:
        if self.max_tokens <= 0:
            raise ValueError(f"max_tokens must be a positive integer, got {self.max_tokens!r}")
        if self.n_ctx <= 0:
            raise ValueError(f"n_ctx must be a positive integer, got {self.n_ctx!r}")
        if self.preferred_backend is not None and self.preferred_backend not in _VALID_BACKENDS:
            raise ValueError(
                f"preferred_backend must be one of {sorted(_VALID_BACKENDS)}, "
                f"got {self.preferred_backend!r}"
            )


class BackendRegistry:
    """Registry for available backend implementations."""
    
    _backends: Dict[str, Type] = {}
    
    @classmethod
    def register(cls, name: str, backend_class: Optional[Type] = None):
        """Register a backend class or use as decorator.
        
        Usage 1 - Direct registration:
            BackendRegistry.register("cuda", LLamaCudaBackend)
            
        Usage 2 - As decorator:
            @BackendRegistry.register("cuda")
            class LLamaCudaBackend:
                pass
        
        Args:
            name: Backend name identifier
            backend_class: Backend class to register (optional if used as decorator)
        """
        
        def decorator(backend: Type):
            cls._backends[name] = backend
            return backend
        
        # If called with a class directly, register it
        if backend_class is not None:
            cls._backends[name] = backend_class
            return backend_class
        
        # Otherwise, return decorator for use as @register decorator
        return decorator
    
    @classmethod
    def get_backend(cls, name: str) -> Optional[Type]:
        """Get a registered backend class by name."""
        
        return cls._backends.get(name.lower())
    
    @classmethod
    def list_backends(cls) -> Dict[str, bool]:
        """List all available backends and their status."""
        
        # Import here to avoid circular import
        from .backend_utils import detect_hardware
        
        hardware = detect_hardware()
        
        results = {}
        for name in cls._backends.keys():
            if name == "rocm":
                results[name] = hardware.has_amd_gpu
            elif name == "cuda":
                results[name] = hardware.has_nvidia_gpu
            else:  # cpu
                results[name] = True
        
        return results


class HardwareDetector:
    """Detect available GPU hardware on the system."""
    
    def __init__(self):
        self.has_amd_gpu = False
        self.has_nvidia_gpu = False
        self.amd_devices: list = []
        self.nvidia_devices: list = []
        
        self._detect_hardware()
    
    def _detect_hardware(self):
        """Detect AMD and NVIDIA GPUs."""
        
        # Check for NVIDIA GPU
        try:
            import subprocess
            
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=index,name", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0 and result.stdout.strip():
                self.has_nvidia_gpu = True
                for line in result.stdout.strip().split("\n"):
                    parts = line.split(",")
                    if len(parts) >= 2:
                        device_id = int(parts[0].strip())
                        name = parts[1].strip()
                        self.nvidia_devices.append({
                            "id": device_id,
                            "name": name
                        })
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        
        # AMD GPU + ROCm stack detection (delegates to backend_utils)
        try:
            from .backend_utils import detect_hardware as _detect
            _info = _detect()
            self.has_amd_gpu = _info.has_amd_gpu
            self.amd_devices = _info.amd_devices
            # rocm_ready is what actually matters for backend selection
            self._rocm_ready = _info.rocm_ready
        except Exception:
            self._rocm_ready = False


class BackendFactory:
    """Factory for creating GPU backend instances."""
    
    def __init__(self, config: Optional[BackendConfig] = None):
        self.config = config or BackendConfig()
        self.hardware = HardwareDetector()
        
        # Register backends with safe imports so a missing wheel (e.g. ROCm
        # not installed) doesn't crash factory construction.
        try:
            from .llama_rocm import LLamaRocmBackend
            BackendRegistry.register("rocm", LLamaRocmBackend)
        except ImportError as exc:
            logger.debug("ROCm backend unavailable: %s", exc)

        try:
            from .llama_cuda import LLamaCudaBackend
            BackendRegistry.register("cuda", LLamaCudaBackend)
        except ImportError as exc:
            logger.debug("CUDA backend unavailable: %s", exc)

        # CPU backend is always required — let any ImportError propagate.
        from .llama_cpu import LLamaCpuBackend
        BackendRegistry.register("cpu", LLamaCpuBackend)
    
    def select_backend(self) -> str:
        """Select the best available backend based on hardware and preferences.
        
        Returns:
            Backend name ("cpu", "cuda", or "rocm")
            
        Raises:
            ValueError: If GPU requested but not available
        """
        
        # Force CPU mode takes priority
        if self.config.force_cpu:
            return "cpu"
        
        # User preference takes priority if explicitly set and available
        if self.config.preferred_backend:
            if self._is_available(self.config.preferred_backend):
                return self.config.preferred_backend
            else:
                # Preferred backend not available, fall through to auto-detection
                pass
        
        # Hardware-based selection (priority order)
        if self.hardware.has_nvidia_gpu:
            return "cuda"

        # Only select ROCm when the software stack is actually installed.
        # has_amd_gpu alone (detected via dxdiag) is not sufficient.
        if getattr(self.hardware, "_rocm_ready", False):
            return "rocm"

        return "cpu"
    
    def _is_available(self, backend_name: str) -> bool:
        """Check if a backend is available on this system."""
        
        if backend_name == "rocm":
            return self.hardware.has_amd_gpu
        
        if backend_name == "cuda":
            return self.hardware.has_nvidia_gpu
        
        return True  # CPU always available
    
    def create_backend(self, model_path: str, force_cpu: bool = False) -> Any:
        """Create a backend instance based on hardware detection.
        
        Args:
            model_path: Path to the GGUF model file
            force_cpu: Force CPU-only mode regardless of GPU availability
            
        Returns:
            Backend instance (LLamaCpuBackend, LLamaRocmBackend, or LLamaCudaBackend)
            
        Raises:
            ValueError: If GPU requested but not available and not forced to CPU
        """
        
        if force_cpu:
            from .llama_cpu import LLamaCpuBackend
            
            return LLamaCpuBackend(
                model_path=model_path,
                n_ctx=self.config.n_ctx,
                verbose=self.config.verbose
            )
        
        selected = self.select_backend()

        if selected == "rocm":
            try:
                from .llama_rocm import LLamaRocmBackend
                return LLamaRocmBackend(
                    model_path=model_path,
                    device_id=0,
                    n_ctx=self.config.n_ctx,
                    verbose=self.config.verbose,
                )
            except ImportError:
                logger.warning(
                    "ROCm backend selected but wheel not installed — falling back to CPU"
                )
                selected = "cpu"

        if selected == "cuda":
            try:
                from .llama_cuda import LLamaCudaBackend
                return LLamaCudaBackend(
                    model_path=model_path,
                    device_id=0,
                    n_ctx=self.config.n_ctx,
                    verbose=self.config.verbose,
                )
            except ImportError:
                logger.warning(
                    "CUDA backend selected but wheel not installed — falling back to CPU"
                )
                selected = "cpu"

        # CPU fallback (also the explicit "cpu" path)
        from .llama_cpu import LLamaCpuBackend
        return LLamaCpuBackend(
            model_path=model_path,
            n_ctx=self.config.n_ctx,
            verbose=self.config.verbose,
        )
    
    def get_backend_info(self) -> Dict[str, Any]:
        """Get information about available backends."""
        
        # Determine selected backend considering force_cpu and preferred_backend
        if self.config.force_cpu:
            selected = "cpu"
        elif self.config.preferred_backend and self._is_available(self.config.preferred_backend):
            selected = self.config.preferred_backend
        else:
            selected = self.select_backend()
        
        return {
            "hardware": {
                "has_amd_gpu": self.hardware.has_amd_gpu,
                "has_nvidia_gpu": self.hardware.has_nvidia_gpu,
                "amd_devices": self.hardware.amd_devices,
                "nvidia_devices": self.hardware.nvidia_devices
            },
            "available_backends": {name: available 
                                   for name, available in BackendRegistry.list_backends().items()},
            "selected_backend": selected,
            "config": {
                "force_cpu": self.config.force_cpu,
                "preferred_backend": self.config.preferred_backend
            }
        }


# Singleton instance for global access — protected by a lock for thread safety.
_factory_instance: Optional[BackendFactory] = None
_factory_lock = threading.Lock()


def get_backend_factory(config: Optional[BackendConfig] = None) -> BackendFactory:
    """
    Return the process-wide BackendFactory singleton.

    Thread-safe via double-checked locking: the common case (already
    initialised) pays only one attribute read; the lock is only acquired
    on first call or after a test reset.
    """
    global _factory_instance

    if _factory_instance is None:
        with _factory_lock:
            if _factory_instance is None:
                _factory_instance = BackendFactory(config)
                logger.debug("BackendFactory singleton created")

    return _factory_instance


# Quick access functions for common use cases
def detect_hardware() -> HardwareDetector:
    """Quick hardware detection."""
    return HardwareDetector()


def get_available_backends() -> Dict[str, bool]:
    """Get list of available backends (initialises singleton if needed)."""
    get_backend_factory()   # ensures _factory_instance is set and backends registered
    return BackendRegistry.list_backends()
