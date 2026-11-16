# /// script
# requires-python = ">=3.11"
# dependencies = ["torch", "torchvision", "numpy", "pillow", "open_clip_torch",
#                 "sentencepiece", "transformers>=5.18.0", "sentence-transformers>=6.1.0"]
# ///
"""Space safety and compatibility tests; no model downloads required."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "model"))
import artifacts
import data
import evaluate
import spaces
import teacher
from teacher_backend import (
    GEMMA_SPEC,
    SIGLIP_SPEC,
    GemmaTeacher,
    checkpoint_targets,
    gemma_revision,
    identity,
    load_targets,
)


class BackendTests(unittest.TestCase):
    def test_gemma_requires_revision_and_rejects_pca(self):
        for spec in ("gemma:google/embeddinggemma-2", "gemma:google/embeddinggemma-2@main"):
            with self.assertRaises(ValueError):
                gemma_revision(spec)
        with self.assertRaises(ValueError):
            identity(GEMMA_SPEC, Path("wrong.npz"))

    def test_host_text_and_image_use_same_normalized_mrl(self):
        class FakeModel:
            def encode(self, values, **kwargs):
                self.last = values, kwargs
                return np.tile(np.arange(1, 769, dtype=np.float32), (len(values), 1))
        model = GemmaTeacher.__new__(GemmaTeacher)
        model.model = FakeModel()
        text = teacher.encode_queries_spec(model, None, ["a fist"], "cpu")
        images = model.encode_images(["test-image"])
        np.testing.assert_array_equal(text, images)
        self.assertEqual(text.shape, (1, 512))
        np.testing.assert_allclose(np.linalg.norm(text, axis=1), 1)
        self.assertEqual(model.model.last[0], [{"image": "test-image"}])
        self.assertEqual(model.model.last[1]["prompt"], "")
        with self.assertRaises(ValueError):
            teacher.encode_queries_spec(model, None, ["a fist"], "cpu", basis={})

    def test_cache_checks_identity_order_hash_and_completeness(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(data, "image_list", return_value=["a.jpg", "b.jpg"]):
            root = Path(tmp)
            with patch.object(data, "CACHE", root):
                tag = f"teacher_{identity(GEMMA_SPEC)['space_id'][:16]}_val2017"
                path = root / f"{tag}.npy"
                values = np.zeros((2, 512), dtype=np.float16)
                values[0, 0] = 1
                np.save(path, values)
                config = identity(GEMMA_SPEC)
                side = {**config, "backend_cache": 1, "teacher": GEMMA_SPEC,
                        "split": "val2017", "names_sha256": hashlib.sha256(json.dumps(["a.jpg", "b.jpg"]).encode()).hexdigest(),
                        "targets_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                path.with_suffix(".json").write_text(json.dumps(side))
                load_targets(tag, "val2017")
                # Gemma has no PCA; it must still select its own targets, not CLIP.
                ref, _, calibration, label = evaluate.teacher_bundle(tag, "val2017", "val2017", "cpu")
                np.testing.assert_array_equal(ref, values)
                np.testing.assert_array_equal(calibration, values)
                self.assertEqual(label, GEMMA_SPEC)
                ckpt = {"teacher": tag, "teacher_space": config["space_id"], "split": "val2017"}
                checkpoint_targets(ckpt)
                with self.assertRaises(ValueError):
                    load_targets(tag, "val2017", require_complete=True)
                with self.assertRaises(ValueError):
                    checkpoint_targets({**ckpt, "teacher_space": "wrong"})
                with patch.object(data, "image_list", return_value=["b.jpg", "a.jpg"]), self.assertRaises(ValueError):
                    load_targets(tag, "val2017")
                path.write_bytes(path.read_bytes() + b"tampered")
                with self.assertRaises(ValueError):
                    load_targets(tag, "val2017")

    def test_shipped_siglip_export_still_resolves(self):
        root = Path(__file__).resolve().parent.parent
        meta = artifacts.load_export(root / "model/runs/so400m-full-a05/export")
        self.assertEqual(meta["spec"], SIGLIP_SPEC)
        self.assertIsNotNone(meta["basis_path"])
        self.assertNotEqual(identity(SIGLIP_SPEC, meta["basis_path"])["space_id"], identity(GEMMA_SPEC)["space_id"])

    def test_unknown_backend_tag_never_falls_back_to_clip(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(spaces, "CACHE", Path(tmp)), self.assertRaises(ValueError):
            spaces.resolve("teacher_missing_val2017")

    def test_gemma_export_rejects_missing_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "export.json").write_text(json.dumps({"spec": GEMMA_SPEC, "embed_dim": 512}))
            with self.assertRaises(SystemExit):
                artifacts.load_export(root)


if __name__ == "__main__":
    unittest.main()
