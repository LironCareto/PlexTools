#!/usr/bin/env python3
"""Regression tests for generic track-merger alignment behaviour."""

import importlib.util
import math
import unittest
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "track_merger"
    / "plex_track_merger.py"
)

spec = importlib.util.spec_from_file_location("plex_track_merger", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


class AlignmentRegressionTests(unittest.TestCase):
    def test_prefers_exact_frame_rate_slope_when_visual_fit_agrees(self):
        expected = 25.0 / (24000.0 / 1001.0)
        intercept = 16.0
        source_times = [180.0, 1200.0, 2400.0, 4200.0, 6000.0, 8400.0]
        jitters = [0.08, -0.04, 0.03, -0.06, 0.05, -0.02]
        matches = [
            module.Match(
                source_time=t,
                target_time=(expected * t) + intercept + jitter,
                distance=0.15,
            )
            for t, jitter in zip(source_times, jitters)
        ]

        model = module.fit_robust_model(
            matches,
            residual_limit=0.75,
            minimum_inliers=3,
            expected_slope=expected,
        )

        self.assertAlmostEqual(model.slope, expected, places=12)
        self.assertAlmostEqual(model.intercept, intercept, delta=0.15)
        self.assertEqual(len(model.matches), len(matches))

    def test_does_not_force_frame_rate_slope_when_visual_mapping_disagrees(self):
        expected = 25.0 / (24000.0 / 1001.0)
        actual = 1.0100
        intercept = 7.5
        source_times = [180.0, 1200.0, 2400.0, 4200.0, 6000.0, 8400.0]
        matches = [
            module.Match(
                source_time=t,
                target_time=(actual * t) + intercept,
                distance=0.12,
            )
            for t in source_times
        ]

        model = module.fit_robust_model(
            matches,
            residual_limit=0.75,
            minimum_inliers=3,
            expected_slope=expected,
        )

        self.assertAlmostEqual(model.slope, actual, places=8)
        self.assertAlmostEqual(model.intercept, intercept, places=6)
        self.assertGreater(abs(model.slope - expected), 0.001)


if __name__ == "__main__":
    unittest.main()
