FROM python:3.13-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir watchdog==6.0.0 httpx==0.28.1

# Copy application code
COPY app /app/app

# Set python path
ENV PYTHONPATH=/app

CMD ["python", "app/watcher.py"]
