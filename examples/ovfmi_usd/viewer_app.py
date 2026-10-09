"""Live ovui viewer: the BouncingBall and Rotator FMUs drive bouncing_ball_live.usda, rendered by ovrtx.

    python viewer_app.py

Each frame ovfmi steps both FMUs and writes their outputs into the ovstage that ovrtx renders:
BouncingBall h -> /World/Ball xformOp:translate, v -> sim:velocity; Rotator angle -> /World/Rotor
xformOp:rotateXYZ (y), omega -> sim:omega. The sliders and the checkbox write FMU inputs through the USD
attributes sim:restitution, sim:speed, sim:acceleration and sim:enabled. Left-drag orbits the
camera, right-drag or the wheel zooms.
"""
from __future__ import annotations

import os

os.environ.setdefault("OVRTX_SKIP_USD_CHECK", "1")  # must be set before importing ovrtx

import argparse
import math
import time
from pathlib import Path

import numpy as np
import omni.ui as ui
import ovrtx
import ovstage
from ovfmi import AttributeWrite, FmiHost
from ovstage import population
from pxr import Usd

SCENE = Path(__file__).with_name("bouncing_ball_live.usda").resolve()
BALL = "/World/Ball"
ROTOR = "/World/Rotor"
CAMERA = "/Camera"
PRODUCT = "/RenderSvc/Product"
WIDTH, HEIGHT = 960, 540  # the product resolution authored in the scene
TARGET = np.array([-0.45, 0.3, 0.0])  # orbit centre
LOOP_SECONDS = 8.0  # restart the FMU after this much simulation time
HISTORY = 240  # samples kept in the plots


class Sim:
    """Owns renderer, ovstage and FMU host. Call every method from one thread."""

    def __init__(self) -> None:
        self.renderer = ovrtx.Renderer()  # first run compiles shaders: a few minutes
        self.stage = ovstage.Stage("ovfmi-usd-live")
        self.renderer.attach_ovstage(self.stage)
        population.open_usd(self.stage, str(SCENE), ordinal=1)
        self.stage.advance_write_floor(1).wait()
        self.ordinal = 1
        self.fmi = FmiHost()
        self.fmi.attach_ovstage(self.stage, source_asset=SCENE)
        self.pending: dict[str, dict[str, float | bool]] = {}  # prim -> attribute -> value, written before the next step
        self.yaw, self.pitch, self.distance = math.radians(20), math.radians(8), 3.6
        self._paths_dict = ovstage.PathDictionary(self.stage)
        self._camera_paths = self._paths_dict.create_path_list_from_strings([CAMERA])
        self._camera_query = self.stage.query_from_path_list(self._camera_paths)
        self._rotor_paths = self._paths_dict.create_path_list_from_strings([ROTOR])
        self._rotor_query = self.stage.query_from_path_list(self._rotor_paths)
        usd_stage = Usd.Stage.Open(str(SCENE))  # keep it alive while the prim is read
        rotor = usd_stage.GetPrimAtPath(ROTOR)  # authored placement and size of the bar
        self._rotor_translate = np.array(rotor.GetAttribute("xformOp:translate").Get())
        self._rotor_scale = np.array(rotor.GetAttribute("xformOp:scale").Get())

    def _camera_matrix(self) -> np.ndarray:
        """Camera-to-world, row-vector convention (rows: right, up, back, position)."""
        offset = self.distance * np.array([math.cos(self.pitch) * math.sin(self.yaw), math.sin(self.pitch),
                                           math.cos(self.pitch) * math.cos(self.yaw)])
        back = offset / np.linalg.norm(offset)
        right = np.cross([0.0, 1.0, 0.0], back)
        right /= np.linalg.norm(right)
        m = np.eye(4)
        m[0, :3], m[1, :3], m[2, :3], m[3, :3] = right, np.cross(back, right), back, TARGET + offset
        return m[None]

    def _rotor_matrix(self, angle_deg: float) -> np.ndarray:
        """Local-to-world of the rotor, row-vector convention: scale, then rotate about Y, then translate."""
        c, s = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
        m = np.eye(4)
        m[:3, :3] = np.diag(self._rotor_scale) @ np.array([[c, 0, -s], [0, 1, 0], [s, 0, c]])
        m[3, :3] = self._rotor_translate
        return m[None]

    def orbit(self, d_yaw: float, d_pitch: float) -> None:
        self.yaw += d_yaw
        self.pitch = min(max(self.pitch + d_pitch, math.radians(-5)), math.radians(85))

    def dolly(self, factor: float) -> None:
        self.distance = min(max(self.distance * factor, 0.6), 8.0)

    def set_input(self, prim: str, attribute: str, value: float | bool) -> None:
        """Takes effect on the next step: ovfmi passes the attribute on to the FMU input it is mapped to."""
        self.pending.setdefault(prim, {})[attribute] = value

    def step(self, dt: float) -> tuple[np.ndarray, dict[str, float]]:
        """Advance both FMUs by dt and render. Returns (HxWx4 pixels, all mapped FMU outputs by name)."""
        if self.pending:
            self.fmi.write([AttributeWrite(prim_paths=(prim,), attribute_name=name, values=[[value]])
                            for prim, attributes in self.pending.items() for name, value in attributes.items()])
            self.fmi.wait_all()
            self.pending.clear()
        self.ordinal += 1
        self.fmi.step_sync(dt)
        self.fmi.write_to_ovstage(self.ordinal)  # every FMU output into this ordinal
        # ovrtx does not re-evaluate the rotation xform op that ovfmi writes, so the FMU angle is also
        # applied as a matrix, the same way as the camera below
        with self.fmi.read(prim_paths=[ROTOR], attribute_names=["xformOp:rotateXYZ"]) as result:
            angle = float(result.groups[0].tensors[0][0][1])
        self.stage.write_attribute(
            self._rotor_query, "omni:xform", self.ordinal, self._rotor_matrix(angle),
            is_array=False, semantic=int(ovstage.AttributeSemantic.MATRIX),
        ).wait()
        self.stage.write_attribute(
            self._camera_query, "omni:xform", self.ordinal, self._camera_matrix(),
            is_array=False, semantic=int(ovstage.AttributeSemantic.MATRIX),
        ).wait()
        self.stage.advance_write_floor(self.ordinal).wait()  # seal the ordinal before rendering it
        products = self.renderer.step(render_products={PRODUCT}, delta_time=max(dt, 1 / 300), ordinal=self.ordinal)
        with self.fmi.read() as result:
            outputs = {f"{path}.{g.attribute_name}": g.tensors[0][0] for g in result.groups for path in g.prim_paths[:1]}
        values = {
            "h": float(outputs[f"{BALL}.xformOp:translate"][1]), "v": float(outputs[f"{BALL}.sim:velocity"][0]),
            "angle": float(outputs[f"{ROTOR}.xformOp:rotateXYZ"][1]), "omega": float(outputs[f"{ROTOR}.sim:omega"][0]),
        }
        for product in products.values():
            for frame in product.frames:
                var = frame.render_vars[f"{PRODUCT}/LdrColor"].map(device=ovrtx.Device.CPU)
                pixels = np.from_dlpack(var).copy()  # copy before unmapping the GPU buffer
                var.unmap()
                return pixels, values
        raise RuntimeError("renderer produced no frame")

    def close(self) -> None:
        self.fmi.release()
        self.stage.release_query(self._camera_query).wait()
        self.stage.release_query(self._rotor_query).wait()
        self._paths_dict.destroy_path_list(self._camera_paths)
        self._paths_dict.destroy_path_list(self._rotor_paths)
        self._paths_dict.destroy()
        self.renderer.detach_ovstage()
        self.stage.destroy()
        self.renderer.destroy()


async def main(snapshot: str | None, frames: int) -> None:
    sim = Sim()
    provider = ui.ByteImageProvider()
    provider.set_data_array(np.full((HEIGHT, WIDTH, 4), 255, dtype=np.uint8), [WIDTH, HEIGHT])

    state = {"playing": True, "h": [1.0] * HISTORY, "v": [0.0] * HISTORY}
    window = ui.Window("ovfmi + OpenUSD", width=WIDTH + 40, height=HEIGHT + 300)
    with window.frame:
        with ui.VStack(spacing=6):
            with ui.HStack(height=30, spacing=8):
                play = ui.Button("Pause", width=80, clicked_fn=lambda: toggle())
                ui.Button("Reset", width=80, clicked_fn=sim.fmi.reset)
                ui.Label("Speed", width=45)
                speed = ui.FloatSlider(min=0.1, max=2.0, width=160)
                speed.model.set_value(1.0)
                ui.Label("Restitution e", width=95)
                e_slider = ui.FloatSlider(min=0.5, max=1.0, width=160)  # range declared by the FMU
                e_slider.model.set_value(0.7)
                ui.Spacer()
                hud = ui.Label("", width=330, alignment=ui.Alignment.RIGHT_CENTER)
            with ui.HStack(height=30, spacing=8):
                ui.Label("Rotator", width=60)
                on_box = ui.CheckBox(width=20)
                on_box.model.set_value(True)
                ui.Label("on", width=20)
                ui.Label("Speed deg/s", width=90)
                speed_slider = ui.FloatSlider(min=-360.0, max=360.0, width=200)
                speed_slider.model.set_value(90.0)
                ui.Label("Acceleration deg/s2", width=130)
                accel_slider = ui.FloatSlider(min=-180.0, max=180.0, width=200)
                accel_slider.model.set_value(0.0)
                ui.Spacer()
                rotor_hud = ui.Label("", width=330, alignment=ui.Alignment.RIGHT_CENTER)
            view = ui.ImageWithProvider(provider, fill_policy=ui.IwpFillPolicy.IWP_PRESERVE_ASPECT_FIT,
                                        opaque_for_mouse_events=True)
            ui.Label("h  (xformOp:translate.y, m)", height=14)
            plot_h = ui.Plot(ui.Type.LINE, 0.0, 1.1, *state["h"], height=60)
            ui.Label("v  (sim:velocity, m/s)", height=14)
            plot_v = ui.Plot(ui.Type.LINE, -5.0, 5.0, *state["v"], height=60)

    drag = {"button": None, "x": 0.0, "y": 0.0}

    def moved(x, y, modifiers, pressed_flag) -> None:
        if drag["button"] is None:
            return
        dx, dy = x - drag["x"], y - drag["y"]
        drag.update(x=x, y=y)
        if drag["button"] == 0:  # left: orbit
            sim.orbit(-dx * 0.008, dy * 0.008)
        else:  # right / middle: zoom
            sim.dolly(math.exp(dy * 0.01))

    view.set_mouse_pressed_fn(lambda x, y, button, mods: drag.update(button=button, x=x, y=y))
    view.set_mouse_moved_fn(moved)
    view.set_mouse_released_fn(lambda x, y, button, mods: drag.update(button=None))
    view.set_mouse_wheel_fn(lambda x, y, mods: sim.dolly(0.9 if y > 0 else 1.1))

    def toggle() -> None:
        state["playing"] = not state["playing"]
        play.text = "Pause" if state["playing"] else "Play"

    e_slider.model.add_value_changed_fn(lambda m: sim.set_input(BALL, "sim:restitution", m.get_value_as_float()))
    speed_slider.model.add_value_changed_fn(lambda m: sim.set_input(ROTOR, "sim:speed", m.get_value_as_float()))
    accel_slider.model.add_value_changed_fn(lambda m: sim.set_input(ROTOR, "sim:acceleration", m.get_value_as_float()))
    on_box.model.add_value_changed_fn(lambda m: sim.set_input(ROTOR, "sim:enabled", m.get_value_as_bool()))

    n, last_fps, fps = 0, time.perf_counter(), 0.0
    dt = 1.0 / 60.0
    while True:
        await ui.next_frame()
        if not state["playing"] and n > 0:
            continue
        if sim.fmi.time >= LOOP_SECONDS:
            sim.fmi.reset()  # the ball comes to rest; start over
        pixels, out = sim.step(dt * speed.model.get_value_as_float())
        h, v = out["h"], out["v"]
        provider.set_data_array(np.ascontiguousarray(pixels), [pixels.shape[1], pixels.shape[0]])
        state["h"] = state["h"][1:] + [h]
        state["v"] = state["v"][1:] + [v]
        plot_h.set_data(*state["h"])
        plot_v.set_data(*state["v"])
        n += 1
        if n % 20 == 0:
            now = time.perf_counter()
            fps = 20 / (now - last_fps)
            last_fps = now
        hud.text = f"t={sim.fmi.time:5.2f} s  h={h:4.2f} m  v={v:5.2f} m/s  {fps:3.0f} fps"
        rotor_hud.text = f"angle={out['angle'] % 360:6.1f} deg  omega={out['omega']:7.1f} deg/s"
        if frames and n >= frames:
            if snapshot:
                from PIL import Image
                Image.fromarray(pixels).save(snapshot)
                print("wrote", snapshot)
            break
    sim.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--frames", type=int, default=0, help="exit after N frames (0 = until the window closes)")
    p.add_argument("--snapshot", help="with --frames: save the last rendered frame as PNG")
    a = p.parse_args()
    ui.run(main(a.snapshot, a.frames))
