"""
GPU Backend Factory for Exo Windows Porting.

This module provides a unified interface for selecting and creating GPU backends
(ROCm, CUDA, CPU) based on available hardware and user configuration.

Author: Exo Windows Porting Team
License: MIT
"""

from typing import Optional, Dict, Any, Type
from dataclasses import dataclass
import os


@dataclass
class BackendConfig:
    """Configuration for GPU backend selection."""
    
    # User preferences
    preferred_backend: Optional[str] = None  # "rocm", "cuda", or "cpu"
    
    # Hardware constraints
    min_gpu_memory_mb: int = 4096  # Minimum required VRAM
    
    # Performance tuning
    max_tokens: int = 512
    n_ctx: int = 8192
    
    # Debug settings
    verbose: bool = False


class BackendRegistry:
    """Registry for available backend implementations."""
    
    _backends: Dict[str, Type] = {}
    
    @classmethod
    def register(cls, name: str):
        """Decorator to register a backend class."""
        
        def decorator(backend_class: Type):
            cls._backends[name] = backend_class
            return backend_class
        
        return decorator
    
    @classmethod
    def get_backend(cls, name: str) -> Optional[Type]:
        """Get a registered backend class by name."""
        
        return cls._backends.get(name.lower())
    
    @classmethod
    def list_backends(cls) -> Dict[str, bool]:
        """List all available backends and their status."""
        
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
        
        # Check for AMD GPU (Windows)
        try:
            import subprocess
            
            result = subprocess.run(
                ["dxdiag", "/T"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0 and "AMD" in result.stdout.upper():
                self.has_amd_gpu = True
                # Parse AMD GPU info from dxdiag output
                for line in result.stdout.split("\n"):
                    if "AMD" in line.upper() or "RADEON" in line.upper():
                        self.amd_devices.append({"name": line.strip()})
        except (subprocess.TimeoutExpired, Exception):
            pass


class BackendFactory:
    """Factory for creating GPU backend instances."""
    
    def __init__(self, config: Optional[BackendConfig] = None):
        self.config = config or BackendConfig()
        self.hardware = HardwareDetector()
        
        # Initialize registry if not already done
        from . import llama_rocm, llama_cuda
        
        BackendRegistry.register("rocm")
        BackendRegistry.register("cuda")
        BackendRegistry.register("cpu")
    
    def select_backend(self) -> str:
        """Select the best available backend based on hardware and preferences."""
        
        # User preference takes priority if explicitly set
        if self.config.preferred_backend:
            if self._is_available(self.config.preferred_backend):
                return self.config.preferred_backend
        
        # Hardware-based selection (priority order)
        if self.hardware.has_nvidia_gpu:
            return "cuda"
        
        if self.hardware.has_amd_gpu:
            return "rocm"
        
        return "cpu"
    
    def _is_available(self, backend_name: str) -> bool:
        """Check if a backend is available on this system."""
        
        if backend_name == "rocm":
            return self.hardware.has_amd_gpu
        
        if backend_name == "cuda":
            return self.hardware.has_nvidia_gpu
        
        return True  # CPU always available
    
    def create_backend(self, model_path: str) -> Any:
        """Create a backend instance based on hardware detection."""
        
        selected = self.select_backend()
        
        if selected == "rocm":
            from .llama_rocm import LLamaRocmBackend
            
            return LLamaRocmBackend(
                model_path=model_path,
                device_id=0,
                n_ctx=self.config.n_ctx
            )
        
        elif selected == "cuda":
            from .llama_cuda import LLamaCudaBackend
            
            return LLamaCudaBackend(
                model_path=model_path,
                device_id=0,
                n_ctx=self.config.n_ctx
            )
        
        else:  # cpu
            from .llama_cpu import LLamaCpuBackend
            
            return LLamaCpuBackend(
                model_path=model_path,
                n_ctx=self.config.n_ctx,
                verbose=self.config.verbose
            )
    
    def get_backend_info(self) -> Dict[str, Any]:
        """Get information about available backends."""
        
        return {
            "hardware": {
                "has_amd_gpu": self.hardware.has_amd_gpu,
                "has_nvidia_gpu": self.hardware.has_nvidia_gpu,
                "amd_devices": self.hardware.amd_devices,
                "nvidia_devices": self.hardware.nvidia_devices
            },
            "available_backends": {name: available 
                                   for name, available in BackendRegistry.list_backends().items()},
            "selected_backend": self.select_backend() if not self.config.preferred_backend else self.config.preferred_backend
        }


# Singleton instance for global access
_factory_instance: Optional[BackendFactory] = None


def get_backend_factory(config: Optional[BackendConfig] = None) -> BackendFactory:
    """Get or create the singleton backend factory instance."""
    
    global _factory_instance
    
    if _factory_instance is None:
        _factory_instance = BackendFactory(config)
    
    return _factory_instance


# Quick access functions for common use cases
def detect_hardware() -> HardwareDetector:
    """Quick hardware detection."""
    return HardwareDetector()


def get_available_backends() -> Dict[str, bool]:
    """Get list of available backends."""
    if _factory_instance is None:
        BackendFactory()  # Initialize singleton
    
    return BackendRegistry.list_backends()
