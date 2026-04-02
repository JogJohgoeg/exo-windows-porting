#!/usr/bin/env python3
"""
API Client Test Script for Exo Windows Porting.

This script demonstrates how to use the REST API endpoints.
Run with: python scripts/api_client_test.py [base_url]

Default base URL: http://127.0.0.1:8000
"""

import requests
import json
import sys


BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"


def print_separator():
    """Print separator line."""
    print("=" * 60)


def test_health():
    """Test health endpoint."""
    
    print("\n🏥 Testing Health Endpoint")
    print(f"   URL: {BASE_URL}/health")
    
    try:
        response = requests.get(f"{BASE_URL}/health", timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            print(f"   ✅ Status: OK")
            print(f"   Timestamp: {data.get('timestamp')}")
            print(f"   Uptime: {data.get('uptime_seconds', 0)}s")
        else:
            print(f"   ❌ Status: {response.status_code}")
            
    except requests.exceptions.RequestException as e:
        print(f"   ❌ Error: {e}")


def test_root():
    """Test root endpoint."""
    
    print("\n🏠 Testing Root Endpoint")
    print(f"   URL: {BASE_URL}/")
    
    try:
        response = requests.get(BASE_URL, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            print(f"   ✅ Status: OK")
            print(f"   Title: {data.get('title')}")
            print(f"   Version: {data.get('version')}")
        else:
            print(f"   ❌ Status: {response.status_code}")
            
    except requests.exceptions.RequestException as e:
        print(f"   ❌ Error: {e}")


def test_backends():
    """Test backend health check."""
    
    print("\n🔧 Testing Backend Health")
    print(f"   URL: {BASE_URL}/v1/health/backends")
    
    try:
        response = requests.get(f"{BASE_URL}/v1/health/backends", timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            print(f"   ✅ Status: OK")
            
            print("\n   Available Backends:")
            for name, available in data.get("available_backends", {}).items():
                status = "✅" if available else "❌"
                print(f"      {status} {name.upper()}")
                
            print(f"\n   Selected Backend: {data.get('selected_backend', 'N/A').upper()}")
            
        else:
            print(f"   ❌ Status: {response.status_code}")
            
    except requests.exceptions.RequestException as e:
        print(f"   ❌ Error: {e}")


def test_inference():
    """Test inference endpoint (requires running server and model)."""
    
    print("\n🧪 Testing Inference Endpoint")
    print(f"   URL: {BASE_URL}/v1/inference")
    
    # Sample request
    payload = {
        "prompt": "What is the meaning of life?",
        "model_path": "/models/Qwen2.5-7B-Instruct.Q4_K_M.gguf",
        "max_tokens": 64,
        "temperature": 0.7
    }
    
    print(f"   Request payload: {json.dumps(payload, indent=6)}")
    
    try:
        response = requests.post(
            f"{BASE_URL}/v1/inference",
            json=payload,
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            print(f"   ✅ Status: OK")
            print(f"\n   Response:")
            print(f"      Success: {data.get('success')}")
            print(f"      Text: {data.get('text', '')[:100]}...")
            print(f"      Tokens Generated: {data.get('tokens_generated', 0)}")
            print(f"      Time: {data.get('time_ms', 0):.2f}ms")
            print(f"      Throughput: {data.get('throughput_tok_s', 0):.2f} tok/s")
        else:
            data = response.json() if response.content else {}
            print(f"   ❌ Status: {response.status_code}")
            print(f"   Error: {data.get('error_message', 'Unknown error')}")
            
    except requests.exceptions.Timeout:
        print(f"   ⏱️ Timeout (server may be busy)")
    except requests.exceptions.RequestException as e:
        print(f"   ❌ Error: {e}")


def test_models():
    """Test model management endpoints."""
    
    print("\n📦 Testing Model Management")
    print(f"   URL: {BASE_URL}/v1/models")
    
    try:
        response = requests.get(f"{BASE_URL}/v1/models", timeout=5)
        
        if response.status_code == 200:
            models = response.json()
            print(f"   ✅ Status: OK")
            print(f"\n   Available Models ({len(models)}):")
            
            for model in models:
                print(f"      - {model.get('path', 'Unknown')}")
                print(f"        Size: {model.get('size_mb', 0):.1f} MB")
                
        else:
            print(f"   ❌ Status: {response.status_code}")
            
    except requests.exceptions.RequestException as e:
        print(f"   ❌ Error: {e}")


def main():
    """Run all API tests."""
    
    print("\n🚀 Exo Windows Porting - REST API Client Test")
    print("=" * 60)
    print(f"Base URL: {BASE_URL}")
    print_separator()
    
    # Run basic health checks first
    test_root()
    test_health()
    test_backends()
    
    # Try model listing (may be empty initially)
    test_models()
    
    # Test inference (will fail if server not running or no models)
    print_separator()
    print("⚠️  Inference test requires a running server with available models")
    print_separator()
    test_inference()
    
    print("\n" + "=" * 60)
    print("✅ API Client Test Complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
