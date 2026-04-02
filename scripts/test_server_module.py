#!/usr/bin/env python3
"""
Quick test for FastAPI server module.

This script tests the API server without actually running it.
Run with: python scripts/test_server_module.py
"""

import sys
import os

# Add project root to Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


def test_imports():
    """Test that all modules can be imported."""
    
    print("=" * 60)
    print("Testing Module Imports")
    print("=" * 60)
    
    try:
        from exo_windows_porting.dashboard.server import (
            app,
            InferenceRequest,
            InferenceResponse,
            ModelInfo,
            ClusterStatus
        )
        
        print("\n✅ All imports successful!")
        
        # Check FastAPI app instance
        print(f"\n📋 FastAPI App Details:")
        print(f"   Title: {app.title}")
        print(f"   Version: {app.version}")
        print(f"   Docs URL: {app.docs_url}")
        
        return True
        
    except ImportError as e:
        print(f"\n❌ Import error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_pydantic_models():
    """Test Pydantic model creation."""
    
    print("\n" + "=" * 60)
    print("Testing Pydantic Models")
    print("=" * 60)
    
    from exo_windows_porting.dashboard.server import (
        InferenceRequest,
        ModelInfo,
        ClusterStatus
    )
    
    try:
        # Test InferenceRequest
        request = InferenceRequest(
            prompt="Test prompt",
            model_path="/models/test.gguf",
            max_tokens=128,
            temperature=0.7
        )
        
        print("\n✅ InferenceRequest created successfully:")
        print(f"   Prompt: {request.prompt}")
        print(f"   Model Path: {request.model_path}")
        print(f"   Max Tokens: {request.max_tokens}")
        
        # Test ModelInfo
        model = ModelInfo(
            path="/models/Qwen2.5-7B-Instruct.Q4_K_M.gguf",
            size_mb=4096,
            modified_time=1712000000.0,
            available=True
        )
        
        print("\n✅ ModelInfo created successfully:")
        print(f"   Path: {model.path}")
        print(f"   Size: {model.size_mb} MB")
        
        # Test ClusterStatus
        cluster = ClusterStatus(
            total_nodes=3,
            active_nodes=2,
            total_gpu_memory_gb=96.0,
            average_load_percent=45.5,
            uptime_seconds=3600
        )
        
        print("\n✅ ClusterStatus created successfully:")
        print(f"   Total Nodes: {cluster.total_nodes}")
        print(f"   Active Nodes: {cluster.active_nodes}")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Model creation error: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests."""
    
    print("\n🧪 Exo Windows Porting - Server Module Test")
    print("=" * 60)
    
    success = True
    
    if not test_imports():
        success = False
    
    if not test_pydantic_models():
        success = False
    
    print("\n" + "=" * 60)
    
    if success:
        print("✅ All tests passed!")
        return 0
    else:
        print("❌ Some tests failed")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
