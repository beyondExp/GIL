# GR00T Installation for GIL - Complete Guide

## Current Status

We have successfully completed the following in WSL2:

1. ✅ **CUDA 12.8** - Installed in WSL2
2. ✅ **Python 3.12** - Available and configured
3. ✅ **Virtual Environment** - Created at `~/groot_env`
4. ✅ **PyTorch 2.10 with CUDA 12.8** - Installed successfully
5. ⏳ **PyTorch3D** - Installation initiated
6. ⏳ **Isaac GR00T** - Installation initiated

## Manual Verification Steps

Due to PowerShell/WSL output buffering issues, please verify the installation manually:

### Step 1: Open WSL2 Terminal

Open a native WSL2 terminal (Windows Terminal with Ubuntu profile, or run `wsl` and interact directly).

### Step 2: Run Verification Script

```bash
bash ~/verify_groot_wsl2.sh
```

This script will:
- Check and complete PyTorch installation
- Install PyTorch3D if needed
- Clone and install Isaac GR00T
- Test all imports

### Step 3: Manual Verification Commands

If you want to check manually:

```bash
# Activate the environment
source ~/groot_env/bin/activate

# Check PyTorch
python3 -c "import torch; print('PyTorch:', torch.__version__); print('CUDA:', torch.cuda.is_available())"

# Check PyTorch3D
python3 -c "import pytorch3d; print('PyTorch3D OK')"

# Check GR00T
python3 << 'EOF'
from gr00t.model.policy import Gr00tPolicy
from gr00t.data.embodiment_tags import EmbodimentTag
print('✓ GR00T imports successful!')
print(f'Embodiments: {len([tag for tag in EmbodimentTag])}')
EOF
```

## Integration with GIL

Once GR00T is installed in WSL2, you have two options:

### Option 1: Direct Integration (Recommended for Testing)

Run the GR00T model server in WSL2 and connect from Windows:

```bash
# In WSL2:
source ~/groot_env/bin/activate
cd ~/Isaac-GR00T

# Start the GR00T inference service (if available)
# python scripts/inference_service.py --port 6771
```

Then update `gil_models/src/main.py` to connect to WSL2's IP address.

### Option 2: Use the GR00T WSL Client

We've created `gil_models/src/groot_wsl_client.py` which can:
- Verify GR00T installation status
- Get WSL2 IP address
- Bridge Windows GIL with WSL2 GR00T

Run from Windows:
```powershell
cd gil_models\src
python groot_wsl_client.py
```

## Troubleshooting

### If GR00T installation fails:

1. **Check CUDA in WSL2:**
   ```bash
   nvcc --version
   ```

2. **Reinstall PyTorch with correct CUDA version:**
   ```bash
   source ~/groot_env/bin/activate
   pip3 install --pre torch torchvision torchaudio --index-url https://download.pytorch.org/whl/nightly/cu128 --force-reinstall
   ```

3. **Install PyTorch3D from source:**
   ```bash
   cd ~/pytorch3d
   pip install -e .
   ```

4. **Install GR00T:**
   ```bash
   cd ~/Isaac-GR00T
   pip install -e .
   ```

### If imports fail:

Check for missing dependencies:
```bash
source ~/groot_env/bin/activate
pip install transformers accelerate pillow opencv-python numpy
```

## Alternative: Use Gemini (Current Working Solution)

The current GIL system works with Google Gemini for vision tasks. If GR00T installation is complex, you can continue using Gemini:

- `gil_models/src/vision_engine.py` already has Gemini integration
- Works without additional GPU setup
- Proven to work with the current `find_object_coordinates` tool

## Next Steps

1. **Verify Installation**: Run `bash ~/verify_groot_wsl2.sh` in WSL2
2. **Test GR00T Client**: Run `python groot_wsl_client.py` from Windows
3. **Update vision_engine.py**: Once verified, update the model loading logic
4. **Test with GIL**: Try the robot control with GR00T for object detection

## Files Created

- `setup_groot_wsl2.sh` - Initial installation script
- `verify_groot_wsl2.sh` - Verification and completion script
- `gil_models/src/groot_wsl_client.py` - Windows-WSL2 bridge client

## Environment Details

- **WSL2**: Ubuntu 24.04
- **CUDA**: 12.8
- **Python**: 3.12.3
- **PyTorch**: 2.10.0.dev20251122+cu128 (nightly)
- **Virtual Environment**: `~/groot_env`
- **GR00T Location**: `~/Isaac-GR00T`
- **PyTorch3D Location**: `~/pytorch3d`


