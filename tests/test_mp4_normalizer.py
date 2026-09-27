#!/usr/bin/env python3
"""Regression tests for the MP4 normalizer."""

import importlib.util
import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "mp4_normalizer"
    / "plex_mp4_normalizer.py"
)

spec = importlib.util.spec_from_file_location("plex_mp4_normalizer", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


def box(kind: bytes, payload: bytes = b"") -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


class Mp4NormalizerTests(unittest.TestCase):
    def test_faststart_parser_reads_real_box_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = root / "good.mp4"
            bad = root / "bad.mp4"
            good.write_bytes(box(b"ftyp") + box(b"moov") + box(b"mdat", b"x" * 32))
            bad.write_bytes(box(b"ftyp") + box(b"mdat", b"x" * 32) + box(b"moov"))

            self.assertTrue(module.faststart_status(good))
            self.assertFalse(module.faststart_status(bad))

    def test_incremental_query_uses_timestamp_and_part_id(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE metadata_items (
                id INTEGER PRIMARY KEY,
                title TEXT,
                added_at INTEGER
            );
            CREATE TABLE media_items (
                id INTEGER PRIMARY KEY,
                metadata_item_id INTEGER,
                container TEXT,
                optimized_for_streaming INTEGER,
                created_at INTEGER
            );
            CREATE TABLE media_parts (
                id INTEGER PRIMARY KEY,
                media_item_id INTEGER,
                file TEXT,
                created_at INTEGER
            );
            INSERT INTO metadata_items VALUES (1, 'One', 100);
            INSERT INTO metadata_items VALUES (2, 'Two', 100);
            INSERT INTO media_items VALUES (10, 1, 'mp4', 0, 100);
            INSERT INTO media_items VALUES (11, 2, 'mp4', 0, 100);
            INSERT INTO media_parts VALUES (20, 10, '/media/one.mp4', 100);
            INSERT INTO media_parts VALUES (21, 11, '/media/two.mp4', 100);
            """
        )

        rows = module.fetch_parts(
            conn,
            [],
            module.Cursor(100, 20),
        )
        self.assertEqual([row.part_id for row in rows], [21])

    def test_media_item_timestamp_is_schema_fallback(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE metadata_items (
                id INTEGER PRIMARY KEY,
                title TEXT,
                added_at INTEGER
            );
            CREATE TABLE media_items (
                id INTEGER PRIMARY KEY,
                metadata_item_id INTEGER,
                container TEXT,
                created_at INTEGER
            );
            CREATE TABLE media_parts (
                id INTEGER PRIMARY KEY,
                media_item_id INTEGER,
                file TEXT
            );
            """
        )

        timestamp_expr, *_ = module.schema_details(conn)
        self.assertEqual(timestamp_expr, "media.created_at")

    def test_help_mentions_incremental_and_backup_behavior(self):
        help_text = module.build_parser().format_help()
        self.assertIn("run", help_text)
        self.assertIn("backfill", help_text)
        self.assertIn("--backup", help_text)
        self.assertIn("read-only", help_text)


if __name__ == "__main__":
    unittest.main()
