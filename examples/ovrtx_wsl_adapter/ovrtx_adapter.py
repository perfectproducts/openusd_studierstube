#!/usr/bin/env python3
"""Content Agents OVRTX-rendering-API adapter for a job-based ovrtx Render Service.

The Content Agents (Material / Physics agents) and the `ovrtx-render-service`
skill stage speak the upstream `ovrtx-rendering-api` contract
(content-agents v0.5.2, apps/ovrtx_rendering_api/openapi.yaml):

    GET  /health  -> {status, gpu_initialized, renderer_initialized, ...}
    POST /render  {url, render_settings{camera_paths, frame_range, camera_parameters, sensors}}
                  -> {status, error, images[frame][camera]["images"] = base64 PNG}

A local "ovrtx Render Service" (e.g. http://127.0.0.1:8000) instead takes a USD
*file path* plus one camera prim, queues a job, and serves the PNG from
/jobs/{id}/result. This adapter translates between the two: it materializes the
request payload (data URI / http(s) / file URL, plain USD or zip/USDZ bundle) to a
local directory, submits one job per (frame, camera), polls, and returns the
V1 response. Frames other than the stage default are rendered by baking the
camera's local transform and lens attributes at that time code into a small
override layer (requires OpenUSD `pxr`; run with a Python that has it).

Run on the Windows host so the backend can read the files it writes:
    <venv-with-pxr>/python ovrtx_adapter.py --backend http://127.0.0.1:8000 --port 8001
Containers reach it through http://host.docker.internal:8001.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

log = logging.getLogger("ovrtx_adapter")

USD_SUFFIXES = (".usd", ".usda", ".usdc")
ROOT_NAMES = ("main", "scene", "stage")  # upstream bundle root priority
TERMINAL = {"done", "failed", "cancelled"}


class Backend:
    def __init__(self, base_url: str, timeout: float, poll: float) -> None:
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.poll = poll
        self.lock = threading.Lock()  # backend renders one job at a time anyway
        self.completed = 0
        self.loaded: str | None = None  # scene path the backend currently holds

    def _req(self, method: str, path: str, body: dict | None = None) -> tuple[int, bytes, str]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, resp.read(), resp.headers.get("content-type", "")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), exc.headers.get("content-type", "")

    def health(self) -> dict:
        try:
            code, raw, _ = self._req("GET", "/health")
            return json.loads(raw) if code == 200 else {"status": "error", "http": code}
        except Exception as exc:  # noqa: BLE001 - health must never raise
            return {"status": "error", "error": str(exc)}

    def render(self, usd_path: Path, camera: str, width: int, height: int) -> bytes:
        # The backend keeps the last scene open. Adapter-written files are immutable
        # (content-addressed cache / per-request frame layers), so reload only when the
        # scene path changes; a failed job forgets the path so the next one reloads.
        path = usd_path.as_posix()
        body = {"usd_path": path, "camera_path": camera, "width": width,
                "height": height, "force_reload": path != self.loaded, "priority": 10}
        self.loaded = None
        code, raw, _ = self._req("POST", "/render", body)
        if code >= 300:
            raise RuntimeError(f"backend /render HTTP {code}: {raw[:300]!r}")
        job = json.loads(raw)["job_id"]
        deadline = time.monotonic() + self.timeout
        while True:
            _, raw, _ = self._req("GET", f"/jobs/{job}")
            info = json.loads(raw)
            if info.get("status") in TERMINAL:
                break
            if time.monotonic() > deadline:
                self._req("DELETE", f"/jobs/{job}")
                raise TimeoutError(f"backend job {job} exceeded {self.timeout}s")
            time.sleep(self.poll)
        if info["status"] != "done":
            raise RuntimeError(f"backend job {job} {info['status']}: {info.get('error')}")
        code, png, ctype = self._req("GET", f"/jobs/{job}/result")
        if code != 200 or not png.startswith(b"\x89PNG"):
            raise RuntimeError(f"backend job {job} result HTTP {code} ({ctype})")
        self.completed += 1
        self.loaded = path
        return png


# --------------------------------------------------------------------------- payload
def fetch_payload(url: str, dest: Path) -> None:
    if url.startswith("data:"):
        head, _, payload = url.partition(",")
        if not payload:
            raise ValueError("malformed data URI")
        dest.write_bytes(base64.b64decode(payload) if ";base64" in head
                         else urllib.parse.unquote_to_bytes(payload))
        return
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme in ("http", "https"):
        with urllib.request.urlopen(url, timeout=300) as resp:
            dest.write_bytes(resp.read())
        return
    if parsed.scheme == "file":
        src = urllib.request.url2pathname(parsed.netloc + parsed.path)
        shutil.copyfile(src, dest)
        return
    if Path(url).exists():  # bare local path
        shutil.copyfile(url, dest)
        return
    raise ValueError(f"unsupported USD url scheme: {url[:60]}")


def pick_root(files: list[Path], archive_order: bool) -> Path:
    usd = [f for f in files if f.suffix.lower() in USD_SUFFIXES]
    if not usd:
        raise ValueError("bundle contains no USD layer")
    if archive_order:
        return usd[0]
    for name in ROOT_NAMES:
        for f in sorted(usd):
            if f.stem.lower() == name and len(f.parts) == min(len(u.parts) for u in usd):
                return f
    return sorted(usd, key=lambda f: (len(f.parts), str(f)))[0]


_cache_lock = threading.Lock()


def materialize_cached(url: str, cache_root: Path) -> Path:
    """Materialize a payload once per content hash.

    Agents resend the same multi-MB scene for every camera view; a stable path per
    payload lets the backend keep the scene loaded instead of reloading per request.
    """
    fd, name = tempfile.mkstemp(prefix="payload_", dir=cache_root)
    os.close(fd)  # Windows: an open handle would block the move below
    tmp = Path(name)
    try:
        fetch_payload(url, tmp)
        digest = hashlib.sha256(tmp.read_bytes()).hexdigest()[:20]
        entry = cache_root / digest
        with _cache_lock:
            marker = entry / ".root"
            if marker.exists():
                return Path(marker.read_text(encoding="utf-8"))
            entry.mkdir(parents=True, exist_ok=True)
            raw = entry / "input.usd"
            shutil.move(str(tmp), raw)
            root = materialize_file(url, raw, entry)
            marker.write_text(str(root), encoding="utf-8")
            return root
    finally:
        tmp.unlink(missing_ok=True)


def materialize_file(url: str, raw: Path, work: Path) -> Path:
    if not zipfile.is_zipfile(raw):
        return raw
    is_usdz = url.lower().split("?")[0].endswith(".usdz") or url.startswith("data:model/vnd.usdz")
    out = work / "bundle"
    with zipfile.ZipFile(raw) as zf:
        names = [i.filename for i in zf.infolist() if not i.is_dir()]
        for name in names:
            target = (out / name).resolve()
            if out.resolve() not in target.parents:
                raise ValueError(f"unsafe zip entry: {name}")
        zf.extractall(out)
    return pick_root([out / n for n in names], archive_order=is_usdz)


def bake_frames(root: Path, cameras: list[str], frames: range, work: Path) -> tuple[Path, dict]:
    """Write ONE layer holding a static sibling camera per (camera, frame).

    The backend only renders the stage default time and reloads the scene whenever the
    file changes, so a layer per frame costs a full scene load per image. Baking every
    frame into sibling cameras of a single layer lets all views render from one load.
    Returns the layer path and {(frame, camera): baked_camera_path}.
    """
    from pxr import Sdf, Usd, UsdGeom

    stage = Usd.Stage.Open(str(root))
    layer = Sdf.Layer.CreateNew(str(work / "frames.usda"))
    layer.subLayerPaths.append(root.as_posix())
    over = Usd.Stage.Open(layer)
    mapping = {}
    for cam_path in cameras:
        prim = stage.GetPrimAtPath(cam_path)
        if not prim:
            continue
        schema_attrs = UsdGeom.Camera(prim).GetSchemaAttributeNames()
        for frame in frames:
            baked = f"{cam_path}_adapterF{frame}"
            cam = UsdGeom.Camera.Define(over, baked)
            cam.AddTransformOp().Set(UsdGeom.Xformable(prim).GetLocalTransformation(frame))
            for attr in schema_attrs:
                a = prim.GetAttribute(attr)
                if a and a.HasAuthoredValue() and not attr.startswith("xformOp"):
                    cam.GetPrim().CreateAttribute(attr, a.GetTypeName()).Set(a.Get(frame))
            mapping[(frame, cam_path)] = baked
    layer.Save()
    return Path(layer.realPath), mapping


# --------------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    backend: Backend
    work_root: Path
    cache_root: Path
    keep: bool

    def log_message(self, fmt, *args):  # route through logging
        log.info("%s - %s", self.address_string(), fmt % args)

    def _json(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/") not in ("/health", ""):
            return self._json(404, {"detail": "Not Found"})
        h = self.backend.health()
        ok = h.get("status") == "ok" and h.get("worker_alive", False)
        self._json(200, {
            "status": "healthy" if ok else "unhealthy", "service": "ovrtx-rendering-api",
            "version": "0.1.0-adapter", "renderer": "ovrtx", "gpu_initialized": ok,
            "renderer_initialized": ok, "daemon_running": ok, "daemon_pid": None,
            "daemon_completed_renders": self.backend.completed, "backend": self.backend.base,
            "backend_health": h,
        })

    def do_POST(self):  # noqa: N802
        if self.path.rstrip("/") != "/render":
            return self._json(404, {"detail": "Not Found"})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        except (ValueError, json.JSONDecodeError) as exc:
            return self._json(422, {"detail": f"invalid JSON: {exc}"})
        self._json(200, self.render(req))

    def render(self, req: dict) -> dict:
        settings = req.get("render_settings") or {}
        cameras = settings.get("camera_paths") or ["/Camera"]
        fr = settings.get("frame_range") or {}
        start, end = int(fr.get("start", 0)), int(fr.get("end", fr.get("start", 0)))
        cp = settings.get("camera_parameters") or {}
        width, height = int(cp.get("width", 1024)), int(cp.get("height", 1024))
        sensors = settings.get("sensors") or []
        warnings = []
        if sensors:
            warnings.append(f"sensors not supported by backend, returned empty: {sensors}")
        if settings.get("apply_background_mask"):
            warnings.append("apply_background_mask ignored by adapter")
        work = Path(tempfile.mkdtemp(prefix="render_", dir=self.work_root))
        t0 = time.monotonic()
        try:
            root = materialize_cached(req["url"], self.cache_root)
            images: dict[str, dict[str, dict[str, str]]] = {}
            frames = range(start, end + 1)
            if start == end == 0:
                layer, baked = root, {(0, c): c for c in cameras}
            else:
                layer, baked = bake_frames(root, cameras, frames, work)
            with self.backend.lock:
                for frame in frames:
                    for cam in cameras:
                        png = self.backend.render(layer, baked.get((frame, cam), cam), width, height)
                        entry = {"images": base64.b64encode(png).decode()}
                        entry.update({s: "" for s in sensors})
                        images.setdefault(str(frame), {})[cam] = entry
            log.info("rendered %d frame(s) x %d camera(s) in %.1fs", end - start + 1, len(cameras),
                     time.monotonic() - t0)
            out = {"status": "success", "error": None, "images": images}
            if warnings:
                out["warnings"] = warnings
            return out
        except Exception as exc:  # noqa: BLE001 - contract reports errors in-band
            log.exception("render failed")
            return {"status": "exception", "error": f"{type(exc).__name__}: {exc}", "images": {}}
        finally:
            if not self.keep:
                shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--backend", default="http://127.0.0.1:8000")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8001)
    p.add_argument("--work-dir", default=str(Path(tempfile.gettempdir()) / "ovrtx_adapter"))
    p.add_argument("--job-timeout", type=float, default=1800)
    p.add_argument("--poll", type=float, default=0.5)
    p.add_argument("--keep", action="store_true", help="keep materialized payloads for debugging")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    Handler.backend = Backend(args.backend, args.job_timeout, args.poll)
    Handler.work_root = Path(args.work_dir)
    Handler.work_root.mkdir(parents=True, exist_ok=True)
    Handler.cache_root = Handler.work_root / "cache"
    Handler.cache_root.mkdir(exist_ok=True)
    Handler.keep = args.keep
    log.info("adapter on %s:%d -> backend %s", args.host, args.port, args.backend)
    AdapterServer((args.host, args.port), Handler).serve_forever()


class AdapterServer(ThreadingHTTPServer):
    # Agents (e.g. the Joint Agent) fire dozens of concurrent /render calls; the stdlib
    # default listen backlog of 5 refuses the rest. Renders are serialized by Backend.lock,
    # so extra requests simply wait their turn.
    request_queue_size = 512
    daemon_threads = True


if __name__ == "__main__":
    main()
