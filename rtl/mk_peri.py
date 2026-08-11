#!/usr/bin/env python3
"""Build a `.peri.xml` for one of the M2 link designs from its `.isf`.

Efinity's Interface Designer is a GUI, and `efx_run.py` will not run the
interface step without an existing `.peri.xml` to start from. The shipped
`efx_run_pt_import_isf.py` merges an ISF into a design that already exists, so
there is a chicken-and-egg problem for a project that has never been opened in
the GUI. `DesignAPI.create()` is the missing half; this script is just those two
calls plus `generate()`, which writes the periphery netlist that place-and-route
consumes.

Runs inside the Efinity container only - see build.sh.

    mk_peri.py <top> <device>      e.g. mk_peri.py link_narrow T8F49
"""
import os
import sys
from pathlib import Path

sys.path.append(os.environ["EFXPT_HOME"] + "/bin")
from api_service.design import DesignAPI  # noqa: E402


def main(top: str, device: str) -> int:
    isf = Path(f"{top}_io.isf")
    if not isf.exists():
        print(f"ERROR: {isf} not found", file=sys.stderr)
        return 1

    design = DesignAPI(is_verbose=True)
    design.create(top, device, ".", auto_save=False, overwrite=True)

    ok, _issues = design.import_design(str(isf), gen_issue_csv=True)
    if not ok:
        print("ERROR: ISF import failed - see import_issue.csv", file=sys.stderr)
        return 1

    # check_design() reports periphery-level problems (an unassigned pin, a
    # clock on a ball with no global buffer, a bank voltage conflict). It is
    # advisory: a warning here often still routes, so log rather than abort.
    if not design.check_design():
        print("WARNING: check_design() reported issues:")
        for issue in design.get_design_check_issue():
            print(f"  {issue}")

    design.save_as(f"{top}.peri.xml", overwrite=True)
    design.generate(enable_bitstream=True, outdir="outflow")
    print(f"wrote {top}.peri.xml and outflow/ periphery files")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
