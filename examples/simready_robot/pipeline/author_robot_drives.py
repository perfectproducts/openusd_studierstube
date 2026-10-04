#!/usr/bin/env python3
"""Author a fixed-base articulation (joints, limits, drives, joint state) from a robot kinematics spec.

The spec (output/<asset>/pipeline/01_context/robot-kinematics.json) comes from the vendor datasheet
plus pivots fitted on the CAD geometry. The result is an over-layer that sublayers the physics USD:
  - the single asset-root rigid body is replaced by one rigid body per link (mass split by volume*density)
  - A1..An revolute joints with datasheet limits mapped from the CAD home pose, angular drives,
    PhysX max joint velocity and zeroed joint state
  - fixed joints world->base and for passive links, articulation root on the default prim
If a Joint Agent rigged package is given, its revolute joints are compared against the spec (report only).

Usage:
  author_robot_drives.py <physics.usd> <rigged.usdz|-> <out.usda> --kinematics robot-kinematics.json --report drives.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

AXES = {"X": Gf.Vec3d(1, 0, 0), "Y": Gf.Vec3d(0, 1, 0), "Z": Gf.Vec3d(0, 0, 1)}
# Position-drive gains in SI (N*m/rad, N*m*s/rad). Datasheet has no torques: sized so gravity sag of
# the ~0.75 t arm stays well below 0.1 deg; tune per task.
DRIVE_SI = {"A1": (2e6, 1e5), "A2": (5e6, 2.5e5), "A3": (3e6, 1.5e5),
            "A4": (3e5, 1.5e4), "A5": (3e5, 1.5e4), "A6": (1e5, 5e3)}
# USD joint angle = sign * (datasheet angle - CAD home angle). Positive rotation about +Y tilts the
# link arm / arm forward-down, which is the increasing direction of KUKA A2/A3. A1/A4/A5/A6 limits
# are symmetric, so their sign only matters when driving with KUKA angles (unverified: +1).
SIGN = {"A2": 1.0, "A3": 1.0}


def mesh_volume(mesh: UsdGeom.Mesh, xf: np.ndarray) -> float:
    pts = np.asarray(mesh.GetPointsAttr().Get(), dtype=float)
    counts = mesh.GetFaceVertexCountsAttr().Get()
    idx = np.asarray(mesh.GetFaceVertexIndicesAttr().Get())
    if pts.size == 0 or counts is None:
        return 0.0
    w = (np.c_[pts, np.ones(len(pts))] @ xf)[:, :3]
    vol, o = 0.0, 0
    for c in counts:
        f = idx[o:o + c]
        for i in range(1, c - 1):
            vol += np.dot(w[f[0]], np.cross(w[f[i]], w[f[i + 1]]))
        o += c
    return abs(vol) / 6.0


def link_masses(stage: Usd.Stage, links: dict[str, Usd.Prim], total: float) -> dict[str, float]:
    xc = UsdGeom.XformCache()
    weight = {}
    for name, prim in links.items():
        w = 0.0
        for p in Usd.PrimRange(prim):
            if p.IsA(UsdGeom.Mesh):
                dens = p.GetAttribute("physics:density")
                d = dens.Get() if dens and dens.HasAuthoredValue() else 1.0
                w += mesh_volume(UsdGeom.Mesh(p), np.array(xc.GetLocalToWorldTransform(p))) * (d or 1.0)
        weight[name] = w
    s = sum(weight.values()) or 1.0
    return {n: total * w / s for n, w in weight.items()}


def joint_frame(body: Usd.Prim, pivot_world: Gf.Vec3d) -> tuple[Gf.Vec3f, Gf.Quatf]:
    """Joint frame aligned with the world axes at pivot_world, expressed in body-local space."""
    inv = UsdGeom.Xformable(body).ComputeLocalToWorldTransform(Usd.TimeCode.Default()).GetInverse()
    pos = inv.Transform(pivot_world)
    rot = inv.RemoveScaleShear().ExtractRotationQuat()
    return Gf.Vec3f(pos), Gf.Quatf(rot.GetNormalized())


def compare_rigged(rigged: Path, spec: list[dict], mpu: float) -> list[dict]:
    """Report how the Joint Agent's revolute joints relate to the spec axes (angle, pivot offset)."""
    st = Usd.Stage.Open(str(rigged))
    scale = (UsdGeom.GetStageMetersPerUnit(st) or mpu) / mpu
    found = []
    for p in st.Traverse():
        if not p.IsA(UsdPhysics.RevoluteJoint):
            continue
        j = UsdPhysics.RevoluteJoint(p)
        b0 = j.GetBody0Rel().GetTargets()
        b1 = j.GetBody1Rel().GetTargets()
        body = st.GetPrimAtPath(b0[0]) if b0 else None
        m = (UsdGeom.Xformable(body).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
             if body and body.IsA(UsdGeom.Xformable) else Gf.Matrix4d(1))
        pivot = m.Transform(Gf.Vec3d(j.GetLocalPos0Attr().Get() or Gf.Vec3f())) * scale
        q = Gf.Rotation(Gf.Quatd(j.GetLocalRot0Attr().Get() or Gf.Quatf(1)))
        axis = m.TransformDir(q.TransformDir(AXES[j.GetAxisAttr().Get() or "X"])).GetNormalized()
        found.append({"prim": str(p.GetPath()), "body0": [str(t) for t in b0], "body1": [str(t) for t in b1],
                      "pivot": pivot, "axis": axis})
    out = []
    for js in spec:
        a = AXES[js["axis"]]
        c = Gf.Vec3d(*js["pivot_mm"])
        best = None
        for f in found:
            ang = math.degrees(math.acos(min(1.0, abs(Gf.Dot(a, f["axis"])))))
            d = f["pivot"] - c
            off = (d - a * Gf.Dot(d, a)).GetLength()  # distance of agent pivot from the spec axis line
            names = " ".join(f["body0"] + f["body1"])
            score = ang + off / 10 - (50 if js["child"] in names.split("/") or f"/{js['child']}" in names else 0)
            if best is None or score < best[0]:
                best = (score, {"joint": js["name"], "agent_prim": f["prim"], "axis_angle_deg": round(ang, 2),
                                "pivot_offset_mm": round(off, 1), "agent_bodies": f["body0"] + f["body1"]})
        out.append(best[1] if best else {"joint": js["name"], "agent_prim": None})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source")
    ap.add_argument("rigged", help="Joint Agent rigged package to cross-check, or '-'")
    ap.add_argument("output")
    ap.add_argument("--kinematics", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args(argv)

    kin = json.loads(Path(args.kinematics).read_text(encoding="utf-8"))
    src, out = Path(args.source).resolve(), Path(args.output).resolve()
    report: dict = {"source": str(src), "output_usd_path": str(out), "kinematics": str(Path(args.kinematics).resolve()),
                    "joints": [], "errors": [], "warnings": []}
    try:
        layer = Sdf.Layer.CreateNew(str(out))
        layer.subLayerPaths.append(os.path.relpath(src, out.parent).replace("\\", "/"))
        stage = Usd.Stage.Open(layer)
        src_stage = Usd.Stage.Open(str(src))
        UsdGeom.SetStageMetersPerUnit(stage, UsdGeom.GetStageMetersPerUnit(src_stage))
        UsdGeom.SetStageUpAxis(stage, UsdGeom.GetStageUpAxis(src_stage))
        root = stage.GetPrimAtPath(src_stage.GetDefaultPrim().GetPath())
        if not root:
            raise RuntimeError(f"{src} has no defaultPrim")
        stage.SetDefaultPrim(root)
        stage.SetEditTarget(layer)
        mpu = UsdGeom.GetStageMetersPerUnit(stage)
        to_stage = 1e-3 / mpu  # spec pivots are mm

        # link groups are the named children of the (single) asset container under the default prim
        container = next((c for c in root.GetChildren() if c.GetChild("k1")), root)
        link_names = ["k1"] + [j["child"] for j in kin["joints"]] + [n for n in kin.get("fixed_links", {}) if n != "k1"]
        links = {n: container.GetChild(n) for n in link_names}
        missing = [n for n, p in links.items() if not p]
        if missing:
            raise RuntimeError(f"link prims not found under {container.GetPath()}: {missing}")

        total = root.GetAttribute("physics:mass").Get() if root.HasAPI(UsdPhysics.MassAPI) else None
        if not total:
            total = float(kin.get("datasheet", {}).get("mass_kg") or 1000.0)
            report["warnings"].append(f"no root mass authored; using {total} kg")
        if root.HasAPI(UsdPhysics.RigidBodyAPI):
            root.RemoveAPI(UsdPhysics.RigidBodyAPI)
        if root.HasAPI(UsdPhysics.MassAPI):
            root.RemoveAPI(UsdPhysics.MassAPI)
        UsdPhysics.ArticulationRootAPI.Apply(root)

        masses = link_masses(stage, links, total)
        for n, prim in links.items():
            UsdPhysics.RigidBodyAPI.Apply(prim)
            UsdPhysics.MassAPI.Apply(prim).CreateMassAttr(masses[n])
        report["link_masses_kg"] = {n: round(m, 2) for n, m in masses.items()}
        report["total_mass_kg"] = round(total, 2)

        jroot = UsdGeom.Scope.Define(stage, root.GetPath().AppendChild("Joints")).GetPrim()

        def fixed(name: str, body0: Usd.Prim | None, body1: Usd.Prim) -> None:
            j = UsdPhysics.FixedJoint.Define(stage, jroot.GetPath().AppendChild(name))
            anchor = Gf.Vec3d(UsdGeom.Xformable(body1).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
                              .ExtractTranslation())
            if body0:
                j.CreateBody0Rel().SetTargets([body0.GetPath()])
                p0, r0 = joint_frame(body0, anchor)
            else:
                p0, r0 = Gf.Vec3f(anchor), Gf.Quatf(1)
            j.CreateBody1Rel().SetTargets([body1.GetPath()])
            p1, r1 = joint_frame(body1, anchor)
            j.CreateLocalPos0Attr(p0); j.CreateLocalRot0Attr(r0)
            j.CreateLocalPos1Attr(p1); j.CreateLocalRot1Attr(r1)

        fixed("base_fixed", None, links["k1"])

        home = kin.get("cad_home_pose_deg", {})
        for js in kin["joints"]:
            name = js["name"]
            b0, b1 = links[js["parent"]], links[js["child"]]
            pivot = Gf.Vec3d(*js["pivot_mm"]) * to_stage
            j = UsdPhysics.RevoluteJoint.Define(stage, jroot.GetPath().AppendChild(name))
            j.CreateAxisAttr(js["axis"])
            j.CreateBody0Rel().SetTargets([b0.GetPath()])
            j.CreateBody1Rel().SetTargets([b1.GetPath()])
            p0, r0 = joint_frame(b0, pivot)
            p1, r1 = joint_frame(b1, pivot)
            j.CreateLocalPos0Attr(p0); j.CreateLocalRot0Attr(r0)
            j.CreateLocalPos1Attr(p1); j.CreateLocalRot1Attr(r1)
            sign, h = SIGN.get(name, 1.0), float(home.get(name, 0.0))
            lo, hi = sorted(sign * (v - h) for v in js["limits_deg"])
            j.CreateLowerLimitAttr(lo)
            j.CreateUpperLimitAttr(hi)

            prim = j.GetPrim()
            k_si, c_si = DRIVE_SI.get(name, (1e5, 5e3))
            per_deg_stage = math.pi / 180.0 * (1.0 / mpu) ** 2  # N*m/rad -> kg*unit^2/s^2 per degree
            drive = UsdPhysics.DriveAPI.Apply(prim, "angular")
            drive.CreateTypeAttr("force")
            drive.CreateStiffnessAttr(k_si * per_deg_stage)
            drive.CreateDampingAttr(c_si * per_deg_stage)
            drive.CreateTargetPositionAttr(0.0)
            prim.AddAppliedSchema("PhysicsJointStateAPI:angular")
            prim.CreateAttribute("state:angular:physics:position", Sdf.ValueTypeNames.Float).Set(0.0)
            prim.CreateAttribute("state:angular:physics:velocity", Sdf.ValueTypeNames.Float).Set(0.0)
            prim.AddAppliedSchema("PhysxJointAPI")
            prim.CreateAttribute("physxJoint:maxJointVelocity", Sdf.ValueTypeNames.Float).Set(
                float(js["max_velocity_deg_s"]))
            prim.CreateAttribute("cad2simready:datasheetLimitsDeg", Sdf.ValueTypeNames.Float2).Set(
                Gf.Vec2f(*js["limits_deg"]))
            prim.CreateAttribute("cad2simready:homePoseDeg", Sdf.ValueTypeNames.Float).Set(h)
            prim.CreateAttribute("cad2simready:datasheetSign", Sdf.ValueTypeNames.Float).Set(sign)
            report["joints"].append({"name": name, "path": str(prim.GetPath()), "parent": js["parent"],
                                     "child": js["child"], "axis": js["axis"], "pivot_mm": js["pivot_mm"],
                                     "limits_usd_deg": [lo, hi], "datasheet_limits_deg": js["limits_deg"],
                                     "max_velocity_deg_s": js["max_velocity_deg_s"],
                                     "stiffness_si": k_si, "damping_si": c_si})

        # passive counterbalance parts ride on their carrier link
        carriers = {"k9": "k2", "k10": "k3"}
        for n in kin.get("fixed_links", {}):
            if n != "k1":
                fixed(f"{n}_fixed", links[carriers.get(n, 'k1')], links[n])
        # overlapping passive parts must not collide with the neighbouring moving links
        filt = {"k9": ["k3", "k10"], "k10": ["k2"]}
        for n, others in filt.items():
            if n in links:
                UsdPhysics.FilteredPairsAPI.Apply(links[n]).CreateFilteredPairsRel().SetTargets(
                    [links[o].GetPath() for o in others if o in links])

        report["articulation_root"] = str(root.GetPath())
        layer.Save()

        if args.rigged not in ("", "-") and Path(args.rigged).exists():
            try:
                report["joint_agent_comparison"] = compare_rigged(Path(args.rigged), kin["joints"], 1e-3)
            except Exception as exc:  # noqa: BLE001 - cross-check only
                report["warnings"].append(f"could not compare Joint Agent package: {exc}")
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(f"{type(exc).__name__}: {exc}")

    Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report.get(k) for k in ("output_usd_path", "articulation_root", "errors", "warnings")}, indent=2))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
