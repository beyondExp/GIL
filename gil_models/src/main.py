import asyncio
import sys
import os
import json
import base64
import numpy as np
from mcp.server.fastmcp import FastMCP
from io import BytesIO
from PIL import Image
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load environment variables
load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), '../../.env'))

# Add src to path to allow imports if needed
sys.path.insert(0, os.path.dirname(__file__))

# Import the Vision Engine
from vision_engine import VisionEngine

# Initialize MCP
mcp = FastMCP("GIL Models (World & Dynamics)")

# Global engine
engine = VisionEngine()

# Pydantic Models for Tool Inputs
class CameraPosition(BaseModel):
    x: float = Field(description="X coordinate in meters")
    y: float = Field(description="Y coordinate in meters")
    z: float = Field(description="Z coordinate in meters")

class CameraInfo(BaseModel):
    position: CameraPosition = Field(description="Camera world position")

class VisionInput(BaseModel):
    """Input structure matching get_latest_image() output"""
    image: str = Field(description="Main camera view (base64)")
    image_left: str = Field(default="", description="Left stereo camera (base64, optional)")
    image_right: str = Field(default="", description="Right stereo camera (base64, optional)")
    camera_info: CameraInfo = Field(description="Camera position data")

@mcp.tool()
async def get_health() -> str:
    """Perception-service health. Vision output is never motion-authoritative."""
    loaded = bool(engine.groot_policy or engine.gemini_client or engine.cosmos_model)
    model = "groot" if engine.groot_policy else ("gemini" if engine.gemini_client else ("cosmos" if engine.cosmos_model else "none"))
    try:
        from gil.core.health import models_health

        return models_health(model=model, loaded=loaded).to_json()
    except Exception:
        return json.dumps(
            {
                "ok": loaded,
                "service": "gil_models",
                "role": "perception",
                "backend": model,
                "ready_for_motion": False,
                "safe_for_motion_authority": False,
            }
        )


@mcp.tool()
async def load_world_model(model_name: str = "groot") -> str:
    """Load the world model for vision and reasoning.
    
    Args:
        model_name: Model to load. Options:
            - "groot": NVIDIA GR00T N1-2B (best for robotics, requires GPU)
            - "gemini": Google Gemini (cloud-based, always available)
            - "cosmos": NVIDIA Cosmos (experimental)
    """
    success = await engine.load_model(model_name)
    return json.dumps({"success": success, "model": model_name})

@mcp.tool()
async def analyze_scene(image_data: str, query: str) -> str:
    """Analyze a scene image using the loaded World Model.
    Args:
        image_data: Base64 encoded image or JSON string with multiple images
        query: What to look for
    """
    # Determine if it's simple base64 or complex object
    try:
        data = json.loads(image_data)
        # If it's the output from get_latest_image() tool of Controls MCP
        img_main = data.get('image')
        img_left = data.get('image_left')
        img_right = data.get('image_right')
        cam_info = data.get('camera_info')
    except:
        # Assume raw base64 string
        img_main = image_data
        img_left = None
        img_right = None
        cam_info = None

    result = await engine.analyze(img_main, query, img_left, img_right, cam_info)
    return json.dumps(result)

@mcp.tool()
async def generate_point_cloud(vision_data: VisionInput) -> str:
    """Generate a 3D point cloud from stereo images.
    Returns a list of detected objects with coordinates.
    
    Args:
        vision_data: Complete vision data from get_latest_image()
    """
    # Convert Pydantic model to dict
    cam_info_dict = vision_data.camera_info.model_dump()
    objects = await engine.compute_point_cloud_objects(
        vision_data.image_left, 
        vision_data.image_right, 
        cam_info_dict
    )
    return json.dumps(objects)

@mcp.tool()
async def find_object_coordinates(vision_data: dict, query: str) -> str:
    """Find 3D coordinates of an object using GR00T vision model.
    
    IMPORTANT: Pass the COMPLETE output from get_latest_image() as vision_data.
    
    Args:
        vision_data: The FULL dict returned by get_latest_image(). 
                    Do NOT extract individual fields - pass the entire dict.
        query: Description of object to find (e.g. "blue cube", "red block")
    
    Returns:
        JSON with x, y, z coordinates or error message
    
    Example usage:
        # Step 1: Get vision data
        vision = get_latest_image()
        
        # Step 2: Find object (pass ENTIRE vision dict)
        result = find_object_coordinates(vision_data=vision, query="blue cube")
    """
    try:
        # Extract data from vision_data dict
        image = vision_data.get('image')
        camera_info = vision_data.get('camera_info')
        image_left = vision_data.get('image_left')
        image_right = vision_data.get('image_right')
        
        if not image or not camera_info:
            return json.dumps({"error": "vision_data must contain 'image' and 'camera_info'"})
        
        result = await engine.find_object_coordinates(
            image, 
            query, 
            image_left, 
            image_right, 
            camera_info
        )
        return json.dumps(result)
        
    except Exception as e:
        return json.dumps({"error": f"Failed to process request: {str(e)}"})

async def main():
    import uvicorn
    
    print("[MODELS] Initializing Vision Engine...")
    # Try to load GR00T first (best for robotics), fallback to Gemini
    groot_loaded = await engine.load_model("groot")
    if not groot_loaded:
        print("[MODELS] GR00T not available, loading Gemini...")
        await engine.load_model("gemini")
    
    print("[MODELS] Starting MCP Server (Streamable HTTP) on Port 6770...")
    config = uvicorn.Config(mcp.streamable_http_app(), host="0.0.0.0", port=6770)
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())

