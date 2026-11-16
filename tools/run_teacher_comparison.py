# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Durable, serial campaign for issue #43. Logs and state live in model/runs/.

Run with a persistent process (e.g. nohup); rerunning resumes completed stages.
Never deploys. Both teachers use fresh seed-0 30k/20-epoch students, identical
splits and optimizer settings. Home panels remain exploratory, not confirmation.
"""
import fcntl
import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOB = ROOT / "model/runs/teacher-comparison-43"
BACKENDS = {"siglip": "347251f243f458a6", "gemma": "8075c704d48b6e4e"}


def paired_summary(left, right):
    """Paired differences by contrast, never merge correlated home/scene suites."""
    result = {}
    for suite in left["suites"]:
        a, b = left["suites"][suite], right["suites"][suite]
        if a.keys() != b.keys():
            raise ValueError("Evaluation contrast sets differ")
        rows = []
        for name in a:
            if a[name]["images"] != b[name]["images"] or a[name]["prompts"] != b[name]["prompts"]:
                raise ValueError("Evaluation pixels/prompts differ")
            x = a[name]["metrics"]["standardized"]["signed_auc"]
            y = b[name]["metrics"]["standardized"]["signed_auc"]
            rows.append({"case": name, "siglip": x, "gemma": y, "delta": y-x})
        n = len(rows)
        delta = sum(r["delta"] for r in rows)/n
        se = (sum((r["delta"]-delta)**2 for r in rows)/(n-1)/n)**.5
        result[suite] = {"rows": rows, "mean_delta": delta, "se": se}
    return result


def main():
    JOB.mkdir(exist_ok=True)
    lock = (JOB / "campaign.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    state_path = JOB / "state.json"
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    state = json.loads(state_path.read_text()) if state_path.exists() else {
        "commit": sha, "completed": [], "seed": 0, "subset": 30000, "epochs": 20,
        "batch": 512, "lr": .006, "infonce": .3,
        "gate": "cross-scene delta > 2*SE and home delta >= 0 before int4; exploratory gate, not deployment approval"}
    if state["commit"] != sha:
        raise SystemExit("Campaign source commit changed; inspect before resuming")

    def save():
        state["pid"] = os.getpid()
        state["updated"] = time.time()
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2)+"\n")
        tmp.replace(state_path)

    def stage(name, command, checkpoint=None):
        if name in state["completed"]:
            return
        if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() != sha:
            raise RuntimeError("Source changed between campaign stages")
        state.update(status="running", stage=name, command=command)
        save()
        print(name, flush=True)
        started = time.time()
        with (JOB / f"{name}.log").open("a") as log:
            run = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=False)
        # distill returns 1 for a negative quality diagnostic, not an IO failure.
        training_complete = False
        if checkpoint and checkpoint.exists():
            config_file = checkpoint.parent / "config.json"
            training_complete = (config_file.exists() and config_file.stat().st_mtime >= started
                                 and json.loads(config_file.read_text()).get("completed_epochs") == 20)
        if run.returncode and not (run.returncode == 1 and training_complete):
            raise RuntimeError(f"{name} exited {run.returncode}; see stage log")
        if checkpoint and not checkpoint.exists():
            raise RuntimeError(f"{name} did not produce its checkpoint")
        state.setdefault("returncodes", {})[name] = run.returncode
        state["completed"].append(name)
        save()

    def command(script, *args):
        return ["uv", "run", "--script", script, *map(str, args)]

    try:
        for backend in BACKENDS:
            stage(f"cache-{backend}", command("tools/cache_teacher.py", "--backend", backend,
                  "--split", "train2017", "--subset", 30000, "--holdout", 1000, "--batch", 16))
        for backend, space in BACKENDS.items():
            name = f"compare43-{backend}-s30k-seed0"
            run_dir = ROOT / "model/runs" / name
            stage(f"train-{backend}", command("model/distill.py", "--split", "train2017",
                  "--targets", f"model/cache/teacher_{space}_train2017.npy", "--subset", 30000,
                  "--holdout", 1000, "--epochs", 20, "--batch", 512, "--lr", .006,
                  "--infonce", .3, "--seed", 0, "--name", name), run_dir / "student.pt")
            stage(f"eval-{backend}", command("tools/eval_student_teacher.py", "--run", name))
        configs = [json.loads((ROOT / f"model/runs/compare43-{b}-s30k-seed0/config.json").read_text()) for b in BACKENDS]
        for key in ("seed", "subset", "epochs", "batch", "lr", "infonce", "initial_state_sha256"):
            if configs[0][key] != configs[1][key]:
                raise ValueError(f"Training configuration mismatch: {key}")
        state["initial_state_sha256"] = configs[0]["initial_state_sha256"]
        reports = [json.loads((ROOT / f"model/runs/compare43-{b}-s30k-seed0/selection-fp32.json").read_text()) for b in BACKENDS]
        comparison = paired_summary(*reports)
        (JOB / "comparison-fp32.json").write_text(json.dumps(comparison, indent=2)+"\n")
        scene = comparison["cross-scene"]
        go = scene["mean_delta"] > 2*scene["se"] and comparison["home"]["mean_delta"] >= 0
        state["int4_gate_passed"] = go
        if go:
            for backend in BACKENDS:
                name = f"compare43-{backend}-s30k-seed0"
                stage(f"quantize-{backend}", command("model/quantize.py", "--run", name))
                stage(f"export-{backend}", command("model/export.py", "--run", name, "--wbits", 4, "--wsearch", "--ends8"))
                stage(f"int4-{backend}", command("tools/eval_student_teacher.py", "--run", name, "--int4"))
            reports = [json.loads((ROOT / f"model/runs/compare43-{b}-s30k-seed0/selection-int4.json").read_text()) for b in BACKENDS]
            (JOB / "comparison-int4.json").write_text(json.dumps(paired_summary(*reports), indent=2)+"\n")
        state["status"] = "complete" if go else "complete-no-int4-gain-gate"
        save()
    except Exception as exc:
        state.update(status="failed", error=str(exc))
        save()
        raise


if __name__ == "__main__":
    main()
