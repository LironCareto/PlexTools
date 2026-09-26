#!/usr/bin/env python3
"""CLI help regression tests for PlexLibraryMaintainer."""

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


class LibraryMaintainerHelpTests(unittest.TestCase):
    def test_help_surfaces_duplicate_report_workflow(self):
        help_text = module.build_parser().format_help()

        self.assertIn("M4 - duplicate media analysis", help_text)
        self.assertIn("--report {duplicates}", help_text)
        self.assertIn("--probe-media", help_text)
        self.assertIn("--tsv FILE", help_text)
        self.assertIn(
            "--report duplicates --probe-media --tsv duplicates.tsv",
            help_text,
        )

    def test_help_groups_collision_modes(self):
        help_text = module.build_parser().format_help()

        self.assertIn("M3 - folder collision handling", help_text)
        self.assertIn("--analyze-collisions", help_text)
        self.assertIn("--merge-ready-collisions", help_text)


if __name__ == "__main__":
    unittest.main()
