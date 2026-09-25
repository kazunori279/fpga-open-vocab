"""Host regressions that need neither hardware nor a downloaded teacher."""

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))
sys.path.insert(0, str(ROOT / "model"))

import demo
import spot
import watch
from artifacts import bundle_basis, load_export


class ExportTests(unittest.TestCase):
    def test_shipped_export_without_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "export"
            shutil.copytree(ROOT / "model/runs/so400m-full-a05/export", bundle)
            meta = load_export(bundle)
            self.assertEqual(meta["basis_path"].parent, bundle)
            meta["basis_path"].write_bytes(b"wrong projection")
            with self.assertRaisesRegex(SystemExit, "basis_sha256"):
                load_export(bundle)

    def test_export_copies_and_hashes_basis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "original.npz"
            source.write_bytes(b"frozen projection")
            bundle = root / "export"
            bundle.mkdir()
            meta = {"run": "test", **bundle_basis(source, bundle)}
            (bundle / "export.json").write_text(json.dumps(meta))
            source.unlink()
            self.assertEqual(load_export(bundle)["basis_path"].read_bytes(),
                             b"frozen projection")
            (bundle / meta["basis"]).unlink()
            with self.assertRaisesRegex(SystemExit, "not found"):
                load_export(bundle)

    def test_unprojected_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            (bundle / "export.json").write_text(json.dumps(bundle_basis(None, bundle)))
            self.assertIsNone(load_export(bundle)["basis_path"])


class QueryNameTests(unittest.TestCase):
    def test_wire_name_matches_enrolment_receipt(self):
        for phrase in ("a book", "a" * 24, "a" * 22 + "é", "a" * 21 + "é",
                       "開いている日本語の本", "📚" * 6):
            with self.subTest(phrase=phrase):
                vec = MagicMock()
                vec.astype.return_value.tobytes.return_value = bytes(512 * 4)
                vecs = MagicMock()
                vecs.shape = (1, 512)
                vecs.__iter__.return_value = iter([vec])
                body = demo.pack_queries([phrase], vecs, [(1.0, 0.0, 1.0)])
                slot = body[16:40]
                self.assertEqual(slot[-1], 0)
                name = slot.split(b"\0", 1)[0].decode("utf-8", "strict")
                self.assertEqual(name, spot.board_name(phrase))
                receipt = f"enrol     : {name}, level +0.12, scatter 0.66 (20 frames, visit 1 of 2)"
                self.assertEqual(spot.ENROL_ONE.match(receipt).group(1), name)


class WatchTests(unittest.TestCase):
    def test_restart_requires_fresh_consecutive_frames(self):
        args = SimpleNamespace(confirm=5, floor=0.0, queries=["book"],
                               on_change=None, port=None, restart=True,
                               restart_wait=0.0)
        calls = 0

        def run_once(_args, _queries, _sched, debounce, _sink):
            nonlocal calls
            calls += 1
            for _ in range(4):
                self.assertIsNone(debounce.push("book", 1.0))
            if calls == 1:
                return 1, None
            self.assertEqual(debounce.push("book", 1.0)[:2], ("book", None))
            raise KeyboardInterrupt

        with patch.object(watch, "run_once", side_effect=run_once), \
             patch.object(watch, "bootsel_note", return_value=None), \
             patch.object(watch.time, "sleep"), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(watch.supervise(args, [], None), 0)
        self.assertEqual(calls, 2)


if __name__ == "__main__":
    unittest.main()
