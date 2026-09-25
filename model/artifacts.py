"""Portable model export metadata and frozen projection artifacts."""

import hashlib
import json
from pathlib import Path


def load_export(path: Path) -> dict:
    """export.json, and the basis file it names, resolved to a usable pair.

    The sidecar is the only thing that says which of the two 512-d spaces a blob
    emits into, so a missing one is fatal rather than a default: the fallback
    would be a guess, and a wrong guess here produces scores instead of errors.
    Re-export the run to get one.
    """
    side = path / "export.json"
    if not side.exists():
        raise SystemExit(
            f"{side}: not found. The query space cannot be guessed - both "
            f"shipped teachers are 512-d, so the wrong one would score instead "
            f"of failing. Re-export:\n  uv run model/export.py --run <RUN> "
            f"--wbits 4 --wsearch --ends8")
    blob = json.loads(side.read_text())
    if blob.get("basis"):
        b = path / blob["basis"]
        if not b.exists():
            raise SystemExit(
                f"{b}: not found, and {blob['run']} emits into the projected "
                f"space it defines. Restore the original basis from the model export")
        expected = blob.get("basis_sha256")
        if not expected or hashlib.sha256(b.read_bytes()).hexdigest() != expected:
            raise SystemExit(f"{b}: missing or mismatched basis_sha256; "
                             "restore the original model export")
        blob["basis_path"] = b
    else:
        blob["basis_path"] = None
    return blob


def bundle_basis(basis: Path | None, destination: Path) -> dict:
    """Copy the exact fitted projection; never refit it during export."""
    if basis is None:
        return {"basis": None}
    data = basis.read_bytes()
    (destination / basis.name).write_bytes(data)
    return {"basis": basis.name, "basis_sha256": hashlib.sha256(data).hexdigest()}
