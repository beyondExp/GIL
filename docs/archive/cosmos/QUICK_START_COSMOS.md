# ⚡ Quick Start: Cosmos Integration

## 🎯 Current Status

**✅ Docker Image:** Downloaded (40.7GB)  
**✅ Your GPU:** RTX 3090 Ti (25.8GB VRAM) - Perfect!  
**⚠️ Blocker:** CUDA 12.9 needed (you have 12.8)

---

## 🚀 Pick Your Path (2 Options)

### Option A: Test NOW (No Driver Update)

**Works immediately with your current setup!**

```bash
# 1. Install dependencies (2 min)
pip install vllm qwen-vl-utils accelerate

# 2. Test readiness (30 sec)
python tests/manual/test_cosmos_hf.py

# 3. Use in your code
python
>>> from src.mcp.core.model_manager import ModelManager
>>> manager = ModelManager()
>>> manager.load_model("nvidia/Cosmos-Reason1-7B")
```

---

### Option B: Use NIM (Requires Driver Update)

**Best performance, needs 15-minute setup:**

```bash
# 1. Download NVIDIA driver 573.85+
# https://www.nvidia.com/download/index.aspx

# 2. Install and restart (10 min)

# 3. Start NIM (instant - image already downloaded!)
docker run -d --name cosmos-nim \
  --gpus all --ipc host --shm-size=32GB \
  -e NGC_API_KEY="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" \
  -v "$HOME\.cache\nim:/opt/nim/.cache" \
  -p 8000:8000 \
  nvcr.io/nim/nvidia/cosmos-reason1-7b:latest

# 4. Test (30 sec)
python tests/manual/test_cosmos_nim.py
python tests/test_cosmos_reasoning.py
```

---

## 📊 Quick Comparison

| Feature | Option A (HF) | Option B (NIM) |
|---------|---------------|----------------|
| Setup Time | 2 min | 15 min* |
| Works Now? | ✅ YES | ❌ Need driver |
| Performance | Fast | Fastest |
| Production Ready | ✅ Yes | ✅ Yes |
| GPU Memory | 14GB | 14GB |

*Includes driver download + install + restart

---

## 💻 Example: Physical AI Reasoning

```python
from src.mcp.core.model_manager import ModelManager

# Initialize
manager = ModelManager()

# Load Cosmos
manager.load_model("nvidia/Cosmos-Reason1-7B")

# Robot navigation
result = manager.predict(
    input_data={
        "video": "warehouse_robot.mp4",
        "text": "Should the robot turn left or right to avoid the obstacles?"
    },
    task="video-text-to-text",
    max_tokens=4096,
    fps=4  # Important: Use 4 FPS for Cosmos
)

# Get reasoning + answer
print("Reasoning:", result['thinking'])
print("Answer:", result['answer'])
```

---

## 🎮 MCP Tools (6 Available)

Use with AI agents via MCP server:

```bash
# Start MCP server
python start_mcp_server.py

# AI agents can use:
# - cosmos_robot_navigation
# - cosmos_object_manipulation
# - cosmos_vehicle_decision
# - cosmos_video_analytics
# - cosmos_physics_reasoning
# - cosmos_spatial_temporal
```

---

## 🧪 Test Commands

```bash
# Test your current setup (works now)
python tests/manual/test_cosmos_hf.py

# Test NIM connection (after driver update)
python tests/manual/test_cosmos_nim.py

# Run all 20 Cosmos tests
python tests/test_cosmos_reasoning.py

# Run everything (120+ tests)
python tests/run_all_multimodal_tests.py
```

---

## 📁 Key Files

| File | Purpose |
|------|---------|
| `COSMOS_FINAL_STATUS.md` | Complete status report |
| `COSMOS_NIM_STATUS.md` | Detailed NIM setup |
| `docs/guides/COSMOS_REASONING_GUIDE.md` | Full integration guide |
| `tests/manual/test_cosmos_hf.py` | Test without NIM (works now!) |
| `tests/manual/test_cosmos_nim.py` | Test NIM connection |
| `tests/test_cosmos_reasoning.py` | 20 comprehensive tests |

---

## ⚡ TL;DR

1. **Want to test NOW?**  
   → `pip install vllm qwen-vl-utils accelerate`  
   → `python tests/manual/test_cosmos_hf.py`

2. **Want best performance?**  
   → Update NVIDIA driver (15 min)  
   → `docker start cosmos-nim`

3. **Either way:**  
   → Your RTX 3090 Ti is perfect ✅  
   → All code is ready ✅  
   → 20 tests waiting ✅

---

## 🎯 My Recommendation

**Try Option A right now** to see Cosmos working, then **update driver this week** for Option B performance.

You get the best of both worlds! 🚀

---

*For full details: See `COSMOS_FINAL_STATUS.md`*


