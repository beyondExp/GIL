# MCP Environment Variables Guide

## Overview

MCP can be configured using environment variables prefixed with `MCP_`. This is useful for:
- Containerized deployments (Docker, Kubernetes)
- CI/CD pipelines
- Cloud deployments
- Keeping sensitive data out of config files

## Quick Setup

### Linux/macOS
```bash
export MCP_API_HUGGINGFACE_TOKEN="your_token_here"
export MCP_EXECUTION_DEVICE="cuda"
export MCP_LOGGING_LEVEL="INFO"
```

### Windows (PowerShell)
```powershell
$env:MCP_API_HUGGINGFACE_TOKEN="your_token_here"
$env:MCP_EXECUTION_DEVICE="cuda"
$env:MCP_LOGGING_LEVEL="INFO"
```

### Windows (Command Prompt)
```cmd
set MCP_API_HUGGINGFACE_TOKEN=your_token_here
set MCP_EXECUTION_DEVICE=cuda
set MCP_LOGGING_LEVEL=INFO
```

### Docker
```dockerfile
ENV MCP_API_HUGGINGFACE_TOKEN=your_token_here
ENV MCP_EXECUTION_DEVICE=cuda
ENV MCP_LOGGING_LEVEL=INFO
```

### Docker Compose
```yaml
services:
  mcp:
    environment:
      - MCP_API_HUGGINGFACE_TOKEN=your_token_here
      - MCP_EXECUTION_DEVICE=cuda
      - MCP_LOGGING_LEVEL=INFO
```

## All Supported Environment Variables

### API Configuration

#### `MCP_API_HUGGINGFACE_TOKEN`
**Type:** String  
**Default:** `null` (not required for public models)  
**Description:** Hugging Face API authentication token for accessing private models or higher rate limits.

**Example:**
```bash
export MCP_API_HUGGINGFACE_TOKEN="hf_xxxxxxxxxxxxxxxxxxxx"
```

**How to get:**
1. Go to https://huggingface.co/settings/tokens
2. Create a new token (read access is sufficient)
3. Copy and set as environment variable

---

#### `MCP_API_TIMEOUT`
**Type:** Integer (seconds)  
**Default:** `30`  
**Description:** Timeout for API requests to Hugging Face Hub.

**Example:**
```bash
export MCP_API_TIMEOUT=60
```

---

### Model Configuration

#### `MCP_MODEL_DEFAULT_TASK`
**Type:** String  
**Default:** `"object-detection"`  
**Description:** Default task to use when not specified. Can be any Hugging Face task.

**Supported Values:**
- NLP: `text-generation`, `sentiment-analysis`, `translation`, `summarization`, `question-answering`, etc.
- Vision: `object-detection`, `image-segmentation`, `image-classification`, etc.
- Audio: `automatic-speech-recognition`, `audio-classification`, etc.

**Example:**
```bash
export MCP_MODEL_DEFAULT_TASK="text-generation"
```

---

#### `MCP_MODEL_MIN_DOWNLOADS`
**Type:** Integer  
**Default:** `1000`  
**Description:** Minimum number of downloads for model selection filtering.

**Example:**
```bash
export MCP_MODEL_MIN_DOWNLOADS=5000
```

---

#### `MCP_MODEL_CACHE_DIR`
**Type:** Path (string)  
**Default:** `null` (uses Hugging Face default: `~/.cache/huggingface`)  
**Description:** Directory to cache downloaded models.

**Example:**
```bash
export MCP_MODEL_CACHE_DIR="/data/model_cache"
```

---

### Execution Configuration

#### `MCP_EXECUTION_DEVICE`
**Type:** String  
**Default:** `"cpu"`  
**Description:** Device to run models on.

**Supported Values:**
- `cpu` - CPU execution (slowest, works everywhere)
- `cuda` - NVIDIA GPU (fastest, requires CUDA)
- `mps` - Apple Silicon GPU (macOS only)

**Example:**
```bash
# For NVIDIA GPU
export MCP_EXECUTION_DEVICE="cuda"

# For Apple Silicon (M1/M2)
export MCP_EXECUTION_DEVICE="mps"

# For CPU only
export MCP_EXECUTION_DEVICE="cpu"
```

---

#### `MCP_EXECUTION_BATCH_SIZE`
**Type:** Integer  
**Default:** `1`  
**Description:** Batch size for processing multiple inputs.

**Example:**
```bash
export MCP_EXECUTION_BATCH_SIZE=4
```

---

#### `MCP_EXECUTION_ENABLE_FP16`
**Type:** Boolean  
**Default:** `false`  
**Description:** Enable half-precision (FP16) for faster GPU inference.

**Supported Values:** `true`, `false`, `1`, `0`, `yes`, `no`

**Example:**
```bash
export MCP_EXECUTION_ENABLE_FP16=true
```

**Note:** Only works with CUDA devices. Can reduce memory usage by ~50%.

---

### Logging Configuration

#### `MCP_LOGGING_LEVEL`
**Type:** String  
**Default:** `"INFO"`  
**Description:** Logging verbosity level.

**Supported Values:**
- `DEBUG` - Very verbose, all details
- `INFO` - Standard information
- `WARNING` - Only warnings and errors
- `ERROR` - Only errors
- `CRITICAL` - Only critical errors

**Example:**
```bash
export MCP_LOGGING_LEVEL="DEBUG"
```

---

#### `MCP_LOGGING_DIR`
**Type:** Path (string)  
**Default:** `"logs"`  
**Description:** Directory to store log files.

**Example:**
```bash
export MCP_LOGGING_DIR="/var/log/mcp"
```

---

### Metrics Configuration

#### `MCP_METRICS_ENABLE`
**Type:** Boolean  
**Default:** `true`  
**Description:** Enable performance metrics tracking.

**Supported Values:** `true`, `false`, `1`, `0`, `yes`, `no`

**Example:**
```bash
export MCP_METRICS_ENABLE=false
```

---

## Common Scenarios

### Development Environment
```bash
export MCP_EXECUTION_DEVICE="cpu"
export MCP_LOGGING_LEVEL="DEBUG"
export MCP_MODEL_MIN_DOWNLOADS=1000
export MCP_METRICS_ENABLE=true
```

### Production with GPU
```bash
export MCP_API_HUGGINGFACE_TOKEN="hf_xxxxxxxxxxxx"
export MCP_EXECUTION_DEVICE="cuda"
export MCP_EXECUTION_ENABLE_FP16=true
export MCP_LOGGING_LEVEL="WARNING"
export MCP_LOGGING_DIR="/var/log/mcp"
export MCP_MODEL_CACHE_DIR="/data/models"
export MCP_METRICS_ENABLE=true
```

### Docker Container
```bash
docker run \
  -e MCP_API_HUGGINGFACE_TOKEN="hf_xxxxxxxxxxxx" \
  -e MCP_EXECUTION_DEVICE="cuda" \
  -e MCP_LOGGING_LEVEL="INFO" \
  -e MCP_MODEL_CACHE_DIR="/cache" \
  -v /host/cache:/cache \
  --gpus all \
  mcp-image
```

### CI/CD Pipeline (GitHub Actions)
```yaml
env:
  MCP_API_HUGGINGFACE_TOKEN: ${{ secrets.HF_TOKEN }}
  MCP_EXECUTION_DEVICE: cpu
  MCP_LOGGING_LEVEL: INFO
  MCP_METRICS_ENABLE: true
```

### Kubernetes ConfigMap/Secret
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: mcp-config
data:
  MCP_EXECUTION_DEVICE: "cuda"
  MCP_LOGGING_LEVEL: "INFO"
  MCP_MODEL_MIN_DOWNLOADS: "5000"
---
apiVersion: v1
kind: Secret
metadata:
  name: mcp-secrets
type: Opaque
stringData:
  MCP_API_HUGGINGFACE_TOKEN: "hf_xxxxxxxxxxxx"
```

---

## .env File Support

Create a `.env` file in your project root:

```bash
# .env file
MCP_API_HUGGINGFACE_TOKEN=hf_xxxxxxxxxxxx
MCP_EXECUTION_DEVICE=cuda
MCP_LOGGING_LEVEL=INFO
MCP_MODEL_DEFAULT_TASK=text-generation
MCP_MODEL_MIN_DOWNLOADS=5000
MCP_METRICS_ENABLE=true
```

Load it in Python:
```python
from dotenv import load_dotenv
load_dotenv()

from src.mcp import ModelManager
manager = ModelManager()  # Will use env vars
```

**Note:** Install python-dotenv: `pip install python-dotenv`

---

## Priority Order

MCP loads configuration in this order (later overrides earlier):

1. **Default values** (hardcoded in code)
2. **Config file** (YAML)
3. **Environment variables** ⬅️ Highest priority
4. **Programmatic overrides** (passed to constructor)

Example:
```python
# Environment: MCP_EXECUTION_DEVICE=cuda
# This will use CPU (programmatic override wins)
config = Config(execution_device="cpu")
```

---

## Verification

Check if environment variables are loaded:

```python
from src.mcp import Config

config = Config()

print(f"Device: {config.execution.device}")
print(f"Task: {config.model.default_task}")
print(f"Log Level: {config.logging.level}")
print(f"Has Token: {config.api.huggingface_token is not None}")
```

---

## Security Best Practices

### ✅ DO:
- Use environment variables for sensitive data (tokens, credentials)
- Use secrets management in production (AWS Secrets Manager, Azure Key Vault, etc.)
- Rotate tokens regularly
- Use read-only tokens when possible

### ❌ DON'T:
- Commit `.env` files to version control
- Hardcode tokens in code
- Share tokens publicly
- Use production tokens in development

### .gitignore Entry
```gitignore
.env
.env.local
.env.*.local
```

---

## Troubleshooting

### Environment variable not being loaded?

1. **Check spelling** - Must be exact (e.g., `MCP_EXECUTION_DEVICE`, not `MCP_EXEC_DEVICE`)
2. **Check type** - Booleans must be `true`/`false`/`1`/`0`
3. **Restart shell** - Changes to `.bashrc`/`.zshrc` need reload
4. **Check priority** - Config file or code might override

### Debug environment loading:

```python
import os
from src.mcp import Config

# Check if env var is set
print(f"Token set: {'MCP_API_HUGGINGFACE_TOKEN' in os.environ}")

# Enable debug logging
config = Config(logging_level="DEBUG")
# Will log which env vars were loaded
```

---

## Summary Table

| Variable | Type | Default | Required | Description |
|----------|------|---------|----------|-------------|
| `MCP_API_HUGGINGFACE_TOKEN` | string | null | No* | HuggingFace API token |
| `MCP_API_TIMEOUT` | int | 30 | No | API timeout (seconds) |
| `MCP_MODEL_DEFAULT_TASK` | string | object-detection | No | Default task type |
| `MCP_MODEL_MIN_DOWNLOADS` | int | 1000 | No | Min downloads filter |
| `MCP_MODEL_CACHE_DIR` | path | ~/.cache/huggingface | No | Model cache location |
| `MCP_EXECUTION_DEVICE` | string | cpu | No | Execution device |
| `MCP_EXECUTION_BATCH_SIZE` | int | 1 | No | Batch size |
| `MCP_EXECUTION_ENABLE_FP16` | bool | false | No | Enable FP16 |
| `MCP_LOGGING_LEVEL` | string | INFO | No | Log level |
| `MCP_LOGGING_DIR` | path | logs | No | Log directory |
| `MCP_METRICS_ENABLE` | bool | true | No | Enable metrics |

\* Required only for private models or rate-limit increases

---

## Quick Reference

**Minimal setup (public models only):**
```bash
export MCP_EXECUTION_DEVICE="cpu"  # or "cuda"
```

**Recommended setup:**
```bash
export MCP_API_HUGGINGFACE_TOKEN="your_token"
export MCP_EXECUTION_DEVICE="cuda"
export MCP_LOGGING_LEVEL="INFO"
```

**Full production setup:**
```bash
export MCP_API_HUGGINGFACE_TOKEN="your_token"
export MCP_EXECUTION_DEVICE="cuda"
export MCP_EXECUTION_ENABLE_FP16=true
export MCP_LOGGING_LEVEL="WARNING"
export MCP_LOGGING_DIR="/var/log/mcp"
export MCP_MODEL_CACHE_DIR="/data/models"
export MCP_MODEL_MIN_DOWNLOADS=10000
export MCP_METRICS_ENABLE=true
```

---

For more configuration options, see [Configuration Documentation](docs/getting_started.md#configuration-options).

