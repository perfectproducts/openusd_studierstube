#!/usr/bin/env python3
"""Geometry complexity of a JT file and of OpenUSD files, for the README comparison.

JT: reads the table of contents and the zlib-compressed Logical Scene Graph segment (JT 9.x layout) and sums the
per-shape vertex/polygon counts that the Tri-Strip Set Shape nodes store, grouped by Range-LOD level. The shape LOD
segments themselves are not decoded. JT polygon counts include the degenerate triangles that stitch triangle strips.

OpenUSD: composes each stage (instance proxies included) and counts meshes, triangles, points, unique positions and
surface area.

  py -3.12 geometry_stats.py ..\\model\\kr270r2700ultra.jt ..\\usd\\kr270r2700ultra_converted.usd ..\\usd\\kr270r2700ultra_articulated.usda
"""
from __future__ import annotations

import argparse
import collections
import struct
import uuid
import zlib
from pathlib import Path

SEGMENT_TYPES = {1: "LSG", 2: "JT B-Rep", 3: "PMI", 4: "MetaData", 6: "Shape", 17: "XT B-Rep", 18: "Wireframe",
                 20: "ULP", 24: "LWPA", **{t: f"ShapeLOD{t - 7}" for t in range(7, 17)}}

GUID_SUFFIX = "-2ac8-11d1-9b6b-0080c7bb5997"
TRI_STRIP = "10dd1077" + GUID_SUFFIX
POLYLINE = "10dd1046" + GUID_SUFFIX
GROUP = "10dd101b" + GUID_SUFFIX
PARTITION = "10dd103e" + GUID_SUFFIX
INSTANCE = "10dd102a" + GUID_SUFFIX
RANGE_LOD = "10dd104c" + GUID_SUFFIX
PART = "ce357244-38fb-11d1-a506-006097bdc6e1"
META_DATA = "ce357245-38fb-11d1-a506-006097bdc6e1"
GROUP_TYPES = {GROUP, PARTITION, RANGE_LOD, PART, META_DATA}


def read_jt(path: Path) -> dict:
    data = path.read_bytes()
    version = data[:80].decode("ascii", "replace").strip()
    e = "<" if data[80] == 0 else ">"
    _, toc = struct.unpack_from(e + "iI", data, 81)
    (count,) = struct.unpack_from(e + "i", data, toc)
    segments = []
    for i in range(count):
        offset, length, attrs = struct.unpack_from(e + "iiI", data, toc + 4 + i * 28 + 16)
        segments.append((offset, length, attrs >> 24))

    lsg_offset = next(off for off, _, t in segments if t == 1)
    flag, clen = struct.unpack_from(e + "ii", data, lsg_offset + 24)
    body = lsg_offset + 24 + 9
    raw = zlib.decompress(data[body:body + clen - 1]) if flag == 2 and data[lsg_offset + 32] == 2 else data[body - 9:]

    nodes, pos = {}, 0
    while pos + 4 <= len(raw):
        (length,) = struct.unpack_from(e + "i", raw, pos)
        if length <= 0 or pos + 4 + length > len(raw):
            break
        elem = raw[pos + 4:pos + 4 + length]
        pos += 4 + length
        kind = str(uuid.UUID(bytes_le=elem[:16]))
        if kind.startswith("ffffffff"):
            continue
        (oid,) = struct.unpack_from(e + "i", elem, 17)
        node, o = {"type": kind}, 21
        if kind in GROUP_TYPES or kind in (TRI_STRIP, POLYLINE, INSTANCE):
            _, _, n_attr = struct.unpack_from(e + "hIi", elem, o)  # base node data
            o += 10 + 4 * n_attr
        if kind in GROUP_TYPES:
            _, n_child = struct.unpack_from(e + "hi", elem, o)
            node["children"] = list(struct.unpack_from(e + f"{n_child}i", elem, o + 6))
        elif kind == INSTANCE:
            node["children"] = [struct.unpack_from(e + "hi", elem, o)[1]]
        elif kind in (TRI_STRIP, POLYLINE):
            o += 2 + 48  # version, reserved + untransformed bbox
            area, _, vmax, _, _, _, pmax = struct.unpack_from(e + "f6i", elem, o)
            node.update(area=area, vertices=vmax, polygons=pmax)
        nodes[oid] = node

    def shapes(oid: int, out: list) -> list:
        node = nodes.get(oid)
        if node is None:
            return out
        if node["type"] == TRI_STRIP:
            out.append(node)
        for child in node.get("children", []):
            shapes(child, out)
        return out

    lods = collections.defaultdict(lambda: {"shapes": 0, "polygons": 0, "vertices": 0, "area": 0.0})
    for node in nodes.values():
        if node["type"] == RANGE_LOD:
            for level, child in enumerate(node["children"]):
                for s in shapes(child, []):
                    lod = lods[level]
                    lod["shapes"] += 1
                    lod["polygons"] += s["polygons"]
                    lod["vertices"] += s["vertices"]
                    lod["area"] += s["area"]

    seg_stats = collections.Counter()
    seg_bytes = collections.Counter()
    for _, length, t in segments:
        seg_stats[SEGMENT_TYPES.get(t, str(t))] += 1
        seg_bytes[SEGMENT_TYPES.get(t, str(t))] += length
    kinds = collections.Counter(n["type"] for n in nodes.values())
    return {"version": version, "size": len(data), "segments": seg_stats, "segment_bytes": seg_bytes,
            "parts": kinds[PART], "instances": kinds[INSTANCE], "range_lods": kinds[RANGE_LOD],
            "polylines": kinds[POLYLINE], "lods": dict(sorted(lods.items()))}


def read_usd(path: Path) -> dict:
    import numpy as np
    from pxr import Usd, UsdGeom, UsdPhysics

    stage = Usd.Stage.Open(str(path))
    stats = collections.Counter()
    types = collections.Counter()
    approx = collections.Counter()
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        types[prim.GetTypeName()] += 1
        stats["instances"] += prim.IsInstance()
        if not prim.IsA(UsdGeom.Mesh):
            continue
        mesh = UsdGeom.Mesh(prim)
        counts = np.array(mesh.GetFaceVertexCountsAttr().Get())
        indices = np.array(mesh.GetFaceVertexIndicesAttr().Get())
        points = np.array(mesh.GetPointsAttr().Get(), dtype=float)
        stats["meshes"] += 1
        stats["faces"] += len(counts)
        stats["triangles"] += int((counts - 2).sum())
        stats["points"] += len(points)
        stats["unique_positions"] += len(np.unique(np.round(points, 4), axis=0))
        # fan-triangulate for area
        starts = np.concatenate(([0], np.cumsum(counts)[:-1]))
        for k in range(1, counts.max() - 1):
            sel = counts > k + 1
            a, b, c = (points[indices[starts[sel] + j]] for j in (0, k, k + 1))
            area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
            stats["area"] += float(area.sum())
            stats["zero_area_triangles"] += int((area < 1e-6).sum())
            stats["triangles_below_0.01"] += int((area < 0.01).sum())
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            attr = UsdPhysics.MeshCollisionAPI(prim).GetApproximationAttr()
            approx[attr.Get() if attr else "none"] += 1
    stats["prims"] = sum(types.values())
    return {"size": path.stat().st_size, "up_axis": UsdGeom.GetStageUpAxis(stage),
            "meters_per_unit": UsdGeom.GetStageMetersPerUnit(stage), "prototypes": len(stage.GetPrototypes()),
            "stats": dict(stats), "collision": dict(approx),
            "joints": {t: n for t, n in types.items() if t.endswith("Joint")}}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path, help=".jt and/or OpenUSD files")
    args = ap.parse_args()
    for path in args.files:
        print(f"== {path.name}")
        if path.suffix.lower() == ".jt":
            jt = read_jt(path)
            print(f"  {jt['version']}, {jt['size']:,} bytes")
            print("  segments: " + ", ".join(f"{k} {v} ({jt['segment_bytes'][k]:,} B)" for k, v in jt["segments"].items()))
            print(f"  parts {jt['parts']}, instances {jt['instances']}, range LODs {jt['range_lods']}, "
                  f"polyline shapes {jt['polylines']}")
            for level, lod in jt["lods"].items():
                print(f"  LOD{level}: shapes {lod['shapes']}, polygons {lod['polygons']:,}, "
                      f"strip vertices {lod['vertices']:,}, area {lod['area']:,.0f}")
        else:
            usd = read_usd(path)
            s = usd["stats"]
            print(f"  {usd['size']:,} bytes, up {usd['up_axis']}, metersPerUnit {usd['meters_per_unit']}")
            print(f"  prims {s.get('prims', 0)}, instances {s.get('instances', 0)}, prototypes {usd['prototypes']}")
            print(f"  meshes {s.get('meshes', 0)}, triangles {s.get('triangles', 0):,}, points {s.get('points', 0):,}, "
                  f"unique positions {s.get('unique_positions', 0):,}")
            print(f"  area {s.get('area', 0):,.0f}, zero-area triangles {s.get('zero_area_triangles', 0)}, "
                  f"triangles < 0.01 {s.get('triangles_below_0.01', 0)}")
            print(f"  collision approximation {usd['collision']}, joints {usd['joints']}")


if __name__ == "__main__":
    main()
