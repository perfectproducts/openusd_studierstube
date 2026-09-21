"""A small HTTP render service around ovrtx.

    python render_service.py --port 8000 --output-dir _output

POST /render           {usd_path, camera_path, width, height, force_reload, priority} -> {job_id}
GET  /jobs/{id}        job status: queued | running | done | failed | cancelled
GET  /jobs/{id}/result the rendered PNG
DELETE /jobs/{id}      cancel a queued or running job
GET  /health           {status, worker_alive, pending, running}

ovrtx's Renderer is single-threaded and loading a scene is expensive, so one worker
thread owns the renderer and processes jobs one at a time. The HTTP layer never touches
the GPU. A scene stays loaded between jobs: a job for the same USD file only swaps the
RenderProduct (camera, resolution), which takes a fraction of a second instead of a
full reload.
"""
from __future__ import annotations

import argparse
import heapq
import itertools
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field

from render_one import asset_path, capture, product_layer, scene_layer

log = logging.getLogger("render_service")


# --------------------------------------------------------------------------- jobs
class RenderRequest(BaseModel):
    usd_path: str = Field(description="Path or URL of the USD scene")
    camera_path: str = Field(description="Absolute prim path of a camera in the scene")
    width: int = Field(1280, ge=16, le=8192)
    height: int = Field(720, ge=16, le=8192)
    force_reload: bool = Field(False, description="Reload the scene even if it is already loaded")
    priority: int = Field(0, description="Higher runs first; ties run in submission order")


@dataclass
class Job:
    request: RenderRequest
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "queued"
    error: str | None = None
    output: Path | None = None
    submitted: float = field(default_factory=time.time)
    finished: float | None = None
    cancel: threading.Event = field(default_factory=threading.Event)

    def public(self) -> dict:
        return {"job_id": self.id, "status": self.status, "error": self.error,
                "usd_path": self.request.usd_path, "camera_path": self.request.camera_path,
                "submitted": self.submitted, "finished": self.finished}


class JobStore:
    """Thread-safe jobs by id plus a priority queue of pending ones."""

    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self._queue: list[tuple[int, int, str]] = []
        self._seq = itertools.count()
        self._cv = threading.Condition()

    def submit(self, request: RenderRequest) -> Job:
        job = Job(request)
        with self._cv:
            self.jobs[job.id] = job
            heapq.heappush(self._queue, (-request.priority, next(self._seq), job.id))
            self._cv.notify()
        return job

    def next(self, timeout: float) -> Job | None:
        with self._cv:
            if not self._queue:
                self._cv.wait(timeout)
            while self._queue:
                job = self.jobs[heapq.heappop(self._queue)[2]]
                if job.status == "queued":
                    job.status = "running"
                    return job
        return None

    def finish(self, job: Job, status: str, error: str | None = None, output: Path | None = None) -> None:
        with self._cv:
            job.status, job.error, job.output, job.finished = status, error, output, time.time()

    def cancel(self, job: Job) -> None:
        with self._cv:
            job.cancel.set()
            if job.status == "queued":  # never reaches the worker
                job.status, job.finished = "cancelled", time.time()

    def counts(self) -> tuple[int, int]:
        with self._cv:
            states = [j.status for j in self.jobs.values()]
        return states.count("queued"), states.count("running")


# --------------------------------------------------------------------------- worker
class RenderWorker(threading.Thread):
    """Owns the ovrtx Renderer; every GPU call happens on this thread."""

    def __init__(self, store: JobStore, output_dir: Path) -> None:
        super().__init__(name="render-worker", daemon=True)
        self.store, self.output_dir = store, output_dir
        self.renderer = self.stage = None
        self.ordinal = 0
        self.scene: str | None = None  # asset path of the scene currently loaded
        self.product: int | None = None  # handle of the RenderProduct reference on top of it

    def run(self) -> None:
        import ovrtx
        import ovstage

        self.ovstage = ovstage
        log.info("creating renderer (the first run compiles shaders)")
        self.renderer = ovrtx.Renderer()
        self.stage = ovstage.Stage("render-service")
        self.renderer.attach_ovstage(self.stage)
        log.info("renderer ready")
        while True:
            job = self.store.next(timeout=1.0)
            if job:
                self.process(job)

    def publish(self) -> int:
        """Seal the edits made so far under a new ordinal; steps must name it."""
        self.ordinal += 1
        return self.ordinal

    def show(self, req: RenderRequest) -> int:
        ovstage = self.ovstage
        if req.force_reload or self.scene != asset_path(req.usd_path):
            if self.ordinal:
                ovstage.population.reset_usd(self.stage)  # otherwise the new scene merges with the old
            self.scene = self.product = None
            ordinal = self.publish()
            ovstage.population.open_usd_from_string(self.stage, scene_layer(req.usd_path), ordinal=ordinal)
            self.stage.advance_write_floor(ordinal)
            self.scene = asset_path(req.usd_path)
        # Swap the RenderProduct as a reference: publishing a product-only layer over the
        # scene would replace the scene instead of adding to it.
        if self.product is not None:
            ovstage.population.remove_usd(self.stage, self.product)
        self.product = ovstage.population.add_usd_reference_from_string(
            self.stage, product_layer(req.camera_path, req.width, req.height), "/")
        ordinal = self.publish()
        ovstage.population.apply_usd_changes(self.stage, ordinal=ordinal)
        self.stage.advance_write_floor(ordinal)
        return ordinal

    def process(self, job: Job) -> None:
        req = job.request
        t0 = time.monotonic()
        try:
            reused = not req.force_reload and self.scene == asset_path(req.usd_path)
            ordinal = self.show(req)
            self.renderer.reset()  # restart accumulation for this view
            pixels = capture(self.renderer, ordinal, cancelled=job.cancel.is_set)
            if pixels is None:
                self.store.finish(job, "cancelled")
                return
            out = self.output_dir / f"{job.id}.png"
            Image.fromarray(pixels).save(out)
            self.store.finish(job, "done", output=out)
            log.info("job %s done in %.1fs (%s scene)", job.id[:8], time.monotonic() - t0,
                     "reused" if reused else "loaded")
        except Exception as exc:  # noqa: BLE001 - reported to the client
            log.exception("job %s failed", job.id[:8])
            self.scene = None  # don't trust the stage after a failure; reload next time
            self.store.finish(job, "failed", error=f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- HTTP
def create_app(output_dir: Path) -> FastAPI:
    output_dir.mkdir(parents=True, exist_ok=True)
    store = JobStore()
    worker = RenderWorker(store, output_dir)
    worker.start()
    app = FastAPI(title="ovrtx render service")

    def get(job_id: str) -> Job:
        job = store.jobs.get(job_id)
        if not job:
            raise HTTPException(404, f"unknown job {job_id}")
        return job

    @app.get("/health")
    def health() -> dict:
        pending, running = store.counts()
        alive = worker.is_alive()
        return {"status": "ok" if alive else "error", "worker_alive": alive,
                "renderer_ready": worker.stage is not None, "pending": pending, "running": running,
                "output_dir": str(output_dir.resolve())}

    @app.post("/render", status_code=202)
    def render(req: RenderRequest) -> dict:
        return {"job_id": store.submit(req).id}

    @app.get("/jobs/{job_id}")
    def job_status(job_id: str) -> dict:
        return get(job_id).public()

    @app.delete("/jobs/{job_id}")
    def cancel(job_id: str) -> dict:
        job = get(job_id)
        store.cancel(job)
        return job.public()

    @app.get("/jobs/{job_id}/result")
    def result(job_id: str):
        job = get(job_id)
        if job.status != "done":
            raise HTTPException(409, f"job is {job.status}")
        return FileResponse(job.output, media_type="image/png")

    return app


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--output-dir", default="_output")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    uvicorn.run(create_app(Path(args.output_dir)), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
