# Chromecast Video Converter

GPU-only transcoding stack that keeps bound media libraries Chromecast Gen 2/3
compatible through an HTTP-orchestrated queue, GPU ffmpeg workers, and a
watchdog-powered file monitor.

- For setup, configuration, and API walkthroughs see `docs/README.md`.
- For the container model and data flow see `docs/architecture/README.md`.

## ⚠️ Security model: trusted network only

The dashboard and API have **no authentication**. Anyone who can reach port
9000 can manage the queue, change configuration, and trigger deletion of
original media files. The stack is designed for a trusted home LAN:

- **Never expose port 9000 to the internet** (no port forwarding, no public
  reverse proxy without auth).
- Prefer binding to a specific interface in `docker-compose.yml`, e.g.
  `127.0.0.1:9000:9000` (localhost only) or `192.168.1.10:9000:9000`.
- If you need remote access, put an authenticating reverse proxy (Caddy,
  nginx + basic auth, Authelia, Tailscale) in front.

See "Security & network exposure" in `docs/user/02_configuration.md` for
details, including why destructive endpoints are gated worker-side.

## Prebuilt images (GHCR)

Every push to `main` builds and publishes public images to the GitHub
Container Registry — no `docker login` needed to pull:

```
ghcr.io/magter/chromecast-video-converter/orchestrator:latest
ghcr.io/magter/chromecast-video-converter/folder-watcher:latest
ghcr.io/magter/chromecast-video-converter/gpu-ffmpeg:latest
```

Besides `latest`, each image is also tagged with the commit (`sha-<short>`)
and, for releases, the version tag (`v*`).

The CUDA/NPP-enabled FFmpeg build (with libx264 and libzimg for HDR
tonemapping) lives in a separate base image,
`ghcr.io/magter/chromecast-video-converter/ffmpeg-cuda`, which is only
rebuilt when `services/gpu-ffmpeg/Dockerfile.ffmpeg` or the patches change.
The `gpu-ffmpeg` worker image layers the Python app on top of it, so code
changes rebuild in seconds instead of recompiling FFmpeg (~15 minutes).
Local `docker compose build` pulls the prebuilt base too; to compile the
base yourself run
`docker build -f services/gpu-ffmpeg/Dockerfile.ffmpeg services/gpu-ffmpeg`.

### Running from GHCR instead of building locally

Create a `docker-compose.override.yml` next to `docker-compose.yml`:

```yaml
services:
  orchestrator:
    image: ghcr.io/magter/chromecast-video-converter/orchestrator:latest
  folder-watcher:
    image: ghcr.io/magter/chromecast-video-converter/folder-watcher:latest
  gpu-ffmpeg:
    image: ghcr.io/magter/chromecast-video-converter/gpu-ffmpeg:latest
```

Then start the stack as usual:

```bash
docker compose pull
docker compose up -d
```

To go back to locally built images, remove the override file and run
`docker compose up -d --build`. Images can also be built and pushed manually
via the **Build and publish Docker images** workflow (`workflow_dispatch`) in
GitHub Actions.

> **Note:** the `gpu-ffmpeg` container still needs an NVIDIA GPU with the
> NVIDIA Container Toolkit at *runtime* (NVENC/NVDEC); only the image build
> itself is GPU-free. CUDA 12.x images are used deliberately so Maxwell/Pascal
> generation GPUs keep working.
