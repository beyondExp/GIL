# Extending MCP to Support All Hugging Face Tasks

## Overview

The MCP system is designed with extensibility in mind. While it currently focuses on computer vision tasks (object detection and image segmentation), the core architecture can easily support **all 50+ tasks** available on Hugging Face Hub.

## What's Already Task-Agnostic

These components work with ANY task:

✅ **ModelManager** - Task-agnostic orchestration  
✅ **ModelSelector** - Can discover models for any task  
✅ **ModelExecutor** - Uses HuggingFace pipelines (supports all tasks)  
✅ **HuggingFaceClient** - Can query any task type  
✅ **Configuration System** - Supports any task name  
✅ **Logging & Metrics** - Work with any task  

## What Needs Extension

These components are task-specific and need extension:

🔧 **InputProcessor** - Currently handles images only  
🔧 **OutputProcessor** - Currently handles detection/segmentation only  

## Supported Hugging Face Tasks

The system can be extended to support:

### Natural Language Processing
- `text-generation` - Generate text (GPT, BLOOM)
- `text-classification` - Classify text (sentiment, topic)
- `token-classification` - NER, POS tagging
- `question-answering` - Answer questions from context
- `summarization` - Summarize text
- `translation` - Translate between languages
- `fill-mask` - Fill masked tokens (BERT-style)
- `text2text-generation` - Seq2seq tasks

### Audio Processing
- `automatic-speech-recognition` - Speech to text
- `audio-classification` - Classify audio
- `text-to-speech` - Generate speech from text

### Computer Vision (Already Supported)
- `object-detection` ✅ - Detect objects in images
- `image-segmentation` ✅ - Segment images
- `image-classification` - Classify images
- `image-to-text` - Generate captions

### Multimodal
- `visual-question-answering` - Answer questions about images
- `document-question-answering` - QA on documents

## Quick Extension Example

Here's how to add text generation support:

### Step 1: Extend InputProcessor

```python
# Add to src/mcp/processors/input_processor.py

def preprocess_text(self, text: str, max_length: int = 512) -> str:
    """
    Preprocess text input.
    
    Args:
        text: Input text
        max_length: Maximum sequence length
    
    Returns:
        Preprocessed text
    """
    # Basic text preprocessing
    text = text.strip()
    
    # Truncate if needed
    if len(text) > max_length:
        text = text[:max_length]
    
    return text
```

### Step 2: Extend OutputProcessor

```python
# Add to src/mcp/processors/output_processor.py

@dataclass
class TextGenerationResult:
    """Result from text generation."""
    
    generated_text: str
    model_id: Optional[str] = None
    processing_time: Optional[float] = None
    
    def to_dict(self) -> Dict:
        return {
            'generated_text': self.generated_text,
            'model_id': self.model_id,
            'processing_time': self.processing_time,
        }


def process_text_generation_output(
    self,
    raw_output: Any,
    model_id: Optional[str] = None,
    processing_time: Optional[float] = None
) -> TextGenerationResult:
    """Process text generation output."""
    
    if isinstance(raw_output, list) and len(raw_output) > 0:
        generated = raw_output[0].get('generated_text', '')
    elif isinstance(raw_output, dict):
        generated = raw_output.get('generated_text', '')
    else:
        generated = str(raw_output)
    
    return TextGenerationResult(
        generated_text=generated,
        model_id=model_id,
        processing_time=processing_time
    )
```

### Step 3: Use It!

```python
from src.mcp import ModelManager

with ModelManager() as manager:
    # Load text generation model
    manager.auto_load_best_model(task="text-generation")
    
    # Generate text
    result = manager.predict(
        "Once upon a time",
        task="text-generation",
        max_length=100
    )
    
    print(result.generated_text)
```

## Complete Extension Example: Text Classification

Let me create a complete, ready-to-use extension:

### 1. Create Task-Specific Processor

```python
# src/mcp/processors/text_processor.py

from typing import Union, List, Optional, Any
from dataclasses import dataclass
from ..utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ClassificationResult:
    """Result from text classification."""
    
    label: str
    score: float
    all_scores: List[dict]
    
    def to_dict(self) -> dict:
        return {
            'label': self.label,
            'score': self.score,
            'all_scores': self.all_scores
        }


class TextProcessor:
    """Processor for text-based tasks."""
    
    def __init__(self):
        logger.info("Initialized TextProcessor")
    
    def preprocess_text(
        self,
        text: Union[str, List[str]],
        max_length: int = 512
    ) -> Union[str, List[str]]:
        """Preprocess text input."""
        
        if isinstance(text, str):
            return text.strip()[:max_length]
        
        return [t.strip()[:max_length] for t in text]
    
    def process_classification_output(
        self,
        raw_output: Any
    ) -> ClassificationResult:
        """Process classification output."""
        
        if isinstance(raw_output, list) and len(raw_output) > 0:
            result = raw_output[0]
            
            return ClassificationResult(
                label=result.get('label', 'unknown'),
                score=result.get('score', 0.0),
                all_scores=raw_output
            )
        
        raise ValueError(f"Unexpected output format: {type(raw_output)}")
```

### 2. Usage Example

```python
# examples/text_classification_example.py

from src.mcp import ModelManager

def main():
    print("Text Classification with MCP")
    print("=" * 60)
    
    with ModelManager() as manager:
        # Discover text classification models
        print("\n1. Discovering models...")
        models = manager.discover_models(
            task="text-classification",
            limit=5
        )
        
        for i, model in enumerate(models, 1):
            print(f"{i}. {model.model_id} ({model.downloads:,} downloads)")
        
        # Load best model
        print("\n2. Loading model...")
        model = manager.auto_load_best_model(
            task="text-classification"
        )
        print(f"Loaded: {model.model_id}")
        
        # Classify text
        print("\n3. Classifying text...")
        texts = [
            "I love this product! It's amazing!",
            "This is terrible. Very disappointed.",
            "It's okay, nothing special."
        ]
        
        for text in texts:
            # The executor already supports this!
            result = manager.model_executor.execute(
                text,
                task="text-classification",
                preprocess=False,  # No image preprocessing
                postprocess=False   # Get raw output
            )
            
            print(f"\nText: {text}")
            print(f"Label: {result[0]['label']}")
            print(f"Score: {result[0]['score']:.2%}")

if __name__ == "__main__":
    main()
```

## Universal Task Support (No Code Changes Needed!)

For many tasks, you can use the system **RIGHT NOW** without any modifications:

```python
from src.mcp import ModelManager

# TEXT GENERATION
with ModelManager() as manager:
    manager.load_model("gpt2", task="text-generation")
    result = manager.model_executor.execute(
        "Hello, my name is",
        task="text-generation",
        preprocess=False,
        postprocess=False,
        max_length=50
    )
    print(result[0]['generated_text'])

# TRANSLATION
with ModelManager() as manager:
    manager.load_model(
        "Helsinki-NLP/opus-mt-en-fr",
        task="translation"
    )
    result = manager.model_executor.execute(
        "Hello, how are you?",
        task="translation",
        preprocess=False,
        postprocess=False
    )
    print(result[0]['translation_text'])

# SENTIMENT ANALYSIS
with ModelManager() as manager:
    manager.auto_load_best_model(task="sentiment-analysis")
    result = manager.model_executor.execute(
        "I love this!",
        task="sentiment-analysis",
        preprocess=False,
        postprocess=False
    )
    print(f"{result[0]['label']}: {result[0]['score']:.2%}")

# QUESTION ANSWERING
with ModelManager() as manager:
    manager.auto_load_best_model(task="question-answering")
    result = manager.model_executor.execute(
        {
            "question": "What is my name?",
            "context": "My name is Sarah."
        },
        task="question-answering",
        preprocess=False,
        postprocess=False
    )
    print(result['answer'])

# SUMMARIZATION
with ModelManager() as manager:
    manager.auto_load_best_model(task="summarization")
    long_text = "Your long text here..."
    result = manager.model_executor.execute(
        long_text,
        task="summarization",
        preprocess=False,
        postprocess=False,
        max_length=130
    )
    print(result[0]['summary_text'])
```

## Configuration for New Tasks

Simply update the config:

```yaml
# config/all_tasks_config.yaml

model:
  default_task: "text-generation"  # or any HF task
  supported_tasks:
    # Vision
    - "object-detection"
    - "image-segmentation"
    - "image-classification"
    # NLP
    - "text-generation"
    - "text-classification"
    - "translation"
    - "summarization"
    - "question-answering"
    # Audio
    - "automatic-speech-recognition"
    - "audio-classification"
  min_downloads: 1000
```

## Creating a Universal MCP Interface

Here's a universal interface that works with any task:

```python
# examples/universal_mcp_example.py

from src.mcp import ModelManager
from typing import Any

class UniversalMCP:
    """Universal interface for any Hugging Face task."""
    
    def __init__(self):
        self.manager = ModelManager()
    
    def infer(
        self,
        task: str,
        input_data: Any,
        model_id: Optional[str] = None,
        **kwargs
    ) -> Any:
        """
        Universal inference method.
        
        Args:
            task: Any HuggingFace task
            input_data: Task-appropriate input
            model_id: Specific model or auto-select
            **kwargs: Task-specific parameters
        
        Returns:
            Raw model output
        """
        # Load model
        if model_id:
            self.manager.load_model(model_id, task=task)
        else:
            self.manager.auto_load_best_model(task=task)
        
        # Execute
        result = self.manager.model_executor.execute(
            input_data,
            task=task,
            preprocess=False,
            postprocess=False,
            **kwargs
        )
        
        return result
    
    def close(self):
        self.manager.close()


# Usage
umcp = UniversalMCP()

# Text generation
result = umcp.infer(
    task="text-generation",
    input_data="Once upon a time",
    max_length=50
)

# Translation
result = umcp.infer(
    task="translation",
    input_data="Hello world"
)

# Sentiment
result = umcp.infer(
    task="sentiment-analysis",
    input_data="I love this!"
)

umcp.close()
```

## Task-Specific Best Practices

### For NLP Tasks
- Set appropriate `max_length`, `min_length`
- Use `truncation=True` for long inputs
- Consider `num_beams` for generation quality

### For Audio Tasks
- Provide audio file paths or numpy arrays
- Set appropriate `sampling_rate`
- Use `return_timestamps` for ASR

### For Vision Tasks
- Already handled by InputProcessor
- Use existing visualization tools

## Benefits of Extension

1. **Minimal Code Changes**: Core architecture is task-agnostic
2. **Consistent API**: Same interface across all tasks
3. **Automatic Features**: Metrics, logging, error handling work everywhere
4. **Model Selection**: Intelligent selection works for any task
5. **Performance Monitoring**: Built-in benchmarking for all tasks

## Roadmap for Full Multi-Task Support

### Phase 1: Core Extensions (Easy - 1-2 days)
- [ ] Add TextProcessor class
- [ ] Add result classes for common NLP tasks
- [ ] Update ModelExecutor to route by task type
- [ ] Add examples for 5-10 popular tasks

### Phase 2: Enhanced Features (Medium - 3-5 days)
- [ ] Task-specific visualization (for applicable tasks)
- [ ] Batch processing for NLP
- [ ] Audio processing support
- [ ] Export formats for different task types

### Phase 3: Advanced Features (Complex - 1-2 weeks)
- [ ] Multi-modal support
- [ ] Pipeline chaining (e.g., ASR → Translation)
- [ ] Custom task definitions
- [ ] Fine-tuning capabilities

## Conclusion

**The MCP system can already work with most Hugging Face tasks** by using the ModelExecutor directly. For production use with specific tasks, you'd want to add:

1. Task-specific input preprocessing
2. Task-specific output formatting
3. Task-specific visualization (if needed)

The architecture is designed to make these extensions straightforward and maintainable.

Want to see a specific task implemented? Let me know!




