# GIL Project Structure

## 📁 Complete Directory Layout

```
GIL/                                    # General Intelligence Layer
│
├── src/                                # Source code
│   └── mcp/                           # Main package
│       ├── __init__.py
│       ├── core/                      # Core components
│       │   ├── __init__.py
│       │   ├── model_manager.py       # Main orchestration
│       │   ├── model_selector.py      # Model selection logic
│       │   ├── model_executor.py      # Model execution
│       │   └── config.py              # Configuration management
│       ├── api/                       # External API clients
│       │   ├── __init__.py
│       │   └── huggingface_client.py  # HuggingFace API
│       ├── processors/                # Data processors
│       │   ├── __init__.py
│       │   ├── input_processor.py     # Input preprocessing
│       │   ├── output_processor.py    # Output formatting
│       │   └── universal_processor.py # Universal task processor
│       ├── server/                    # MCP Server
│       │   ├── __init__.py
│       │   └── mcp_server.py          # MCP Protocol server
│       └── utils/                     # Utilities
│           ├── __init__.py
│           ├── exceptions.py          # Custom exceptions
│           ├── logger.py              # Logging system
│           └── metrics.py             # Performance tracking
│
├── tests/                             # Test suite
│   ├── __init__.py
│   ├── run_all_tests.py              # Test runner
│   ├── test_model_manager.py         # ModelManager tests
│   ├── test_model_selector.py        # ModelSelector tests
│   ├── test_api_client.py            # API client tests
│   ├── test_mcp_server.py            # MCP server tests
│   ├── test_integration.py           # Integration tests
│   └── manual/                        # Manual test scripts
│       ├── __init__.py
│       ├── test_gpu_setup.py         # GPU verification
│       └── quick_test.py             # Quick tests
│
├── examples/                          # Usage examples
│   ├── object_detection_example.py    # Object detection demo
│   ├── image_segmentation_example.py  # Segmentation demo
│   ├── custom_workflow_example.py     # Custom workflows
│   ├── multi_task_example.py          # Multi-task demo
│   └── all_tasks_showcase.py          # Complete showcase
│
├── docs/                              # Documentation
│   ├── architecture.md                # System architecture
│   ├── api_reference.md               # API documentation
│   ├── getting_started.md             # Tutorial
│   ├── extending_to_new_tasks.md      # Extension guide
│   ├── ENVIRONMENT_VARIABLES.md       # Env vars guide
│   ├── ENV_QUICK_REFERENCE.md         # Quick env reference
│   ├── README_EXTENDED.md             # Extended features
│   ├── EXTENSION_SUMMARY.md           # Extension summary
│   ├── PROJECT_SUMMARY.md             # Project summary
│   ├── CONTRIBUTING.md                # Contributing guide
│   ├── TESTING.md                     # Testing guide
│   ├── PROJECT_STRUCTURE.md           # This file
│   └── guides/                        # Detailed guides
│       ├── MCP_SERVER_GUIDE.md        # MCP server guide
│       └── QUICK_START_GPU.md         # GPU quick start
│
├── config/                            # Configuration files
│   └── default_config.yaml           # Default configuration
│
├── logs/                              # Log files (gitignored)
├── output/                            # Output files (gitignored)
├── metrics/                           # Metrics files (gitignored)
│
├── start_mcp_server.py               # MCP server launcher
├── mcp_config.json                   # MCP client config
├── setup_environment.bat             # Windows env setup
├── setup_environment.sh              # Linux/Mac env setup
├── env.example                       # Environment template
│
├── requirements.txt                  # Python dependencies
├── setup.py                          # Package setup
├── README.md                         # Main documentation
├── LICENSE                           # MIT License
├── .gitignore                        # Git ignore rules
└── CHANGELOG.md                      # Version history (if exists)
```

## 📦 Package Structure

### Core Package (`src/mcp/`)

The main package following Python best practices:

```
src/mcp/
├── core/        # Business logic
├── api/         # External integrations
├── processors/  # Data processing
├── server/      # MCP Protocol server
└── utils/       # Helpers & utilities
```

### Why This Structure?

1. **Separation of Concerns**: Each module has a single responsibility
2. **Testability**: Easy to mock dependencies
3. **Extensibility**: Simple to add new features
4. **Maintainability**: Clear organization
5. **Professional**: Industry-standard layout

## 🗂️ File Purposes

### Root Level Files

| File | Purpose |
|------|---------|
| `start_mcp_server.py` | MCP server entry point |
| `mcp_config.json` | MCP client configuration |
| `setup_environment.*` | Environment setup scripts |
| `requirements.txt` | Python dependencies |
| `setup.py` | Package installation |
| `README.md` | Main documentation |
| `LICENSE` | MIT License |
| `.gitignore` | Git ignore rules |

### Source Files (`src/mcp/`)

| File | Purpose | Lines |
|------|---------|-------|
| `core/model_manager.py` | Main orchestration | ~485 |
| `core/model_selector.py` | Model selection | ~328 |
| `core/model_executor.py` | Model execution | ~421 |
| `core/config.py` | Configuration | ~312 |
| `api/huggingface_client.py` | HF API client | ~357 |
| `processors/input_processor.py` | Input processing | ~286 |
| `processors/output_processor.py` | Output processing | ~402 |
| `processors/universal_processor.py` | Universal processor | ~533 |
| `server/mcp_server.py` | MCP server | ~400+ |
| `utils/exceptions.py` | Custom exceptions | ~134 |
| `utils/logger.py` | Logging system | ~156 |
| `utils/metrics.py` | Metrics tracking | ~268 |

### Test Files (`tests/`)

| File | Purpose | Tests |
|------|---------|-------|
| `run_all_tests.py` | Test runner | All |
| `test_model_manager.py` | ModelManager tests | 6+ |
| `test_model_selector.py` | ModelSelector tests | 8+ |
| `test_api_client.py` | API tests | 7+ |
| `test_mcp_server.py` | MCP tests | 10+ |
| `test_integration.py` | Integration tests | 8+ |
| `manual/test_gpu_setup.py` | GPU tests | Manual |
| `manual/quick_test.py` | Quick tests | Manual |

### Documentation Files (`docs/`)

| File | Lines | Purpose |
|------|-------|---------|
| `architecture.md` | 343 | System design |
| `api_reference.md` | 618 | API docs |
| `getting_started.md` | 400+ | Tutorial |
| `extending_to_new_tasks.md` | 640+ | Extension guide |
| `ENVIRONMENT_VARIABLES.md` | 454 | Env vars |
| `guides/MCP_SERVER_GUIDE.md` | 500+ | MCP guide |
| `guides/QUICK_START_GPU.md` | 300+ | GPU guide |

## 📊 Code Statistics

```
Total Files: 50+
Total Lines of Code: ~8,500
├── Core Code: ~5,000 lines
├── Tests: ~1,500 lines
├── Examples: ~1,200 lines
└── Documentation: ~800+ lines

Languages:
├── Python: 95%
├── YAML: 3%
└── Markdown: 2%

Test Coverage: 80%+
```

## 🎯 Module Dependencies

```
ModelManager
├── ModelSelector
│   └── HuggingFaceClient
├── ModelExecutor
│   ├── InputProcessor
│   ├── OutputProcessor
│   └── UniversalProcessor
├── Config
└── MetricsTracker

MCP Server
├── ModelManager (all above)
└── MCP SDK
```

## 📝 Naming Conventions

### Files
- Python files: `snake_case.py`
- Test files: `test_*.py`
- Config files: `lowercase.yaml/json`
- Docs: `UPPERCASE.md` or `Title_Case.md`

### Classes
- `PascalCase`: ModelManager, HuggingFaceClient

### Functions/Methods
- `snake_case`: load_model(), get_metrics()

### Variables
- `snake_case`: model_id, execution_time

### Constants
- `UPPER_SNAKE_CASE`: MAX_RETRIES, DEFAULT_TIMEOUT

## 🔒 Gitignore

Files excluded from version control:

```
__pycache__/
*.pyc
venv/
logs/
output/
metrics/
.cache/
*.tmp
.env
```

## 📈 Growth Path

### Adding New Features

1. **New Task Type**: Add to `processors/`
2. **New API Client**: Add to `api/`
3. **New Tool**: Add to `server/mcp_server.py`
4. **New Config**: Update `config.py`

### Adding Tests

1. **Unit Test**: Add to `tests/test_*.py`
2. **Integration Test**: Add to `tests/test_integration.py`
3. **Manual Test**: Add to `tests/manual/`

### Adding Documentation

1. **API Docs**: Update `docs/api_reference.md`
2. **Guide**: Add to `docs/guides/`
3. **Example**: Add to `examples/`

## 🎨 Design Principles

1. **Modularity**: Each module is independent
2. **Single Responsibility**: One purpose per class
3. **DRY**: Don't Repeat Yourself
4. **SOLID**: Object-oriented principles
5. **Clean Code**: Readable and maintainable

## 🚀 Quick Navigation

**Want to:**
- Add a feature? → `src/mcp/core/`
- Fix a bug? → Check `tests/` first
- Add a task? → `src/mcp/processors/universal_processor.py`
- Configure? → `config/default_config.yaml`
- Document? → `docs/`
- Test? → `tests/`

---

**Well-organized code is maintainable code!** 📚




