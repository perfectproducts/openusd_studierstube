"""Render the before/after USD hierarchy of the KR 270 example as SVG trees."""
from html import escape

FONT = "Consolas, 'JetBrains Mono', Menlo, monospace"
CW = 0.55  # monospace char width / font size
C = {
    "bg": "#FFFFFF", "text": "#1F2328", "muted": "#6B7280", "line": "#C3C8CF", "title": "#111827",
    "xform": ("#F3F4F6", "#9CA3AF"), "group": ("#F3F4F6", "#6B7280"),
    "mesh": ("#EEF2F7", "#475569"), "body": ("#FDEBDD", "#C2410C"), "root": ("#EDE9FE", "#6D28D9"),
    "rev": ("#E0EDFF", "#1D4ED8"), "fixed": ("#EDEFF2", "#4B5563"), "axis": ("#E6F6EC", "#15803D"),
    "scope": ("#F3F4F6", "#9CA3AF"),
}
ROW, IND, X0, Y0, NAME_PX, CHIP_PX = 40, 30, 28, 78, 15, 12


def tw(s, px):
    return len(s) * px * CW


def chip(x, y, label, kind):
    fill, fg = C[kind]
    w = tw(label, CHIP_PX) + 16
    return (f'<rect x="{x:.1f}" y="{y - 11:.1f}" width="{w:.1f}" height="22" rx="11" fill="{fill}"/>'
            f'<text x="{x + 8:.1f}" y="{y + 4:.1f}" font-size="{CHIP_PX}" fill="{fg}" font-weight="600">{escape(label)}</text>'), w


def render(title, subtitle, rows, path):
    """rows: (depth, name, kind, [chips (label, kind)], note); note may be a tuple of columns."""
    # sibling groups: consecutive rows with the same depth and parent -> aligned columns
    group, gid, stack = [], 0, []
    for depth, *_ in rows:
        while stack and stack[-1][0] >= depth:
            stack.pop()
        key = (depth, stack[-1][1] if stack else -1)
        gid_here = key
        group.append(gid_here)
        stack.append((depth, len(group) - 1))
    cols = {}
    for g, (depth, name, kind, chips, note) in zip(group, rows):
        c = cols.setdefault(g, {"name": 0, "chips": {}, "note": {}})
        c["name"] = max(c["name"], tw(name, NAME_PX))
        for k, (label, _) in enumerate(chips):
            c["chips"][k] = max(c["chips"].get(k, 0), tw(label, CHIP_PX) + 16 if label else 0)
        for k, n in enumerate(note if isinstance(note, tuple) else (note,)):
            c["note"][k] = max(c["note"].get(k, 0), tw(n, 13))
    parts, width, last_y = [], 0, {}
    for i, (g, (depth, name, kind, chips, note)) in enumerate(zip(group, rows)):
        y, x = Y0 + i * ROW, X0 + depth * IND
        if depth > 0:
            px, py = X0 + (depth - 1) * IND + 7, last_y.get(depth - 1, y)
            parts.append(f'<path d="M{px},{py + 9} V{y} H{x - 4}" fill="none" stroke="{C["line"]}" stroke-width="1.5"/>')
        last_y[depth] = y
        for d in [k for k in last_y if k > depth]:
            del last_y[d]
        fill, fg = C[kind]
        parts.append(f'<rect x="{x}" y="{y - 7}" width="14" height="14" rx="{7 if kind in ("rev", "fixed") else 3}" '
                     f'fill="{fill}" stroke="{fg}" stroke-width="1.5"/>')
        tx = x + 24
        parts.append(f'<text x="{tx}" y="{y + 5}" font-size="{NAME_PX}" font-weight="700" fill="{C["text"]}">{escape(name)}</text>')
        c = cols[g]
        cx = tx + c["name"] + 16
        for k, (label, ck) in enumerate(chips):
            if label:
                parts.append(chip(cx, y, label, ck)[0])
            cx += c["chips"][k] + 8
        for k, n in enumerate(note if isinstance(note, tuple) else (note,)):
            if n:
                parts.append(f'<text x="{cx + 6:.1f}" y="{y + 5}" font-size="13" fill="{C["muted"]}">{escape(n)}</text>')
            cx += c["note"][k] + 22
        width = max(width, cx)
    width = int(max(width, tw(title, 20) + X0 + 20, tw(subtitle, 13) + X0 + 20) + 28)
    height = Y0 + len(rows) * ROW - 8
    head = (f'<text x="{X0}" y="34" font-size="20" font-weight="700" fill="{C["title"]}">{escape(title)}</text>'
            f'<text x="{X0}" y="56" font-size="13" fill="{C["muted"]}">{escape(subtitle)}</text>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
           f'font-family="{FONT}"><rect width="100%" height="100%" fill="{C["bg"]}"/>{head}{"".join(parts)}</svg>')
    open(path, "w", encoding="utf-8").write(svg)
    return width, height


before = [
    (0, "kr270r2700ultra", "xform", [], "default prim"),
    (1, "kr270r2700ultra", "xform", [], "CAD assembly"),
    (2, "k1", "group", [("6 meshes", "mesh")], "solid_0_0, solid_0_1, solid_0_61 …"),
    (2, "k2", "group", [("12 meshes", "mesh")], "e456vc, e457vc, e458vc …"),
    (2, "k3", "group", [("4 meshes", "mesh")], "e1vc, e2vc, e3vc, e4vc"),
    (2, "k4", "group", [("14 meshes", "mesh")], "e1vc, e2vc, e3vc …"),
    (2, "k5", "group", [("1 mesh", "mesh")], "e1vc"),
    (2, "k6", "group", [("1 mesh", "mesh")], "e1vc"),
    (2, "k7", "group", [("1 mesh", "mesh")], "bo2"),
    (2, "k9", "group", [("2 meshes", "mesh")], "e1vc, e2vc"),
    (2, "k10", "group", [("1 mesh", "mesh")], "e1vc"),
]
after = [
    (0, "kr270r2700ultra", "root", [("ArticulationRoot", "root")], ""),
    (1, "kr270r2700ultra", "xform", [], "42 meshes: material, collider, density"),
    (2, "k1", "body", [("RigidBody est. 233 kg", "body")], "base"),
    (2, "k2", "body", [("RigidBody est. 266 kg", "body")], "rotating column"),
    (2, "k3", "body", [("RigidBody est. 106 kg", "body")], "link arm"),
    (2, "k4", "body", [("RigidBody est. 112 kg", "body")], "arm"),
    (2, "k5", "body", [("RigidBody est. 9.8 kg", "body")], "in-line wrist"),
    (2, "k6", "body", [("RigidBody est. 12 kg", "body")], "wrist housing"),
    (2, "k7", "body", [("RigidBody est. 3.3 kg", "body")], "flange"),
    (2, "k9", "body", [("RigidBody est. 3.8 kg", "body")], "counterbalance cylinder"),
    (2, "k10", "body", [("RigidBody est. 0.5 kg", "body")], "counterbalance rod"),
    (1, "PhysicsScene", "scope", [], ""),
    (1, "Joints", "scope", [], ""),
    (2, "base_fixed", "fixed", [("Fixed", "fixed"), ("", "axis")], ("world → k1", "")),
    (2, "A1", "rev", [("Revolute", "rev"), ("axis Z", "axis")], ("k1 → k2", "−185 … 185°")),
    (2, "A2", "rev", [("Revolute", "rev"), ("axis Y", "axis")], ("k2 → k3", "−50 … 85°")),
    (2, "A3", "rev", [("Revolute", "rev"), ("axis Y", "axis")], ("k3 → k4", "−210 … 65°")),
    (2, "A4", "rev", [("Revolute", "rev"), ("axis X", "axis")], ("k4 → k5", "−350 … 350°")),
    (2, "A5", "rev", [("Revolute", "rev"), ("axis Y", "axis")], ("k5 → k6", "−122.5 … 122.5°")),
    (2, "A6", "rev", [("Revolute", "rev"), ("axis X", "axis")], ("k6 → k7", "−350 … 350°")),
    (2, "k9_fixed", "fixed", [("Fixed", "fixed"), ("", "axis")], ("k2 → k9", "")),
    (2, "k10_fixed", "fixed", [("Fixed", "fixed"), ("", "axis")], ("k3 → k10", "")),
]
if __name__ == "__main__":
    print(render("Before: the converted JT file", "Nine sibling part groups, no bodies, no joints", before, "tree_before.svg"))
    print(render("After: the articulated SimReady asset", "Rigid bodies per link (estimated masses), joints A1–A6, fixed base, articulation root", after, "tree_after.svg"))
