# Contributing to MCP

Thank you for your interest in contributing to the Dynamic AI Model Control Panel (MCP)! This document provides guidelines and instructions for contributing.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Contribution Workflow](#contribution-workflow)
- [Coding Standards](#coding-standards)
- [Testing](#testing)
- [Documentation](#documentation)
- [Pull Request Process](#pull-request-process)

## Code of Conduct

### Our Pledge

We are committed to providing a welcoming and inspiring community for all. Please be respectful and constructive in all interactions.

### Our Standards

- Use welcoming and inclusive language
- Be respectful of differing viewpoints
- Accept constructive criticism gracefully
- Focus on what is best for the community
- Show empathy towards other community members

## Getting Started

### Prerequisites

- Python 3.8 or higher
- Git
- Basic understanding of object-oriented Python
- Familiarity with machine learning concepts (helpful but not required)

### Development Setup

1. **Fork the repository**

```bash
# Visit https://github.com/yourusername/mcp and click "Fork"
```

2. **Clone your fork**

```bash
git clone https://github.com/YOUR_USERNAME/mcp.git
cd mcp
```

3. **Set up upstream remote**

```bash
git remote add upstream https://github.com/original/mcp.git
```

4. **Create a virtual environment**

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

5. **Install dependencies**

```bash
# Install package in development mode
pip install -e .

# Install development dependencies
pip install pytest pytest-cov black flake8 mypy
```

6. **Verify installation**

```bash
python -m pytest tests/
```

## Contribution Workflow

### 1. Find or Create an Issue

- Check existing issues before creating a new one
- For bug reports, include steps to reproduce
- For feature requests, explain the use case and benefits

### 2. Create a Branch

```bash
# Update your fork
git checkout main
git pull upstream main

# Create a feature branch
git checkout -b feature/your-feature-name
# or
git checkout -b fix/your-bug-fix
```

### 3. Make Your Changes

- Write clean, readable code
- Follow the coding standards (see below)
- Add tests for new functionality
- Update documentation as needed

### 4. Test Your Changes

```bash
# Run tests
python -m pytest tests/

# Check code coverage
python -m pytest --cov=src/mcp tests/

# Run linter
flake8 src/ tests/

# Format code
black src/ tests/
```

### 5. Commit Your Changes

```bash
# Stage your changes
git add .

# Commit with a descriptive message
git commit -m "Add feature: brief description"
```

**Commit Message Guidelines:**
- Use present tense ("Add feature" not "Added feature")
- Use imperative mood ("Move cursor to..." not "Moves cursor to...")
- Limit first line to 72 characters
- Reference issues and pull requests when relevant

Examples:
```
Add model caching for improved performance (#123)
Fix memory leak in ModelExecutor (#456)
Update documentation for Configuration class
```

### 6. Push and Create Pull Request

```bash
# Push to your fork
git push origin feature/your-feature-name
```

Then create a Pull Request on GitHub.

## Coding Standards

### Python Style Guide

We follow [PEP 8](https://www.python.org/dev/peps/pep-0008/) with some modifications:

- Line length: 100 characters (not 80)
- Use double quotes for strings
- Use type hints where applicable

### Code Formatting

Use `black` for automatic formatting:

```bash
black src/ tests/
```

### Naming Conventions

- **Classes**: PascalCase (`ModelManager`, `HuggingFaceClient`)
- **Functions/Methods**: snake_case (`load_model`, `get_statistics`)
- **Constants**: UPPER_SNAKE_CASE (`MAX_RETRIES`, `DEFAULT_TIMEOUT`)
- **Private methods**: _leading_underscore (`_make_request`, `_validate`)

### Type Hints

Use type hints for function signatures:

```python
def process_image(
    image: Union[str, Path, Image.Image],
    resize: bool = True
) -> np.ndarray:
    """Process an image."""
    pass
```

### Docstrings

Use Google-style docstrings:

```python
def select_model(self, task: str, criteria: Optional[SelectionCriteria] = None) -> ModelInfo:
    """
    Select the best model for a given task.
    
    Args:
        task: Task type (e.g., 'object-detection')
        criteria: Selection criteria (uses default if None)
    
    Returns:
        ModelInfo for the selected model
    
    Raises:
        ModelNotFoundError: If no suitable model is found
    
    Example:
        >>> selector = ModelSelector(client)
        >>> model = selector.select_model("object-detection")
    """
    pass
```

### Error Handling

- Use custom exceptions from `utils.exceptions`
- Always include error context in exception messages
- Log errors before raising exceptions

```python
from ..utils.exceptions import ModelLoadError
from ..utils.logger import get_logger

logger = get_logger(__name__)

try:
    # Some operation
    pass
except Exception as e:
    logger.error(f"Failed to load model: {e}")
    raise ModelLoadError(model_id=model_id, reason=str(e))
```

## Testing

### Writing Tests

- Place tests in the `tests/` directory
- Name test files as `test_*.py`
- Name test functions as `test_*`
- Use descriptive test names

```python
def test_model_selector_filters_by_downloads():
    """Test that ModelSelector correctly filters models by download count."""
    # Arrange
    models = [...]
    criteria = SelectionCriteria(min_downloads=5000)
    
    # Act
    filtered = selector._apply_filters(models, criteria)
    
    # Assert
    assert all(m.downloads >= 5000 for m in filtered)
```

### Running Tests

```bash
# Run all tests
pytest tests/

# Run specific test file
pytest tests/test_model_manager.py

# Run specific test
pytest tests/test_model_manager.py::TestModelManager::test_initialization

# Run with coverage
pytest --cov=src/mcp tests/

# Generate HTML coverage report
pytest --cov=src/mcp --cov-report=html tests/
```

### Test Coverage

- Aim for >80% code coverage
- Focus on critical paths and edge cases
- Test error conditions

## Documentation

### Code Documentation

- All public classes and methods must have docstrings
- Include usage examples in docstrings where helpful
- Keep docstrings up-to-date with code changes

### User Documentation

When adding new features, update:

- `README.md` - High-level overview
- `docs/getting_started.md` - Tutorial content
- `docs/api_reference.md` - API documentation
- `docs/architecture.md` - Architecture diagrams (if applicable)

### Examples

Add usage examples to the `examples/` directory:

```python
"""
Example: Using the New Feature

This example demonstrates...
"""

from src.mcp import ModelManager

# Example code here
```

## Pull Request Process

### Before Submitting

- [ ] Code follows style guidelines
- [ ] All tests pass
- [ ] New tests added for new features
- [ ] Documentation updated
- [ ] No linter errors
- [ ] Commit messages are clear

### PR Description Template

```markdown
## Description
Brief description of changes

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Related Issues
Fixes #(issue number)

## Testing
Describe the tests you ran

## Checklist
- [ ] Code follows style guidelines
- [ ] Self-review completed
- [ ] Comments added where needed
- [ ] Documentation updated
- [ ] No new warnings
- [ ] Tests added
- [ ] All tests pass
```

### Review Process

1. Submit your PR
2. Address reviewer feedback
3. Make requested changes
4. Update PR with new commits
5. Once approved, maintainer will merge

### After Your PR is Merged

1. Delete your branch
2. Update your local repository
3. Celebrate! 🎉

```bash
git checkout main
git pull upstream main
git branch -d feature/your-feature-name
```

## Project Structure

Understanding the codebase:

```
src/mcp/
├── core/           # Core business logic
├── api/            # External API integrations
├── processors/     # Data processing
└── utils/          # Utilities and helpers

tests/              # Test files
examples/           # Usage examples
docs/               # Documentation
config/             # Configuration files
```

## Areas for Contribution

### Good First Issues

Look for issues labeled `good-first-issue`:
- Documentation improvements
- Adding examples
- Simple bug fixes
- Test coverage improvements

### High Priority

- Performance optimizations
- Additional model sources
- Enhanced error handling
- Better visualization options

### Feature Requests

- Multi-GPU support
- Model quantization
- Web API interface
- Additional tasks support

## Questions?

- **General questions**: Open a discussion on GitHub
- **Bug reports**: Create an issue with the bug template
- **Feature ideas**: Create an issue with the feature template
- **Security issues**: Email security@example.com (do not create public issues)

## Recognition

Contributors will be:
- Listed in CONTRIBUTORS.md
- Acknowledged in release notes
- Given credit for their contributions

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

---

Thank you for contributing to MCP! Your efforts help make AI more accessible to everyone.

