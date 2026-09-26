#!/usr/bin/env python3
"""Regression tests for duplicate-report visual quality assessment."""

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "library_maintainer"
    / "plex_library_maintainer.py"
)

spec = importlib.util.spec_from_file_location("plex_library_maintainer", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


class VisualQualityAssessmentTests(unittest.TestCase):
    def test_sharper_when_blur_is_materially_lower_without_more_blocking(self):
        left = {"samples": 6, "blur": 2.0, "blockiness": 1.0}
        right = {"samples": 6, "blur": 3.0, "blockiness": 1.0}

        label, confidence = module.classify_visual_pair(left, right)

        self.assertEqual(label, "SHARPER")
        self.assertEqual(confidence, "HIGH")

    def test_cleaner_but_softer_tradeoff(self):
        left = {"samples": 6, "blur": 3.0, "blockiness": 0.5}
        right = {"samples": 6, "blur": 2.0, "blockiness": 1.0}

        label, confidence = module.classify_visual_pair(left, right)

        self.assertEqual(label, "CLEANER BUT SOFTER")
        self.assertEqual(confidence, "HIGH")

    def test_similar_for_small_metric_differences(self):
        left = {"samples": 6, "blur": 2.05, "blockiness": 1.05}
        right = {"samples": 6, "blur": 2.0, "blockiness": 1.0}

        label, confidence = module.classify_visual_pair(left, right)

        self.assertEqual(label, "SIMILAR")
        self.assertEqual(confidence, "MEDIUM")

    def test_inconclusive_when_metrics_missing(self):
        label, confidence = module.classify_visual_pair(
            {"samples": 2, "blur": None, "blockiness": None},
            {"samples": 6, "blur": 2.0, "blockiness": 1.0},
        )

        self.assertEqual(label, "INCONCLUSIVE")
        self.assertEqual(confidence, "LOW")

    def test_visual_columns_are_part_of_export_schema(self):
        for field in (
            "visual_samples",
            "visual_blur",
            "visual_blockiness",
            "visual_assessment",
            "visual_confidence",
            "visual_notes",
        ):
            self.assertIn(field, module.DUPLICATE_TSV_FIELDNAMES)


if __name__ == "__main__":
    unittest.main()
