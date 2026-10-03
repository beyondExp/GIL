# GIL (General Intelligence Layer) MCP Server Guide

## 🎯 What is This?

The **GIL MCP Server** exposes your AI model capabilities through the **Model Context Protocol**, allowing AI agents (like Claude) to dynamically use **ANY Hugging Face model** for 50+ tasks.

## 🏗️ Architecture

```
AI Agent (Claude, etc.)
        ↓
   MCP Protocol
        ↓
   GIL MCP Server  ← You are here
        ↓
  Model Manager
        ↓
  Hugging Face Models (on your RTX 3090Ti)
```

## 🚀 Quick Start

### Step 1: Install MCP SDK

```bash
pip install mcp
```

### Step 2: Start the Server

```bash
python start_mcp_server.py
```

The server will:
- ✅ Initialize with your RTX 3090Ti (CUDA)
- ✅ Enable FP16 for faster inference
- ✅ Use your Hugging Face token
- ✅ Expose 5 MCP tools for AI agents

### Step 3: Connect an AI Agent

#### Option A: Claude Desktop

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on Mac):

```json
{
  "mcpServers": {
    "gil-server": {
      "command": "python",
      "args": ["D:\\BBotOldANDExperiments\\Experiments\\GIL\\start_mcp_server.py"],
      "env": {
        "MCP_EXECUTION_DEVICE": "cuda",
        "MCP_API_HUGGINGFACE_TOKEN": "hf_REPLACE_ME",
        "MCP_EXECUTION_ENABLE_FP16": "true"
      }
    }
  }
}
```

#### Option B: Any MCP Client

Your server runs on stdio, so any MCP-compatible client can connect.

## 🛠️ Available Tools

The GIL server exposes these tools to AI agents:

### 1. `discover_models`
Find available models for any task.

**Parameters:**
- `task` (required): Task type (e.g., "text-generation", "object-detection")
- `limit` (optional): Max models to return (default: 10)
- `min_downloads` (optional): Minimum downloads filter (default: 1000)

**Example:**
```json
{
  "task": "text-generation",
  "limit": 5
}
```

### 2. `execute_model`
Execute any AI model on input data.

**Parameters:**
- `task` (required): Task type
- `input_data` (required): Text, image path, or JSON
- `model_id` (optional): Specific model (auto-selects if not provided)
- `max_length` (optional): For generation tasks
- `score_threshold` (optional): Confidence threshold (default: 0.5)

**Examples:**
```json
// Text generation
{
  "task": "text-generation",
  "input_data": "The future of AI is",
  "max_length": 50
}

// Sentiment analysis
{
  "task": "sentiment-analysis",
  "input_data": "I love this product!"
}

// Question answering
{
  "task": "question-answering",
  "input_data": "{\"question\": \"What is AI?\", \"context\": \"AI stands for Artificial Intelligence.\"}"
}

// Object detection (on image)
{
  "task": "object-detection",
  "input_data": "path/to/image.jpg"
}
```

### 3. `get_model_info`
Get detailed information about a specific model.

**Parameters:**
- `model_id` (required): Hugging Face model ID

**Example:**
```json
{
  "model_id": "gpt2"
}
```

### 4. `list_supported_tasks`
Get all 50+ supported tasks.

**Parameters:** None

### 5. `get_best_model`
Get the best model recommendation for a task.

**Parameters:**
- `task` (required): Task type
- `min_downloads` (optional): Minimum downloads (default: 1000)

**Example:**
```json
{
  "task": "translation",
  "min_downloads": 5000
}
```

## 📊 Supported Tasks (50+)

### Natural Language Processing
- text-generation
- text-classification  
- sentiment-analysis
- token-classification (NER)
- question-answering
- translation
- summarization
- fill-mask
- zero-shot-classification
- conversational

### Computer Vision
- object-detection
- image-segmentation
- image-classification
- image-to-text
- depth-estimation
- zero-shot-image-classification

### Audio
- automatic-speech-recognition
- audio-classification
- text-to-speech

### Multimodal
- visual-question-answering
- document-question-answering
- feature-extraction

## 🎮 Usage Examples

### Example 1: AI Agent Generates Text

**Agent asks server:**
```
Use the execute_model tool with:
{
  "task": "text-generation",
  "input_data": "Once upon a time",
  "max_length": 100
}
```

**Server responds:**
```json
{
  "generated_text": "Once upon a time in a land far away...",
  "model_id": "gpt2",
  "processing_time": 0.75
}
```

### Example 2: AI Agent Analyzes Sentiment

**Agent asks:**
```
{
  "task": "sentiment-analysis",
  "input_data": "This is amazing!"
}
```

**Server responds:**
```json
{
  "label": "POSITIVE",
  "score": 0.9998,
  "model_id": "distilbert-base-uncased",
  "processing_time": 0.02
}
```

### Example 3: AI Agent Detects Objects

**Agent asks:**
```
{
  "task": "object-detection",
  "input_data": "image.jpg",
  "score_threshold": 0.7
}
```

**Server responds:**
```json
{
  "num_detections": 3,
  "boxes": [
    {
      "label": "person",
      "score": 0.95,
      "xmin": 100,
      "ymin": 50,
      "xmax": 300,
      "ymax": 400
    }
  ]
}
```

## 🔧 Configuration

### Environment Variables

Set these before starting the server:

```bash
export MCP_API_HUGGINGFACE_TOKEN="your_token"
export MCP_EXECUTION_DEVICE="cuda"
export MCP_EXECUTION_ENABLE_FP16="true"
export MCP_LOGGING_LEVEL="INFO"
```

### Configuration File

Or use a YAML config file:

```yaml
# gil_config.yaml
api:
  huggingface_token: "hf_xxxx"
  
execution:
  device: "cuda"
  enable_fp16: true

logging:
  level: "INFO"
```

Start with config:
```bash
python start_mcp_server.py gil_config.yaml
```

## 🚨 Troubleshooting

### MCP SDK not installed
```bash
pip install mcp
```

### Server won't start
1. Check Python version: `python --version` (need 3.8+)
2. Check MCP installation: `python -c "import mcp"`
3. Check logs in `logs/mcp.log`

### CUDA not available
Set to CPU mode:
```bash
export MCP_EXECUTION_DEVICE="cpu"
```

### Models downloading slowly
- Normal on first use
- Models cached in `~/.cache/huggingface/`
- Subsequent uses are instant

## 📈 Performance

With RTX 3090Ti + FP16:
- **Text Generation**: ~20-50 tokens/sec
- **Sentiment Analysis**: ~100-200 samples/sec
- **Object Detection**: ~30-60 FPS
- **Translation**: ~50-100 samples/sec

## 🔒 Security

- ✅ Server runs locally (no external exposure)
- ✅ Token stays on your machine
- ✅ AI agents can only use defined tools
- ✅ No arbitrary code execution

## 🎯 Use Cases

1. **Multi-Model AI Agents**: Let Claude use any AI model dynamically
2. **Automated Pipelines**: Chain multiple AI tasks
3. **Research**: Experiment with different models easily
4. **Production**: Serve AI capabilities to your applications

## 📚 Resources

- **MCP Protocol**: https://modelcontextprotocol.io/
- **Hugging Face**: https://huggingface.co/
- **Configuration**: `ENVIRONMENT_VARIABLES.md`
- **Examples**: `examples/` folder

## 🎉 What This Enables

Your AI agents can now:
- ✅ Generate text with GPT models
- ✅ Translate between languages
- ✅ Analyze sentiment
- ✅ Answer questions
- ✅ Detect objects in images
- ✅ Segment images
- ✅ Transcribe audio
- ✅ And 50+ more tasks!

**All dynamically, on-demand, powered by your RTX 3090Ti!** 🚀

---

## Quick Test

After starting the server, test with Claude:

> "Use the discover_models tool to find text generation models"

> "Use execute_model to generate text: 'The future of AI is'"

> "Use list_supported_tasks to show me all AI capabilities"

---

**Your General Intelligence Layer is ready!** 🎯

