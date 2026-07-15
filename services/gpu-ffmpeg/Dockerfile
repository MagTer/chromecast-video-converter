# GPU worker image: layers the Python app on top of the prebuilt FFmpeg base
# (see Dockerfile.ffmpeg). Code changes only rebuild these cheap layers; the
# FFmpeg compile lives in the ffmpeg-cuda base image.
ARG FFMPEG_IMAGE=ghcr.io/magter/chromecast-video-converter/ffmpeg-cuda:latest
FROM ${FFMPEG_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt
COPY app /app/app

ENV PYTHONPATH=/app

ENTRYPOINT ["python3", "-m", "app.worker"]
