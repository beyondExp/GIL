#!/bin/bash
# GR00T Installation Verification and Completion Script for WSL2

set -e  # Exit on error

echo "========================================="
echo "GR00T Installation Verification"
echo "========================================="
echo ""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

VENV_DIR="$HOME/groot_env"

# Check if virtual environment exists
if [ ! -d "$VENV_DIR" ]; then
    echo -e "${RED}✗ Virtual environment not found at $VENV_DIR${NC}"
    echo "Creating virtual environment..."
    python3.12 -m venv $VENV_DIR
    echo -e "${GREEN}✓ Virtual environment created${NC}"
fi

# Activate virtual environment
echo "Activating virtual environment..."
source $VENV_DIR/bin/activate
echo -e "${GREEN}✓ Virtual environment activated${NC}"
echo ""

# Check PyTorch installation
echo "Checking PyTorch..."
if python3 -c "import torch" 2>/dev/null; then
    TORCH_VERSION=$(python3 -c "import torch; print(torch.__version__)")
    CUDA_AVAILABLE=$(python3 -c "import torch; print(torch.cuda.is_available())")
    echo -e "${GREEN}✓ PyTorch installed: $TORCH_VERSION${NC}"
    echo -e "${GREEN}✓ CUDA available: $CUDA_AVAILABLE${NC}"
else
    echo -e "${RED}✗ PyTorch not found. Installing...${NC}"
    pip install --upgrade pip setuptools wheel
    pip3 install --pre torch torchvision torchaudio --index-url https://download.pytorch.org/whl/nightly/cu128
    echo -e "${GREEN}✓ PyTorch installed${NC}"
fi
echo ""

# Check/Install PyTorch3D
echo "Checking PyTorch3D..."
if python3 -c "import pytorch3d" 2>/dev/null; then
    echo -e "${GREEN}✓ PyTorch3D already installed${NC}"
else
    echo -e "${YELLOW}Installing PyTorch3D from source...${NC}"
    PYTORCH3D_DIR="$HOME/pytorch3d"
    if [ ! -d "$PYTORCH3D_DIR" ]; then
        git clone https://github.com/facebookresearch/pytorch3d.git $PYTORCH3D_DIR
    fi
    cd $PYTORCH3D_DIR
    pip install -e .
    echo -e "${GREEN}✓ PyTorch3D installed${NC}"
fi
echo ""

# Check/Install Isaac GR00T
echo "Checking Isaac GR00T..."
GROOT_DIR="$HOME/Isaac-GR00T"
if [ ! -d "$GROOT_DIR" ]; then
    echo -e "${YELLOW}Cloning Isaac GR00T repository...${NC}"
    git clone https://github.com/NVIDIA/Isaac-GR00T $GROOT_DIR
fi

cd $GROOT_DIR

# Install GR00T
echo "Installing GR00T..."
pip install -e .
echo -e "${GREEN}✓ GR00T installed${NC}"
echo ""

# Test GR00T imports
echo "Testing GR00T imports..."
python3 << 'PYEOF'
import sys
try:
    from gr00t.model.policy import Gr00tPolicy
    from gr00t.data.embodiment_tags import EmbodimentTag
    print('\033[0;32m✓ GR00T imports successful!\033[0m')
    embodiments = [tag.name for tag in EmbodimentTag]
    print(f'\033[0;32m✓ Available embodiments ({len(embodiments)}): {embodiments[:5]}...\033[0m')
except Exception as e:
    print(f'\033[0;31m✗ GR00T import failed: {e}\033[0m')
    import traceback
    traceback.print_exc()
    sys.exit(1)
PYEOF

echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}Installation Complete!${NC}"
echo -e "${GREEN}=========================================${NC}"
echo ""
echo "Virtual environment: $VENV_DIR"
echo "To activate: source $VENV_DIR/bin/activate"
echo ""
echo "Installed packages:"
pip list | grep -E "(torch|pytorch3d|gr00t)" | head -10
echo ""


