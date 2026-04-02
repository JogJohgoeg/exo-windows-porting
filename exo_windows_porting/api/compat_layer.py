"""
Exo API Compatibility Layer for Exo Windows Porting.

This module provides compatibility with the original Exo API protocol,
enabling seamless integration with existing Exo ecosystem tools and clients.

Author: Exo Windows Porting Team
License: MIT
"""

from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, asdict
import json
import time


@dataclass
class ExoMessage:
    """Exo protocol message format."""
    
    # Message type
    type: str = "request"  # request, response, heartbeat, discovery
    
    # Unique identifier
    message_id: Optional[str] = None
    
    # Source/destination node IDs
    from_node: Optional[str] = None
    to_node: Optional[str] = None
    
    # Payload (varies by message type)
    payload: Optional[Dict[str, Any]] = None
    
    # Timestamps
    timestamp: float = 0.0
    ttl_seconds: int = 300


@dataclass
class InferenceRequest(ExoMessage):
    """Inference request for LLM generation."""
    
    type: str = "inference_request"
    
    # Model specification
    model_id: str = ""
    model_path: Optional[str] = None
    
    # Generation parameters
    prompt: str = ""
    max_tokens: int = 512
    temperature: float = 0.7
    top_p: float = 0.9
    stop_sequences: List[str] = None
    
    # Hardware requirements
    gpu_required: bool = False
    min_gpu_memory_mb: Optional[int] = None
    
    def __post_init__(self):
        if self.stop_sequences is None:
            self.stop_sequences = []


@dataclass
class InferenceResponse(ExoMessage):
    """Inference response with generated text."""
    
    type: str = "inference_response"
    
    # Original request reference
    original_message_id: Optional[str] = None
    
    # Generated content
    text: str = ""
    
    # Performance metrics
    tokens_generated: int = 0
    time_ms: float = 0.0
    throughput_tok_s: float = 0.0
    
    # Error information (if applicable)
    error_code: Optional[int] = None
    error_message: Optional[str] = None


@dataclass
class NodeInfo(ExoMessage):
    """Node information for cluster discovery."""
    
    type: str = "node_info"
    
    node_id: str = ""
    host: str = ""
    port: int = 0
    
    # Hardware capabilities
    gpu_model: Optional[str] = None
    gpu_memory_total_mb: int = 0
    cpu_cores: int = 0
    
    # Current status
    is_ready: bool = False
    current_load_percent: float = 0.0


class ExoProtocolHandler:
    """Handle Exo protocol message encoding/decoding."""
    
    @staticmethod
    def serialize(message: ExoMessage) -> str:
        """Serialize a message to JSON string."""
        
        data = asdict(message)
        
        # Ensure timestamp is set
        if not data.get("timestamp"):
            data["timestamp"] = time.time()
        
        return json.dumps(data, indent=2)
    
    @staticmethod
    def deserialize(json_str: str) -> ExoMessage:
        """Deserialize a JSON string to message object."""
        
        data = json.loads(json_str)
        
        msg_type = data.get("type", "request")
        
        # Map to appropriate message class
        if msg_type == "inference_request":
            return InferenceRequest(**data)
        elif msg_type == "inference_response":
            return InferenceResponse(**data)
        elif msg_type == "node_info":
            return NodeInfo(**data)
        
        return ExoMessage(**data)


class ExoAPIServer:
    """Exo-compatible API server."""
    
    def __init__(self, backend_factory):
        self.backend_factory = backend_factory
        self.protocol_handler = ExoProtocolHandler()
        
        # Request tracking
        self.pending_requests: Dict[str, InferenceRequest] = {}
        self.completed_responses: List[InferenceResponse] = []
    
    async def handle_inference_request(self, request: InferenceRequest) -> InferenceResponse:
        """Handle an incoming inference request."""
        
        # Validate request
        if not request.prompt:
            return InferenceResponse(
                original_message_id=request.message_id,
                error_code=400,
                error_message="Empty prompt provided"
            )
        
        # Create response object
        response = InferenceResponse(
            original_message_id=request.message_id,
            from_node=request.to_node,
            to_node=request.from_node
        )
        
        try:
            # Get or create backend instance
            if request.model_path:
                backend = self.backend_factory.create_backend(request.model_path)
            else:
                raise ValueError("No model path provided")
            
            # Execute inference
            start_time = time.time()
            result = await backend.generate(
                prompt=request.prompt,
                max_tokens=request.max_tokens
            )
            elapsed_ms = (time.time() - start_time) * 1000
            
            # Calculate throughput
            tokens_generated = len(result.split())
            throughput = tokens_generated / (elapsed_ms / 1000) if elapsed_ms > 0 else 0
            
            response.text = result
            response.tokens_generated = tokens_generated
            response.time_ms = elapsed_ms
            response.throughput_tok_s = round(throughput, 2)
            
        except Exception as e:
            response.error_code = 500
            response.error_message = str(e)
        
        return response
    
    def create_request(self, prompt: str, model_path: str, **kwargs) -> InferenceRequest:
        """Create a new inference request."""
        
        import uuid
        
        return InferenceRequest(
            message_id=str(uuid.uuid4()),
            from_node=kwargs.get("from_node"),
            to_node=kwargs.get("to_node"),
            model_path=model_path,
            prompt=prompt,
            max_tokens=kwargs.get("max_tokens", 512),
            temperature=kwargs.get("temperature", 0.7),
            gpu_required=kwargs.get("gpu_required", False)
        )


# Quick API functions for simple use cases
def create_exo_server(backend_factory) -> ExoAPIServer:
    """Create an Exo-compatible API server."""
    
    return ExoAPIServer(backend_factory)


async def run_inference(prompt: str, model_path: str, backend_factory) -> Dict[str, Any]:
    """Quick inference execution with Exo protocol wrapper."""
    
    from exo_windows_porting.backend.factory import get_backend_factory
    
    factory = backend_factory or get_backend_factory()
    server = create_exo_server(factory)
    
    request = server.create_request(
        prompt=prompt,
        model_path=model_path
    )
    
    response = await server.handle_inference_request(request)
    
    return {
        "success": response.error_code is None,
        "text": response.text,
        "tokens_generated": response.tokens_generated,
        "time_ms": response.time_ms,
        "throughput_tok_s": response.throughput_tok_s,
        "error": response.error_message if response.error_code else None
    }
