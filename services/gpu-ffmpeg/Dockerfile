FROM nvidia/cuda:12.9.1-devel-ubuntu24.04

# Install Python runtime and build deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    ca-certificates \
    git \
    pkg-config \
    yasm \
    nasm \
    autoconf \
    automake \
    libtool \
    cmake \
    build-essential \
    libnuma-dev \
    libssl-dev \
    libx264-dev \
    libx265-dev \
    && rm -rf /var/lib/apt/lists/*

# Build and install nv-codec-headers (use SDK 12.0 headers compatible with FFmpeg 6.1.x)
RUN set -eux; \
    git clone --branch sdk/12.0 https://github.com/FFmpeg/nv-codec-headers.git /tmp/nv-codec-headers; \
    make -C /tmp/nv-codec-headers install; \
    rm -rf /tmp/nv-codec-headers

# Build FFmpeg from source with CUDA/NVENC/NPP (includes tonemap_cuda)
RUN set -eux; \
    FF_VER="6.1.1"; \
    git clone --branch n"$FF_VER" https://github.com/FFmpeg/FFmpeg.git /tmp/ffmpeg; \
    cd /tmp/ffmpeg; \
    export PATH=/usr/local/cuda/bin:$PATH; \
    export PKG_CONFIG_PATH=/usr/local/lib/pkgconfig:/usr/lib/pkgconfig; \
    export CUDA_HOME=/usr/local/cuda; \
    ./configure \
        --prefix=/opt/ffmpeg \
        --pkg-config-flags="--static" \
        --enable-nonfree \
        --enable-cuda-nvcc \
        --enable-libnpp \
        --enable-nvenc \
        --enable-cuvid \
        --enable-cuda \
        --disable-encoder=hevc_nvenc \
        --disable-encoder=av1_nvenc \
        --disable-encoder=vp9_nvenc \
        --extra-cflags=-I/usr/local/cuda/include \
        --extra-ldflags=-L/usr/local/cuda/lib64 \
        --disable-debug \
        --disable-doc \
        --disable-static \
        --enable-shared; \
    make -j"$(nproc)"; \
    make install; \
    rm -rf /tmp/ffmpeg; \
    ln -sf /opt/ffmpeg/bin/ffmpeg /usr/local/bin/ffmpeg; \
    ln -sf /opt/ffmpeg/bin/ffprobe /usr/local/bin/ffprobe

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY app /app/app

ENV PYTHONPATH=/app

ENTRYPOINT ["python3"]
CMD ["-m", "app.worker"]
