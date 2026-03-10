#!/bin/bash
# GR00T Installation Script for WSL2 Ubuntu
# This script installs NVIDIA GR00T N1-2B with all dependencies

set -e  # Exit on error

echo "========================================="
echo "GR00T Installation for WSL2"
echo "========================================="

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check if running in WSL
if ! grep -qi microsoft /proc/version; then
    echo -e "${RED}Error: This script must be run in WSL2${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Running in WSL2${NC}"

# Update system
echo -e "${YELLOW}Updating system packages...${NC}"
sudo apt update
sudo apt install -y build-essential git wget curl ffmpeg libsm6 libxext6

# Check for CUDA
if ! command -v nvcc &> /dev/null; then
    echo -e "${YELLOW}CUDA not found. Installing CUDA 12.8 for WSL2...${NC}"
    
    # Install CUDA using WSL-specific repository
    wget https://developer.download.nvidia.com/compute/cuda/repos/wsl-ubuntu/x86_64/cuda-keyring_1.1-1_all.deb
    sudo dpkg -i cuda-keyring_1.1-1_all.deb
    rm cuda-keyring_1.1-1_all.deb
    
    sudo apt-get update
    sudo apt-get -y install cuda-toolkit-12-8
    
    # Add CUDA to PATH
    echo 'export PATH=/usr/local/cuda/bin${PATH:+:${PATH}}' >> ~/.bashrc
    echo 'export LD_LIBRARY_PATH=/usr/local/cuda/lib64${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}' >> ~/.bashrc
    export PATH=/usr/local/cuda/bin:$PATH
    export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH
    
    echo -e "${GREEN}✓ CUDA 12.8 installed${NC}"
else
    echo -e "${GREEN}✓ CUDA already installed: $(nvcc --version | grep release)${NC}"
fi

# Install Python 3.11 (Ubuntu 24.04 default, compatible with GR00T)
echo -e "${YELLOW}Setting up Python 3.11...${NC}"
sudo apt install -y python3.11 python3.11-venv python3.11-dev python3-pip

# Create virtual environment
VENV_DIR="$HOME/groot_env"
if [ ! -d "$VENV_DIR" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    python3.11 -m venv $VENV_DIR
    echo -e "${GREEN}✓ Virtual environment created at $VENV_DIR${NC}"
fi

# Activate virtual environment
source $VENV_DIR/bin/activate
echo -e "${GREEN}✓ Virtual environment activated${NC}"

# Upgrade pip
pip install --upgrade pip setuptools wheel

# Install PyTorch with CUDA 12.8 support
echo -e "${YELLOW}Installing PyTorch nightly with CUDA 12.8...${NC}"
pip3 install --pre torch torchvision torchaudio --index-url https://download.pytorch.org/whl/nightly/cu128

# Clone and install PyTorch3D
echo -e "${YELLOW}Installing PyTorch3D from source...${NC}"
PYTORCH3D_DIR="$HOME/pytorch3d"
if [ ! -d "$PYTORCH3D_DIR" ]; then
    git clone https://github.com/facebookresearch/pytorch3d.git $PYTORCH3D_DIR
fi
cd $PYTORCH3D_DIR
pip install -e .
echo -e "${GREEN}✓ PyTorch3D installed${NC}"

# Clone and install Isaac GR00T
echo -e "${YELLOW}Installing NVIDIA Isaac GR00T...${NC}"
GROOT_DIR="$HOME/Isaac-GR00T"
if [ ! -d "$GROOT_DIR" ]; then
    git clone https://github.com/NVIDIA/Isaac-GR00T $GROOT_DIR
fi
cd $GROOT_DIR
pip install -e .
echo -e "${GREEN}✓ GR00T installed${NC}"

# Test installation
echo -e "${YELLOW}Testing GR00T installation...${NC}"
python3 -c "
from gr00t.model.policy import Gr00tPolicy
from gr00t.data.embodiment_tags import EmbodimentTag
print('✓ GR00T imports successful!')
print(f'Available embodiments: {[tag.name for tag in EmbodimentTag][:5]}...')
"

echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}Installation Complete!${NC}"
echo -e "${GREEN}=========================================${NC}"
echo ""
echo "To use GR00T, activate the environment:"
echo "  source $VENV_DIR/bin/activate"
echo ""
echo "To test GR00T:"
echo "  cd $GROOT_DIR"
echo "  python scripts/inference_service.py --help"
echo ""

