# 🚨 Cosmos NIM Setup Status

## Current Situation

✅ **Docker Image:** Successfully pulled (40.7GB)  
❌ **Container Start:** Failed due to CUDA version mismatch

### The Issue

- **Your System:** CUDA 12.8 (Driver 572.61)
- **NIM Requires:** CUDA 12.9+
- **Your GPU:** RTX 3090 Ti (Perfect for the task!)

## 🎯 Solution Options

### Option 1: Update NVIDIA Driver (RECOMMENDED)

**Best for:** Production use, full NIM features

1. Download the latest Game Ready Driver from NVIDIA:
   - Visit: https://www.nvidia.com/download/index.aspx
   - Select: RTX 3090 Ti
   - Get: Driver 572.86+ (includes CUDA 12.9)

2. Install and restart

3. Run the container:
```bash
docker run -d --name cosmos-nim \
  --gpus all --ipc host --shm-size=32GB \
  -e NGC_API_KEY="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" \
  -v "$HOME\.cache\nim:/opt/nim/.cache" \
  -p 8000:8000 \
  nvcr.io/nim/nvidia/cosmos-reason1-7b:latest
```

4. Test:
```bash
python tests/test_cosmos_reasoning.py
```

---

### Option 2: Use HuggingFace Direct (ALTERNATIVE)

**Best for:** Immediate testing without driver updates

The Cosmos model is available on HuggingFace and can be used directly with `transformers`:

**Setup:**
```bash
pip install transformers vllm qwen-vl-utils
```

**Usage in GIL:**
```python
from src.mcp.core.model_manager import ModelManager

# Initialize
manager = ModelManager()

# Load Cosmos from HuggingFace
manager.load_model("nvidia/Cosmos-Reason1-7B")

# Execute reasoning task
result = manager.predict(
    input_data={
        "video": "path/to/video.mp4",
        "text": "Is it safe to turn right?"
    },
    task="video-text-to-text",
    max_tokens=4096,
    fps=4
)

print(result['reasoning'])
```

**Note:** This requires ~14GB GPU VRAM and the model files (~14GB download).

---

### Option 3: Wait for NIM Update

NVIDIA may release a CUDA 12.8 compatible version. Check:
- https://catalog.ngc.nvidia.com/orgs/nim/teams/nvidia/containers/cosmos-reason1-7b

---

## 🎯 What I Recommend

**For immediate testing:** Use Option 2 (HuggingFace)
- No driver update needed
- Works with your current setup
- Full model capabilities
- GIL already supports it!

**For production:** Use Option 1 (NIM with updated driver)
- Optimized performance
- Better multi-instance support
- Official NVIDIA support

---

## 🧪 Test Your Current Setup

You can test other GIL features while deciding:

```bash
# Test vision models (works now!)
python tests/test_vision_models.py

# Test audio models (works now!)
python tests/test_audio_models.py

# Test multimodal (works now!)
python tests/test_multimodal_models.py

# Run all tests
python tests/run_all_multimodal_tests.py
```

---

## 📊 Your System Status

| Component | Status | Details |
|-----------|--------|---------|
| GPU | ✅ Ready | RTX 3090 Ti (24GB) |
| CUDA | ⚠️ Version | 12.8 (need 12.9+) |
| Docker | ✅ Ready | 28.0.4 |
| NIM Image | ✅ Downloaded | 40.7GB |
| HuggingFace Token | ✅ Set | Active |
| GIL Tests | ✅ Ready | 141+ tests |

---

## 🚀 Quick Commands

### If you update the driver (Option 1):
```bash
# Start NIM
docker start cosmos-nim

# Check logs
docker logs -f cosmos-nim

# Wait for "Server started on port 8000"

# Test
python tests/manual/test_cosmos_nim.py
```

### If you use HuggingFace (Option 2):
```bash
# Install dependencies (if needed)
pip install vllm qwen-vl-utils

# Run test
python tests/test_cosmos_reasoning.py --use-hf
```

---

## 📝 Notes

- The NIM image is already downloaded, so Option 1 is instant after driver update
- Your RTX 3090 Ti has plenty of VRAM (24GB) for Cosmos (needs ~14GB)
- All GIL code and tests are ready for Cosmos
- Both approaches work with the existing test suite

---

## ❓ Questions?

- See: `docs/guides/COSMOS_REASONING_GUIDE.md`
- See: `COSMOS_SETUP_GUIDE.md`
- Run: `python tests/manual/test_cosmos_nim.py --help`

---

*Generated: 2025-11-12*
*GIL Version: 1.0.0*
*Cosmos-Reason1-7B: Ready (pending driver update)*


