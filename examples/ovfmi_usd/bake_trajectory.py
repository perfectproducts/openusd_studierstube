"""Step the BouncingBall FMU with ovfmi and bake the mapped outputs into a time-sampled OpenUSD file.

h -> xformOp:translate (y) and v -> sim:velocity become time samples; the result plays back without the FMU.
"""
from pathlib import Path

import ovstage
from ovfmi import FmiHost
from ovstage import population
from pxr import Gf, Sdf, Usd, UsdGeom

HERE = Path(__file__).parent.resolve()
SRC = HERE / "bouncing_ball.usda"
OUT = HERE / "bouncing_ball_baked.usda"
BALL = "/World/Ball"
FPS = 60
SECONDS = 5

stage = ovstage.Stage("ovfmi_usd_bake")
population.open_usd(stage, str(SRC), ordinal=1)

out = Usd.Stage.CreateNew(str(OUT))
out.SetTimeCodesPerSecond(FPS)
out.SetFramesPerSecond(FPS)
out.SetStartTimeCode(0)
out.SetEndTimeCode(FPS * SECONDS)
UsdGeom.SetStageUpAxis(out, UsdGeom.Tokens.y)
UsdGeom.SetStageMetersPerUnit(out, 1.0)
world = UsdGeom.Xform.Define(out, "/World")
out.SetDefaultPrim(world.GetPrim())
ball = UsdGeom.Sphere.Define(out, BALL)
ball.GetRadiusAttr().Set(0.1)
translate = ball.AddTranslateOp()
velocity = ball.GetPrim().CreateAttribute("sim:velocity", Sdf.ValueTypeNames.Double)
ball.GetPrim().CreateAttribute("sim:restitution", Sdf.ValueTypeNames.Double).Set(0.7)  # constant: the FMU default

with FmiHost() as fmi:
    fmi.attach_ovstage(stage, source_asset=SRC)
    translate.Set(Gf.Vec3d(0, 1, 0), 0)  # FMU start state
    velocity.Set(0.0, 0)
    for frame in range(1, FPS * SECONDS + 1):
        fmi.step_sync(1.0 / FPS)
        with fmi.read(prim_paths=[BALL]) as result:
            values = {g.attribute_name: g.tensors[0][0] for g in result.groups}
        translate.Set(Gf.Vec3d(0, float(values["xformOp:translate"][1]), 0), frame)
        velocity.Set(float(values["sim:velocity"][0]), frame)

out.Save()
print(f"wrote {OUT.name}: {FPS * SECONDS + 1} samples")
