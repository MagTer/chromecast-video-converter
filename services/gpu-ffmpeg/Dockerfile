FROM jellyfin/jellyfin:latest

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/usr/lib/jellyfin-ffmpeg:${PATH}"

# Install Python runtime so the worker can run alongside Jellyfin's FFmpeg stack
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
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
