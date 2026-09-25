"""Exercise real testbench failure branches in temporary copies."""

import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RTL = ROOT / "rtl"
VEC = RTL / "build/vec"


class SimulationExitTests(unittest.TestCase):
    def test_failure_status(self):
        cases = [
            ("tb_link.v", False, "if (fail_narrow + fail_wide == 0)", "if (1 == 0)"),
        ]
        for name, wide in (("tb_gemm.v", False), ("tb_gemm_link.v", False),
                           ("tb_gemm_link.v", True)):
            cases.append((name, wide, "if (ncase !== `VEC_NCASE)", "if (1 == 1)"))
            delay = "#400000000;" if name == "tb_gemm.v" else "#4000000000;"
            cases.append((name, wide, delay, "#1;"))
        for name, wide, old, new in cases:
            with self.subTest(testbench=name, wide=wide, branch=old), \
                 tempfile.TemporaryDirectory() as tmp:
                source = (RTL / name).read_text()
                self.assertIn(old, source)
                source = source.replace(old, new)
                path = Path(tmp)
                (path / "tb.v").write_text(source)
                cmd = ["iverilog", "-g2005", "-o", str(path / "tb"),
                       f'-DVECDIR="{VEC}"', f"-I{VEC}"]
                if wide:
                    cmd.append("-DLINK_WIDE")
                cmd.append(str(path / "tb.v"))
                if name == "tb_link.v":
                    cmd += ["link_core.v", "link_narrow.v", "link_wide.v"]
                else:
                    cmd += ["gemm_tile.v", "im2col_feed.v"]
                    if name == "tb_gemm_link.v":
                        cmd += ["gemm_link.v", "gemm_top_wide.v" if wide else "gemm_top.v"]
                subprocess.run(cmd, cwd=RTL, check=True, capture_output=True, text=True)
                result = subprocess.run([str(path / "tb"), "+cases=1"], cwd=RTL,
                                        capture_output=True, text=True, timeout=180, check=False)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("FAIL", result.stdout)


if __name__ == "__main__":
    unittest.main()
