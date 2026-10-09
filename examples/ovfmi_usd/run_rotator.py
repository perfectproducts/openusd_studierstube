"""Step the Rotator FMU mapped in rotator.usda and print angle and omega.

Writes the three FMU inputs from Python: acceleration at 1 s, enabled = False at 2 s (the rotation
freezes), enabled = True and a new speed at 3 s.
"""
from pathlib import Path

import ovstage
from ovfmi import AttributeWrite, FmiHost
from ovstage import population

USD = Path(__file__).with_name("rotator.usda").resolve()
ROTOR = "/World/Rotor"
DT = 1.0 / 60.0
STEPS = 240

# step -> writes applied before that step
WRITES = {
    60: [("sim:acceleration", 90.0)],
    120: [("sim:enabled", False)],
    180: [("sim:enabled", True), ("sim:speed", 30.0)],
}

stage = ovstage.Stage("ovfmi_usd_rotator")
population.open_usd(stage, str(USD), ordinal=1)

with FmiHost() as fmi:
    report = fmi.attach_ovstage(stage, source_asset=USD)
    for info in report.instances:
        print("instance:", info)

    for step in range(STEPS):
        for attribute, value in WRITES.get(step, []):
            print(f"-- writing {attribute} = {value}")
            fmi.write([AttributeWrite(prim_paths=(ROTOR,), attribute_name=attribute, values=[[value]])])
        fmi.wait_all()
        fmi.step_sync(DT)
        if step % 15:
            continue
        with fmi.read() as result:
            values = {g.attribute_name: g.tensors[0][0] for g in result.groups}
        print(f"t={fmi.time:5.3f}s  angle={float(values['xformOp:rotateXYZ'][1]):8.2f} deg  omega={float(values['sim:omega'][0]):7.2f} deg/s")
