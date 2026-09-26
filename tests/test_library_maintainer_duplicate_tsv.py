#!/usr/bin/env python3
"""Regression tests for incremental duplicate TSV writing."""

import csv
import importlib.util
import tempfile
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


class DuplicateTsvWritingTests(unittest.TestCase):
    def test_initialize_replaces_stale_report_with_current_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "duplicates.tsv"
            path.write_text("old\theader\nold\trow\n", encoding="utf-8")

            module.initialize_duplicate_tsv(path)

            with path.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.reader(handle, delimiter="\t"))

            self.assertEqual(rows, [module.DUPLICATE_TSV_FIELDNAMES])

    def test_append_writes_completed_rows_without_rewriting_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "duplicates.tsv"
            module.initialize_duplicate_tsv(path)

            module.append_duplicate_tsv_rows(
                path,
                [
                    {
                        "library": "Movies",
                        "title": "Example Movie",
                        "media_id": 42,
                        "visual_assessment": "SIMILAR",
                    }
                ],
            )

            with path.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["title"], "Example Movie")
            self.assertEqual(rows[0]["media_id"], "42")
            self.assertEqual(rows[0]["visual_assessment"], "SIMILAR")


if __name__ == "__main__":
    unittest.main()
