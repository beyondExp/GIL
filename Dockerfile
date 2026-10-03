FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY profiles ./profiles
COPY gil_controls ./gil_controls
COPY tests ./tests
RUN pip install --no-cache-dir -e ".[dev]"
CMD ["python", "-m", "gil.gates"]
