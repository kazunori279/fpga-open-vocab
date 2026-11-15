# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy"]
# ///
"""Numerical safeguards for the teacher evaluation in issue #43."""
import unittest

import numpy as np
from eval_embeddinggemma import metrics, unit


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

    def test_bad_vectors_are_rejected(self):
        for bad in ([[0, 0]], [[float("nan"), 1]], [[float("inf"), 1]]):
            with self.assertRaises(ValueError):
                unit(bad)


if __name__ == "__main__":
    unittest.main()
