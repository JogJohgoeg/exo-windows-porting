#!/usr/bin/env python3
"""
Quick test script for Exo API compatibility layer.

This script demonstrates the usage of the Exo protocol and backend factory.
Run with: python scripts/test_api_compat.py
"""

import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def test_backend_factory():
    """Test the backend factory and hardware detection."""
    
    print("=" * 60)
    print("Testing Backend Factory")
    print("=" * 60)
    
    from exo_windows_porting.backend.factory import get_backend_factory, HardwareDetector
    
    # Detect hardware
    hardware = HardwareDetector()
    
    print(f"\n🎮 GPU Detection:")
    print(f"   NVIDIA GPUs: {len(hardware.nvidia_devices)}")
    for device in hardware.nvidia_devices:
        print(f"      - ID {device['id']}: {device['name']} ({device['memory_total_mb'] / 1024:.1f} GB)")
    
    print(f"   AMD GPUs: {len(hardware.amd_devices)}")
    for device in hardware.amd_devices:
        print(f"      - {device['name']}")
    
    # Get backend factory
    factory = get_backend_factory()
    info = factory.get_backend_info()
    
    print(f"\n🔧 Available Backends:")
    for name, available in info["available_backends"].items():
        status = "✅" if available else "❌"
        print(f"   {status} {name.upper()}")
    
    print(f"\n🎯 Selected Backend: {info['selected_backend'].upper()}")
    
    return True


async def test_exo_protocol():
    """Test the Exo protocol message handling."""
    
    print("\n" + "=" * 60)
    print("Testing Exo Protocol")
    print("=" * 60)
    
    from exo_windows_porting.api.compat_layer import (
        InferenceRequest,
        InferenceResponse,
        NodeInfo,
        ExoProtocolHandler
    )
    
    handler = ExoProtocolHandler()
    
    # Create test messages
    request = InferenceRequest(
        message_id="test-001",
        from_node="client-001",
        to_node="node-001",
        model_path="/models/Qwen2.5-7B-Instruct.Q4_K_M.gguf",
        prompt="What is the meaning of life?",
        max_tokens=128,
        temperature=0.7
    )
    
    print(f"\n📤 Created Request:")
    print(f"   Message ID: {request.message_id}")
    print(f"   Prompt: {request.prompt[:50]}...")
    print(f"   Max Tokens: {request.max_tokens}")
    
    # Serialize and deserialize
    json_str = handler.serialize(request)
    deserialized = handler.deserialize(json_str)
    
    print(f"\n📋 Serialization Test:")
    print(f"   JSON Length: {len(json_str)} bytes")
    print(f"   Deserialized ID: {deserialized.message_id}")
    print(f"   Match: {'✅' if request.message_id == deserialized.message_id else '❌'}")
    
    return True


async def test_api_server():
    """Test the Exo API server (without actual inference)."""
    
    print("\n" + "=" * 60)
    print("Testing Exo API Server")
    print("=" * 60)
    
    from exo_windows_porting.backend.factory import get_backend_factory
    from exo_windows_porting.api.compat_layer import create_exo_server
    
    factory = get_backend_factory()
    server = create_exo_server(factory)
    
    # Create a test request (won't execute without model file)
    request = server.create_request(
        prompt="Test prompt",
        model_path="/models/test.gguf"
    )
    
    print(f"\n📤 API Request Created:")
    print(f"   Message ID: {request.message_id}")
    print(f"   Model Path: {request.model_path}")
    print(f"   Prompt: {request.prompt}")
    
    # Simulate error response (model not found)
    import asyncio
    
    async def simulate_request():
        response = await server.handle_inference_request(request)
        
        if response.error_code:
            print(f"\n⚠️ Expected Error Response:")
            print(f"   Error Code: {response.error_code}")
            print(f"   Message: {response.error_message}")
    
    await simulate_request()
    
    return True


async def main():
    """Run all tests."""
    
    print("\n🚀 Exo Windows Porting - API Compatibility Layer Test")
    print("=" * 60)
    
    try:
        # Run tests
        success = True
        
        if not await test_backend_factory():
            success = False
        
        if not await test_exo_protocol():
            success = False
        
        if not await test_api_server():
            success = False
        
        print("\n" + "=" * 60)
        
        if success:
            print("✅ All tests passed!")
            return 0
        else:
            print("❌ Some tests failed")
            return 1
            
    except Exception as e:
        print(f"\n❌ Test error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
