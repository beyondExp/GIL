# MCP Environment Variables - Quick Reference

## 🚀 Minimal Setup

Only need this to get started:

```bash
export MCP_EXECUTION_DEVICE="cpu"  # or "cuda" if you have GPU
```

## 🔑 Most Common Variables

```bash
# Authentication (only needed for private models)
export MCP_API_HUGGINGFACE_TOKEN="hf_xxxxxxxxxxxx"

# Hardware selection
export MCP_EXECUTION_DEVICE="cuda"  # cpu, cuda, or mps

# Task selection
export MCP_MODEL_DEFAULT_TASK="text-generation"

# Logging
export MCP_LOGGING_LEVEL="INFO"  # DEBUG, INFO, WARNING, ERROR
```

## 📋 All Variables

| Variable | Type | Default | Example |
|----------|------|---------|---------|
| **API** ||||
| `MCP_API_HUGGINGFACE_TOKEN` | string | null | `"hf_abc123..."` |
| `MCP_API_TIMEOUT` | int | 30 | `60` |
| **Model** ||||
| `MCP_MODEL_DEFAULT_TASK` | string | object-detection | `"text-generation"` |
| `MCP_MODEL_MIN_DOWNLOADS` | int | 1000 | `5000` |
| `MCP_MODEL_CACHE_DIR` | path | ~/.cache/hf | `"/data/cache"` |
| **Execution** ||||
| `MCP_EXECUTION_DEVICE` | string | cpu | `"cuda"`, `"mps"` |
| `MCP_EXECUTION_BATCH_SIZE` | int | 1 | `4` |
| `MCP_EXECUTION_ENABLE_FP16` | bool | false | `true`, `1`, `yes` |
| **Logging** ||||
| `MCP_LOGGING_LEVEL` | string | INFO | `"DEBUG"`, `"ERROR"` |
| `MCP_LOGGING_DIR` | path | logs | `"/var/log/mcp"` |
| **Metrics** ||||
| `MCP_METRICS_ENABLE` | bool | true | `false`, `0`, `no` |

## 🎯 Common Scenarios

### Local Development
```bash
export MCP_EXECUTION_DEVICE="cpu"
export MCP_LOGGING_LEVEL="DEBUG"
```

### GPU Production
```bash
export MCP_API_HUGGINGFACE_TOKEN="your_token"
export MCP_EXECUTION_DEVICE="cuda"
export MCP_EXECUTION_ENABLE_FP16=true
export MCP_LOGGING_LEVEL="WARNING"
export MCP_MODEL_CACHE_DIR="/data/models"
```

### Docker
```bash
docker run \
  -e MCP_EXECUTION_DEVICE=cuda \
  -e MCP_LOGGING_LEVEL=INFO \
  --gpus all \
  your-image
```

### Using .env file
```bash
# Copy template
cp env.example .env

# Edit .env with your values
nano .env

# Load in Python
pip install python-dotenv

# In your code:
from dotenv import load_dotenv
load_dotenv()
```

## 🔍 Check Configuration

```python
from src.mcp import Config
import os

# Check env vars
print(f"Device: {os.getenv('MCP_EXECUTION_DEVICE', 'not set')}")
print(f"Task: {os.getenv('MCP_MODEL_DEFAULT_TASK', 'not set')}")

# Check loaded config
config = Config()
print(f"Using device: {config.execution.device}")
print(f"Using task: {config.model.default_task}")
```

## 📚 Full Documentation

See [ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md) for complete details.

