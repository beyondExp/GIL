FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY . /app/gil_controls
ENV PYTHONPATH=/app/src:/app/gil_controls/src
EXPOSE 8766 6769
CMD ["python", "gil_controls/src/main.py"]
