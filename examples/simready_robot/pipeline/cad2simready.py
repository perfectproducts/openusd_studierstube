#!/usr/bin/env python3
"""CAD -> SimReady pipeline orchestrator.

Thin, resumable driver around the stage reference scripts of the
`omniverse-cad-to-simready` skill. Each stage runs the skill's own
`references/<stage>/scripts/run.py`, keeps its JSON/Markdown report under
`<output_root>/pipeline/NN_<stage>/`, and hands the concrete `output_usd_path`
from that report to the next stage. No conversion, assignment or validation
logic lives here.

Stage order (per the skill workflow):
  preflight -> context -> convert -> minimum -> assign -> conform
  -> validate (asset, geometry, physics, simready) -> [conform rerun] -> render -> report

Usage:
  py -3.12 cad2simready.py testfiles/kr270r2700ultra.jt
  py -3.12 cad2simready.py testfiles/kr270r2700ultra.jt --from assign
  py -3.12 cad2simready.py part.jt --skip-assignment        # conversion + validation only
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

SKILL_ROOT = Path(
    os.environ.get(
        "CAD2SIMREADY_SKILL_ROOT",
        Path.home() / ".claude" / "skills" / "omniverse-cad-to-simready",
    )
)
REFS = SKILL_ROOT / "references"
PY = sys.executable

STAGES = ["preflight", "context", "convert", "minimum", "assign", "joints", "conform", "validate", "render", "report"]
VALIDATORS = [
    ("asset", "omni-asset-validate"),
    ("geometry", "omni-asset-validate-geometry"),
    ("physics", "omni-asset-validate-physics"),
]
# simready-validate requirement IDs that simready-conform-profile can repair without extra inputs.
AUTO_REPAIRABLE = {"UN.007", "NP.002", "NP.006", "RB.MB.001"}


class StageBlocked(RuntimeError):
    pass


def multipart(fields: dict[str, str], file_field: str, path: Path) -> tuple[bytes, str]:
    boundary = f"----cad2simready{int(time.time() * 1000)}"
    crlf = "\r\n"
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}{crlf}Content-Disposition: form-data; name="{k}"{crlf}{crlf}{v}{crlf}'.encode())
    parts.append(f'--{boundary}{crlf}Content-Disposition: form-data; name="{file_field}"; '
                 f'filename="{path.name}"{crlf}Content-Type: application/octet-stream{crlf}{crlf}'.encode())
    parts.append(path.read_bytes())
    parts.append(f"{crlf}--{boundary}--{crlf}".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class Pipeline:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.source = Path(args.source).resolve()
        self.name = args.asset_name or re.sub(r"[^A-Za-z0-9_]", "_", self.source.stem)
        self.root = Path(args.output_root or Path("output") / self.name).resolve()
        self.pipe = self.root / "pipeline"
        self.state_path = self.pipe / "pipeline-state.json"
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        self.state.setdefault("steps", {})
        self.env = os.environ.copy()
        self.expose_managed_venvs()
        # Provided (user-owned) endpoints: preflight probes them instead of deploying.
        for var, value in (("RENDER_ENDPOINT", args.render_endpoint),
                           ("CONTENT_AGENTS_MATERIAL_AGENT_BASE_URL", args.material_url),
                           ("CONTENT_AGENTS_PHYSICS_AGENT_BASE_URL", args.physics_url)):
            if value:
                self.env[var] = value

    def expose_managed_venvs(self) -> None:
        """Point preflight at the managed converter / validator venvs it installs but does not export."""
        venvs = Path(self.env.get("OMNIVERSE_CAD_TO_SIMREADY_HOME", Path.home() / ".omniverse-cad-to-simready")) / "venvs"
        bindir = "Scripts" if os.name == "nt" else "bin"
        exe = ".exe" if os.name == "nt" else ""
        cad_py = venvs / "usd-convert-cad" / bindir / f"python{exe}"
        if cad_py.exists():
            self.env.setdefault("USD_CONVERT_CAD_PYTHON", str(cad_py))
        validator_bin = venvs / "simready-validate" / bindir
        if (validator_bin / f"omni_asset_validate{exe}").exists():
            self.env["PATH"] = f"{validator_bin}{os.pathsep}{self.env.get('PATH', '')}"

    # ------------------------------------------------------------------ helpers
    def save(self) -> None:
        self.pipe.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, indent=2))

    def stage_dir(self, idx: int, name: str) -> Path:
        d = self.pipe / f"{idx:02d}_{name}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def run(self, ref: str, *argv: str | Path, log: Path, script: str = "run.py") -> int:
        cmd = [PY, str(REFS / ref / "scripts" / script), *map(str, argv)]
        print(f"\n>> {ref}\n   {' '.join(shlex.quote(c) for c in cmd)}", flush=True)
        with open(log, "w", encoding="utf-8") as fh:
            proc = subprocess.run(cmd, env=self.env, stdout=fh, stderr=subprocess.STDOUT)
        tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-8:]
        print("   " + "\n   ".join(tail), flush=True)
        print(f"   exit={proc.returncode}", flush=True)
        return proc.returncode

    @staticmethod
    def load(report: Path) -> dict:
        return json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}

    def record(self, stage: str, **info) -> None:
        self.state["steps"][stage] = {"finished": dt.datetime.now().isoformat(timespec="seconds"), **info}
        self.save()

    def usd(self, key: str) -> str:
        path = self.state.get(key)
        if not path or not Path(path).exists():
            raise StageBlocked(f"missing USD handoff '{key}' ({path}); rerun the earlier stage")
        return path

    def load_env_file(self, env_file: Path) -> None:
        """Load a POSIX env file written by preflight (export K=V lines)."""
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            line = line.removeprefix("export ").strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            try:
                value = " ".join(shlex.split(value)) if value else ""
            except ValueError:
                value = value.strip("'\"")
            if key == "PATH":
                continue  # POSIX PATH from preflight does not apply to Windows subprocesses
            self.env[key] = value

    # ------------------------------------------------------------------ stages
    def st_preflight(self) -> None:
        d = self.stage_dir(0, "preflight")
        env_file = d / "cad-to-simready-preflight.env"
        report = d / "cad-to-simready-preflight.json"
        if not (self.args.rerun_preflight or not report.exists() or self.load(report).get("status") != "ready"):
            print(">> preflight: reusing ready manifest", flush=True)
        else:
            argv: list = [
                "--targets", "conversion,validation" + ("" if self.args.skip_assignment else ",content-agents"),
                "--source-asset", self.source, "--output-root", self.root,
                "--report", report, "--env-file", env_file,
                "--powershell-env-file", d / "cad-to-simready-preflight.ps1",
                "--markdown-report", d / "cad-to-simready-preflight.md",
            ]
            if self.args.skip_assignment:
                argv.append("--skip-content-agents")
            elif os.name == "nt" or self.args.skip_deploy:
                # Managed deployment needs a Linux Docker host; on Windows deploy from WSL2
                # (see README) and only verify the endpoints here.
                argv.append("--skip-deploy")
            if self.args.secrets and not (os.name == "nt" or self.args.skip_deploy):
                argv += ["--content-agents-secret-env-file", Path(self.args.secrets).expanduser()]
            self.run("preflight", *argv, log=d / "preflight.log", script="preflight.py")
        rep = self.load(report)
        self.record("preflight", status=rep.get("status"), report=str(report))
        if rep.get("status") != "ready":
            raise StageBlocked(f"preflight not ready: {rep.get('blockers') or rep.get('errors')}")
        self.load_env_file(env_file)

    def st_context(self) -> None:
        d = self.stage_dir(1, "context")
        report = d / "asset-context.json"
        self.run("identify-asset-context", self.source, "--report", report,
                 "--markdown-report", d / "asset-context.md", log=d / "context.log")
        # Web research is agent/human work; its result lives in asset-context-research.json.
        research = self.load(d / "asset-context-research.json")
        prompt = self.args.prompt or research.get("material_physics_prompt")
        self.state["context_prompt"] = prompt
        self.record("context", status="passed" if report.exists() else "failed", report=str(report),
                    research_report=str(d / "asset-context-research.json") if research else None,
                    prompt_source="cli" if self.args.prompt else ("research" if prompt else "none"))
        if not report.exists():
            raise StageBlocked("asset context inspection produced no report")

    def st_convert(self) -> None:
        d = self.stage_dir(2, "convert")
        report = d / "conversion.json"
        self.run("convert-to-usd", self.source, d / "usd", "--report", report,
                 "--markdown-report", d / "conversion.md", log=d / "convert.log")
        rep = self.load(report)
        out = rep.get("output_usd_path")
        self.record("convert", status=rep.get("status", "passed" if out else "failed"),
                    report=str(report), output_usd_path=out, converter=rep.get("selected_converter") or rep.get("converter"))
        if not out or not Path(out).exists():
            raise StageBlocked(f"conversion failed: {rep.get('errors')}")
        self.state["converted_usd"] = out

    def st_minimum(self) -> None:
        d = self.stage_dir(3, "minimum")
        report = d / "minimum-usd.json"
        self.run("validate-usd-minimum", self.usd("converted_usd"), "--report", report,
                 "--markdown-report", d / "minimum-usd.md", log=d / "minimum.log")
        rep = self.load(report)
        self.record("minimum", status="passed" if rep.get("passed") else "failed", report=str(report),
                    metadata=rep.get("metadata"), errors=rep.get("errors"))
        if not rep.get("passed"):
            raise StageBlocked(f"minimum USD validation failed: {rep.get('errors')}")
        self.state["latest_usd"] = self.state["converted_usd"]

    def st_assign(self) -> None:
        if self.args.skip_assignment:
            self.record("assign", status="skipped")
            self.state["sim_usd"] = self.usd("converted_usd")
            return
        d = self.stage_dir(4, "assign")
        report = d / "content-agents.json"
        assign_input = self.usd("converted_usd")
        calls = ["material", "physics"]
        if self.args.resume_material_session:
            assign_input = self.resume_material(self.args.resume_material_session, d / "material")
            calls = ["physics"]
        argv: list = [assign_input, "--output-dir", d]
        for call in calls + (["texture"] if self.args.texture else []):
            argv += ["--call", call]
        argv += ["--convert-physics-output-to-usd", "--timeout", str(self.args.assign_timeout),
                 "--report", report, "--markdown-report", d / "content-agents.md"]
        if self.state.get("context_prompt"):
            argv += ["--prompt", self.state["context_prompt"]]
        self.run("content-agents", *argv, log=d / "assign.log")
        rep = self.load(report)
        out = rep.get("output_usd_path")
        self.record("assign", status=rep.get("status"), report=str(report), output_usd_path=out,
                    textured_usdz_path=rep.get("textured_usdz_path"), errors=rep.get("errors"))
        if not rep.get("passed") or not out or not Path(out).exists():
            raise StageBlocked(f"Content Agents assignment failed: {rep.get('errors')}")
        self.state["sim_usd"] = self.state["physics_usd"] = out

    def resume_material(self, session: str, out_dir: Path) -> str:
        """Wait for an already-submitted Material Agent session and download its output USD."""
        import urllib.request

        base = (self.args.material_url or "http://localhost:8100").rstrip("/")
        deadline = time.monotonic() + self.args.assign_timeout
        while True:
            with urllib.request.urlopen(f"{base}/pipeline/{session}/status", timeout=60) as resp:
                status = json.load(resp)
            state = str(status.get("status", "")).lower()
            step = (status.get("current_step") or {}).get("display_name")
            print(f"   material session {session[:8]}: {state} ({step})", flush=True)
            if state in ("completed", "complete", "succeeded", "success", "done"):
                break
            if state in ("failed", "error", "cancelled", "canceled") or time.monotonic() > deadline:
                raise StageBlocked(f"material session {session} ended as {state}: {status.get('error')}")
            time.sleep(30)
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{Path(self.usd('converted_usd')).stem}_material.usd"
        with urllib.request.urlopen(f"{base}/artifacts/{session}/output", timeout=600) as resp:
            out.write_bytes(resp.read())
        (out_dir / "material-session-status.json").write_text(json.dumps(status, indent=2))
        self.state["materialized_usd"] = str(out)
        print(f"   downloaded {out} ({out.stat().st_size} bytes)", flush=True)
        return str(out)

    def conform(self, usd: str, idx: int, tag: str, repairs: list[str] = (), validation_report: Path | None = None) -> str:
        d = self.stage_dir(idx, tag)
        report = d / "simready-conform-profile.json"
        argv: list = [usd, "--output-dir", d, "--profile", self.args.profile,
                      "--profile-version", self.args.profile_version, "--source-asset", self.source,
                      "--report", report, "--markdown-report", d / "simready-conform-profile.md"]
        if not self.args.skip_assignment:
            argv += ["--pipeline-step", "material-agent-client", "--pipeline-step", "physics-agent-client"]
        for r in repairs:
            argv += ["--repair", r]
        if validation_report:
            argv += ["--validation-report", validation_report]
        for p in self.args.grasp_point or []:
            argv += ["--grasp-point", p]
        self.run("simready-conform-profile", *argv, log=d / "conform.log")
        rep = self.load(report)
        out = rep.get("output_usd_path")
        self.record(tag, status=rep.get("status"), report=str(report), output_usd_path=out,
                    repairs=repairs, errors=rep.get("errors"), warnings=rep.get("warnings"))
        if not out or not Path(out).exists():
            raise StageBlocked(f"conformance authoring failed: {rep.get('errors')}")
        return out

    def st_joints(self) -> None:
        """Joint Agent (research preview): infer revolute/prismatic joints on the physics USD,
        then author drives, joint state and the articulation root (author_robot_drives.py)."""
        if not self.args.joints:
            self.record("joints", status="skipped")
            return
        import urllib.request

        d = self.stage_dir(4, "joints")
        src = self.state.setdefault("physics_usd", self.usd("sim_usd"))
        base = self.args.joint_url.rstrip("/")
        fields = {"render_backend": "remote", "apply_joint_rigger": "true"}
        kin_path = self.pipe / "01_context" / "robot-kinematics.json"
        if kin_path.exists():
            kin = json.loads(kin_path.read_text())
            names = {"k1": "fixed base"} | {j["child"]: f"link moved by {j['name']}" for j in kin["joints"]}
            fields["user_prompt"] = (
                f"{kin['model']}, {len(kin['joints'])}-axis serial robot, floor mounted, Z up, stage units mm. "
                "The top-level Xforms under the asset root are the rigid links: "
                + ", ".join(f"{k}={v}" for k, v in names.items())
                + "; " + ", ".join(f"{k}={v}" for k, v in kin.get("fixed_links", {}).items() if k != "k1")
                + ". Joints (parent->child, axis, pivot mm, range deg): "
                + "; ".join(f"{j['name']} {j['parent']}->{j['child']} revolute about {j['axis']} at "
                            f"{tuple(j['pivot_mm'])}, {j['limits_deg'][0]}..{j['limits_deg'][1]}" for j in kin["joints"])
                + ". Assign node roles only to prims inside k1..k10; never assign a role to the asset root prim.")
        elif self.state.get("context_prompt"):
            fields["user_prompt"] = ("Industrial serial robot arm; identify the rotary axes between base, "
                                     "rotating column, links and wrist. " + self.state["context_prompt"])
        (d / "joint-user-prompt.txt").write_text(fields.get("user_prompt", ""))
        session = self.args.resume_joint_session
        if not session:
            body, ctype = multipart(fields, "usd_file", Path(src))
            req = urllib.request.Request(f"{base}/pipeline", data=body, headers={"Content-Type": ctype})
            with urllib.request.urlopen(req, timeout=600) as resp:
                session = json.load(resp)["session_id"]
        print(f"   joint session {session}", flush=True)
        self.state["joint_session"] = session
        self.save()
        status = self.wait_session(base, session, "joint")
        (d / "joint-session-status.json").write_text(json.dumps(status, indent=2))
        for name, fn in (("predictions", "predictions.jsonl"), ("report", "joint-report.html"),
                         ("joint-rigger-diagnostics", "joint-rigger-diagnostics.json")):
            try:
                with urllib.request.urlopen(f"{base}/artifacts/{session}/{name}", timeout=600) as resp:
                    (d / fn).write_bytes(resp.read())
            except Exception as exc:  # noqa: BLE001 - optional artifacts
                print(f"   (no {name}: {exc})", flush=True)
        rigged = d / "rigged.usdz"
        rigged.unlink(missing_ok=True)
        agent_error = None
        try:
            with urllib.request.urlopen(f"{base}/artifacts/{session}/joint-rigger-output", timeout=600) as resp:
                rigged.write_bytes(resp.read())
        except Exception as exc:  # noqa: BLE001
            agent_error = f"Joint Agent produced no rigged package (no accepted joint candidates?): {exc}"
            print(f"   {agent_error}", flush=True)
        if not kin_path.exists():
            raise StageBlocked(agent_error or f"no kinematics spec {kin_path} for drive authoring")
        # The datasheet spec is authoritative; the Joint Agent package is only cross-checked against it.
        out = d / f"{Path(src).stem}_articulated.usda"
        rc = self.run_py("author_robot_drives.py", src, rigged if rigged.exists() else "-", out,
                         "--kinematics", kin_path, "--report", d / "drives.json", log=d / "drives.log")
        rep = self.load(d / "drives.json")
        self.record("joints", status="passed" if rc == 0 and out.exists() else "failed", session=session,
                    report=str(d / "drives.json"), rigged_usdz=str(rigged) if rigged.exists() else None,
                    joint_agent_error=agent_error, output_usd_path=str(out),
                    joints=[j["name"] for j in rep.get("joints") or []], articulation_root=rep.get("articulation_root"))
        if rc != 0 or not out.exists():
            raise StageBlocked(f"joint/drive authoring failed: {rep.get('errors')}")
        self.state["sim_usd"] = str(out)

    def wait_session(self, base: str, session: str, label: str) -> dict:
        import urllib.request

        deadline = time.monotonic() + self.args.assign_timeout
        last = None
        while True:
            with urllib.request.urlopen(f"{base}/pipeline/{session}/status", timeout=60) as resp:
                status = json.load(resp)
            state = str(status.get("status", "")).lower()
            step = (status.get("current_step") or {}).get("display_name")
            if (state, step) != last:
                print(f"   {label} session {session[:8]}: {state} ({step})", flush=True)
                last = (state, step)
            if state in ("completed", "complete", "succeeded", "success", "done"):
                return status
            if state in ("failed", "failure", "error", "cancelled", "canceled") or time.monotonic() > deadline:
                raise StageBlocked(f"{label} session {session} ended as {state}: {status.get('error')}")
            time.sleep(15)

    def run_py(self, script: str, *argv, log: Path) -> int:
        """Run a repo-local helper with the managed OpenUSD venv."""
        venv = Path.home() / ".omniverse-cad-to-simready" / "venvs" / "simready-validate"
        py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        cmd = [str(py), str(Path(__file__).with_name(script)), *map(str, argv)]
        print(f"\n>> {script}\n   {' '.join(shlex.quote(c) for c in cmd)}", flush=True)
        with open(log, "w", encoding="utf-8") as fh:
            proc = subprocess.run(cmd, env=self.env, stdout=fh, stderr=subprocess.STDOUT)
        print("   " + "\n   ".join(log.read_text(encoding="utf-8").strip().splitlines()[-8:]), flush=True)
        print(f"   exit={proc.returncode}", flush=True)
        return proc.returncode

    def st_conform(self) -> None:
        self.state["conformed_usd"] = self.conform(self.usd("sim_usd"), 5, "conform")

    def validate(self, usd: str, idx: int, tag: str) -> list[str]:
        """Run all validation gates; findings never stop the pipeline. Returns failing requirement IDs."""
        d = self.stage_dir(idx, tag)
        results = {}
        for key, ref in VALIDATORS:
            report = d / f"{key}.json"
            self.run(ref, usd, "--report", report, "--markdown-report", d / f"{key}.md", log=d / f"{key}.log")
            rep = self.load(report)
            results[key] = {"passed": rep.get("passed"), "report": str(report),
                            "errors": len(rep.get("errors") or []), "warnings": len(rep.get("warnings") or [])}
        report = d / "simready-profile.json"
        self.run("simready-validate", usd, "--profile", self.args.profile, "--profile-version",
                 self.args.profile_version, "--report", report, "--markdown-report", d / "simready-profile.md",
                 log=d / "simready.log")
        rep = self.load(report)
        failing = sorted(set(re.findall(r"\b[A-Z]{2,4}(?:\.[A-Z]{2,4})?\.\d{3}\b", json.dumps(rep.get("errors") or rep.get("failures") or []))))
        results["simready"] = {"passed": rep.get("passed"), "status": rep.get("status"), "report": str(report),
                               "failing_requirements": failing}
        self.record(tag, usd=usd, gates=results,
                    status="passed" if all(r.get("passed") for r in results.values()) else "needs_rerun")
        return [] if rep.get("passed") else failing

    def st_validate(self) -> None:
        usd = self.usd("conformed_usd")
        failing = self.validate(usd, 6, "validate")
        repairs = sorted(set(failing) & AUTO_REPAIRABLE)
        if "GSP.001" in failing and not self.args.grasp_point:
            self.state["fet005_blocked"] = ("GSP.001 needs visually selected grasp points; render the asset, "
                                            "pick points and rerun with --grasp-point x,y,z --from conform")
        elif "GSP.001" in failing:
            repairs.append("GSP.001")
        if repairs and not self.args.no_repair_loop:
            self.state["conformed_usd"] = self.conform(usd, 7, "conform_rerun", repairs,
                                                       self.pipe / "06_validate" / "simready-profile.json")
            self.validate(self.state["conformed_usd"], 8, "validate_rerun")
        self.save()

    def st_render(self) -> None:
        d = self.stage_dir(9, "render")
        png = d / "thumbnail.png"
        report = d / "ovrtx-render-service.json"
        self.run("ovrtx-render-service", self.usd("conformed_usd"), png, "--report", report,
                 "--markdown-report", d / "ovrtx-render-service.md", log=d / "render.log")
        rep = self.load(report)
        self.record("render", status="passed" if rep.get("passed") else "failed", report=str(report),
                    render_preview_path=str(png) if rep.get("passed") and png.exists() else None,
                    errors=rep.get("errors"))

    def st_report(self) -> None:
        steps = self.state["steps"]
        gates = (steps.get("validate_rerun") or steps.get("validate") or {}).get("gates", {})
        failed_hard = [s for s in ("preflight", "convert", "minimum", "assign", "conform")
                       if s in steps and steps[s].get("status") not in ("ready", "passed", "skipped", "success", None)
                       and not steps[s].get("output_usd_path")]
        rerun = [f"{g}: {', '.join(v.get('failing_requirements') or []) or 'failed'}"
                 for g, v in gates.items() if not v.get("passed")]
        if steps.get("render", {}).get("status") == "failed":
            rerun.append("render: OVRTX render failed")
        if self.state.get("fet005_blocked"):
            rerun.append("FET005 (GSP.001) blocked: " + self.state["fet005_blocked"])
        if self.state.get("blocked"):
            overall = "blocked"
            rerun.insert(0, "blocked: " + self.state["blocked"])
        else:
            overall = "failed" if failed_hard else ("needs_rerun" if rerun else "passed")
        summary = {
            "status": overall, "passed": overall == "passed", "needs_rerun": overall == "needs_rerun",
            "rerun_reasons": rerun, "source_asset_path": str(self.source),
            "source_format": self.source.suffix.lstrip(".").lower(), "output_root": str(self.root),
            "simready_profile": f"{self.args.profile}@{self.args.profile_version}",
            "property_assignment_status": steps.get("assign", {}).get("status"),
            "output_usd_path": self.state.get("converted_usd"),
            "physics_usd_path": self.state.get("sim_usd"),
            "conformed_usd_path": self.state.get("conformed_usd"),
            "render_preview_path": steps.get("render", {}).get("render_preview_path"),
            "steps": steps,
        }
        (self.root / "omniverse-cad-to-simready-report.json").write_text(json.dumps(summary, indent=2))
        lines = [f"# CAD to SimReady report: {self.name}", "", f"**Overall status:** `{overall}`", "",
                 f"- Source: `{self.source}`", f"- Profile: `{summary['simready_profile']}`",
                 f"- Converted USD: `{summary['output_usd_path']}`", f"- Physics USD: `{summary['physics_usd_path']}`",
                 f"- Final (conformed) USD: `{summary['conformed_usd_path']}`",
                 f"- Render: `{summary['render_preview_path']}`", "", "## Stages", "",
                 "| Stage | Status | Report |", "|---|---|---|"]
        for name, s in steps.items():
            if name == "report":
                continue
            lines.append(f"| {name} | {s.get('status')} | `{s.get('report', '')}` |")
        lines += ["", "## Validation gates", "", "| Gate | Passed | Failing requirements |", "|---|---|---|"]
        for g, v in gates.items():
            lines.append(f"| {g} | {v.get('passed')} | {', '.join(v.get('failing_requirements') or []) or '-'} |")
        lines += ["", "## Rerun reasons", ""] + [f"- {r}" for r in rerun or ["none"]]
        (self.root / "omniverse-cad-to-simready-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.record("report", status=overall, report=str(self.root / "omniverse-cad-to-simready-report.md"))
        print(f"\n== overall: {overall}\n   {self.root / 'omniverse-cad-to-simready-report.md'}")

    # ------------------------------------------------------------------ driver
    def main(self) -> int:
        if not self.source.exists():
            print(f"source asset not found: {self.source}", file=sys.stderr)
            return 2
        self.state.update(source=str(self.source), output_root=str(self.root))
        self.state.pop("blocked", None)
        start = STAGES.index(self.args.start)
        stop = STAGES.index(self.args.stop)
        try:
            # preflight always runs (cheap when ready) so downstream stages get the manifest env
            self.st_preflight()
            for stage in STAGES[max(start, 1): stop + 1]:
                getattr(self, f"st_{stage}")()
        except StageBlocked as exc:
            print(f"\n!! BLOCKED: {exc}", file=sys.stderr)
            self.state["blocked"] = str(exc)
            self.save()
            self.st_report()
            return 1
        return 0


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("source", help="CAD/mesh/USD source asset")
    p.add_argument("--output-root", help="default: output/<asset_name>")
    p.add_argument("--asset-name")
    p.add_argument("--profile", default="Prop-Robotics-Neutral")
    p.add_argument("--profile-version", default="1.0.0")
    p.add_argument("--prompt", help="material/physics context prompt (default: from 01_context/asset-context-research.json)")
    p.add_argument("--secrets", default="~/.omniverse-cad-to-simready/secrets.env",
                   help="private dotenv with provider keys for Content Agents deployment")
    p.add_argument("--skip-assignment", action="store_true", help="conversion + validation only (no Content Agents)")
    p.add_argument("--texture", action="store_true", help="also run the Texture Agent")
    p.add_argument("--joints", action="store_true",
                   help="run the Joint Agent + drive authoring (use with --profile Robot-Body-Neutral)")
    p.add_argument("--resume-joint-session", metavar="SESSION_ID",
                   help="reuse a running/finished Joint Agent session instead of submitting a new one")
    p.add_argument("--joint-url", default=os.environ.get("CONTENT_AGENTS_JOINT_AGENT_BASE_URL", "http://localhost:8400"))
    p.add_argument("--grasp-point", action="append", help="x,y,z grasp point for FET005 (repeat twice)")
    p.add_argument("--no-repair-loop", action="store_true")
    p.add_argument("--assign-timeout", type=int, default=7200,
                   help="seconds to wait per Content Agents session (skill default 1800 is too short for big CAD)")
    p.add_argument("--resume-material-session", metavar="SESSION_ID",
                   help="reuse a running/finished Material Agent session (e.g. after a client timeout); runs physics only")
    p.add_argument("--rerun-preflight", action="store_true")
    p.add_argument("--skip-deploy", action="store_true",
                   help="only verify Content Agents endpoints (implied on Windows)")
    win = os.name == "nt"
    p.add_argument("--render-endpoint", default=os.environ.get("RENDER_ENDPOINT") or
                   ("http://host.docker.internal:8001" if win else None),
                   help="OVRTX rendering API (on Windows: ovrtx_adapter.py, reachable from containers)")
    p.add_argument("--material-url", default=os.environ.get("CONTENT_AGENTS_MATERIAL_AGENT_BASE_URL") or
                   ("http://localhost:8100" if win else None))
    p.add_argument("--physics-url", default=os.environ.get("CONTENT_AGENTS_PHYSICS_AGENT_BASE_URL") or
                   ("http://localhost:8200" if win else None))
    p.add_argument("--from", dest="start", choices=STAGES, default="preflight")
    p.add_argument("--to", dest="stop", choices=STAGES, default="report")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(Pipeline(parse_args()).main())
