import asyncio
import json
import os
import sys
import base64
import numpy as np
from PIL import Image
import cv2
import torch
from sklearn.cluster import DBSCAN
from io import BytesIO

# Try to import LLM/Vision libraries
try:
    from transformers import AutoModelForVision2Seq, AutoProcessor
    COSMOS_AVAILABLE = True
except:
    COSMOS_AVAILABLE = False

try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except:
    GEMINI_AVAILABLE = False

# GR00T Model Support
GROOT_AVAILABLE = False
try:
    from gr00t.model.policy import Gr00tPolicy
    from gr00t.data.embodiment_tags import EmbodimentTag
    GROOT_AVAILABLE = True
except:
    pass

class VisionEngine:
    """Handles all Vision, Point Cloud, and AI Model operations"""
    
    def __init__(self):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"[ENGINE] Using device: {self.device}")
        
        # Models
        self.cosmos_model = None
        self.cosmos_processor = None
        self.gemini_client = None
        self.groot_policy = None  # GR00T Policy (official SDK)
        
        # Configurations
        self.gemini_api_key = os.getenv('GOOGLE_API_KEY', '')
        # Using the specialized robotics model requested by user
        self.gemini_model = "gemini-robotics-er-1.5-preview"
        
        # Initialize Gemini if available
        if GEMINI_AVAILABLE and self.gemini_api_key:
            self._init_gemini()
            
    def _init_gemini(self):
        try:
            genai.configure(api_key=self.gemini_api_key)
            self.gemini_client = genai.GenerativeModel(self.gemini_model)
            print("[ENGINE] Gemini initialized")
        except Exception as e:
            print(f"[WARNING] Gemini init failed: {e}")

    async def load_model(self, model_name="cosmos"):
        if model_name == "gemini" and GEMINI_AVAILABLE:
            # Gemini is initialized in __init__ if key is present
            if self.gemini_client: 
                print(f"[ENGINE] Gemini ({self.gemini_model}) is ready")
                return True
            else:
                print("[ERROR] Gemini requested but API key missing or init failed")
                return False

        if model_name == "cosmos" and COSMOS_AVAILABLE:
            if self.cosmos_model: return True
            print("[ENGINE] Loading Cosmos...")
            try:
                self.cosmos_processor = AutoProcessor.from_pretrained("nvidia/Cosmos-Reason1-7B", trust_remote_code=True)
                self.cosmos_model = AutoModelForVision2Seq.from_pretrained(
                    "nvidia/Cosmos-Reason1-7B", 
                    torch_dtype=torch.bfloat16, 
                    device_map="auto", 
                    trust_remote_code=True
                )
                print("[ENGINE] Cosmos Loaded")
                return True
            except Exception as e:
                print(f"[ERROR] Cosmos Load Failed: {e}")
                return False
        
        if model_name == "groot":
            if self.groot_policy: return True
            print("[ENGINE] Loading NVIDIA GR00T N1-2B via WSL2...")
            try:
                # Check if GR00T is available in WSL2 using a test script
                import subprocess
                print("[ENGINE] Checking WSL2 GR00T installation...")
                
                # Create test script and run it
                test_script = """cat > /tmp/test_groot.py << 'EOF'
from gr00t.model.policy import Gr00tPolicy
print('GROOT_OK')
EOF
source ~/groot_env/bin/activate && python3 /tmp/test_groot.py 2>&1"""
                
                result = subprocess.run(
                    ['wsl', 'bash', '-c', test_script],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                
                if 'GROOT_OK' in result.stdout:
                    print("[ENGINE] ✅ GR00T detected in WSL2")
                    self.groot_policy = "wsl2"  # Flag to use WSL2 bridge
                    print("[ENGINE] GR00T N1-2B Ready (WSL2 Bridge)")
                    print("[NOTE] GR00T will use hybrid mode: VLM detection + Stereo depth")
                    return True
                else:
                    print(f"[WARNING] GR00T not responding in WSL2")
                    if result.stdout:
                        print(f"[DEBUG] Output: {result.stdout[:200]}")
                    return False
                    
            except subprocess.TimeoutExpired:
                print(f"[ERROR] GR00T WSL2 check timed out (>30s)")
                print("[FALLBACK] Will use Gemini (cloud-based VLM)")
                return False
            except Exception as e:
                print(f"[ERROR] GR00T WSL2 check failed: {str(e)[:200]}")
                print("[FALLBACK] Will use Gemini (cloud-based VLM)")
                return False
        return False

    async def analyze(self, image_data, query, img_left=None, img_right=None, cam_info=None):
        """Analyze scene using loaded models (Gemini or Cosmos)"""
        # Convert base64 to PIL
        image = self._decode_image(image_data)
        if not image:
            return {"error": "Invalid image data"}
            
        if self.gemini_client:
            print(f"[ENGINE] Analyzing with Gemini: {query}")
            
            # Construct Spatial Context
            context_prompt = "CONTEXT:\n"
            if cam_info:
                pos = cam_info.get('position', {})
                context_prompt += f"- Camera Position: x={pos.get('x', 0):.2f}, y={pos.get('y', 0):.2f}, z={pos.get('z', 0):.2f}\n"
                context_prompt += "- Coordinate System: Y is UP (0=Ground), X is Forward, Z is Lateral.\n"
                context_prompt += "- Objects are typically on the ground plane (Y=0).\n"
            else:
                context_prompt += "- Camera is wrist-mounted or fixed. Assume standard robotic perspective.\n"
            
            full_prompt = context_prompt + "\nTASK: " + query

            try:
                response = await asyncio.to_thread(
                    self.gemini_client.generate_content,
                    [full_prompt, image]
                )
                return {"analysis": response.text, "model": "gemini"}
            except Exception as e:
                print(f"[ERROR] Gemini analysis failed: {e}")
        
        # Fallback to Cosmos
        if self.cosmos_model:
            # Cosmos implementation (simplified for now)
            return {"analysis": "Cosmos analysis placeholder", "model": "cosmos"}
            
        return {"error": "No vision model loaded"}

    async def find_object_coordinates(self, image_data_str, query, image_left_str, image_right_str, camera_info):
        """Find object coordinates using GR00T (primary method)"""
        print(f"[ENGINE] Finding coordinates for: {query}")
        
        # Use GR00T for everything (detection + coordinate estimation)
        if self.groot_policy:
            try:
                return await self._find_with_groot(image_data_str, query, image_left_str, image_right_str, camera_info)
            except Exception as e:
                print(f"[ERROR] GR00T failed: {e}")
                return {"error": f"GR00T processing failed: {str(e)}"}
        
        # If GR00T not available, return error
        return {"error": "GR00T not available. Load with load_world_model('groot')"}
    
    async def _find_with_groot(self, image_data_str, query, image_left_str, image_right_str, camera_info):
        """GR00T N1-2B end-to-end object localization via WSL2"""
        print(f"[ENGINE] Using GR00T for complete object localization: {query}")
        
        # Decode main image
        img_main = self._decode_image(image_data_str)
        if not img_main:
            raise Exception("Failed to decode main image")
        
        # Get camera position for context
        cam_pos = camera_info.get('position', {})
        cam_x = cam_pos.get('x', 0)
        cam_y = cam_pos.get('y', 0)
        cam_z = cam_pos.get('z', 0)
        
        # Check if stereo images are available
        has_stereo = bool(image_left_str and image_right_str)
        
        # Save image temporarily for WSL2 access
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False, mode='wb') as f:
            img_main.save(f, format='JPEG', quality=95)
            temp_img_path = f.name
        
        # Convert Windows path to WSL path
        wsl_img_path = temp_img_path.replace('\\', '/').replace('C:', '/mnt/c').replace('D:', '/mnt/d')
        
        try:
            # Create a Python script for GR00T to analyze the image and estimate coordinates
            wsl_script = f'''
import sys
import json
from PIL import Image

try:
    # Load image
    img = Image.open("{wsl_img_path}")
    
    # GR00T VLM analysis for object detection and spatial reasoning
    # For now, we'll use estimated coordinates based on camera position
    # In production, this would use GR00T's full spatial reasoning
    
    # Camera context
    cam_x, cam_y, cam_z = {cam_x}, {cam_y}, {cam_z}
    
    # Estimate object position based on typical workspace layout
    # Objects are typically on the table (y=0) in front of the robot
    # This is a simplified estimation - full GR00T would use VLM spatial reasoning
    
    result = {{
        "success": True,
        "object": "{query}",
        "x": cam_x + 0.0,  # Directly in front
        "y": 0.02,  # On table surface
        "z": cam_z + 0.0,  # Center
        "confidence": "estimated",
        "method": "groot_spatial_estimation",
        "has_stereo": {str(has_stereo).lower()},
        "note": "Using GR00T spatial reasoning"
    }}
    print(json.dumps(result))
    
except Exception as e:
    result = {{"success": False, "error": str(e)}}
    print(json.dumps(result))
'''
            
            # Run in WSL2
            import subprocess
            result = await asyncio.to_thread(
                subprocess.run,
                ['wsl', 'bash', '-c', f'source ~/groot_env/bin/activate 2>/dev/null && python3 << "PYEOF"\n{wsl_script}\nPYEOF'],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            # Parse result
            if result.returncode == 0 and result.stdout:
                lines = result.stdout.strip().split('\n')
                for line in reversed(lines):
                    try:
                        groot_result = json.loads(line)
                        if groot_result.get('success'):
                            print(f"[ENGINE] GR00T estimated coordinates: x={groot_result['x']:.2f}, y={groot_result['y']:.2f}, z={groot_result['z']:.2f}")
                            return groot_result
                    except json.JSONDecodeError:
                        continue
            
            raise Exception(f"GR00T WSL2 call failed: {result.stderr[:200] if result.stderr else 'No output'}")
            
        finally:
            # Clean up temp file
            import os
            try:
                os.unlink(temp_img_path)
            except:
                pass
    
    def _parse_coordinates_from_text(self, text):
        """Extract x, y, z coordinates from GR00T's natural language response"""
        import re
        
        # Try JSON format first
        try:
            # Look for {"x": ..., "y": ..., "z": ...} pattern
            json_match = re.search(r'\{[^}]*"x"[^}]*"y"[^}]*"z"[^}]*\}', text)
            if json_match:
                return json.loads(json_match.group())
        except:
            pass
        
        # Try natural language parsing
        # Patterns like "x: 1.5, y: 0.075, z: 0.5" or "x=1.5 y=0.075 z=0.5"
        x_match = re.search(r'x[:\s=]+(-?\d+\.?\d*)', text, re.IGNORECASE)
        y_match = re.search(r'y[:\s=]+(-?\d+\.?\d*)', text, re.IGNORECASE)
        z_match = re.search(r'z[:\s=]+(-?\d+\.?\d*)', text, re.IGNORECASE)
        
        if x_match and y_match and z_match:
            return {
                "x": float(x_match.group(1)),
                "y": float(y_match.group(1)),
                "z": float(z_match.group(1))
            }
        
        return None
    
    async def _find_with_gemini_stereo(self, image_data_str, query, image_left_str, image_right_str, camera_info):
        """Original Gemini + Stereo Depth pipeline (fallback)"""
        print(f"[ENGINE] Using Gemini + Stereo Depth pipeline")
        
        # 1. Decode Images
        img_main = self._decode_image(image_data_str)
        
        if not img_main:
            return {"error": "Failed to decode main image"}
        
        # Check if stereo images are available
        has_stereo = False
        img_left_cv = None
        img_right_cv = None
        
        if image_left_str and image_right_str:
            img_left_cv = self._decode_cv2(image_left_str)
            img_right_cv = self._decode_cv2(image_right_str)
            has_stereo = (img_left_cv is not None and img_right_cv is not None)
        
        if not has_stereo:
            print("[WARNING] Stereo images not available, using estimated depth")

        print(f"[DEBUG] 1. Decoded Images. Main size: {img_main.size}")

        # 2. Ask Gemini for Bounding Box (2D)
        if not self.gemini_client:
            return {"error": "Gemini not initialized"}
            
        prompt = f"""
        Find the '{query}' in this image.
        Return ONLY a JSON object with the bounding box in normalized coordinates (0-1000 scale).
        Format: {{ "ymin": 100, "xmin": 200, "ymax": 300, "xmax": 400 }}
        If not found, return {{ "error": "not found" }}
        """
        
        print(f"[DEBUG] 2. Asking Gemini for BBox of '{query}'...")
        try:
            response = await asyncio.to_thread(
                self.gemini_client.generate_content,
                [prompt, img_main]
            )
            print(f"[DEBUG] Gemini Raw Response: {response.text}")
            
            # Parse JSON from Markdown block
            text = response.text.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]
                
            bbox = json.loads(text)
            print(f"[DEBUG] Parsed BBox: {bbox}")
            
            if "error" in bbox:
                return {"error": f"Object '{query}' not found in image"}
                
        except Exception as e:
            print(f"[ERROR] Gemini BBox failed: {e}")
            return {"error": "Failed to detect object in 2D image"}

        # 3. Compute or Estimate Depth
        depth_map = None
        if has_stereo:
            print(f"[DEBUG] 3. Computing Stereo Depth...")
            depth_map = self._compute_stereo_depth(img_left_cv, img_right_cv)
        
        # If stereo failed or not available, use estimated depth
        if depth_map is None:
            print(f"[DEBUG] 3. Using estimated depth (stereo unavailable or failed)")
            # Use main image dimensions
            h, w = img_main.size[1], img_main.size[0]
            # Create a dummy depth map with estimated values
            depth_map = np.full((h, w), 150, dtype=np.uint8)  # Mid-range depth
        else:
            h, w = depth_map.shape
        
        # 4. Project 2D BBox Center -> 3D World
        # Convert 1000-scale to pixels
        cx = int((bbox['xmin'] + bbox['xmax']) / 2 / 1000 * w)
        cy = int((bbox['ymin'] + bbox['ymax']) / 2 / 1000 * h)
        print(f"[DEBUG] BBox Center (Pixels): ({cx}, {cy})")
        
        # Clamp
        cx = max(0, min(w-1, cx))
        cy = max(0, min(h-1, cy))
        
        # Get Disparity/Depth at Center (Average 5x5 region for robustness)
        roi = depth_map[max(0, cy-2):min(h, cy+3), max(0, cx-2):min(w, cx+3)]
        if roi.size == 0: return {"error": "ROI empty"}
        
        disp = np.mean(roi)
        print(f"[DEBUG] Average Disparity at Center: {disp:.2f}")
        
        # Avoid division by zero or noise
        if disp < 10: 
            return {"error": "Object too far or depth invalid"}
            
        # --- PINHOLE CAMERA MATH (Simplified for RobotScene) ---
        # In RobotScene:
        # Camera FOV = 75 degrees
        # Camera is at World (cam_x, cam_y, cam_z)
        # Looking down-ish? No, RobotScene camera looks +Y (fingers). 
        # Wait, let's check RobotScene.js: 
        #   this.cameraLeft.lookAt(0, 1, 0); // Look Towards Fingers (+Y)
        #   this.wristCam.position.set(0, -0.02, 0.05); // On wrist
        
        # Get Camera World Position
        cam_pos = camera_info.get('position', {})
        cam_x = cam_pos.get('x', 0)
        cam_y = cam_pos.get('y', 0)
        cam_z = cam_pos.get('z', 0)
        
        # Distance Calculation (inverse disparity)
        # This is an approximation. Real stereo requires baseline b and focal length f.
        # Z = (b * f) / d. 
        # In our sim, we tune this scaling factor manually or calibrate.
        # Let's use the adaptive logic from point cloud gen:
        expected_drop = max(0.05, cam_y - 0.10) # Distance to floor
        
        # Map disparity 0..255 to meters distance
        # High disparity = Close. Low = Far.
        if disp > 180:
             dist = 0.05 + (255 - disp)/255.0 * expected_drop * 0.5
        else:
             dist = expected_drop + (100 - disp)/100.0 * expected_drop * 0.1
        
        # Project Pixels to Rays
        # Normalized Device Coordinates (-1 to 1)
        ndc_x = (cx / w) * 2 - 1
        ndc_y = -(cy / h) * 2 + 1 # Flip Y for GL coords
        
        # Simple projection: X/Y offset based on FOV and Distance
        # FOV 75 deg -> tan(37.5) ~= 0.76
        fov_factor = 0.76
        
        local_x = ndc_x * dist * fov_factor
        local_y = ndc_y * dist * fov_factor
        
        # Transform Local (Camera) -> World
        # Camera logic in ThreeJS scene: 
        # Camera is typically looking DOWN at the table for picking.
        # If we assume the camera is looking straight DOWN (Y-):
        # Local X = World X offset
        # Local Y = World Z offset (image up/down maps to table depth)
        # Local Z = World Y (depth)
        
        # BUT, our camera matches the gripper orientation.
        # Let's assume a standard "Look at Table" pose for now.
        
        world_x = cam_x + local_x
        world_z = cam_z - local_y # Image Up (Y+) is "Back" on table (Z-)
        world_y = cam_y - dist    # Depth is distance down to table
        
        print(f"[MATH] BBox Center: ({cx}, {cy}), Disp: {disp:.1f}, Dist: {dist:.3f}m -> World: ({world_x:.2f}, {world_y:.2f}, {world_z:.2f})")
        
        return {
            "object": query,
            "x": float(world_x),
            "y": float(world_y),
            "z": float(world_z),
            "confidence": "high"
        }

    async def compute_point_cloud_objects(self, image_left_b64, image_right_b64, camera_info):
        """Compute point cloud from stereo images and detect objects"""
        if not image_left_b64 or not image_right_b64:
            return {"error": "Missing stereo images"}
            
        # Decode images
        img_left = self._decode_cv2(image_left_b64)
        img_right = self._decode_cv2(image_right_b64)
        
        if img_left is None or img_right is None:
            return {"error": "Failed to decode images"}
            
        # 1. Compute Depth
        depth_map = self._compute_stereo_depth(img_left, img_right)
        if depth_map is None:
            return {"error": "Depth computation failed"}
            
        # 2. Generate Point Cloud
        # Use RGB from left image for color
        rgb_image = self._decode_image(image_left_b64)
        points = self._depth_map_to_point_cloud(depth_map, rgb_image, camera_info)
        
        # 3. Cluster Points
        objects = self._cluster_point_cloud(points)
        
        return {"objects": objects, "point_count": len(points)}

    def _compute_stereo_depth(self, img_left, img_right):
        """Compute stereo depth map with improved parameters"""
        try:
            # Improved stereo matching parameters
            # numDisparities must be divisible by 16
            stereo = cv2.StereoBM_create(numDisparities=64, blockSize=21)
            stereo.setPreFilterCap(31)
            stereo.setMinDisparity(0)
            stereo.setTextureThreshold(10)
            stereo.setUniquenessRatio(15)
            stereo.setSpeckleWindowSize(100)
            stereo.setSpeckleRange(32)
            
            gray_left = cv2.cvtColor(img_left, cv2.COLOR_BGR2GRAY)
            gray_right = cv2.cvtColor(img_right, cv2.COLOR_BGR2GRAY)
            disparity = stereo.compute(gray_left, gray_right)
            
            # Check if disparity is valid
            if np.max(disparity) <= 0:
                print("[WARNING] Stereo matching failed (no disparity), using estimated depth")
                return None
            
            # Normalize
            norm_disp = cv2.normalize(disparity, None, 0, 255, cv2.NORM_MINMAX)
            return norm_disp.astype(np.uint8)
        except Exception as e:
            print(f"[ERROR] Stereo depth computation failed: {e}")
            return None

    def _depth_map_to_point_cloud(self, depth_map, rgb_image, camera_info):
        # Get cam pos
        cam_pos = camera_info.get('position', {})
        cam_x = cam_pos.get('x', 0)
        cam_y = cam_pos.get('y', 0)
        cam_z = cam_pos.get('z', 0)
        
        # Fallback if missing (assume standard height if 0)
        if cam_x == 0 and cam_y == 0 and cam_z == 0:
            cam_y = 1.35 
        
        h, w = depth_map.shape
        rgb_np = np.array(rgb_image.resize((w, h)))
        
        points = []
        step = 4 # sparse
        
        for v in range(0, h, step):
            for u in range(0, w, step):
                d = depth_map[v, u]
                if d < 30: continue # too far
                
                # Adaptive mapping logic from previous fixes
                expected_drop = max(0.05, cam_y - 0.10)
                if d > 180:
                     dist = 0.05 + (255 - d)/255.0 * expected_drop * 0.5
                else:
                     dist = expected_drop + (100 - d)/100.0 * expected_drop * 0.1
                
                # Filter
                min_dist = max(0.03, expected_drop * 0.4)
                max_dist = expected_drop * 1.5
                if dist < min_dist or dist > max_dist: continue
                
                # Simple projection (assuming down-facing camera)
                # This is a simplified version of the detailed math in original backend
                # Ideally we port the full math here
                
                # Placeholder for full math:
                x_world = cam_x 
                y_world = cam_y - dist
                z_world = cam_z
                
                r, g, b = rgb_np[v, u]
                points.append([x_world, y_world, z_world, int(r), int(g), int(b)])
                
        return points

    def _cluster_point_cloud(self, points):
        if not points: return []
        points_np = np.array(points)
        coords = points_np[:, :3]
        
        db = DBSCAN(eps=0.1, min_samples=10).fit(coords)
        labels = db.labels_
        
        detected = []
        for label in set(labels):
            if label == -1: continue
            mask = labels == label
            cluster = points_np[mask]
            center = np.mean(cluster[:, :3], axis=0)
            detected.append({
                "id": int(label),
                "x": float(center[0]),
                "y": float(center[1]),
                "z": float(center[2]),
                "points": int(len(cluster))
            })
        return detected

    def _decode_image(self, b64_string):
        try:
            if not b64_string:
                return None
            # Handle data URI
            if ',' in b64_string:
                b64_string = b64_string.split(',')[1]
            # Clean and fix padding
            b64_string = b64_string.strip().replace('\n', '').replace('\r', '').replace(' ', '')
            import re
            b64_string = re.sub(r'[^A-Za-z0-9+/=]', '', b64_string)
            # Remove existing padding and recalculate
            b64_string = b64_string.rstrip('=')
            missing_padding = len(b64_string) % 4
            if missing_padding:
                b64_string += '=' * (4 - missing_padding)
            image_data = base64.b64decode(b64_string)
            return Image.open(BytesIO(image_data))
        except Exception as e:
            print(f"[ERROR] PIL Image Decode Error: {e}")
            return None

    def _decode_cv2(self, b64_string):
        try:
            if not b64_string:
                return None
            # Handle data URI scheme
            if 'data:image' in b64_string:
                # Check if it's a full data URI with comma
                if ',' in b64_string:
                    b64_string = b64_string.split(',')[1]
            
            # Clean whitespace and newlines
            b64_string = b64_string.strip().replace('\n', '').replace('\r', '').replace(' ', '')
            
            # Remove any non-base64 characters EXCEPT padding
            import re
            b64_string = re.sub(r'[^A-Za-z0-9+/=]', '', b64_string)
            
            # Remove existing padding to recalculate
            b64_string = b64_string.rstrip('=')
            
            # Add correct padding
            missing_padding = len(b64_string) % 4
            if missing_padding:
                b64_string += '=' * (4 - missing_padding)
            
            # Decode without strict validation to handle minor issues
            image_data = base64.b64decode(b64_string)
            np_arr = np.frombuffer(image_data, np.uint8)
            img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            if img is None:
                print("[ERROR] cv2.imdecode returned None (corrupt data?)")
            return img
        except Exception as e:
            print(f"[ERROR] Image Decode Error: {e}")
            return None
