"""Render a scene with a video texture with ovrtx - a still, an MP4 or a GIF.

    python render_video.py video_studio.usda /World/Studio/Camera -o still.png
    python render_video.py video_studio.usda /World/Studio/Camera -o studio.mp4 --fps 30
    python render_video.py video_studio.usda /World/Studio/Camera -o studio.gif --fps 25 --end 3

A video texture is an .mp4 bound to a material like any image texture. Two things
make it work outside Kit:

* GStreamer plugins. ovrtx decodes video through GStreamer and loads the plugins only
  from its own package (ovrtx/bin/plugins/gpu.foundation/gstreamer-1.0). The Linux
  wheels ship them, the Windows wheel does not, and without them the first render
  step blocks forever. ``--gstreamer`` (or GSTREAMER_PLUGINS) names Kit's copy, which
  is copied into ovrtx before the renderer starts:
  <kit>/extscache/omni.gpu_foundation-*/bin/deps/gstreamer-1.0

* The renderer clock. Video plays on ovrtx's own clock, which every step advances by
  its delta_time, not on USD time. Left alone, the warm-up steps of each frame run the
  video ahead of the animation. So the clock is reset to the first frame's time and
  moved by exactly the gap between frames, holding still while each frame converges.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import Iterator

import numpy as np
import ovrtx
import ovstage
from PIL import Image

PRODUCT = "/RenderSvc/Product"
WARMUP_FRAMES = 40  # first frame: let path tracing converge and textures stream in
FRAME_WARMUP = 8    # every later frame: only the moved parts need to settle


def ensure_gstreamer(source: Path | None) -> None:
    """Copy the GStreamer plugins into ovrtx unless it already has them."""
    target = Path(ovrtx.__file__).parent / "bin" / "plugins" / "gpu.foundation" / "gstreamer-1.0"
    if target.is_dir():
        return
    if source is None or not source.is_dir():
        sys.exit(f"ovrtx has no GStreamer plugins at {target}: a video texture would hang the "
                 "renderer. Pass --gstreamer <kit>/extscache/omni.gpu_foundation-*/bin/deps/gstreamer-1.0")
    if sys.platform == "win32" and len(str(target)) > 225:
        print(f"warning: {target} is near the 260-character path limit; Windows may not load "
              "the plugins. Move the venv to a shorter path if rendering hangs.")
    # Copy beside the target, then rename: a copy cut short never looks installed.
    partial = target.with_name("gstreamer-1.0.partial")
    shutil.rmtree(partial, ignore_errors=True)
    shutil.copytree(source, partial)
    partial.rename(target)
    print(f"installed GStreamer plugins into {target}")


def render_layer(usd: str, camera: str, width: int, height: int) -> str:
    """The scene as a sublayer plus a RenderProduct bound to its camera.

    A layer opened from a string has no folder of its own, so the path is absolute.
    """
    return f"""#usda 1.0
(
    subLayers = [@{Path(usd).resolve().as_posix()}@]
)

def Scope "RenderSvc"
{{
    def RenderProduct "Product"
    {{
        rel camera = <{camera}>
        int2 resolution = ({width}, {height})
        rel orderedVars = <LdrColor>

        def RenderVar "LdrColor"
        {{
            string sourceName = "LdrColor"
        }}
    }}
}}
"""


class Scene:
    """The ovstage the renderer draws from; every change is published under a new ordinal."""

    def __init__(self, renderer: ovrtx.Renderer, layer: str) -> None:
        self.stage = ovstage.Stage("video-textures")
        renderer.attach_ovstage(self.stage)
        self.ordinal = 1
        ovstage.population.open_usd_from_string(self.stage, layer, ordinal=self.ordinal)
        self._seal()

    def set_time(self, seconds: float) -> int:
        self.ordinal += 1
        ovstage.population.update_from_usd_time(self.stage, self.ordinal, seconds)
        self._seal()
        return self.ordinal

    def _seal(self) -> None:
        # A step naming an unsealed ordinal fails with "exceeds ovstage write floor".
        self.stage.advance_write_floor(self.ordinal).wait()


def capture(renderer: ovrtx.Renderer, ordinal: int, warmup: int, advance: float) -> np.ndarray:
    """Move the clock by ``advance`` on the first step, hold it for the rest, return LdrColor."""
    deltas = [advance] + [0.0] * warmup
    for delta in deltas:
        products = renderer.step(render_products={PRODUCT}, delta_time=delta, ordinal=ordinal)
    for product in products.values():
        for frame in product.frames:
            var = frame.render_vars[f"{PRODUCT}/LdrColor"].map(device=ovrtx.Device.CPU)
            pixels = np.from_dlpack(var).copy()  # copy before unmapping the GPU buffer
            var.unmap()
            return pixels
    raise RuntimeError("renderer produced no frame")


def frames(renderer: ovrtx.Renderer, scene: Scene, times: list[float]) -> Iterator[np.ndarray]:
    """The product at each of ``times`` (seconds), with the renderer clock on stage time."""
    clock = max(times[0], 0.0)  # the clock cannot go below zero
    renderer.reset(clock)
    for index, seconds in enumerate(times):
        ordinal = scene.set_time(seconds)
        advance = max(seconds - clock, 0.0)
        clock += advance
        yield capture(renderer, ordinal, WARMUP_FRAMES if index == 0 else FRAME_WARMUP, advance)


def write_mp4(path: Path, images: Iterator[np.ndarray], fps: float) -> int:
    """H.264, yuv420p so every player takes it (yuv420p needs even width and height)."""
    import imageio_ffmpeg

    writer = None
    count = 0
    for pixels in images:
        if writer is None:
            height, width = pixels.shape[:2]
            writer = imageio_ffmpeg.write_frames(
                str(path), (width, height), fps=fps, codec="libx264", pix_fmt_in="rgb24",
                pix_fmt_out="yuv420p", macro_block_size=1, output_params=["-movflags", "+faststart"])
            writer.send(None)
        writer.send(np.ascontiguousarray(pixels[..., :3]))
        count += 1
    if writer is not None:
        writer.close()
    return count


def write_gif(path: Path, images: Iterator[np.ndarray], fps: float) -> int:
    """GIF delays are whole centiseconds: 25 or 50 fps play true, 30 fps runs a little fast."""
    pictures = [Image.fromarray(pixels) for pixels in images]
    pictures[0].save(path, save_all=True, append_images=pictures[1:], duration=1000 / fps, loop=0)
    return len(pictures)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("usd")
    p.add_argument("camera", help="absolute prim path of a camera in the scene")
    p.add_argument("-o", "--output", default="studio.mp4", help=".png renders a still, .mp4 or .gif an animation")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--start", type=float, default=0.0, help="first frame (seconds); for a .png, its time")
    p.add_argument("--end", type=float, default=10.0, help="last frame (seconds); the demo clip is 10 s")
    p.add_argument("--fps", type=float, default=30.0)
    p.add_argument("--gstreamer", type=Path, default=os.environ.get("GSTREAMER_PLUGINS"),
                   help="Kit's gstreamer-1.0 plugin folder, copied into ovrtx if it has none")
    args = p.parse_args()

    out = Path(args.output)
    if out.suffix.lower() == ".png":
        times = [args.start]
    else:
        count = int(round((args.end - args.start) * args.fps)) + 1
        times = [args.start + i / args.fps for i in range(count)]

    ensure_gstreamer(args.gstreamer)
    renderer = ovrtx.Renderer()  # first run compiles and caches shaders: be patient
    scene = Scene(renderer, render_layer(args.usd, args.camera, args.width, args.height))

    images = frames(renderer, scene, times)
    if out.suffix.lower() == ".png":
        Image.fromarray(next(images)).save(out)
        written = 1
    elif out.suffix.lower() == ".gif":
        written = write_gif(out, images, args.fps)
    else:
        written = write_mp4(out, images, args.fps)
    print(f"wrote {out} ({written} frame{'s' if written != 1 else ''})")

    renderer.detach_ovstage()
    scene.stage.destroy()
    renderer.destroy()


if __name__ == "__main__":
    main()
