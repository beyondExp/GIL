# Quick Start Guide - RTX 3090Ti Setup

## 🎯 Your Configuration

- **GPU**: RTX 3090Ti with CUDA support ✓
- **Token**: Configured ✓
- **Ready**: Let's go! 🚀

## 🚀 3-Step Quick Start

### Step 1: Set Environment Variables

**Windows (PowerShell):**
```powershell
# Run the setup script
.\setup_environment.bat

# Or set manually:
$env:MCP_API_HUGGINGFACE_TOKEN="hf_REPLACE_ME"
$env:MCP_EXECUTION_DEVICE="cuda"
$env:MCP_EXECUTION_ENABLE_FP16="true"
```

**Linux/macOS:**
```bash
# Run the setup script
source setup_environment.sh

# Or set manually:
export MCP_API_HUGGINGFACE_TOKEN="hf_REPLACE_ME"
export MCP_EXECUTION_DEVICE="cuda"
export MCP_EXECUTION_ENABLE_FP16="true"
```

### Step 2: Verify CUDA

```bash
# Check PyTorch CUDA support
python -c "import torch; print(f'CUDA: {torch.cuda.is_available()}')"
```

**If CUDA not available, install PyTorch with CUDA:**
```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
```

### Step 3: Run Tests

```bash
# Quick test (fastest - 1 minute)
python quick_test.py

# Full GPU test (comprehensive - 5 minutes)
python test_gpu_setup.py

# Example showcase
python examples/all_tasks_showcase.py
```

## ⚡ Quick Test Code

Create a file `my_test.py`:

```python
import os
os.environ['MCP_API_HUGGINGFACE_TOKEN'] = 'hf_REPLACE_ME'
os.environ['MCP_EXECUTION_DEVICE'] = 'cuda'
os.environ['MCP_EXECUTION_ENABLE_FP16'] = 'true'

from src.mcp import ModelManager

# Test 1: Text Generation
with ModelManager() as manager:
    result = manager.predict(
        "Artificial intelligence will",
        task="text-generation",
        auto_load=True,
        max_length=50
    )
    print(f"Generated: {result.generated_text}")

# Test 2: Sentiment Analysis
with ModelManager() as manager:
    result = manager.predict(
        "I love my RTX 3090Ti!",
        task="sentiment-analysis",
        auto_load=True
    )
    print(f"Sentiment: {result.label} ({result.score:.2%})")

print("✅ GPU working perfectly!")
```

Run it:
```bash
python my_test.py
```

## 🎮 What You Can Do Now

### 1. Text Tasks
```python
from src.mcp import ModelManager

with ModelManager() as manager:
    # Text generation
    result = manager.predict("Hello", task="text-generation", auto_load=True)
    
    # Sentiment
    result = manager.predict("Great!", task="sentiment-analysis", auto_load=True)
    
    # Translation
    result = manager.predict("Hello", task="translation", auto_load=True)
    
    # Question answering
    result = manager.predict(
        {"question": "What is AI?", "context": "AI is..."},
        task="question-answering",
        auto_load=True
    )
```

### 2. Vision Tasks (if you have images)
```python
with ModelManager() as manager:
    result = manager.predict(
        "path/to/image.jpg",
        task="object-detection",
        auto_load=True
    )
    
    for box in result.boxes:
        print(f"{box.label}: {box.score:.2%}")
```

### 3. Benchmark Your GPU
```python
with ModelManager() as manager:
    manager.auto_load_best_model(task="text-generation")
    
    stats = manager.benchmark_model(
        input_data="Test",
        iterations=20
    )
    
    print(f"FPS: {1/stats['mean_time']:.2f}")
```

## 📊 Expected Performance

With RTX 3090Ti + FP16:
- **Text Generation**: ~20-50 tokens/sec
- **Sentiment Analysis**: ~100-200 samples/sec
- **Object Detection**: ~30-60 FPS
- **Translation**: ~50-100 samples/sec

## 🐛 Troubleshooting

### CUDA not found?
```bash
# Reinstall PyTorch with CUDA
pip uninstall torch torchvision
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
```

### Out of memory?
```python
# Disable FP16 or use smaller models
os.environ['MCP_EXECUTION_ENABLE_FP16'] = 'false'
```

### Model download slow?
- Normal on first run (models cached after)
- Check internet connection
- Models stored in `~/.cache/huggingface/`

## 📚 Next Steps

1. **Run comprehensive test**: `python test_gpu_setup.py`
2. **Try all tasks**: `python examples/all_tasks_showcase.py`
3. **Read docs**: `docs/getting_started.md`
4. **Check API**: `docs/api_reference.md`

## 💡 Pro Tips

1. **FP16 gives ~2x speedup** on RTX 3090Ti
2. **First run downloads models** (be patient)
3. **Models are cached** (subsequent runs faster)
4. **Unload models** to free VRAM: `manager.unload_model()`
5. **Monitor GPU**: Use `nvidia-smi` in another terminal

## 🎉 Ready to Go!

Your RTX 3090Ti is configured and ready for AI inference!

```bash
python quick_test.py  # Start here!
```

---

**Need help?** Check:
- ENVIRONMENT_VARIABLES.md
- docs/getting_started.md
- examples/ folder

