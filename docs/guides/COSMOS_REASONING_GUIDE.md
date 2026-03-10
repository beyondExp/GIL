# NVIDIA Cosmos-Reason1-7B Integration Guide

## 🤖 Overview

**NVIDIA Cosmos-Reason1-7B** is a specialized physical AI reasoning model integrated into GIL for robotics, autonomous vehicles, and video analytics applications.

### Key Capabilities

- 🧠 **Physical AI Reasoning** - Understands space, time, and fundamental physics
- 🤖 **Robotics Planning** - Plans robot actions with common sense reasoning
- 🚗 **Autonomous Vehicles** - Makes driving decisions based on video analysis
- 📹 **Video Analytics** - Analyzes video with temporal-spatial understanding
- 🔗 **Chain-of-Thought** - Provides detailed step-by-step reasoning

## 📊 Model Specifications

| Property | Value |
|----------|-------|
| **Model ID** | `nvidia/Cosmos-Reason1-7B` |
| **Architecture** | Qwen2.5-VL-7B-Instruct (post-trained) |
| **Parameters** | 7B (7.07B LLM + 675M ViT) |
| **Input** | Video (FPS=4) + Text |
| **Output** | `<think>reasoning</think><answer>answer</answer>` |
| **Max Context** | 128K tokens |
| **Recommended Output** | 4096+ tokens |
| **License** | NVIDIA Open Model License + Apache 2.0 |
| **Commercial Use** | ✅ Yes |

## 🚀 Quick Start

### Basic Usage

```python
from src.mcp import ModelManager

# Simple physics reasoning
with ModelManager() as manager:
    result = manager.predict(
        {
            "question": "If I drop a ball, what will happen?",
            "system_prompt": "Answer in format: <think>reasoning</think><answer>answer</answer>.",
            "max_tokens": 4096
        },
        task="text-generation",
        model_id="nvidia/Cosmos-Reason1-7B",
        auto_load=True
    )
    print(result)
```

### Video Analysis

```python
# Analyze video for spatial-temporal reasoning
cosmos_input = {
    "video": "path/to/video.mp4",
    "question": "Is it safe to turn right?",
    "fps": 4,  # REQUIRED: Use FPS=4
    "max_tokens": 4096,
    "system_prompt": "Answer in format: <think>reasoning</think><answer>answer</answer>."
}

with ModelManager() as manager:
    result = manager.predict(
        cosmos_input,
        task="visual-question-answering",
        model_id="nvidia/Cosmos-Reason1-7B",
        auto_load=True
    )
    print(result)
```

## 🎯 Use Cases

### 1. Robotics Planning

#### Navigation Planning
```python
question = """
A robot is in a warehouse with boxes and people moving around.
It needs to reach the loading dock. What path should it take?
"""

cosmos_input = {
    "video": "warehouse_video.mp4",
    "question": question,
    "fps": 4,
    "max_tokens": 4096,
    "system_prompt": "Answer in format: <think>reasoning</think><answer>answer</answer>."
}

with ModelManager() as manager:
    result = manager.predict(
        cosmos_input,
        task="visual-question-answering",
        model_id="nvidia/Cosmos-Reason1-7B",
        auto_load=True
    )
```

#### Object Manipulation
```python
question = """
A robot arm needs to pick up a fragile glass and place it on a high shelf.
What sequence of actions should it perform considering physics?
"""

cosmos_input = {
    "question": question,
    "max_tokens": 4096,
    "system_prompt": "Answer in format: <think>reasoning</think><answer>answer</answer>."
}

with ModelManager() as manager:
    result = manager.predict(
        cosmos_input,
        task="text-generation",
        model_id="nvidia/Cosmos-Reason1-7B",
        auto_load=True
    )
```

### 2. Autonomous Vehicles

#### Traffic Decision Making
```python
question = """
The traffic light is yellow, there's a pedestrian approaching the crosswalk,
and a car is following closely. Should the vehicle stop or proceed?
"""

cosmos_input = {
    "video": "traffic_scene.mp4",
    "question": question,
    "fps": 4,
    "max_tokens": 4096,
    "system_prompt": "Answer in format: <think>reasoning</think><answer>answer</answer>."
}

with ModelManager() as manager:
    result = manager.predict(
        cosmos_input,
        task="visual-question-answering",
        model_id="nvidia/Cosmos-Reason1-7B",
        auto_load=True
    )
```

#### Lane Change Reasoning
```python
question = """
Analyze the traffic conditions and determine if it's safe to change lanes.
Consider vehicle speeds, distances, and road conditions.
"""

cosmos_input = {
    "video": "highway_driving.mp4",
    "question": question,
    "fps": 4,
    "max_tokens": 4096
}

with ModelManager() as manager:
    result = manager.predict(
        cosmos_input,
        task="visual-question-answering",
        model_id="nvidia/Cosmos-Reason1-7B",
        auto_load=True
    )
```

### 3. Video Analytics

#### Industrial Monitoring
```python
question = """
Analyze this manufacturing process video for any anomalies or safety concerns.
Provide timestamp-specific observations.
"""

cosmos_input = {
    "video": "factory_floor.mp4",
    "question": question,
    "fps": 4,
    "max_tokens": 4096,
    "system_prompt": "Answer in format: <think>reasoning</think><answer>answer</answer>."
}

with ModelManager() as manager:
    result = manager.predict(
        cosmos_input,
        task="visual-question-answering",
        model_id="nvidia/Cosmos-Reason1-7B",
        auto_load=True
    )
```

#### Root Cause Analysis
```python
question = """
A defect occurred at timestamp 2:35 in the production line.
What events led to this defect based on the video?
"""

cosmos_input = {
    "video": "production_video.mp4",
    "question": question,
    "fps": 4,
    "max_tokens": 4096
}

with ModelManager() as manager:
    result = manager.predict(
        cosmos_input,
        task="visual-question-answering",
        model_id="nvidia/Cosmos-Reason1-7B",
        auto_load=True
    )
```

## 🔧 Configuration

### Required Parameters

```python
cosmos_config = {
    # REQUIRED for video input
    "fps": 4,  # Must be 4 to match training
    
    # REQUIRED for detailed reasoning
    "max_tokens": 4096,  # Minimum 4096 for full chain-of-thought
    
    # RECOMMENDED system prompt
    "system_prompt": """You are a helpful assistant. 
Answer the question in the following format: 
<think>
your reasoning
</think>

<answer>
your answer
</answer>."""
}
```

### Optional Parameters

```python
sampling_params = {
    "temperature": 0.6,      # Default for reasoning
    "top_p": 0.95,           # Nucleus sampling
    "repetition_penalty": 1.05,  # Reduce repetition
    "max_tokens": 4096       # For long reasoning
}
```

## 📈 Performance Optimization

### GPU Configuration

```bash
# Enable GPU acceleration
export MCP_EXECUTION_DEVICE="cuda"
export MCP_EXECUTION_ENABLE_FP16="true"

# For multi-GPU
export CUDA_VISIBLE_DEVICES="0,1"
```

### Batch Processing

```python
questions = [
    "Is it safe to turn right?",
    "What is the vehicle speed?",
    "Are there any pedestrians?"
]

with ModelManager() as manager:
    results = []
    for question in questions:
        result = manager.predict(
            {"question": question, "video": "scene.mp4", "fps": 4, "max_tokens": 4096},
            task="visual-question-answering",
            model_id="nvidia/Cosmos-Reason1-7B"
        )
        results.append(result)
```

## 🔗 MCP Server Integration

### Via MCP Tools

```python
# AI agents can call via MCP
{
  "tool": "execute_model",
  "arguments": {
    "model_id": "nvidia/Cosmos-Reason1-7B",
    "task": "visual-question-answering",
    "input": {
      "video": "scene.mp4",
      "question": "What actions should the robot take?",
      "fps": 4,
      "max_tokens": 4096
    }
  }
}
```

### MCP Server Configuration

Add to `mcp_config.json`:

```json
{
  "specialized_models": {
    "physical_ai": {
      "model_id": "nvidia/Cosmos-Reason1-7B",
      "tasks": [
        "visual-question-answering",
        "text-generation"
      ],
      "default_params": {
        "fps": 4,
        "max_tokens": 4096
      }
    }
  }
}
```

## 📊 Output Format

### Chain-of-Thought Format

Cosmos outputs structured reasoning:

```xml
<think>
Step 1: Analyze the current position of the robot
Step 2: Identify obstacles in the path
Step 3: Calculate the optimal trajectory
Step 4: Consider dynamic elements (people, other robots)
Step 5: Plan waypoints with safety margins
</think>

<answer>
The robot should:
1. Move forward 2 meters
2. Turn 45 degrees right
3. Navigate around the obstacle
4. Resume path to target
Estimated time: 15 seconds
</answer>
```

### Parsing Output

```python
def parse_cosmos_output(output):
    """Parse Cosmos chain-of-thought output."""
    import re
    
    # Extract thinking
    think_match = re.search(r'<think>(.*?)</think>', output, re.DOTALL | re.IGNORECASE)
    thinking = think_match.group(1).strip() if think_match else ""
    
    # Extract answer
    answer_match = re.search(r'<answer>(.*?)</answer>', output, re.DOTALL | re.IGNORECASE)
    answer = answer_match.group(1).strip() if answer_match else ""
    
    return {
        "reasoning": thinking,
        "answer": answer,
        "full_output": output
    }

# Usage
result = manager.predict(cosmos_input, ...)
parsed = parse_cosmos_output(result)
print("Reasoning:", parsed["reasoning"])
print("Answer:", parsed["answer"])
```

## 🧪 Testing

### Run Cosmos Tests

```bash
# Run all Cosmos tests (20 tests)
python tests/test_cosmos_reasoning.py

# Expected output:
# - Basic reasoning (3 tests)
# - Robotics planning (3 tests)
# - Autonomous vehicles (4 tests)
# - Video analytics (3 tests)
# - Chain-of-thought (3 tests)
# - Performance (4 tests)
```

### Test Categories

1. **Basic Reasoning** - Spatial, temporal, physics
2. **Robotics** - Navigation, manipulation, obstacles
3. **Autonomous Vehicles** - Turn safety, pedestrians, traffic
4. **Video Analytics** - Action recognition, anomalies, root cause
5. **Chain-of-Thought** - Complex reasoning, counterfactuals
6. **Performance** - FPS, long output, caching

## 📚 Best Practices

### 1. Video Input
- ✅ **Use FPS=4** - Required to match training
- ✅ **Add timestamps** - Model recognizes frame timestamps
- ✅ **Quality matters** - Clear, well-lit video works best

### 2. Prompting
- ✅ **Use system prompt** - Encourages structured output
- ✅ **Be specific** - "Is it safe to turn right?" > "What should I do?"
- ✅ **Request reasoning** - Ask for step-by-step explanations

### 3. Output Handling
- ✅ **Set max_tokens=4096+** - Avoid truncated reasoning
- ✅ **Parse structure** - Extract `<think>` and `<answer>`
- ✅ **Handle long outputs** - Reasoning can be detailed

### 4. Performance
- ✅ **Use GPU** - Model optimized for NVIDIA GPUs
- ✅ **Cache model** - Reuse loaded model for multiple queries
- ✅ **Batch when possible** - Process multiple questions efficiently

## ⚠️ Important Notes

### Model Requirements
- **GPU**: Optimized for NVIDIA GPUs (tested on H100)
- **Precision**: BF16 precision for inference
- **OS**: Tested on Linux (other OS not tested)
- **Memory**: ~14GB GPU memory for model

### Video Requirements
- **FPS**: Must use FPS=4 for input video
- **Format**: MP4 recommended
- **Quality**: Higher quality = better results
- **Length**: Model handles various lengths

### Safety Guardrails
⚠️ **Important**: Do not bypass, disable, or circumvent safety guardrails. Doing so terminates your license rights under NVIDIA Open Model License.

## 🔍 Troubleshooting

### Issue: "Model not loading"
```bash
# Check GPU availability
python -c "import torch; print(torch.cuda.is_available())"

# Set device explicitly
export MCP_EXECUTION_DEVICE="cuda"
```

### Issue: "Truncated reasoning output"
```python
# Increase max_tokens
cosmos_input["max_tokens"] = 8192  # or higher
```

### Issue: "FPS mismatch"
```python
# Always use FPS=4
cosmos_input["fps"] = 4  # Required
```

### Issue: "Out of memory"
```bash
# Use CPU (slower but works)
export MCP_EXECUTION_DEVICE="cpu"

# Or use model quantization
cosmos_input["quantization"] = "int8"
```

## 📖 Example Workflows

### Complete Robotics Workflow

```python
from src.mcp import ModelManager

def robot_decision_pipeline(video_path, task_description):
    """Complete pipeline for robot decision making."""
    
    with ModelManager() as manager:
        # Step 1: Understand the scene
        scene_input = {
            "video": video_path,
            "question": "Describe the environment and all objects present.",
            "fps": 4,
            "max_tokens": 4096
        }
        scene_understanding = manager.predict(
            scene_input,
            task="visual-question-answering",
            model_id="nvidia/Cosmos-Reason1-7B",
            auto_load=True
        )
        
        # Step 2: Plan actions
        planning_input = {
            "question": f"Given this task: {task_description}. What steps should the robot take?",
            "max_tokens": 4096,
            "system_prompt": "Answer in format: <think>reasoning</think><answer>answer</answer>."
        }
        action_plan = manager.predict(
            planning_input,
            task="text-generation",
            model_id="nvidia/Cosmos-Reason1-7B"
        )
        
        # Step 3: Verify safety
        safety_input = {
            "video": video_path,
            "question": "Are there any safety concerns with this plan?",
            "fps": 4,
            "max_tokens": 4096
        }
        safety_check = manager.predict(
            safety_input,
            task="visual-question-answering",
            model_id="nvidia/Cosmos-Reason1-7B"
        )
        
        return {
            "scene": scene_understanding,
            "plan": action_plan,
            "safety": safety_check
        }

# Usage
result = robot_decision_pipeline(
    "warehouse_scene.mp4",
    "Pick up the box and move it to the loading dock"
)
```

## 🎓 Additional Resources

- **Model Card**: https://build.nvidia.com/nvidia/cosmos-reason1-7b/modelcard
- **Technical Paper**: Cosmos-Reason1 Documentation
- **HuggingFace**: https://huggingface.co/nvidia/Cosmos-Reason1-7B
- **GIL Documentation**: `docs/guides/`
- **Test Suite**: `tests/test_cosmos_reasoning.py`

## 📞 Support

For issues with:
- **GIL Integration**: See `docs/guides/`
- **Cosmos Model**: NVIDIA Build Documentation
- **MCP Server**: `docs/guides/MCP_SERVER_GUIDE.md`
- **Testing**: `tests/test_cosmos_reasoning.py`

---

**Model**: NVIDIA Cosmos-Reason1-7B  
**Integration**: GIL v1.0+  
**License**: NVIDIA Open Model License + Apache 2.0  
**Commercial Use**: ✅ Approved




