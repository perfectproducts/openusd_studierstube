"""Step the BouncingBall FMU mapped in bouncing_ball.usda and print every mapped attribute.

Shows all three mappings: h -> xformOp:translate (y), v -> sim:velocity, and e <- sim:restitution,
which is changed from Python after the first bounce.
"""
from pathlib import Path

import ovstage
from ovfmi import AttributeWrite, FmiHost
from ovstage import population

USD = Path(__file__).with_name("bouncing_ball.usda").resolve()
BALL = "/World/Ball"
DT = 1.0 / 60.0
STEPS = 240
E_LATER = 0.95  # restitution written after the first bounce
E_STEP = 30

stage = ovstage.Stage("ovfmi_usd_bouncing_ball")
population.open_usd(stage, str(USD), ordinal=1)

with FmiHost() as fmi:
    report = fmi.attach_ovstage(stage, source_asset=USD)
    for info in report.instances:
        print("instance:", info)

    for step in range(STEPS):
        if step == E_STEP:
            print(f"-- writing sim:restitution = {E_LATER}")
            fmi.write([AttributeWrite(prim_paths=(BALL,), attribute_name="sim:restitution", values=[[E_LATER]])])
            fmi.wait_all()
        fmi.step_sync(DT)
        if step % 10:
            continue
        with fmi.read() as result:
            # one group per mapped attribute; row 0 belongs to the first target prim
            values = {g.attribute_name: g.tensors[0][0] for g in result.groups}
        print(f"t={fmi.time:5.3f}s  h={float(values['xformOp:translate'][1]):6.3f} m"
              f"  v={float(values['sim:velocity'][0]):7.3f} m/s")
