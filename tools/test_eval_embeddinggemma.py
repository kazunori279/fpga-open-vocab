# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy"]
# ///
"""Numerical safeguards for the teacher evaluation in issue #43."""
import unittest

import numpy as np
from eval_embeddinggemma import home_dataset, metrics, unit


class EvaluationTests(unittest.TestCase):
    def test_signed_auc_does_not_hide_inverted_semantics(self):
        images = unit([[2, 0], [1, 0], [0, 2], [0, 1]])
        text = np.eye(2)
        for metric in ("raw", "standardized"):
            self.assertEqual(metrics(images, text, 2)[metric]["signed_auc"], 1)
            inverted = metrics(images, text[::-1], 2)[metric]
            self.assertEqual(inverted["signed_auc"], 0)
            self.assertEqual(inverted["folded_auc"], 1)

    def test_ties_are_half_credit_even_with_constant_scores(self):
        images = np.ones((4, 2))
        for result in metrics(images, np.eye(2), 2).values():
            self.assertEqual(result["signed_auc"], 0.5)

    def test_truncation_is_renormalized(self):
        vectors = unit([[1, 2, 3], [3, 2, 1]])
        truncated = unit(vectors[:, :2])
        np.testing.assert_allclose(np.linalg.norm(truncated, axis=1), 1)
        self.assertFalse(np.allclose(vectors[:, :2], truncated))

    def test_home_screen_uses_archived_labels_and_all_approved_cases(self):
        records = {name: (paths, npos, prompts)
                   for name, paths, npos, prompts in home_dataset()}
        self.assertEqual(set(records), {"towel", "mug", "banana", "glasses",
                                        "tray", "plant", "hand_control"})
        self.assertEqual(records["hand_control"][2], ["an open hand", "a fist"])
        self.assertEqual(records["banana"][2], ["an unpeeled banana", "a peeled banana"])
        for paths, npos, _ in records.values():
            self.assertEqual(npos, 6)
            self.assertEqual(len(set(paths)), 12)
            self.assertTrue(paths[0].stem.endswith("r0-c0"))
            self.assertTrue(paths[npos].stem.endswith("r0-c1"))
            self.assertTrue(all("-r3-" not in p.name for p in paths))

    def test_bad_vectors_are_rejected(self):
        for bad in ([[0, 0]], [[float("nan"), 1]], [[float("inf"), 1]]):
            with self.assertRaises(ValueError):
                unit(bad)


if __name__ == "__main__":
    unittest.main()
