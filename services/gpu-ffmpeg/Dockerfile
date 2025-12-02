# Stage 1: Build FFmpeg with CUDA/NPP support on CUDA 12.9.1
FROM nvidia/cuda:12.9.1-devel-ubuntu24.04 AS ffmpeg-builder

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH=/usr/local/cuda/bin:${PATH}
ENV PKG_CONFIG_PATH=/usr/local/lib/pkgconfig

COPY patches/ffmpeg-disable-ptx.patch /tmp/ffmpeg-disable-ptx.patch

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    pkg-config \
    yasm \
    nasm \
    cmake \
    wget \
    curl \
    libtool \
    libc6-dev \
    libc6 \
    unzip \
    ca-certificates \
    cuda-libraries-dev-12-9 \
    cuda-cudart-dev-12-9 \
    cuda-nvcc-12-9 \
    libnpp-dev-12-9 \
    libx264-dev \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://git.videolan.org/git/ffmpeg/nv-codec-headers.git && \
    cd nv-codec-headers && \
    make && \
    make install

WORKDIR /tmp/ffmpeg_build
RUN wget -O ffmpeg.tar.bz2 https://ffmpeg.org/releases/ffmpeg-7.1.3.tar.bz2 && \
    tar xjvf ffmpeg.tar.bz2 && \
    cd ffmpeg-7.1.3 && \
    patch -p0 < /tmp/ffmpeg-disable-ptx.patch

WORKDIR /tmp/ffmpeg_build/ffmpeg-7.1.3
RUN set -eux; \
    NVCC_FLAGS="-gencode arch=compute_52,code=sm_52 -gencode arch=compute_60,code=sm_60 -gencode arch=compute_61,code=sm_61 -gencode arch=compute_70,code=sm_70 -gencode arch=compute_75,code=sm_75 -gencode arch=compute_80,code=sm_80 -gencode arch=compute_86,code=sm_86 -gencode arch=compute_89,code=sm_89 -gencode arch=compute_90,code=sm_90 -gencode arch=compute_90,code=compute_90 -O2 -allow-unsupported-compiler"; \
    ./configure \
        --prefix=/usr/local \
        --enable-gpl \
        --enable-nonfree \
        --enable-libx264 \
        --enable-cuda-nvcc \
        --enable-libnpp \
        --enable-cuvid \
        --enable-nvenc \
        --enable-nvdec \
        --extra-cflags="-I/usr/local/cuda/include" \
    --extra-ldflags="-L/usr/local/cuda/lib64 -Wl,-rpath,/usr/local/cuda/lib64" \
    --extra-libs="-lcudart" \
    --nvccflags="${NVCC_FLAGS}" \
    --disable-doc \
    --disable-static \
    --enable-shared \
    && make -j"$(nproc)" \
    && make install

# Stage 2: Runtime image for the gpu-ffmpeg worker
FROM nvidia/cuda:12.9.1-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH=/usr/local/cuda/bin:${PATH}
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    cuda-libraries-12-9 \
    libnpp-12-9 \
    python3 \
    python3-pip \
    ca-certificates \
    libx264-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ffmpeg-builder /usr/local/bin/ffmpeg /usr/local/bin/ffmpeg
COPY --from=ffmpeg-builder /usr/local/bin/ffprobe /usr/local/bin/ffprobe
COPY --from=ffmpeg-builder /usr/local/lib /usr/local/lib
COPY --from=ffmpeg-builder /usr/local/include /usr/local/include
RUN ldconfig

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt
COPY app /app/app

ENV PYTHONPATH=/app

ENTRYPOINT ["python3", "-m", "app.worker"]
