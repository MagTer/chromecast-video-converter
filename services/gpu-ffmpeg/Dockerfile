FROM nvidia/cuda:13.0.2-devel-ubuntu24.04

# Install python, curl, and build deps for ffmpeg build
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    ca-certificates \
    curl \
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
    && rm -rf /var/lib/apt/lists/*

# Build and install nv-codec-headers (required for NVENC/NVDEC)
RUN set -eux; \
    git clone https://github.com/FFmpeg/nv-codec-headers.git /tmp/nv-codec-headers; \
    make -C /tmp/nv-codec-headers install; \
    rm -rf /tmp/nv-codec-headers

# Build FFmpeg from source with CUDA/NVENC/NPP (tonemap_cuda, scale_cuda)
RUN set -eux; \
    FF_VER="6.1.1"; \
    git clone --branch n"$FF_VER" https://github.com/FFmpeg/FFmpeg.git /tmp/ffmpeg; \
    cd /tmp/ffmpeg; \
    ./configure \
        --prefix=/opt/ffmpeg \
        --pkg-config-flags="--static" \
        --enable-nonfree \
        --enable-cuda-nvcc \
        --enable-libnpp \
        --enable-nvenc \
        --enable-cuvid \
        --enable-cuda \
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
RUN pip3 install --no-cache-dir -r requirements.txt

COPY app /app/app

ENV PYTHONPATH=/app

CMD ["python3", "-m", "app.worker"]
