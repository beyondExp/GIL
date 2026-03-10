# NVIDIA Cosmos-Reason1-7B Quick Start

## 🚀 30-Second Start

### 1. Basic Physics Reasoning

```python
from src.mcp import ModelManager

with ModelManager() as manager:
    result = manager.predict(
        {
            "question": "If I drop a ball, what will happen?",
            "max_tokens": 4096
        },
        task="text-generation",
        model_id="nvidia/Cosmos-Reason1-7B",
        auto_load=True
    )
    print(result)
```

### 2. Robot Navigation

```python
from src.mcp.server.cosmos_tools import CosmosTools

with CosmosTools() as cosmos:
    plan = cosmos.robot_navigation_planning(
        video_path="warehouse.mp4",
        task_description="Move from A to B avoiding obstacles"
    )
    print(plan)
```

### 3. Autonomous Vehicle Decision

```python
with CosmosTools() as cosmos:
    decision = cosmos.autonomous_vehicle_decision(
        video_path="traffic.mp4",
        decision_type="turn"
    )
    print(decision)
```

## 🧪 Run Tests

```bash
# All Cosmos tests (20)
python tests/test_cosmos_reasoning.py

# Via master suite
python tests/run_all_multimodal_tests.py --suite cosmos

# All tests (141+)
python tests/run_all_multimodal_tests.py
```

## 📖 Learn More

- **Complete Guide**: `docs/guides/COSMOS_REASONING_GUIDE.md`
- **Integration Summary**: `COSMOS_INTEGRATION_SUMMARY.md`
- **Test Suite**: `tests/test_cosmos_reasoning.py`
- **MCP Tools**: `src/mcp/server/cosmos_tools.py`

## ⚡ Key Parameters

```python
cosmos_config = {
    "fps": 4,           # REQUIRED for video input
    "max_tokens": 4096, # REQUIRED for full reasoning
    "model_id": "nvidia/Cosmos-Reason1-7B"
}
```

## 🎯 Use Cases

| Use Case | Tool | Description |
|----------|------|-------------|
| Robot Navigation | `cosmos_robot_navigation` | Plan paths with obstacles |
| Object Manipulation | `cosmos_object_manipulation` | Plan picking/placing |
| AV Decisions | `cosmos_vehicle_decision` | Traffic decisions |
| Video Analytics | `cosmos_video_analytics` | Analyze videos |
| Physics Reasoning | `cosmos_physics_reasoning` | Understand physics |

## 📊 What You Get

```
✅ 20 comprehensive tests
✅ 6 MCP tools for AI agents
✅ Complete documentation
✅ Robotics planning
✅ Autonomous vehicle support
✅ Video analytics
✅ Chain-of-thought reasoning
```

---

**Start:** `python tests/test_cosmos_reasoning.py`


