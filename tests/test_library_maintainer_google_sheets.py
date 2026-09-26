#!/usr/bin/env python3
"""Regression tests for duplicate-report Google Sheets helpers."""

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


class GoogleSheetsExportTests(unittest.TestCase):
    def test_google_sheet_settings_defaults_worksheet(self):
        settings = module.google_sheet_settings(
            {
                "google_sheets": {
                    "credentials_file": "/tmp/service-account.json",
                    "spreadsheet_id": "sheet-id",
                }
            }
        )

        self.assertEqual(
            settings["credentials_file"],
            Path("/tmp/service-account.json"),
        )
        self.assertEqual(settings["spreadsheet_id"], "sheet-id")
        self.assertEqual(settings["worksheet"], "duplicates")

    def test_duplicate_sheet_values_matches_tsv_column_order(self):
        row = {
            "library": "Movies",
            "title": "Example Movie",
            "year": 2001,
            "media_id": 42,
        }

        values = module.duplicate_sheet_values([row])

        self.assertEqual(values[0], module.DUPLICATE_TSV_FIELDNAMES)
        self.assertEqual(len(values[1]), len(module.DUPLICATE_TSV_FIELDNAMES))
        self.assertEqual(
            values[1][module.DUPLICATE_TSV_FIELDNAMES.index("title")],
            "Example Movie",
        )
        self.assertEqual(
            values[1][module.DUPLICATE_TSV_FIELDNAMES.index("media_id")],
            42,
        )
        self.assertEqual(
            values[1][module.DUPLICATE_TSV_FIELDNAMES.index("probe_errors")],
            "",
        )


if __name__ == "__main__":
    unittest.main()
