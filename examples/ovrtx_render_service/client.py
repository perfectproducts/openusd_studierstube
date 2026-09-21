"""Submit a render job, wait for it and save the PNG (standard library only).

    python client.py scene.usda /World/Camera -o out.png --url http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request


def call(method: str, url: str, body: dict | None = None) -> bytes:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def render(base: str, usd: str, camera: str, width: int, height: int, timeout: float = 900) -> bytes:
    job = json.loads(call("POST", f"{base}/render", {"usd_path": usd, "camera_path": camera,
                                                     "width": width, "height": height}))["job_id"]
    deadline = time.monotonic() + timeout
    while True:
        info = json.loads(call("GET", f"{base}/jobs/{job}"))
        if info["status"] in ("done", "failed", "cancelled"):
            break
        if time.monotonic() > deadline:
            call("DELETE", f"{base}/jobs/{job}")
            raise TimeoutError(f"job {job} still {info['status']} after {timeout}s")
        time.sleep(0.5)
    if info["status"] != "done":
        raise RuntimeError(f"job {job} {info['status']}: {info['error']}")
    return call("GET", f"{base}/jobs/{job}/result")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("usd")
    p.add_argument("camera")
    p.add_argument("-o", "--output", default="render.png")
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    args = p.parse_args()
    t0 = time.monotonic()
    png = render(args.url.rstrip("/"), args.usd, args.camera, args.width, args.height)
    with open(args.output, "wb") as fh:
        fh.write(png)
    print(f"wrote {args.output} ({len(png)} bytes) in {time.monotonic() - t0:.1f}s")


if __name__ == "__main__":
    main()
