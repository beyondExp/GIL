# How to Pass Multimodal Data to GIL MCP Server

## 📌 Summary

GIL's MCP server accepts multiple data formats for different task types. Here's how to pass each type:

## 🎯 Quick Answer

### Via MCP Server

```json
{
  "name": "execute_model",
  "arguments": {
    "task": "task-type",
    "input_data": "your-data-here"
  }
}
```

### Via Python API

```python
from src.mcp import ModelManager

with ModelManager() as manager:
    result = manager.predict(
        input_data,  # Can be text, path, PIL image, dict, etc.
        task="task-type",
        auto_load=True
    )
```

## 📝 Input Formats by Data Type

### 1. Text Data

**Simple:**
```python
input_data = "Your text here"
```

**Complex (QA, etc.):**
```python
import json
input_data = json.dumps({
    "question": "What is AI?",
    "context": "AI is artificial intelligence..."
})
```

### 2. Image Data

**Option A: File Path** (Recommended)
```python
input_data = "path/to/image.jpg"
input_data = "/absolute/path/to/image.jpg"
```

**Option B: URL**
```python
input_data = "https://example.com/image.jpg"
```

**Option C: Base64**
```python
import base64
with open('image.jpg', 'rb') as f:
    data = base64.b64encode(f.read()).decode()
    input_data = f"data:image/jpeg;base64,{data}"
```

**Option D: PIL Image** (Python API only)
```python
from PIL import Image
input_data = Image.open("image.jpg")
```

### 3. Audio Data

**Option A: File Path** (Recommended)
```python
input_data = "path/to/audio.wav"
```

**Option B: URL**
```python
input_data = "https://example.com/audio.mp3"
```

**Option C: Base64**
```python
import base64
with open('audio.wav', 'rb') as f:
    data = base64.b64encode(f.read()).decode()
    input_data = f"data:audio/wav;base64,{data}"
```

### 4. Multimodal Data (Image + Text)

**Visual Question Answering:**
```python
import json
input_data = json.dumps({
    "image": "path/to/image.jpg",
    "question": "What is in this image?"
})
```

**Document Question Answering:**
```python
import json
input_data = json.dumps({
    "image": "document.pdf",
    "question": "What is the total amount?"
})
```

## 🔧 Complete MCP Examples

### Example 1: Image Classification

```json
{
  "name": "execute_model",
  "arguments": {
    "task": "image-classification",
    "input_data": "photos/cat.jpg"
  }
}
```

### Example 2: Speech Recognition

```json
{
  "name": "execute_model",
  "arguments": {
    "task": "automatic-speech-recognition",
    "input_data": "recordings/speech.wav"
  }
}
```

### Example 3: Visual QA

```json
{
  "name": "execute_model",
  "arguments": {
    "task": "visual-question-answering",
    "input_data": "{\"image\": \"vacation.jpg\", \"question\": \"Where was this taken?\"}"
  }
}
```

### Example 4: Object Detection with URL

```json
{
  "name": "execute_model",
  "arguments": {
    "task": "object-detection",
    "input_data": "https://example.com/street-photo.jpg",
    "score_threshold": 0.7
  }
}
```

## 🐍 Python API Examples

### Text

```python
with ModelManager() as manager:
    result = manager.predict(
        "This is amazing!",
        task="sentiment-analysis",
        auto_load=True
    )
    print(result.label)  # POSITIVE
```

### Image

```python
# Method 1: File path
with ModelManager() as manager:
    result = manager.predict(
        "photo.jpg",
        task="image-classification",
        auto_load=True
    )

# Method 2: PIL Image
from PIL import Image
with ModelManager() as manager:
    img = Image.open("photo.jpg")
    result = manager.predict(
        img,
        task="object-detection",
        auto_load=True
    )
```

### Multimodal

```python
with ModelManager() as manager:
    vqa_input = {
        "image": "photo.jpg",
        "question": "What color is the sky?"
    }
    result = manager.predict(
        vqa_input,
        task="visual-question-answering",
        auto_load=True
    )
    print(result.answer)
```

## 📊 Format Recommendations

| Use Case | Recommended Format | Why |
|----------|-------------------|-----|
| Local files | File paths | Most efficient |
| Remote resources | URLs | Direct access |
| API transport | Base64 | Works over JSON |
| Python scripts | Native objects (PIL) | Most convenient |

## ✅ Best Practices

### 1. Use Absolute Paths

```python
from pathlib import Path

# ✅ Good
input_data = str(Path("image.jpg").absolute())

# ❌ Risky
input_data = "../images/photo.jpg"
```

### 2. Validate Files Exist

```python
from pathlib import Path

if Path("image.jpg").exists():
    input_data = "image.jpg"
else:
    raise FileNotFoundError("Image not found")
```

### 3. Optimize Large Images

```python
from PIL import Image

img = Image.open("large.jpg")
img.thumbnail((800, 800))  # Resize for faster processing
img.save("optimized.jpg")
input_data = "optimized.jpg"
```

### 4. Use Appropriate Audio Format

- **Best:** WAV (16kHz for ASR)
- **Good:** FLAC, MP3
- **OK:** OGG, M4A

## 🐛 Common Issues & Solutions

### Issue: "File not found"
```python
# Solution: Use absolute path
from pathlib import Path
input_data = str(Path("file.jpg").absolute())
```

### Issue: "Invalid image format"
```python
# Solution: Verify with PIL
from PIL import Image
try:
    Image.open("image.jpg")
except Exception as e:
    print(f"Invalid: {e}")
```

### Issue: "JSON parsing error"
```python
# Solution: Validate JSON
import json
data = {"image": "photo.jpg", "question": "What?"}
input_data = json.dumps(data)  # Ensure valid JSON
```

## 📚 Full Documentation

For complete details:
- **[Multimodal Data Guide](docs/guides/MULTIMODAL_DATA_GUIDE.md)** - Complete guide with all details
- **[Quick Reference](docs/MULTIMODAL_QUICK_REFERENCE.md)** - Quick lookup table
- **[Example Code](examples/multimodal_mcp_example.py)** - Working examples
- **[API Reference](docs/api_reference.md)** - Full API documentation

## 🚀 Try It Now

```bash
# Run the examples
python examples/multimodal_mcp_example.py

# Start MCP server
python start_mcp_server.py
```

## 💡 Key Takeaways

1. **Text:** Just pass as string
2. **Images:** Use file paths (easiest) or PIL images
3. **Audio:** Use file paths
4. **Multimodal:** Use JSON with multiple fields
5. **MCP Server:** All data goes through `input_data` parameter
6. **Python API:** Supports native Python objects (PIL Image, dict, etc.)

---

**Need help?** See [docs/guides/MULTIMODAL_DATA_GUIDE.md](docs/guides/MULTIMODAL_DATA_GUIDE.md) for complete documentation.


