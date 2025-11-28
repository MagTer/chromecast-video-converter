FROM nvidia/cuda:12.2.0-base-ubuntu22.04

# Install python and ffmpeg dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY app /app/app

ENV PYTHONPATH=/app

CMD ["python3", "-m", "app.worker"]
