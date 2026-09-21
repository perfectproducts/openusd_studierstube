"""Add a perspective, an orthographic top and an orthographic side camera to an OpenUSD scene.

    uv run --with usd-core python add_cameras.py robot.usd            # writes robot_cameras.usda

The new layer sublayers the scene and frames all three cameras on its bounding box, in the
scene's own units:
    /Cameras/Perspective   three-quarter view from the front right
    /Cameras/Top           orthographic, looking down the up axis
    /Cameras/Side          orthographic, looking along +Y (Z up) or -Z (Y up)
Orthographic apertures are given in tenths of a scene unit, as UsdGeom.Camera defines them.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

from pxr import Gf, Sdf, Usd, UsdGeom

MARGIN = 1.15  # framing margin around the bounding box


def define_camera(stage: Usd.Stage, path: str, eye: Gf.Vec3d, target: Gf.Vec3d, up: Gf.Vec3d,
                  ortho_extent: float | None, clip: tuple[float, float]) -> None:
    cam = UsdGeom.Camera.Define(stage, path)
    cam.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(eye, target, up).GetInverse())
    cam.CreateClippingRangeAttr(Gf.Vec2f(*clip))
    if ortho_extent is None:
        cam.CreateFocalLengthAttr(24.0)
        cam.CreateHorizontalApertureAttr(20.955)
        cam.CreateVerticalApertureAttr(20.955)
    else:
        cam.CreateProjectionAttr(UsdGeom.Tokens.orthographic)
        cam.CreateHorizontalApertureAttr(ortho_extent * 10.0)  # tenths of a scene unit
        cam.CreateVerticalApertureAttr(ortho_extent * 10.0)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("scene")
    p.add_argument("-o", "--output", help="default: <scene>_cameras.usda next to the scene")
    args = p.parse_args()

    src = Path(args.scene).resolve()
    out = Path(args.output).resolve() if args.output else src.with_name(f"{src.stem}_cameras.usda")
    scene = Usd.Stage.Open(str(src))
    box = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"]).ComputeWorldBound(
        scene.GetPseudoRoot()).ComputeAlignedRange()
    lo, hi = box.GetMin(), box.GetMax()
    center, size = (lo + hi) / 2, hi - lo
    radius = size.GetLength() / 2
    z_up = UsdGeom.GetStageUpAxis(scene) == UsdGeom.Tokens.z
    up = Gf.Vec3d(0, 0, 1) if z_up else Gf.Vec3d(0, 1, 0)

    layer = Sdf.Layer.CreateNew(str(out))
    layer.subLayerPaths.append(src.as_posix())
    stage = Usd.Stage.Open(layer)
    UsdGeom.SetStageMetersPerUnit(stage, UsdGeom.GetStageMetersPerUnit(scene))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.GetStageUpAxis(scene))
    UsdGeom.Scope.Define(stage, "/Cameras")
    far = radius * 20

    # perspective: 24 mm lens, 21 mm aperture -> ~47 deg field of view; distance fits the sphere
    dist = radius * MARGIN / math.sin(math.radians(47 / 2))
    view = Gf.Vec3d(1, -1, 0.6) if z_up else Gf.Vec3d(1, 0.6, 1)
    define_camera(stage, "/Cameras/Perspective", center + view.GetNormalized() * dist, center, up,
                  None, (radius * 0.01, far))

    if z_up:
        top_eye, top_up, top_extent = center + Gf.Vec3d(0, 0, radius * 3), Gf.Vec3d(0, 1, 0), max(size[0], size[1])
        side_eye, side_extent = center - Gf.Vec3d(0, radius * 3, 0), max(size[0], size[2])
    else:
        top_eye, top_up, top_extent = center + Gf.Vec3d(0, radius * 3, 0), Gf.Vec3d(0, 0, -1), max(size[0], size[2])
        side_eye, side_extent = center + Gf.Vec3d(0, 0, radius * 3), max(size[0], size[1])
    define_camera(stage, "/Cameras/Top", top_eye, center, top_up, top_extent * MARGIN, (1e-3 * radius, far))
    define_camera(stage, "/Cameras/Side", side_eye, center, up, side_extent * MARGIN, (1e-3 * radius, far))

    layer.Save()
    print(f"wrote {out} with /Cameras/Perspective, /Cameras/Top, /Cameras/Side")


if __name__ == "__main__":
    main()
