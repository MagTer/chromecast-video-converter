FROM nvidia/cuda:13.0.2-base-ubuntu24.04

# Install python, curl, and build deps for ffmpeg runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    ca-certificates \
    curl \
    xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Install NVIDIA-provided FFmpeg build with CUDA filters (tonemap_cuda, scale_cuda, etc.)
RUN set -eux; \
    FF_VER="6.1.1"; \
    DIST="ubuntu2204"; \
    ARCHIVE="ffmpeg-${FF_VER}-${DIST}-x86_64-nvidia.tar.xz"; \
    URL="https://developer.download.nvidia.com/compute/redist/ffmpeg/${ARCHIVE}"; \
    curl -fL "$URL" -o /tmp/ffmpeg.tar.xz; \
    mkdir -p /opt/ffmpeg-nvidia; \
    tar -xJf /tmp/ffmpeg.tar.xz -C /opt/ffmpeg-nvidia --strip-components=1; \
    ln -sf /opt/ffmpeg-nvidia/bin/ffmpeg /usr/local/bin/ffmpeg; \
    ln -sf /opt/ffmpeg-nvidia/bin/ffprobe /usr/local/bin/ffprobe; \
    rm /tmp/ffmpeg.tar.xz

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY app /app/app

ENV PYTHONPATH=/app

CMD ["python3", "-m", "app.worker"]
