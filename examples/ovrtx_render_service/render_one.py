"""Render one PNG from a USD file with ovrtx - no service, no queue.

    python render_one.py scene.usda /World/Camera -o out.png --width 1280 --height 720

The camera must already exist in the scene. The script composes a small root layer
that sublayers the scene and adds a RenderProduct bound to that camera (plus a dome
light, so geometry without lights is still visible), renders a few warm-up frames and
saves the product's LdrColor output.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import ovrtx
import ovstage
from PIL import Image

PRODUCT = "/RenderSvc/Product"
WARMUP_FRAMES = 40  # let path tracing converge and textures stream in


def asset_path(usd: str) -> str:
    return usd if "://" in usd else Path(usd).resolve().as_posix()


def scene_layer(usd: str) -> str:
    """Root layer: the scene as a sublayer, plus a dome light."""
    return f"""#usda 1.0
(
    subLayers = [@{asset_path(usd)}@]
)

def DomeLight "RenderSvcSky"
{{
    float inputs:intensity = 1000
}}
"""


def product_layer(camera: str, width: int, height: int) -> str:
    """A RenderProduct bound to an existing camera, writing LdrColor."""
    return f"""#usda 1.0

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


def capture(renderer: ovrtx.Renderer, ordinal: int, warmup: int = WARMUP_FRAMES,
            cancelled=lambda: False) -> np.ndarray | None:
    """Step the renderer and return the LdrColor pixels as an HxWx4 uint8 array.

    Returns None if ``cancelled()`` turns true during warm-up, the only safe point to stop.
    """
    for _ in range(warmup):
        if cancelled():
            return None
        renderer.step(render_products={PRODUCT}, delta_time=1 / 60, ordinal=ordinal)
    products = renderer.step(render_products={PRODUCT}, delta_time=1 / 60, ordinal=ordinal)
    for product in products.values():
        for frame in product.frames:
            var = frame.render_vars[f"{PRODUCT}/LdrColor"].map(device=ovrtx.Device.CPU)
            pixels = np.from_dlpack(var).copy()  # copy before unmapping the GPU buffer
            var.unmap()
            return pixels
    raise RuntimeError("renderer produced no frame")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("usd")
    p.add_argument("camera", help="absolute prim path of a camera in the scene")
    p.add_argument("-o", "--output", default="render.png")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    args = p.parse_args()

    renderer = ovrtx.Renderer()  # first run compiles and caches shaders: be patient
    stage = ovstage.Stage("render-one")
    renderer.attach_ovstage(stage)

    # Scene and product in one layer; publish it under ordinal 1 and seal that ordinal.
    layer = scene_layer(args.usd) + product_layer(args.camera, args.width, args.height).replace("#usda 1.0\n", "")
    ovstage.population.open_usd_from_string(stage, layer, ordinal=1)
    stage.advance_write_floor(1)

    Image.fromarray(capture(renderer, ordinal=1)).save(args.output)
    print(f"wrote {args.output}")

    renderer.detach_ovstage()
    stage.destroy()
    renderer.destroy()


if __name__ == "__main__":
    main()
