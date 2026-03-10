"""
GIL WSL2 Bridge - Vision Engine Server with GR00T Support
This runs in WSL2 and listens on all interfaces so Windows can connect
"""
import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from vision_engine import VisionEngine
from pydantic import BaseModel
from typing import Optional
from mcp.server.fastmcp import FastMCP
import asyncio
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(name)s] %(message)s'
)
logger = logging.getLogger("MODELS-WSL")

# Pydantic models for tool inputs
class CameraPosition(BaseModel):
    x: float
    y: float
    z: float

class CameraInfo(BaseModel):
    position: CameraPosition
    baseline: float
    fov: float
    width: int
    height: int

class VisionInput(BaseModel):
    image: str  # base64 encoded
    image_left: str  # base64 encoded
    image_right: str  # base64 encoded
    camera_info: CameraInfo

# Initialize FastMCP server
mcp = FastMCP("GIL-Vision-WSL")

# Global vision engine
vision_engine: Optional[VisionEngine] = None

@mcp.tool()
async def load_world_model(model_name: str = "groot") -> dict:
    """
    Load a specific world/vision model.
    
    Args:
        model_name: Either 'groot' (NVIDIA GR00T N1-2B) or 'gemini' (Google Gemini)
    
    Returns:
        Status of model loading
    """
    global vision_engine
    if vision_engine is None:
        vision_engine = VisionEngine()
    
    success = await vision_engine.load_model(model_name)
    return {
        "model": model_name,
        "loaded": success,
        "device": vision_engine.device
    }

@mcp.tool()
async def get_latest_image() -> dict:
    """
    Retrieve the latest camera vision data from the robot simulator.
    This is used by the agent to see the current state of the world.
    
    Returns:
        A bundle containing stereo images and camera calibration info
    """
    # This will be populated by WebSocket from Windows frontend
    # For now, return empty structure
    return {
        "image": "",
        "image_left": "",
        "image_right": "",
        "camera_info": {
            "position": {"x": 0, "y": 0, "z": 0},
            "baseline": 0.2,
            "fov": 60,
            "width": 320,
            "height": 240
        }
    }

@mcp.tool()
async def generate_point_cloud(
    image_left: str,
    image_right: str,
    camera_info: dict
) -> dict:
    """
    Generate a 3D point cloud from stereo camera images.
    
    Args:
        image_left: Base64-encoded left camera image
        image_right: Base64-encoded right camera image  
        camera_info: Camera calibration parameters
        
    Returns:
        3D point cloud data with coordinates
    """
    global vision_engine
    if vision_engine is None:
        vision_engine = VisionEngine()
    
    result = await vision_engine.generate_point_cloud(
        image_left, image_right, camera_info
    )
    return result

@mcp.tool()
async def find_object_coordinates(vision_data: VisionInput, query: str) -> dict:
    """
    Find 3D world coordinates of an object using vision AI (GR00T or Gemini).
    
    **IMPORTANT for Agent:** Pass the FULL output of get_latest_image() as vision_data.
    Example: find_object_coordinates(vision_data=get_latest_image(), query="red cube")
    
    Args:
        vision_data: Complete vision bundle from get_latest_image() containing all camera data
        query: Natural language description of the object to find (e.g., "red cube", "blue ball")
    
    Returns:
        Object location in 3D world coordinates: {x, y, z, confidence, method}
    """
    global vision_engine
    if vision_engine is None:
        vision_engine = VisionEngine()
        # Try to load GR00T first, fallback to Gemini
        await vision_engine.load_model("groot")
    
    logger.info(f"[DEBUG] Finding object: {query}")
    logger.info(f"[DEBUG] Vision data keys: {list(vision_data.model_dump().keys())}")
    
    # Extract data from Pydantic model
    data = vision_data.model_dump()
    
    result = await vision_engine.find_object_coordinates(
        image_data=data["image"],
        query=query,
        image_left=data["image_left"],
        image_right=data["image_right"],
        camera_info=data["camera_info"]
    )
    
    logger.info(f"[DEBUG] Result: {result}")
    return result

# Main entry point
if __name__ == "__main__":
    logger.info("Initializing Vision Engine (WSL2)...")
    vision_engine = VisionEngine()
    
    # Pre-load GR00T if available
    logger.info("Attempting to load GR00T...")
    asyncio.run(vision_engine.load_model("groot"))
    
    if not vision_engine.groot_policy:
        logger.info("GR00T not available, loading Gemini...")
        asyncio.run(vision_engine.load_model("gemini"))
    
    logger.info(f"Starting MCP Server (SSE) on Port 6770 (accessible from Windows)...")
    
    # Run server on all interfaces so Windows can connect
    mcp.run(
        transport="sse",
        sse_params={
            "host": "0.0.0.0",  # Listen on all interfaces (accessible from Windows)
            "port": 6770
        }
    )


