## GIL Testing Guide

## 📋 Test Structure

```
tests/
├── __init__.py
├── run_all_tests.py              # Run all tests
├── test_model_manager.py         # Unit tests for ModelManager
├── test_model_selector.py        # Unit tests for ModelSelector
├── test_api_client.py            # Unit tests for API client
├── test_mcp_server.py            # Unit tests for MCP server
├── test_integration.py           # Integration tests
└── manual/                       # Manual testing scripts
    ├── __init__.py
    ├── test_gpu_setup.py         # GPU verification
    └── quick_test.py             # Quick functionality test
```

## 🚀 Running Tests

### Run All Tests

```bash
# Run complete test suite
python tests/run_all_tests.py
```

### Run Specific Test Suites

```bash
# Unit tests only
python -m pytest tests/test_*.py -v

# Integration tests
python -m pytest tests/test_integration.py -v

# MCP server tests
python -m pytest tests/test_mcp_server.py -v

# Specific test class
python -m pytest tests/test_model_manager.py::TestModelManager -v

# Specific test method
python -m pytest tests/test_model_manager.py::TestModelManager::test_initialization -v
```

### Run Manual Tests

```bash
# Quick GPU test (30 seconds)
python tests/manual/quick_test.py

# Comprehensive GPU test (5 minutes)
python tests/manual/test_gpu_setup.py
```

## 📊 Test Coverage

### Unit Tests

**test_model_manager.py**
- ✅ Initialization
- ✅ Model discovery
- ✅ Model selection
- ✅ Model loading
- ✅ Prediction
- ✅ Context manager usage
- ✅ Configuration handling

**test_model_selector.py**
- ✅ Selection criteria
- ✅ Filtering by downloads
- ✅ Filtering by likes
- ✅ Filtering by library
- ✅ Scoring algorithms
- ✅ Multiple model selection
- ✅ Diverse model selection

**test_api_client.py**
- ✅ HuggingFace client initialization
- ✅ Model search
- ✅ Model info retrieval
- ✅ Error handling
- ✅ Retry logic
- ✅ Context manager

**test_mcp_server.py**
- ✅ Server initialization
- ✅ Tool registration
- ✅ discover_models tool
- ✅ execute_model tool
- ✅ get_model_info tool
- ✅ list_supported_tasks tool
- ✅ get_best_model tool
- ✅ Error handling
- ✅ JSON input parsing

### Integration Tests

**test_integration.py**
- ✅ End-to-end text generation flow
- ✅ Auto-loading feature
- ✅ Multiple tasks sequentially
- ✅ Metrics collection
- ✅ Configuration loading
- ✅ NLP tasks discovery
- ✅ Vision tasks discovery
- ✅ Error recovery

## 🔧 Test Requirements

### Minimum Requirements (Unit Tests)

```bash
pip install pytest pytest-cov
```

### Full Requirements (All Tests)

```bash
pip install -r requirements.txt
pip install mcp  # For MCP server tests
```

### For GPU Tests

- CUDA-capable GPU
- PyTorch with CUDA support
- Sufficient disk space for model downloads

## 📈 Running with Coverage

```bash
# Generate coverage report
python -m pytest --cov=src/mcp tests/ --cov-report=html

# View report
open htmlcov/index.html  # On Mac/Linux
start htmlcov/index.html # On Windows
```

## 🎯 Test Categories

### Fast Tests (< 1 second)
- Unit tests (mocked dependencies)
- Configuration tests
- Utility tests

### Medium Tests (1-30 seconds)
- API integration tests (require internet)
- Model discovery tests

### Slow Tests (30+ seconds)
- Model loading tests
- Inference tests
- GPU tests

## 🏷️ Running by Category

```bash
# Fast tests only (no model downloads)
python -m pytest tests/ -m "not slow" -v

# Skip integration tests
python -m pytest tests/ --ignore=tests/test_integration.py -v

# MCP tests only
python -m pytest tests/test_mcp_server.py -v
```

## 🐛 Debugging Failed Tests

### Enable Debug Logging

```bash
export MCP_LOGGING_LEVEL=DEBUG
python -m pytest tests/test_model_manager.py -v -s
```

### Run Single Test with Output

```bash
python -m pytest tests/test_model_manager.py::TestModelManager::test_initialization -v -s
```

### Check Test Logs

```bash
# Tests create logs in logs/
cat logs/mcp.log
```

## 📝 Writing New Tests

### Unit Test Template

```python
import unittest
from unittest.mock import Mock, patch
from src.mcp import ModelManager

class TestNewFeature(unittest.TestCase):
    def setUp(self):
        """Set up before each test."""
        self.config = Mock()
    
    def test_something(self):
        """Test description."""
        # Arrange
        manager = ModelManager(config=self.config)
        
        # Act
        result = manager.some_method()
        
        # Assert
        self.assertIsNotNone(result)
```

### Integration Test Template

```python
import unittest
from src.mcp import ModelManager

class TestNewIntegration(unittest.TestCase):
    def test_end_to_end_flow(self):
        """Test complete workflow."""
        try:
            with ModelManager() as manager:
                # Your test here
                pass
        except Exception as e:
            self.skipTest(f"Test requires network: {e}")
```

## ✅ Test Checklist

Before committing code, ensure:

- [ ] All unit tests pass
- [ ] Integration tests pass (if applicable)
- [ ] MCP tests pass (if modified MCP server)
- [ ] Code coverage > 80%
- [ ] No linting errors
- [ ] Documentation updated
- [ ] Manual tests run (for major changes)

## 🚨 Common Issues

### Issue: Tests fail with "Model not found"

**Solution:** Requires internet connection for first run

### Issue: Tests fail with "CUDA out of memory"

**Solution:** Set `MCP_EXECUTION_DEVICE=cpu` for tests

### Issue: MCP tests skipped

**Solution:** Install MCP SDK: `pip install mcp`

### Issue: Tests hang

**Solution:** Some tests download models. Wait or skip with Ctrl+C

## 📊 CI/CD Integration

### GitHub Actions Example

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
        with:
          python-version: '3.10'
      - run: pip install -r requirements.txt
      - run: python tests/run_all_tests.py
```

## 📚 Resources

- **pytest documentation**: https://docs.pytest.org/
- **unittest documentation**: https://docs.python.org/3/library/unittest.html
- **Coverage.py**: https://coverage.readthedocs.io/

## 🎯 Test Goals

- **Unit Tests**: 100% coverage of core logic
- **Integration Tests**: Cover main workflows
- **MCP Tests**: Verify all MCP tools work
- **Manual Tests**: Verify GPU/hardware features

---

**Run tests before every commit!** ✅




