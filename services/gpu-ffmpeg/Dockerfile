FROM nvidia/cuda:12.9.0-runtime-ubuntu22.04

RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        ffmpeg \
        bash \
        python3 \
        python3-pip \
        && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY services/gpu-ffmpeg/requirements.txt ./requirements.txt
RUN python3 -m pip install --no-cache-dir -r requirements.txt

COPY services/gpu-ffmpeg/worker.py ./worker.py
COPY services/gpu-ffmpeg/entrypoint.sh ./entrypoint.sh
COPY services/orchestrator/alembic.ini ./alembic.ini
COPY services/orchestrator/alembic ./alembic
RUN chmod +x ./entrypoint.sh

ENTRYPOINT ["./entrypoint.sh"]
