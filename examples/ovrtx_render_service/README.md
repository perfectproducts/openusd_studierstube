# Simple OVRTX render service (Windows)

A minimal HTTP render service around NVIDIA [ovrtx](https://github.com/nvidia-omniverse/ovrtx): clients queue a USD
file and a camera, poll the job, and download the PNG. One worker thread owns the renderer; a scene stays loaded
between jobs, so further views of the same OpenUSD scene only swap the render product.

![Design: clients, FastAPI, job store, render worker, output folder](images/design.png)

| File | What it is |
| --- | --- |
| `render_one.py` | Render one PNG with ovrtx, no service (the building blocks) |
| `render_service.py` | FastAPI service: job store, render worker, HTTP API |
| `client.py` | Submit, poll and download (standard library only) |
| `add_cameras.py` | Add a perspective, an orthographic top and an orthographic side camera to any OpenUSD scene |
| `pyproject.toml` | Pinned `ovrtx` / `ovstage` and the web dependencies |
| `images/` | Design diagram (PNG + Mermaid source) and sample renders |

## Run (PowerShell or cmd, not Git Bash)

```powershell
uv sync
uv run python render_service.py --port 8000 --output-dir _output
uv run python client.py C:\scenes\robot.usda /World/Camera -o out.png
```

No camera in the scene? `add_cameras.py` frames three on its bounding box (needs `usd-core`):

```powershell
uv run --with usd-core python add_cameras.py C:\scenes\robot.usd      # writes robot_cameras.usda
uv run python client.py C:\scenes\robot_cameras.usda /Cameras/Top -o top.png
```

Requirements: Windows with an NVIDIA RTX GPU and current driver, Python 3.10–3.13, `uv`. The OpenUSD scene must contain the
camera. The first start compiles shaders (a few minutes).

## API

| Endpoint | |
| --- | --- |
| `POST /render` | `{usd_path, camera_path, width, height, force_reload, priority}` → `{job_id}` |
| `GET /jobs/{id}` | `{status: queued \| running \| done \| failed \| cancelled, error, ...}` |
| `GET /jobs/{id}/result` | rendered PNG (409 until done) |
| `DELETE /jobs/{id}` | cancel (queued, or running during warm-up) |
| `GET /health` | `{status, worker_alive, renderer_ready, pending, running}` |

This is the job API expected by [`../ovrtx_wsl_adapter`](../ovrtx_wsl_adapter), which exposes it to Docker containers on
WSL2.

## Tested

![Perspective, orthographic top and orthographic side views from one scene load](images/service_views.png)


KUKA KR 270 (42 meshes), 1024 × 1024, warm shader cache: first view 1.5 s (scene load), next views 0.4–0.5 s; perspective and orthographic cameras alike.
Fresh environment: renderer creation about 3 min, first job about 40 s.
