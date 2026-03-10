#!/usr/bin/env python3
"""
GR00T WSL2 Client for GIL Models
This script connects the Windows GIL system to the GR00T model running in WSL2.
"""

import os
import subprocess
import json
import socket
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class GR00TWSLClient:
    """Client to interact with GR00T model running in WSL2"""
    
    def __init__(self, wsl_host='localhost', wsl_port=6771):
        self.wsl_host = wsl_host
        self.wsl_port = wsl_port
        self.wsl_available = self.check_wsl_availability()
        
    def check_wsl_availability(self):
        """Check if WSL2 is available and GR00T is accessible"""
        try:
            result = subprocess.run(
                ['wsl', 'bash', '-c', 'which python3'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                logger.info("✓ WSL2 is available")
                return True
            else:
                logger.warning("✗ WSL2 not available")
                return False
        except Exception as e:
            logger.error(f"Error checking WSL2: {e}")
            return False
    
    def verify_groot_installation(self):
        """Verify GR00T is installed in WSL2"""
        if not self.wsl_available:
            return {"success": False, "error": "WSL2 not available"}
        
        try:
            # Create a test script
            test_script = """
source ~/groot_env/bin/activate 2>/dev/null || true
python3 << 'PYEOF'
import json
result = {"success": False, "error": "Unknown"}
try:
    import torch
    result["torch_version"] = torch.__version__
    result["cuda_available"] = torch.cuda.is_available()
    
    try:
        import pytorch3d
        result["pytorch3d_available"] = True
    except ImportError:
        result["pytorch3d_available"] = False
    
    try:
        from gr00t.model.policy import Gr00tPolicy
        from gr00t.data.embodiment_tags import EmbodimentTag
        result["groot_available"] = True
        result["embodiments_count"] = len([tag for tag in EmbodimentTag])
        result["success"] = True
        result["error"] = None
    except ImportError as e:
        result["groot_available"] = False
        result["error"] = str(e)
        result["success"] = False
        
except Exception as e:
    result["error"] = str(e)
    
print(json.dumps(result))
PYEOF
"""
            
            # Run the test script in WSL2
            result = subprocess.run(
                ['wsl', 'bash', '-c', test_script],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.stdout:
                # Try to parse JSON from output
                lines = result.stdout.strip().split('\n')
                for line in reversed(lines):  # Start from the end to find JSON
                    try:
                        status = json.loads(line)
                        return status
                    except json.JSONDecodeError:
                        continue
            
            return {
                "success": False,
                "error": f"Could not parse output: {result.stdout[:200]}"
            }
            
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Verification timeout"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def get_wsl_ip(self):
        """Get the IP address of the WSL2 instance"""
        try:
            result = subprocess.run(
                ['wsl', 'bash', '-c', 'ip addr show eth0 | grep "inet " | awk \'{print $2}\' | cut -d/ -f1'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                wsl_ip = result.stdout.strip()
                logger.info(f"WSL2 IP: {wsl_ip}")
                return wsl_ip
            return '172.17.0.1'  # Default fallback
        except Exception as e:
            logger.error(f"Error getting WSL IP: {e}")
            return '172.17.0.1'
    
    def install_groot_in_wsl(self):
        """Run the GR00T installation script in WSL2"""
        if not self.wsl_available:
            return {"success": False, "error": "WSL2 not available"}
        
        logger.info("Starting GR00T installation in WSL2...")
        logger.info("This may take 15-30 minutes...")
        
        try:
            # Run the verification script which also installs if needed
            result = subprocess.run(
                ['wsl', 'bash', '~/verify_groot_wsl2.sh'],
                capture_output=True,
                text=True,
                timeout=1800  # 30 minutes
            )
            
            if result.returncode == 0:
                return {"success": True, "output": result.stdout}
            else:
                return {
                    "success": False,
                    "error": f"Installation failed: {result.stderr[:500]}"
                }
                
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Installation timeout (>30min)"}
        except Exception as e:
            return {"success": False, "error": str(e)}


def main():
    """Main function to test GR00T WSL2 setup"""
    print("=" * 60)
    print("GR00T WSL2 Client - Installation Verification")
    print("=" * 60)
    print()
    
    client = GR00TWSLClient()
    
    if not client.wsl_available:
        print("❌ WSL2 is not available on this system")
        print("\nPlease ensure WSL2 is installed and configured.")
        return
    
    print("✅ WSL2 is available")
    print("\nChecking GR00T installation...")
    
    status = client.verify_groot_installation()
    
    print("\n" + "=" * 60)
    print("Installation Status:")
    print("=" * 60)
    print(json.dumps(status, indent=2))
    print()
    
    if status.get("success"):
        print("✅ GR00T is installed and working!")
        print(f"   - PyTorch: {status.get('torch_version')}")
        print(f"   - CUDA: {status.get('cuda_available')}")
        print(f"   - PyTorch3D: {status.get('pytorch3d_available')}")
        print(f"   - GR00T: {status.get('groot_available')}")
        print(f"   - Embodiments: {status.get('embodiments_count')}")
        print()
        print("🎉 GIL can now use GR00T for object detection!")
        print()
        wsl_ip = client.get_wsl_ip()
        print(f"WSL2 IP: {wsl_ip}")
        print(f"You can now run GR00T inference services in WSL2")
    else:
        print(f"❌ GR00T is not fully installed: {status.get('error')}")
        print()
        print("To complete installation, run in a WSL2 terminal:")
        print("  bash ~/verify_groot_wsl2.sh")
        print()
        print("Or run this script with --install flag")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--install":
        client = GR00TWSLClient()
        result = client.install_groot_in_wsl()
        print(json.dumps(result, indent=2))
    else:
        main()


