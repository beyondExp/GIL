# GIL Testing Quick Start

## 🚀 Run Tests in 30 Seconds

### Option 1: Run Everything (Recommended)

```bash
# Install dependencies
pip install -r requirements.txt

# Set your HuggingFace token
export MCP_API_HUGGINGFACE_TOKEN="your_token_here"

# Run ALL 96+ multimodal tests
python tests/run_all_multimodal_tests.py
```

### Option 2: Run Specific Suite

```bash
# Text models (30 tests) - LLaMA, GPT, etc.
python tests/run_all_multimodal_tests.py --suite text

# Vision models (22 tests) - Image processing
python tests/run_all_multimodal_tests.py --suite vision

# Audio models (22 tests) - Speech, TTS
python tests/run_all_multimodal_tests.py --suite audio

# Multimodal (22 tests) - VQA, Document QA
python tests/run_all_multimodal_tests.py --suite multimodal
```

### Option 3: Individual Test Files

```bash
# Vision tests
python tests/test_vision_models.py

# Audio tests
python tests/test_audio_models.py

# Multimodal tests
python tests/test_multimodal_models.py

# LLaMA tests
python tests/test_llama_mcp.py
```

## 📋 What Gets Tested

### ✅ Text Models (30 tests)
- Text generation
- Question answering
- Code generation
- Translation
- Summarization
- Conversation
- Edge cases
- Performance

### ✅ Vision Models (22 tests)
- Image classification (PIL, NumPy, Base64)
- Object detection (confidence, large images)
- Image segmentation (semantic, masks)
- Image captioning
- Depth estimation
- Zero-shot classification

### ✅ Audio Models (22 tests)
- Speech recognition (different sample rates)
- Audio classification
- Text-to-speech
- Audio enhancement
- Voice activity detection
- Noise handling

### ✅ Multimodal (22 tests)
- Visual question answering (VQA)
- Document question answering
- Image captioning
- Feature extraction
- Batch processing
- Edge cases

## 🔧 Configuration

### Minimum (Unit Tests Only)

```bash
pip install torch transformers pillow numpy
```

### Full Setup (Live Model Tests)

```bash
# Install all dependencies
pip install -r requirements.txt

# Windows
setup_environment.bat

# Linux/Mac
source setup_environment.sh

# Manual setup
export MCP_API_HUGGINGFACE_TOKEN="hf_xxxxx"
export MCP_EXECUTION_DEVICE="cuda"  # or "cpu"
export MCP_EXECUTION_ENABLE_FP16="true"
```

## 📊 Expected Output

### Success
```
========================================================================
GIL COMPREHENSIVE MULTIMODAL TEST SUITE
========================================================================

SUITE 1/4: TEXT MODELS (LLaMA 3.1)
Ran 30 tests in 45.2s
OK (skipped=0)

SUITE 2/4: VISION MODELS
Ran 22 tests in 32.1s
OK (skipped=0)

SUITE 3/4: AUDIO MODELS
Ran 22 tests in 28.5s
OK (skipped=0)

SUITE 4/4: MULTIMODAL MODELS
Ran 22 tests in 38.7s
OK (skipped=0)

========================================================================
FINAL TEST SUMMARY
========================================================================
  [✓] PASS   - Text Models (LLaMA)
  [✓] PASS   - Vision Models
  [✓] PASS   - Audio Models
  [✓] PASS   - Multimodal Models

Suites Passed: 4/4

========================================================================
ALL TESTS PASSED!
========================================================================
```

### With Skips (Expected if models unavailable)
```
Ran 96 tests in 120.5s
OK (skipped=12)

Tests run: 96
Successes: 84
Skipped: 12
```

## 🐛 Troubleshooting

### "Model not found"

Tests will skip automatically. This is normal if you don't have the models downloaded yet.

### "CUDA out of memory"

```bash
# Use CPU mode
export MCP_EXECUTION_DEVICE="cpu"
```

### "Import error"

```bash
# Install missing packages
pip install torch transformers pillow numpy scipy
```

### Tests are slow

- **Expected:** First run downloads models
- **CPU mode:** Slower than GPU
- **Solution:** Run specific suite instead of all

## 📚 Learn More

- **[Multimodal Testing Guide](docs/guides/MULTIMODAL_TESTING_GUIDE.md)** - Complete guide
- **[Multimodal Data Guide](docs/guides/MULTIMODAL_DATA_GUIDE.md)** - Input formats
- **[LLaMA Testing Guide](docs/guides/LLAMA_TESTING_GUIDE.md)** - Text models
- **[Test Summary](MULTIMODAL_TEST_SUMMARY.md)** - Full details

## 🎯 Next Steps

1. ✅ Run tests: `python tests/run_all_multimodal_tests.py`
2. 📖 Read guides: `docs/guides/MULTIMODAL_TESTING_GUIDE.md`
3. 🔧 Try examples: `python examples/multimodal_mcp_example.py`
4. 🚀 Start MCP server: `python start_mcp_server.py`

---

**Quick command:** `python tests/run_all_multimodal_tests.py`


