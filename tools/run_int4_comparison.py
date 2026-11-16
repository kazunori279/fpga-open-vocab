# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Continue #43 with BOTH int4 demos, regardless of the replacement-gain gate.

The user requested an independently selectable Gemma demonstration. Preserve
the historical fp32 campaign, verify its checkpoints, and record this separately.
No hardware access or deployment. Resume completed stages from the same commit.
"""
import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from run_teacher_comparison import BACKENDS, paired_summary

ROOT = Path(__file__).resolve().parent.parent
JOB = ROOT / "model/runs/teacher-comparison-43/int4"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--accept-source-update", action="store_true",
                        help="record an explicitly reviewed continuation-code update")
    args = parser.parse_args()
    JOB.mkdir(parents=True, exist_ok=True)
    lock = (JOB / "campaign.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    path = JOB / "state.json"
    checkpoints = {}
    for backend in BACKENDS:
        run = ROOT / f"model/runs/compare43-{backend}-s30k-seed0"
        digest = hashlib.sha256((run / "student.pt").read_bytes()).hexdigest()
        original = json.loads((run / "selection-fp32.json").read_text())
        if original["checkpoint_sha256"] != digest:
            raise ValueError("Checkpoint changed since fp32 evaluation")
        checkpoints[backend] = digest
    state = json.loads(path.read_text()) if path.exists() else {
        "commit": commit, "checkpoints": checkpoints, "completed": [],
        "objective": "Support selectable SigLIP and Gemma int4 demos; no relative-gain gate",
        "input_difference": "fp32 uses RGB888; C int4 includes RGB565 conversion",
        "parent": "../state.json"}
    if state["checkpoints"] != checkpoints:
        raise ValueError("Continuation checkpoint identity changed")
    if state["commit"] != commit:
        if not args.accept_source_update:
            raise ValueError("Continuation source changed; review before --accept-source-update")
        state.setdefault("history", []).append({"commit": state["commit"],
                                                 "status": state["status"], "error": state.get("error")})
        state["commit"] = commit
        state.pop("error", None)

    def save():
        state.update(pid=os.getpid(), updated=time.time())
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(state, indent=2)+"\n")
        temp.replace(path)

    def stage(name, script, *args):
        if name in state["completed"]:
            return
        command = ["uv", "run", "--script", script, *args]
        state.update(status="running", stage=name, command=command)
        save()
        started = time.time()
        with (JOB / f"{name}.log").open("w") as log:
            result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=False)
        state.setdefault("returncodes", {})[name] = result.returncode
        if result.returncode == 1 and script == "model/quantize.py":
            log_text = (JOB / f"{name}.log").read_text()
            artifact = ROOT / "model/runs" / args[1] / "quant.json"
            if ("RESULT : FAIL - int8 moves the output embedding" in log_text
                    and artifact.exists() and artifact.stat().st_mtime >= started):
                state.setdefault("quality_warnings", {})[name] = (
                    "int8 cosine diagnostic failed; calibration exists; continue to measure int4 task quality")
                state["completed"].append(name)
                save()
                return
        if result.returncode:
            raise RuntimeError(f"{name} failed with exit {result.returncode}; inspect its log")
        state["completed"].append(name)
        save()

    try:
        for backend in BACKENDS:
            name = f"compare43-{backend}-s30k-seed0"
            stage(f"quantize-{backend}", "model/quantize.py", "--run", name)
            stage(f"export-{backend}", "model/export.py", "--run", name,
                  "--wbits", "4", "--wsearch", "--ends8")
            stage(f"eval-{backend}", "tools/eval_student_teacher.py", "--run", name, "--int4")
        reports = {backend: json.loads((ROOT / f"model/runs/compare43-{backend}-s30k-seed0/selection-int4.json").read_text())
                   for backend in BACKENDS}
        comparison = paired_summary(reports["siglip"], reports["gemma"])
        (JOB / "comparison-int4.json").write_text(json.dumps(comparison, indent=2)+"\n")
        changes = {}
        for backend, integer in reports.items():
            fp32 = json.loads((ROOT / f"model/runs/compare43-{backend}-s30k-seed0/selection-fp32.json").read_text())
            if fp32["checkpoint_sha256"] != integer["checkpoint_sha256"]:
                raise ValueError("Precision comparison checkpoints differ")
            changes[backend] = {}
            for suite, data in paired_summary(fp32, integer).items():
                rows = [{"case": r["case"], "fp32_rgb888": r["siglip"], "int4_rgb565": r["gemma"],
                         "delta": r["delta"]} for r in data["rows"]]
                changes[backend][suite] = {"rows": rows, "mean_delta": data["mean_delta"], "se": data["se"]}
        (JOB / "precision-changes.json").write_text(json.dumps(changes, indent=2)+"\n")
        state["status"] = "complete"
        save()
    except Exception as exc:
        state.update(status="failed", error=str(exc))
        save()
        raise


if __name__ == "__main__":
    main()
