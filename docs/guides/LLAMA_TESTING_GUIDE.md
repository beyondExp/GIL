# LLaMA 3.1 Testing Guide for GIL

## Overview

This guide explains how to test the GIL MCP server with Meta's LLaMA 3.1 model, including 30 comprehensive test cases.

## 📋 Test Suite Overview

### Location
- **Unit/Integration Tests**: `tests/test_llama_mcp.py` (30 test cases)
- **Live Tests**: `tests/manual/test_llama_live.py` (7 real execution tests)

### Test Categories

1. **Basic Generation** (5 tests)
   - Simple generation
   - Short prompts
   - Long prompts
   - Question answering
   - Instruction following

2. **Parameters** (5 tests)
   - Max length variations
   - Temperature control
   - Empty prompt handling
   - Special characters
   - Unicode characters

3. **Use Cases** (5 tests)
   - Code generation
   - Creative writing
   - Summarization
   - Translation
   - Math problems

4. **Conversational AI** (3 tests)
   - Multi-turn conversation
   - Follow-up questions
   - Context understanding

5. **Edge Cases** (5 tests)
   - Very long output
   - Repeated tokens
   - Mixed languages
   - Numbers only
   - Heavy punctuation

6. **Performance** (4 tests)
   - Sequential requests
   - Model caching
   - Resource cleanup
   - Stress testing

7. **MCP Server** (3 tests)
   - MCP execute_model tool
   - Long prompt handling
   - Error handling

## 🚀 Running the Tests

### Option 1: Unit Tests (Mocked - Fast)

```bash
# Run all LLaMA tests
python tests/test_llama_mcp.py

# Run specific test class
python -m pytest tests/test_llama_mcp.py::TestLLaMA3_1_BasicGeneration -v

# Run specific test
python -m pytest tests/test_llama_mcp.py::TestLLaMA3_1_BasicGeneration::test_01_simple_generation -v
```

### Option 2: Live Tests (Real Model - Slow)

```bash
# Set environment variables
export MCP_API_HUGGINGFACE_TOKEN="your_token_here"
export MCP_EXECUTION_DEVICE="cuda"  # or "cpu"

# Run live tests
python tests/manual/test_llama_live.py
```

## 📊 Test Details

### Test 1-5: Basic Generation

```python
# Test 1: Simple generation
"Hello, how are you?" → Model generates response

# Test 2: Short prompt
"Hi" → Model handles minimal input

# Test 3: Long prompt
"Explain AI..." * 5 → Model handles extended input

# Test 4: Question answering
"What is the capital of France?" → "Paris"

# Test 5: Instruction following
"Write a haiku about AI" → Generates haiku
```

### Test 6-10: Parameters

```python
# Test 6: Max length variations
Tests with max_length: 20, 50, 100

# Test 7: Temperature (if supported)
Tests generation randomness control

# Test 8: Empty prompt
"" → Handles gracefully or rejects

# Test 9: Special characters
"What is 2+2? @#$%^&*()" → Handles special chars

# Test 10: Unicode
"Hello in Chinese: 你好" → Handles Unicode
```

### Test 11-15: Use Cases

```python
# Test 11: Code generation
"Write a Python function..." → Generates code

# Test 12: Creative writing
"Write a story about..." → Creative output

# Test 13: Summarization
"Summarize this text..." → Summary

# Test 14: Translation
"Translate to Spanish..." → Translation

# Test 15: Math
"If a train travels..." → Math solution
```

### Test 16-18: Conversational

```python
# Test 16: Multi-turn
Turn 1: "What is ML?"
Turn 2: "Give me an example?"

# Test 17: Follow-ups
"First, what is AI? Second, why important?"

# Test 18: Context
Given context, answer questions
```

### Test 19-23: Edge Cases

```python
# Test 19: Very long output
max_length=500

# Test 20: Repeated tokens
"AI AI AI AI AI"

# Test 21: Mixed languages
"Hello, 你好, Hola, Bonjour"

# Test 22: Numbers only
"123 456 789"

# Test 23: Heavy punctuation
"What??? Is!!! AI...????"
```

### Test 24-26: Performance

```python
# Test 24: Sequential requests
3 requests in sequence

# Test 25: Model caching
Tests reusing loaded model

# Test 26: Resource cleanup
Multiple load/unload cycles
```

### Test 27-30: MCP Server

```python
# Test 27: MCP execute_model
Via MCP server interface

# Test 28: MCP long prompt
Very long input via MCP

# Test 29: MCP error handling
Error cases via MCP

# Test 30: MCP tool definition
Structural verification
```

## 🔧 Requirements

### For Unit Tests (Mocked)
- Python 3.9+
- GIL dependencies
- No model download needed

### For Live Tests
- Python 3.9+
- GIL dependencies
- **HuggingFace Token** (required)
- **~15GB disk space** for model
- **16GB+ RAM** (CPU mode)
- **8GB+ VRAM** (GPU mode, recommended)
- Internet connection for first run

## 📝 Configuration

### Environment Variables

```bash
# Required
export MCP_API_HUGGINGFACE_TOKEN="hf_..."

# Optional
export MCP_EXECUTION_DEVICE="cuda"      # or "cpu"
export MCP_EXECUTION_ENABLE_FP16="true" # GPU only
export MCP_LOGGING_LEVEL="INFO"
```

### Model Selection

```python
# Default model (used in tests)
model_id = "meta-llama/Llama-3.1-8B-Instruct"

# Alternative models you can test:
# - "meta-llama/Llama-3.1-70B-Instruct" (requires more resources)
# - "meta-llama/Llama-3.2-1B-Instruct" (smaller, faster)
```

## 📈 Expected Results

### Success Criteria

```
✓ All 30 unit tests should pass (with mocking)
✓ Live tests may skip if model not available
✓ Generation should be coherent and relevant
✓ Performance should be consistent
✓ No memory leaks or resource issues
```

### Performance Benchmarks

**CPU Mode (16-core):**
- First request: ~3-5 seconds (model loading)
- Subsequent: ~1-2 seconds per generation

**GPU Mode (RTX 3090Ti):**
- First request: ~2-3 seconds (model loading)
- Subsequent: ~0.3-0.5 seconds per generation

## 🐛 Troubleshooting

### Issue: "Model not found"

**Solution:**
```python
# Check HuggingFace token
print(os.environ.get('MCP_API_HUGGINGFACE_TOKEN'))

# Verify model ID
model_id = "meta-llama/Llama-3.1-8B-Instruct"
```

### Issue: "Out of memory"

**Solution:**
```bash
# Use CPU mode
export MCP_EXECUTION_DEVICE="cpu"

# Or use smaller model
model_id = "meta-llama/Llama-3.2-1B-Instruct"
```

### Issue: "Download timeout"

**Solution:**
```python
# Increase timeout in config
# Or manually download model first
from transformers import AutoModel
AutoModel.from_pretrained("meta-llama/Llama-3.1-8B-Instruct")
```

### Issue: "Tests are slow"

**Solution:**
```bash
# Run only unit tests (mocked - fast)
python tests/test_llama_mcp.py

# Skip live tests
python -m pytest tests/test_llama_mcp.py -v
```

## 📊 Test Output Example

```
======================================================================
LLaMA 3.1 MCP Test Suite
======================================================================

Model: meta-llama/Llama-3.1-8B-Instruct
Total Tests: 30
======================================================================

[Test 1] Simple text generation
✓ Generated: Hello! I'm doing well, thanks for asking...

[Test 2] Short prompt
✓ Response generated

[Test 3] Long prompt
✓ Long prompt handled

...

======================================================================
Test Summary
======================================================================
Tests run: 30
Successes: 30
Failures: 0
Errors: 0
Skipped: 0
======================================================================
```

## 🎓 Adding Your Own Tests

### Template

```python
def test_31_your_test_name(self):
    """Test 31: Description."""
    print("\n[Test 31] Your test name")
    
    with ModelManager() as manager:
        try:
            result = manager.predict(
                "Your prompt",
                task=self.task,
                model_id=self.model_id,
                max_length=50
            )
            self.assertIsNotNone(result)
            print(f"✓ Test passed")
        except Exception as e:
            self.skipTest(f"Model not available: {e}")
```

### Best Practices

1. **Always use try/except** for live model tests
2. **Use skipTest()** when model unavailable
3. **Print progress** for visibility
4. **Test one thing** per test case
5. **Document expected behavior**

## 📚 Additional Resources

- **[API Reference](../api_reference.md)** - Complete API docs
- **[MCP Server Guide](MCP_SERVER_GUIDE.md)** - MCP setup
- **[Testing Guide](../TESTING.md)** - General testing
- **[LLaMA Documentation](https://llama.meta.com/)** - Official docs

## 🎯 Next Steps

1. **Run unit tests** to verify structure
2. **Set up HF token** for live tests
3. **Run live tests** with actual model
4. **Add custom tests** for your use case
5. **Benchmark performance** on your hardware

---

**Questions?** See [GitHub Issues](https://github.com/yourusername/GIL/issues)




