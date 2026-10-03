# API Reference

Complete API documentation for the Dynamic AI Model Control Panel (MCP).

## Table of Contents

- [ModelManager](#modelmanager)
- [ModelSelector](#modelselector)
- [ModelExecutor](#modelexecutor)
- [HuggingFaceClient](#huggingfaceclient)
- [Configuration](#configuration)
- [Data Classes](#data-classes)
- [Exceptions](#exceptions)

---

## ModelManager

Main orchestration class for the MCP system.

### Constructor

```python
ModelManager(
    config: Optional[Config] = None,
    config_file: Optional[Path] = None,
    **config_overrides
)
```

**Parameters:**
- `config` (Config, optional): Configuration object
- `config_file` (Path, optional): Path to YAML configuration file
- `**config_overrides`: Keyword arguments to override config values

**Example:**
```python
manager = ModelManager(
    execution_device="cuda",
    logging_level="DEBUG"
)
```

### Methods

#### discover_models

```python
discover_models(
    task: Optional[str] = None,
    limit: int = 20,
    **search_params
) -> List[ModelInfo]
```

Discover available models for a task.

**Parameters:**
- `task` (str, optional): Task type (uses default from config if None)
- `limit` (int): Maximum number of models to return
- `**search_params`: Additional search parameters

**Returns:** List of ModelInfo objects

**Example:**
```python
models = manager.discover_models(
    task="object-detection",
    limit=10
)
```

#### select_best_model

```python
select_best_model(
    task: Optional[str] = None,
    criteria: Optional[SelectionCriteria] = None
) -> ModelInfo
```

Select the best model for a task.

**Parameters:**
- `task` (str, optional): Task type
- `criteria` (SelectionCriteria, optional): Selection criteria

**Returns:** ModelInfo for the selected model

#### load_model

```python
load_model(
    model: Union[str, ModelInfo],
    task: Optional[str] = None
)
```

Load a model for inference.

**Parameters:**
- `model` (str or ModelInfo): Model ID or ModelInfo object
- `task` (str, optional): Task type (required if model is a string)

#### auto_load_best_model

```python
auto_load_best_model(
    task: Optional[str] = None,
    criteria: Optional[SelectionCriteria] = None
) -> ModelInfo
```

Automatically select and load the best model.

**Returns:** ModelInfo of the loaded model

**Example:**
```python
model = manager.auto_load_best_model(
    task="object-detection"
)
```

#### predict

```python
predict(
    input_data: Any,
    task: Optional[str] = None,
    auto_load: bool = True,
    score_threshold: float = 0.5,
    **kwargs
) -> Union[DetectionResult, SegmentationResult]
```

Perform prediction on input data.

**Parameters:**
- `input_data` (Any): Input data (image path, PIL Image, etc.)
- `task` (str, optional): Task type
- `auto_load` (bool): Automatically load a model if none is loaded
- `score_threshold` (float): Minimum confidence score
- `**kwargs`: Additional inference arguments

**Returns:** Prediction result object

**Example:**
```python
result = manager.predict(
    "image.jpg",
    task="object-detection",
    score_threshold=0.7
)
```

#### predict_batch

```python
predict_batch(
    input_batch: List[Any],
    task: Optional[str] = None,
    auto_load: bool = True,
    **kwargs
) -> List[Any]
```

Perform prediction on a batch of inputs.

**Parameters:**
- `input_batch` (List): List of input data
- `task` (str, optional): Task type
- `auto_load` (bool): Auto-load model
- `**kwargs`: Additional arguments

**Returns:** List of prediction results

#### visualize_result

```python
visualize_result(
    result: Any,
    image: Any,
    output_path: Optional[Path] = None
) -> Image
```

Visualize prediction results on an image.

**Parameters:**
- `result`: Prediction result
- `image`: Original image (path or PIL Image)
- `output_path` (Path, optional): Path to save visualization

**Returns:** PIL Image with visualizations

#### export_results

```python
export_results(
    result: Any,
    output_path: Path,
    format: str = 'json'
)
```

Export results to file.

**Parameters:**
- `result`: Prediction result
- `output_path` (Path): Output file path
- `format` (str): Export format ('json' or 'csv')

#### get_metrics

```python
get_metrics() -> dict
```

Get performance metrics.

**Returns:** Dictionary with metrics statistics

#### benchmark_model

```python
benchmark_model(
    input_data: Any,
    iterations: int = 10,
    task: Optional[str] = None
) -> dict
```

Benchmark the currently loaded model.

**Returns:** Dictionary with benchmark statistics

**Example:**
```python
stats = manager.benchmark_model(
    "test.jpg",
    iterations=20
)
print(f"Mean: {stats['mean_time']:.3f}s")
```

#### close

```python
close()
```

Clean up resources and unload models.

---

## ModelSelector

Intelligent model selection based on criteria.

### Constructor

```python
ModelSelector(
    hf_client: HuggingFaceClient,
    default_criteria: Optional[SelectionCriteria] = None
)
```

### Methods

#### select_model

```python
select_model(
    task: str,
    criteria: Optional[SelectionCriteria] = None,
    use_cache: bool = True
) -> ModelInfo
```

Select the best model for a task.

#### select_multiple_models

```python
select_multiple_models(
    task: str,
    count: int = 3,
    criteria: Optional[SelectionCriteria] = None,
    diverse: bool = True
) -> List[ModelInfo]
```

Select multiple models for a task.

**Parameters:**
- `task` (str): Task type
- `count` (int): Number of models to select
- `criteria` (SelectionCriteria, optional): Selection criteria
- `diverse` (bool): Ensure diversity in selection

---

## SelectionCriteria

Configuration for model selection.

### Constructor

```python
SelectionCriteria(
    task: str,
    min_downloads: int = 1000,
    min_likes: int = 0,
    preferred_libraries: List[str] = None,
    required_tags: List[str] = None,
    excluded_tags: List[str] = None,
    sort_by: str = "downloads",
    custom_scorer: Optional[Callable] = None
)
```

**Parameters:**
- `task` (str): Task type (required)
- `min_downloads` (int): Minimum number of downloads
- `min_likes` (int): Minimum number of likes
- `preferred_libraries` (List[str]): Preferred ML libraries
- `required_tags` (List[str]): Required model tags
- `excluded_tags` (List[str]): Tags to exclude
- `sort_by` (str): Sorting criteria ("downloads", "likes", or "custom")
- `custom_scorer` (Callable): Custom scoring function

**Example:**
```python
criteria = SelectionCriteria(
    task="object-detection",
    min_downloads=5000,
    min_likes=50,
    preferred_libraries=["transformers"],
    required_tags=["pytorch"],
    excluded_tags=["gated"],
    sort_by="downloads"
)
```

---

## Config

Configuration management class.

### Constructor

```python
Config(
    config_file: Optional[Path] = None,
    **kwargs
)
```

**Parameters:**
- `config_file` (Path, optional): Path to YAML config file
- `**kwargs`: Configuration overrides

**Example:**
```python
config = Config(
    execution_device="cuda",
    model_default_task="image-segmentation",
    logging_level="INFO"
)
```

### Configuration Sections

#### APIConfig

```python
config.api.huggingface_token      # str
config.api.huggingface_api_url    # str
config.api.timeout                # int
config.api.max_retries            # int
```

#### ModelConfig

```python
config.model.default_task         # str
config.model.supported_tasks      # List[str]
config.model.min_downloads        # int
config.model.cache_models         # bool
config.model.model_cache_dir      # Path
```

#### ExecutionConfig

```python
config.execution.device           # str ("cpu", "cuda", "mps")
config.execution.batch_size       # int
config.execution.timeout          # int
config.execution.enable_fp16      # bool
```

#### LoggingConfig

```python
config.logging.level              # str
config.logging.log_dir            # Path
config.logging.console_output     # bool
```

---

## Data Classes

### ModelInfo

Information about a Hugging Face model.

```python
@dataclass
class ModelInfo:
    model_id: str
    task: str
    downloads: int
    likes: int
    tags: List[str]
    pipeline_tag: Optional[str]
    library_name: Optional[str]
```

**Methods:**
- `to_dict() -> Dict`: Convert to dictionary

### DetectionResult

Results from object detection.

```python
@dataclass
class DetectionResult:
    boxes: List[BoundingBox]
    image_size: Optional[Tuple[int, int]]
    processing_time: Optional[float]
    model_id: Optional[str]
```

**Methods:**
- `filter_by_score(threshold: float) -> DetectionResult`
- `filter_by_label(labels: List[str]) -> DetectionResult`
- `to_dict() -> Dict`

### BoundingBox

Single object detection.

```python
@dataclass
class BoundingBox:
    xmin: float
    ymin: float
    xmax: float
    ymax: float
    label: str
    score: float
```

**Properties:**
- `width: float`
- `height: float`
- `area: float`
- `center: Tuple[float, float]`

### SegmentationResult

Results from image segmentation.

```python
@dataclass
class SegmentationResult:
    masks: List[SegmentationMask]
    image_size: Optional[Tuple[int, int]]
    processing_time: Optional[float]
    model_id: Optional[str]
```

**Methods:**
- `filter_by_score(threshold: float) -> SegmentationResult`
- `to_dict() -> Dict`

### SegmentationMask

Single segmentation mask.

```python
@dataclass
class SegmentationMask:
    mask: np.ndarray
    label: str
    score: float
```

**Properties:**
- `area: int` - Number of pixels in mask

---

## Exceptions

### MCPException

Base exception for all MCP errors.

```python
class MCPException(Exception):
    def __init__(self, message: str, details: dict = None)
```

### ModelNotFoundError

Raised when a model cannot be found.

```python
class ModelNotFoundError(MCPException):
    def __init__(self, model_id: str, task: str = None)
```

### ModelLoadError

Raised when model loading fails.

```python
class ModelLoadError(MCPException):
    def __init__(self, model_id: str, reason: str = None)
```

### ModelExecutionError

Raised when model execution fails.

```python
class ModelExecutionError(MCPException):
    def __init__(self, model_id: str, reason: str = None)
```

### APIError

Raised when API calls fail.

```python
class APIError(MCPException):
    def __init__(self, service: str, status_code: int = None, reason: str = None)
```

### ConfigurationError

Raised for configuration issues.

```python
class ConfigurationError(MCPException):
    def __init__(self, config_key: str = None, reason: str = None)
```

---

## Usage Examples

### Complete Example

```python
from src.mcp import ModelManager, Config
from src.mcp.core.model_selector import SelectionCriteria
from src.mcp.utils.exceptions import MCPException

# Configure
config = Config(
    execution_device="cuda",
    logging_level="INFO"
)

# Create criteria
criteria = SelectionCriteria(
    task="object-detection",
    min_downloads=10000,
    preferred_libraries=["transformers"]
)

try:
    with ModelManager(config=config) as manager:
        # Discover
        models = manager.discover_models(task="object-detection")
        
        # Load
        model = manager.auto_load_best_model(
            task="object-detection",
            criteria=criteria
        )
        
        # Predict
        result = manager.predict("image.jpg", score_threshold=0.7)
        
        # Process
        for box in result.boxes:
            print(f"{box.label}: {box.score:.2%}")
        
        # Visualize
        manager.visualize_result(result, "image.jpg", "output.jpg")
        
except MCPException as e:
    print(f"Error: {e}")
```

---

For more examples, see the `examples/` directory in the repository.




