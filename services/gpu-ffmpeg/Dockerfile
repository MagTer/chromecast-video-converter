FROM jrottenberg/ffmpeg:7.1-nvidia2404

# Install Python runtime for the worker
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY app /app/app

ENV PYTHONPATH=/app

ENTRYPOINT ["python3"]
CMD ["-m", "app.worker"]
