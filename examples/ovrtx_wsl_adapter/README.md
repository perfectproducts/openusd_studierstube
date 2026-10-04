# OVRTX rendering for WSL2 containers

OVRTX cannot start inside Docker on WSL2: the NVIDIA driver exposes CUDA, DirectX and DirectML to WSL, but no
NVIDIA Vulkan/OpenGL driver. Containers that need OVRTX renders (for example the
[USD Content Agents](https://github.com/nvidia-omniverse/content-agents)) can instead use a renderer running natively
on Windows. `ovrtx_adapter.py` makes that native renderer look like the upstream `ovrtx-rendering-api` container.

```
Docker on WSL2                      Windows host
agents --POST /render--> adapter :8001 --POST /render, poll /jobs--> OVRTX render service :8000
        (host.docker.internal)
```

## Requirements

- An OVRTX render service on Windows with a job API (a single worker around NVIDIA
  [ovrtx](https://github.com/nvidia-omniverse/ovrtx)):

  | Endpoint | Purpose |
  | --- | --- |
  | `POST /render` | `{usd_path, camera_path, width, height, force_reload, priority}` -> `{job_id}` |
  | `GET /jobs/{id}` | `{status: queued \| running \| done \| failed \| cancelled, error}` |
  | `GET /jobs/{id}/result` | rendered PNG |
  | `DELETE /jobs/{id}` | cancel |
  | `GET /health` | `{status: "ok", worker_alive: true, ...}` |

- Python 3.10+ (standard library only). OpenUSD (`pxr`, e.g. `pip install usd-core`) is needed only when agents
  request frames other than 0 (animated cameras).

## Run

```powershell
python ovrtx_adapter.py --backend http://127.0.0.1:8000 --port 8001
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--backend` | `http://127.0.0.1:8000` | Render service base URL |
| `--host` / `--port` | `0.0.0.0` / `8001` | Listen address (must be reachable from WSL2) |
| `--work-dir` | `%TEMP%\ovrtx_adapter` | Payload cache and per-request work folders |
| `--job-timeout` | `1800` | Seconds per backend job |
| `--poll` | `0.5` | Job polling interval (s) |
| `--keep` | off | Keep per-request files for debugging |

Point the containers at the host:

```yaml
services:
  joint-agent-service:
    environment:
      - JA_RENDER_BACKEND=remote
      - RENDER_ENDPOINT=${RENDER_ENDPOINT:-http://host.docker.internal:8001}
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

For the Material and Physics agents, export `RENDER_ENDPOINT=http://host.docker.internal:8001` before deploying.

Check: `curl http://127.0.0.1:8001/health` should report `"status": "healthy"` and the backend health.

## What it does

1. Materialises the request's `url` (data URI, http(s), file URL or path; plain USD or zip/USDZ bundle) once per
   content hash, so repeated requests for the same scene keep one stable path and the backend loads it only once.
2. Bakes animated cameras into one layer of static per-frame cameras when `frame_range` is not 0.
3. Renders one backend job per (frame, camera), serialised under a lock, and returns
   `images[frame][camera]["images"]` as base64 PNGs; errors are reported in-band (`status: "exception"`).

## Limitations

- RGB only: requested sensors (depth, segmentation) come back empty, with a warning; `apply_background_mask` is ignored.
- Renders are serialised (the renderer is single-threaded); raise client timeouts for large assets.
- The payload cache is never pruned; clear the work directory now and then.
- The adapter and render service are plain Windows processes: restart them after a reboot.
