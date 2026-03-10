# Passing Multimodal Data to GIL MCP Server

## Overview

GIL's MCP server supports multimodal data inputs for vision, audio, and multimodal tasks. This guide explains how to pass different types of data to the MCP server.

## 📋 Table of Contents

1. [Input Data Formats](#input-data-formats)
2. [Text Data](#text-data)
3. [Image Data](#image-data)
4. [Audio Data](#audio-data)
5. [Multimodal Data](#multimodal-data)
6. [Examples](#examples)
7. [Best Practices](#best-practices)

## Input Data Formats

The MCP server's `execute_model` tool accepts data in several formats:

### Supported Input Types

1. **Plain Text** - For NLP tasks
2. **File Paths** - For local files (images, audio)
3. **URLs** - For remote resources
4. **Base64 Encoded** - For binary data over JSON
5. **JSON Objects** - For complex/multimodal inputs

## Text Data

### Simple Text (NLP Tasks)

```json
{
  "task": "text-generation",
  "input_data": "The future of AI is"
}
```

```json
{
  "task": "sentiment-analysis",
  "input_data": "This product is amazing!"
}
```

### Complex Text (Question Answering)

For tasks requiring multiple text inputs, use JSON:

```json
{
  "task": "question-answering",
  "input_data": "{\"question\": \"What is AI?\", \"context\": \"AI is artificial intelligence\"}"
}
```

Or as a JSON object (when using Python client):

```python
import json

input_dict = {
    "question": "What is AI?",
    "context": "AI stands for Artificial Intelligence. It refers to computer systems that can perform tasks requiring human intelligence."
}

# Pass as JSON string
input_data = json.dumps(input_dict)
```

## Image Data

### Method 1: File Path (Recommended for Local Files)

```json
{
  "task": "object-detection",
  "input_data": "path/to/image.jpg"
}
```

```json
{
  "task": "image-classification",
  "input_data": "/absolute/path/to/photo.png"
}
```

### Method 2: URL (For Remote Images)

```json
{
  "task": "image-segmentation",
  "input_data": "https://example.com/image.jpg"
}
```

### Method 3: Base64 Encoded (For API/JSON Transport)

```json
{
  "task": "image-classification",
  "input_data": "data:image/jpeg;base64,/9j/4AAQSkZJRg..."
}
```

**Python Example:**

```python
import base64
import json
from pathlib import Path

def image_to_base64(image_path: str) -> str:
    """Convert image to base64 string."""
    with open(image_path, 'rb') as f:
        image_data = f.read()
        base64_data = base64.b64encode(image_data).decode('utf-8')
        return f"data:image/jpeg;base64,{base64_data}"

# Use with MCP
input_data = image_to_base64("photo.jpg")
```

### Method 4: PIL Image Object (Python API Only)

```python
from PIL import Image
from src.mcp import ModelManager

# Open image
image = Image.open("photo.jpg")

# Use directly
with ModelManager() as manager:
    result = manager.predict(
        image,
        task="object-detection",
        auto_load=True
    )
```

## Audio Data

### Method 1: File Path (Recommended)

```json
{
  "task": "automatic-speech-recognition",
  "input_data": "audio/recording.wav"
}
```

```json
{
  "task": "audio-classification",
  "input_data": "/path/to/sound.mp3"
}
```

### Method 2: URL

```json
{
  "task": "automatic-speech-recognition",
  "input_data": "https://example.com/audio.wav"
}
```

### Method 3: Base64 Encoded

```python
import base64

def audio_to_base64(audio_path: str) -> str:
    """Convert audio to base64 string."""
    with open(audio_path, 'rb') as f:
        audio_data = f.read()
        base64_data = base64.b64encode(audio_data).decode('utf-8')
        return f"data:audio/wav;base64,{base64_data}"

input_data = audio_to_base64("recording.wav")
```

## Multimodal Data

For tasks requiring multiple inputs (image + text), use JSON objects.

### Visual Question Answering (VQA)

```json
{
  "task": "visual-question-answering",
  "input_data": "{\"image\": \"photo.jpg\", \"question\": \"What is in this image?\"}"
}
```

**Python Example:**

```python
import json

# Method 1: With file path
vqa_input = {
    "image": "path/to/photo.jpg",
    "question": "What color is the car?"
}

input_data = json.dumps(vqa_input)
```

```python
# Method 2: With URL
vqa_input = {
    "image": "https://example.com/photo.jpg",
    "question": "How many people are in the image?"
}

input_data = json.dumps(vqa_input)
```

```python
# Method 3: With base64
vqa_input = {
    "image": "data:image/jpeg;base64,/9j/4AAQ...",
    "question": "What is the main subject?"
}

input_data = json.dumps(vqa_input)
```

### Document Question Answering

```json
{
  "task": "document-question-answering",
  "input_data": "{\"image\": \"document.pdf\", \"question\": \"What is the total amount?\"}"
}
```

## Examples

### Example 1: Image Classification via MCP

```json
// MCP Tool Call
{
  "name": "execute_model",
  "arguments": {
    "task": "image-classification",
    "input_data": "photos/cat.jpg",
    "model_id": "google/vit-base-patch16-224"
  }
}
```

### Example 2: Object Detection with URL

```json
{
  "name": "execute_model",
  "arguments": {
    "task": "object-detection",
    "input_data": "https://images.example.com/street.jpg",
    "score_threshold": 0.7
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

### Example 4: Visual Question Answering

```python
import json

# Prepare multimodal input
vqa_input = {
    "image": "vacation_photo.jpg",
    "question": "What landmark is visible in this photo?"
}

# MCP tool call
mcp_call = {
    "name": "execute_model",
    "arguments": {
        "task": "visual-question-answering",
        "input_data": json.dumps(vqa_input)
    }
}
```

### Example 5: Python Client with Multimodal Data

```python
from src.mcp import ModelManager
from PIL import Image

# Text task
with ModelManager() as manager:
    result = manager.predict(
        "Hello world",
        task="translation",
        auto_load=True
    )

# Image task
with ModelManager() as manager:
    image = Image.open("photo.jpg")
    result = manager.predict(
        image,
        task="image-classification",
        auto_load=True
    )

# Multimodal task
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

## Best Practices

### 1. **Choose the Right Format**

| Scenario | Recommended Format |
|----------|-------------------|
| Local files | File paths |
| Remote resources | URLs |
| API/JSON transport | Base64 encoding |
| Python scripts | PIL/native objects |

### 2. **File Path Guidelines**

```python
# ✅ Good: Absolute paths
input_data = "/home/user/images/photo.jpg"

# ✅ Good: Relative to working directory
input_data = "data/images/photo.jpg"

# ❌ Bad: Relative to unknown location
input_data = "../somewhere/photo.jpg"
```

### 3. **URL Guidelines**

```python
# ✅ Good: Direct image URLs
input_data = "https://example.com/images/photo.jpg"

# ✅ Good: Publicly accessible
input_data = "https://storage.googleapis.com/bucket/image.png"

# ❌ Bad: Requires authentication (without providing credentials)
input_data = "https://private.example.com/image.jpg"
```

### 4. **Base64 Guidelines**

```python
# ✅ Good: Include mime type
input_data = "data:image/jpeg;base64,/9j/4AAQ..."

# ✅ Good: For small files (< 1MB)
# Base64 increases size by ~33%

# ❌ Bad: For large files
# Use file paths or URLs instead
```

### 5. **JSON Structure for Multimodal**

```python
# ✅ Good: Clear structure
{
    "image": "photo.jpg",
    "question": "What is this?"
}

# ✅ Good: Multiple fields
{
    "question": "What is AI?",
    "context": "AI is artificial intelligence..."
}

# ❌ Bad: Unclear structure
{
    "data": "something",
    "other": "something else"
}
```

### 6. **Error Handling**

```python
import json
from pathlib import Path

def prepare_image_input(image_source: str) -> str:
    """Prepare image input with validation."""
    # Check if it's a file path
    if Path(image_source).exists():
        return str(Path(image_source).absolute())
    
    # Check if it's a URL
    if image_source.startswith(('http://', 'https://')):
        return image_source
    
    # Check if it's base64
    if image_source.startswith('data:image'):
        return image_source
    
    raise ValueError(f"Invalid image source: {image_source}")
```

## Performance Considerations

### 1. **Image Size**

```python
from PIL import Image

def optimize_image(image_path: str, max_size: tuple = (800, 800)) -> str:
    """Resize image for faster processing."""
    img = Image.open(image_path)
    img.thumbnail(max_size, Image.Resampling.LANCZOS)
    
    output_path = "optimized_" + Path(image_path).name
    img.save(output_path)
    return output_path

# Use optimized image
input_data = optimize_image("large_photo.jpg")
```

### 2. **Audio Format**

- Prefer **WAV** or **FLAC** for speech recognition
- **MP3** or **OGG** are acceptable but may need conversion
- Sample rate: **16kHz** is standard for ASR

### 3. **Batch Processing**

For multiple files, process sequentially:

```python
images = ["photo1.jpg", "photo2.jpg", "photo3.jpg"]

with ModelManager() as manager:
    for image_path in images:
        result = manager.predict(
            image_path,
            task="image-classification",
            auto_load=True
        )
        print(f"{image_path}: {result.label}")
```

## Troubleshooting

### Issue: "File not found"

**Solution:** Use absolute paths or verify working directory

```python
from pathlib import Path

# Convert to absolute path
input_data = str(Path("photo.jpg").absolute())
```

### Issue: "Invalid image format"

**Solution:** Ensure PIL can read the format

```python
from PIL import Image

# Test if image is valid
try:
    Image.open("photo.jpg")
except Exception as e:
    print(f"Invalid image: {e}")
```

### Issue: "URL timeout"

**Solution:** Download the image first

```python
import requests
from PIL import Image
from io import BytesIO

# Download and save
response = requests.get(image_url)
img = Image.open(BytesIO(response.content))
img.save("downloaded_image.jpg")

# Use local file
input_data = "downloaded_image.jpg"
```

### Issue: "JSON parsing error"

**Solution:** Validate JSON structure

```python
import json

# Validate before sending
try:
    input_dict = {"image": "photo.jpg", "question": "What?"}
    input_data = json.dumps(input_dict)
    # Test parsing
    json.loads(input_data)
except json.JSONDecodeError as e:
    print(f"Invalid JSON: {e}")
```

## Summary

| Data Type | Format | Example |
|-----------|--------|---------|
| Text | String | `"Hello world"` |
| Text (complex) | JSON | `"{\"question\":\"...\", \"context\":\"...\"}"` |
| Image | File path | `"image.jpg"` |
| Image | URL | `"https://example.com/img.jpg"` |
| Image | Base64 | `"data:image/jpeg;base64,..."` |
| Audio | File path | `"audio.wav"` |
| Audio | URL | `"https://example.com/audio.mp3"` |
| Multimodal | JSON | `"{\"image\":\"...\", \"question\":\"...\"}"` |

## Additional Resources

- [API Reference](../api_reference.md) - Complete API documentation
- [Examples](../../examples/) - Working code examples
- [MCP Server Guide](MCP_SERVER_GUIDE.md) - MCP setup and usage
- [Universal Processor](../../src/mcp/processors/universal_processor.py) - Input processing implementation

---

**Need help?** Check the [GitHub Issues](https://github.com/yourusername/GIL/issues) or refer to the [documentation](../getting_started.md).




