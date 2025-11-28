FROM python:3.10-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir watchdog httpx

# Copy application code
COPY app /app/app

# Set python path
ENV PYTHONPATH=/app

CMD ["python", "app/watcher.py"]
