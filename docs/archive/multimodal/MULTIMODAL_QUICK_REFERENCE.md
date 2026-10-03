# Multimodal Data - Quick Reference

## 🎯 Quick Input Format Guide

### Text Tasks

```python
# Simple text
input_data = "Your text here"

# Complex text (QA)
input_data = json.dumps({
    "question": "What is AI?",
    "context": "AI is artificial intelligence..."
})
```

### Image Tasks

```python
# File path (recommended)
input_data = "path/to/image.jpg"

# URL
input_data = "https://example.com/image.jpg"

# Base64
input_data = "data:image/jpeg;base64,/9j/4AAQ..."

# PIL Image (Python API only)
from PIL import Image
input_data = Image.open("image.jpg")
```

### Audio Tasks

```python
# File path (recommended)
input_data = "path/to/audio.wav"

# URL
input_data = "https://example.com/audio.mp3"

# Base64
input_data = "data:audio/wav;base64,UklGRiQAAABXQVZF..."
```

### Multimodal Tasks

```python
# Visual Question Answering
input_data = json.dumps({
    "image": "photo.jpg",
    "question": "What is in this image?"
})

# Document QA
input_data = json.dumps({
    "image": "document.pdf",
    "question": "What is the total?"
})
```

## 📋 Task-Specific Input Formats

| Task | Input Format | Example |
|------|--------------|---------|
| **text-generation** | String | `"The future of AI is"` |
| **sentiment-analysis** | String | `"This is great!"` |
| **translation** | String | `"Hello world"` |
| **summarization** | String | `"Long text to summarize..."` |
| **question-answering** | JSON | `{"question": "...", "context": "..."}` |
| **image-classification** | Path/URL/Base64 | `"image.jpg"` |
| **object-detection** | Path/URL/Base64 | `"photo.png"` |
| **image-segmentation** | Path/URL/Base64 | `"picture.jpg"` |
| **automatic-speech-recognition** | Path/URL/Base64 | `"audio.wav"` |
| **audio-classification** | Path/URL/Base64 | `"sound.mp3"` |
| **visual-question-answering** | JSON | `{"image": "...", "question": "..."}` |
| **document-question-answering** | JSON | `{"image": "...", "question": "..."}` |

## 🔧 MCP Server Examples

### Example 1: Text Generation

```json
{
  "name": "execute_model",
  "arguments": {
    "task": "text-generation",
    "input_data": "AI will revolutionize",
    "max_length": 50
  }
}
```

### Example 2: Image Classification

```json
{
  "name": "execute_model",
  "arguments": {
    "task": "image-classification",
    "input_data": "photos/cat.jpg"
  }
}
```

### Example 3: Speech Recognition

```json
{
  "name": "execute_model",
  "arguments": {
    "task": "automatic-speech-recognition",
    "input_data": "recordings/speech.wav"
  }
}
```

### Example 4: Visual QA

```json
{
  "name": "execute_model",
  "arguments": {
    "task": "visual-question-answering",
    "input_data": "{\"image\": \"photo.jpg\", \"question\": \"What color is the car?\"}"
  }
}
```

### Example 5: Question Answering

```json
{
  "name": "execute_model",
  "arguments": {
    "task": "question-answering",
    "input_data": "{\"question\": \"What is AI?\", \"context\": \"AI is artificial intelligence...\"}"
  }
}
```

## 🐍 Python API Examples

### Text

```python
from src.mcp import ModelManager

with ModelManager() as manager:
    result = manager.predict(
        "Text to analyze",
        task="sentiment-analysis",
        auto_load=True
    )
```

### Image

```python
from src.mcp import ModelManager
from PIL import Image

with ModelManager() as manager:
    # Method 1: File path
    result = manager.predict(
        "photo.jpg",
        task="image-classification",
        auto_load=True
    )
    
    # Method 2: PIL Image
    image = Image.open("photo.jpg")
    result = manager.predict(
        image,
        task="image-classification",
        auto_load=True
    )
```

### Audio

```python
with ModelManager() as manager:
    result = manager.predict(
        "audio.wav",
        task="automatic-speech-recognition",
        auto_load=True
    )
```

### Multimodal

```python
with ModelManager() as manager:
    vqa_input = {
        "image": "photo.jpg",
        "question": "What is this?"
    }
    result = manager.predict(
        vqa_input,
        task="visual-question-answering",
        auto_load=True
    )
```

## ⚡ Tips & Best Practices

### 1. Choose the Right Format

- **Local files**: Use file paths (most efficient)
- **Remote resources**: Use URLs
- **API transport**: Use Base64 (less efficient, larger)

### 2. File Paths

```python
# ✅ Good
input_data = "/absolute/path/to/file.jpg"
input_data = "relative/path/to/file.jpg"

# ❌ Avoid
input_data = "../../../somewhere/file.jpg"
```

### 3. Image Optimization

```python
from PIL import Image

# Resize large images for faster processing
img = Image.open("large.jpg")
img.thumbnail((800, 800))
img.save("optimized.jpg")
```

### 4. Audio Format

- **Best**: WAV (16kHz for ASR)
- **Good**: FLAC, MP3
- **OK**: OGG, M4A

### 5. Error Handling

```python
from pathlib import Path

# Validate file exists
if not Path("file.jpg").exists():
    raise FileNotFoundError("Image not found")

# Or use absolute path
input_data = str(Path("file.jpg").absolute())
```

## 🔍 Input Validation

### Check File Type

```python
from pathlib import Path

def validate_image(path: str) -> bool:
    """Validate image file."""
    valid_ext = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
    return Path(path).suffix.lower() in valid_ext

def validate_audio(path: str) -> bool:
    """Validate audio file."""
    valid_ext = {'.wav', '.mp3', '.flac', '.ogg', '.m4a'}
    return Path(path).suffix.lower() in valid_ext
```

### Prepare Input

```python
import json
from pathlib import Path

def prepare_input(data, task: str):
    """Prepare input based on task type."""
    # Text tasks
    if task in ['text-generation', 'sentiment-analysis']:
        return str(data)
    
    # Image tasks
    elif task in ['image-classification', 'object-detection']:
        if isinstance(data, str):
            # File path or URL
            return data
        else:
            # Assume PIL Image
            return data
    
    # Multimodal tasks
    elif task in ['visual-question-answering']:
        if isinstance(data, dict):
            return json.dumps(data)
        return data
    
    return data
```

## 📊 Data Size Limits

| Data Type | Recommended Max | Notes |
|-----------|----------------|-------|
| Text | 512 tokens | Model-dependent |
| Image | 4096x4096 px | Resize larger images |
| Audio | 30 seconds | For ASR |
| Base64 | 1 MB | Use paths for larger |

## 🎓 Common Patterns

### Pattern 1: Image from URL

```python
import requests
from PIL import Image
from io import BytesIO

# Download image
response = requests.get(url)
image = Image.open(BytesIO(response.content))

# Use with GIL
result = manager.predict(image, task="image-classification")
```

### Pattern 2: Multiple Images

```python
images = ["photo1.jpg", "photo2.jpg", "photo3.jpg"]

with ModelManager() as manager:
    for img_path in images:
        result = manager.predict(
            img_path,
            task="image-classification",
            auto_load=True
        )
        print(f"{img_path}: {result.label}")
```

### Pattern 3: Streaming Audio (Not Yet Supported)

```python
# Future feature
# For now, save audio chunks to file first
```

## 🐛 Troubleshooting

| Issue | Solution |
|-------|----------|
| File not found | Use absolute path: `str(Path("file").absolute())` |
| Invalid format | Check file extension and content |
| URL timeout | Download file first, then use local path |
| Large file | Optimize/resize before processing |
| JSON error | Validate JSON: `json.loads(json.dumps(data))` |

## 📚 Full Documentation

For complete details, see:
- [Multimodal Data Guide](guides/MULTIMODAL_DATA_GUIDE.md)
- [API Reference](api_reference.md)
- [Examples](../examples/multimodal_mcp_example.py)

---

**Quick Start:** `python examples/multimodal_mcp_example.py`




