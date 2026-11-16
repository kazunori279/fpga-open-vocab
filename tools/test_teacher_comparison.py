# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Paired campaign reporting must not silently compare different inputs."""
import copy
import unittest

from run_teacher_comparison import paired_summary


def report(values):
    return {"suites": {"cross-scene": {
        str(i): {"images": ["same-image"], "prompts": ["a", "b"],
                 "metrics": {"standardized": {"signed_auc": v}}}
        for i, v in enumerate(values)}}}


class CampaignTests(unittest.TestCase):
    def test_paired_difference_and_se(self):
        result = paired_summary(report([.5, .6, .7]), report([.6, .7, .8]))["cross-scene"]
        self.assertAlmostEqual(result["mean_delta"], .1)
        self.assertAlmostEqual(result["se"], 0)

    def test_input_mismatch_rejected(self):
        a = report([.5, .6])
        for field in ("images", "prompts"):
            b = copy.deepcopy(a)
            b["suites"]["cross-scene"]["0"][field] = ["changed"]
            with self.assertRaises(ValueError):
                paired_summary(a, b)

    def test_missing_contrast_rejected(self):
        with self.assertRaises(ValueError):
            paired_summary(report([.5, .6]), report([.5]))


if __name__ == "__main__":
    unittest.main()
