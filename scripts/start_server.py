#!/usr/bin/env python3
"""
FastAPI Server Startup Script for Exo Windows Porting.

Usage:
    python scripts/start_server.py [--port 8000] [--host 127.0.0.1]

Options:
    --port PORT     Port number to listen on (default: 8000)
    --host HOST     Host address to bind to (default: 127.0.0.1)
"""

import sys
import argparse


def parse_args():
    """Parse command line arguments."""
    
    parser = argparse.ArgumentParser(
        description="Start Exo Windows Porting FastAPI server"
    )
    
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=8000,
        help="Port number to listen on (default: 8000)"
    )
    
    parser.add_argument(
        "--host", "-H",
        type=str,
        default="127.0.0.1",
        help="Host address to bind to (default: 127.0.0.1)"
    )
    
    return parser.parse_args()


def main():
    """Main entry point."""
    
    args = parse_args()
    
    print(f"🚀 Starting Exo Windows Porting API Server...")
    print(f"   Host: {args.host}")
    print(f"   Port: {args.port}")
    print(f"   API Docs: http://{args.host}:{args.port}/docs")
    print()
    
    # Import and run uvicorn
    try:
        import uvicorn
        
        uvicorn.run(
            "exo_windows_porting.dashboard.server:app",
            host=args.host,
            port=args.port,
            reload=False,  # Set to True for development
            log_level="info"
        )
        
    except ImportError as e:
        print(f"\n❌ Error: {e}")
        print("\nTo run the server, please install uvicorn:")
        print("   pip install uvicorn")
        sys.exit(1)


if __name__ == "__main__":
    main()
