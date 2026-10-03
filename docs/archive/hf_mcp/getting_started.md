# Getting Started with MCP

This guide will help you get up and running with the Dynamic AI Model Control Panel (MCP) quickly.

## Prerequisites

Before you begin, ensure you have:

- Python 3.8 or higher installed
- pip package manager
- Basic understanding of Python and object-oriented programming
- (Optional) CUDA-capable GPU for faster inference

## Installation

### Step 1: Clone the Repository

```bash
git clone https://github.com/yourusername/mcp.git
cd mcp
```

### Step 2: Create a Virtual Environment (Recommended)

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate
```

### Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 4: Install the Package

```bash
# Install in development mode
pip install -e .
```

## Quick Start Tutorial

### Example 1: Your First Object Detection

Create a file called `my_first_detection.py`:

```python
from src.mcp import ModelManager

# Initialize the Model Manager
with ModelManager() as manager:
    # Automatically discover, select, and load the best model
    model = manager.auto_load_best_model(task="object-detection")
    print(f"Loaded model: {model.model_id}")
    
    # Note: Replace with your actual image path
    # result = manager.predict("path/to/your/image.jpg")
    # print(f"Detected {len(result.boxes)} objects")
```

Run it:

```bash
python my_first_detection.py
```

### Example 2: Custom Configuration

Create `custom_config.py`:

```python
from src.mcp import ModelManager, Config

# Create custom configuration
config = Config(
    execution_device="cpu",  # Use "cuda" if you have GPU
    model_default_task="object-detection",
    model_min_downloads=5000,
    logging_level="INFO"
)

# Initialize with custom config
with ModelManager(config=config) as manager:
    # Your code here
    print(f"Using device: {manager.config.execution.device}")
```

### Example 3: Working with Images

```python
from src.mcp import ModelManager
from pathlib import Path

# Prepare your image
image_path = "path/to/your/image.jpg"

with ModelManager() as manager:
    # Load model
    manager.auto_load_best_model(task="object-detection")
    
    # Run detection
    result = manager.predict(
        image_path,
        score_threshold=0.7  # Only show detections with >70% confidence
    )
    
    # Print results
    print(f"Found {len(result.boxes)} objects:")
    for i, box in enumerate(result.boxes, 1):
        print(f"{i}. {box.label}: {box.score:.2%}")
        print(f"   Location: ({box.xmin:.0f}, {box.ymin:.0f}) to ({box.xmax:.0f}, {box.ymax:.0f})")
    
    # Visualize
    output_path = Path("output/detection_result.jpg")
    output_path.parent.mkdir(exist_ok=True)
    manager.visualize_result(result, image_path, output_path)
    print(f"Visualization saved to: {output_path}")
    
    # Export to JSON
    manager.export_results(result, Path("output/results.json"))
```

## Configuration Options

### Method 1: Configuration File

Create `config/my_config.yaml`:

```yaml
model:
  default_task: "object-detection"
  min_downloads: 5000

execution:
  device: "cpu"
  batch_size: 1

logging:
  level: "INFO"
  log_dir: "logs"
```

Load it:

```python
from pathlib import Path
manager = ModelManager(config_file=Path("config/my_config.yaml"))
```

### Method 2: Environment Variables

```bash
# Set environment variables
export MCP_EXECUTION_DEVICE="cuda"
export MCP_MODEL_DEFAULT_TASK="image-segmentation"
export MCP_LOGGING_LEVEL="DEBUG"

# Run your script
python your_script.py
```

### Method 3: Programmatic

```python
from src.mcp import Config, ModelManager

config = Config(
    execution_device="cuda",
    model_min_downloads=10000,
    logging_level="DEBUG"
)

manager = ModelManager(config=config)
```

## Common Use Cases

### Use Case 1: Batch Processing Multiple Images

```python
from src.mcp import ModelManager
from pathlib import Path

image_dir = Path("images")
image_paths = list(image_dir.glob("*.jpg"))

with ModelManager() as manager:
    # Load model once
    manager.auto_load_best_model(task="object-detection")
    
    # Process all images
    for image_path in image_paths:
        result = manager.predict(str(image_path))
        print(f"{image_path.name}: {len(result.boxes)} objects")
```

### Use Case 2: Selecting Specific Model

```python
from src.mcp import ModelManager
from src.mcp.core.model_selector import SelectionCriteria

with ModelManager() as manager:
    # Define selection criteria
    criteria = SelectionCriteria(
        task="object-detection",
        min_downloads=10000,
        preferred_libraries=["transformers"],
        sort_by="likes"
    )
    
    # Select and load
    model = manager.auto_load_best_model(
        task="object-detection",
        criteria=criteria
    )
    
    print(f"Selected: {model.model_id}")
```

### Use Case 3: Image Segmentation

```python
from src.mcp import ModelManager

with ModelManager() as manager:
    # Load segmentation model
    manager.auto_load_best_model(task="image-segmentation")
    
    # Run segmentation
    result = manager.predict(
        "image.jpg",
        task="image-segmentation",
        score_threshold=0.8
    )
    
    # Process masks
    for mask in result.masks:
        print(f"{mask.label}: {mask.area} pixels ({mask.score:.2%})")
```

### Use Case 4: Performance Benchmarking

```python
from src.mcp import ModelManager

with ModelManager() as manager:
    manager.auto_load_best_model(task="object-detection")
    
    # Benchmark
    stats = manager.benchmark_model(
        input_data="test_image.jpg",
        iterations=20
    )
    
    print(f"Mean time: {stats['mean_time']:.3f}s")
    print(f"FPS: {1/stats['mean_time']:.1f}")
```

## Understanding Results

### Detection Results

```python
result = manager.predict("image.jpg", task="object-detection")

# Access individual detections
for box in result.boxes:
    print(f"Label: {box.label}")
    print(f"Confidence: {box.score:.2%}")
    print(f"Position: ({box.xmin}, {box.ymin}) to ({box.xmax}, {box.ymax})")
    print(f"Size: {box.width} x {box.height}")
    print(f"Area: {box.area}")
    print()

# Filter results
high_conf = result.filter_by_score(0.9)  # Only >90% confidence
cars_only = result.filter_by_label(['car', 'truck'])

# Convert to dictionary
data = result.to_dict()
```

### Segmentation Results

```python
result = manager.predict("image.jpg", task="image-segmentation")

# Access masks
for mask in result.masks:
    print(f"Label: {mask.label}")
    print(f"Score: {mask.score:.2%}")
    print(f"Area: {mask.area} pixels")
    print(f"Shape: {mask.shape}")
    
    # Access mask array
    mask_array = mask.mask  # numpy array
```

## Troubleshooting

### Issue: Model Download is Slow

**Solution**: Models are cached after first download. Subsequent runs will be faster.

### Issue: Out of Memory

**Solution**: 
```python
# Unload model after use
manager.unload_model()

# Or use smaller models
criteria = SelectionCriteria(
    task="object-detection",
    required_tags=["small", "mobile"]
)
```

### Issue: CUDA Out of Memory

**Solution**:
```python
# Use CPU instead
config = Config(execution_device="cpu")
manager = ModelManager(config=config)
```

### Issue: Import Errors

**Solution**:
```bash
# Reinstall dependencies
pip install -r requirements.txt --force-reinstall
```

## Next Steps

1. **Explore Examples**: Check out the `examples/` directory for more advanced usage
2. **Read API Reference**: See `docs/api_reference.md` for detailed API documentation
3. **Understand Architecture**: Read `docs/architecture.md` to understand the system design
4. **Contribute**: See `CONTRIBUTING.md` for contribution guidelines

## Getting Help

- **Documentation**: Browse the `docs/` directory
- **Examples**: Check `examples/` for working code
- **Issues**: Open an issue on GitHub
- **Discussions**: Join our community discussions

## Best Practices

1. **Always use context managers** (`with` statement) for automatic cleanup
2. **Set appropriate score thresholds** to filter low-confidence results
3. **Monitor performance** using the built-in metrics tracker
4. **Cache models** by not unloading them if processing multiple images
5. **Handle exceptions** gracefully in production code

```python
from src.mcp import ModelManager
from src.mcp.utils.exceptions import MCPException

try:
    with ModelManager() as manager:
        result = manager.predict("image.jpg", auto_load=True)
except MCPException as e:
    print(f"MCP Error: {e}")
except Exception as e:
    print(f"Unexpected error: {e}")
```

Happy coding with MCP! 🚀




