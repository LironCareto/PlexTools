#!/usr/bin/env python3
"""Incrementally normalize MP4 files that Plex has just added.

Plex is the watcher/catalogue. This tool only reads Plex's SQLite database,
uses a small high-water-mark state file, and remuxes newly-added MP4 files
that are not fast-start/streaming optimized.

The Plex database is always opened in SQLite read-only mode. Files are only
changed when --write is explicitly supplied.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

LIBRARY_DB = "com.plexapp.plugins.library.db"
DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config.json"
DEFAULT_STATE = Path.home() / ".local" / "state" / "plextools" / "mp4-normalizer.json"
DURATION_TOLERANCE_SECONDS = 0.5


@dataclass(frozen=True, order=True)
class Cursor:
    added_at: int
    part_id: int


@dataclass(frozen=True)
class Part:
    part_id: int
    media_id: int
    metadata_id: int
    title: str
    plex_path: str
    local_path: Path
    added_at: int
    container: str
    optimized_for_streaming: bool | None


def open_readonly(db_path: Path) -> sqlite3.Connection:
    """Open Plex SQLite read-only without copying or locking it for writes."""
    if not db_path.is_file():
        raise FileNotFoundError(f"Database not found: {db_path}")

    uri = db_path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def load_config(config_path: Path) -> dict:
    if not config_path.is_file():
        return {}

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read config file {config_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Config file {config_path} must contain a JSON object")
    return data


def parse_path_map(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("path mapping must have the form FROM=TO")
    source, target = value.split("=", 1)
    source = source.rstrip("/\\")
    target = target.rstrip("/\\")
    if not source or not target:
        raise argparse.ArgumentTypeError("path mapping must have the form FROM=TO")
    return source, target


def config_path_maps(plex_config: dict) -> list[tuple[str, str]]:
    raw = plex_config.get("path_maps", [])
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("'plex.path_maps' in config must be a JSON array")

    mappings: list[tuple[str, str]] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError("Each 'plex.path_maps' entry must be a FROM=TO string")
        mappings.append(parse_path_map(item))
    return mappings


def apply_path_maps(path: str, mappings: list[tuple[str, str]]) -> Path:
    for source, target in mappings:
        if path == source:
            return Path(target)
        for separator in ("/", "\\"):
            prefix = source + separator
            if path.startswith(prefix):
                remainder = path[len(source):].lstrip("/\\")
                return Path(target) / Path(remainder)
    return Path(path)


def configured_executable(value, key: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError(f"'{key}' in config must be a string")

    path = Path(value)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError(f"Configured {key} is not executable: {path}")
    return str(path)


def resolve_media_tool(config: dict, key: str, executable: str) -> str:
    configured = configured_executable(config.get(key), f"media_tools.{key}")
    if configured:
        return configured

    found = shutil.which(executable)
    if found:
        return found

    candidates = [
        Path(f"/bin/{executable}"),
        Path(f"/usr/bin/{executable}"),
        Path(f"/usr/local/bin/{executable}"),
    ]
    candidates.extend(sorted(Path("/var/packages").glob(f"*/target/bin/{executable}")))

    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    raise ValueError(
        f"{executable} is required. Configure media_tools.{key} in config.json "
        f"or make {executable} available in PATH."
    )


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}


def schema_details(conn: sqlite3.Connection) -> tuple[str, bool, bool, bool]:
    """Return timestamp expression and optional-column availability."""
    part_columns = table_columns(conn, "media_parts")
    media_columns = table_columns(conn, "media_items")
    metadata_columns = table_columns(conn, "metadata_items")

    if "created_at" in part_columns:
        timestamp_expr = "parts.created_at"
    elif "added_at" in part_columns:
        timestamp_expr = "parts.added_at"
    elif "added_at" in metadata_columns:
        # Older/unusual schemas: less precise for later versions, but still useful.
        timestamp_expr = "metadata.added_at"
    else:
        raise ValueError(
            "Could not find a Plex addition timestamp in media_parts or metadata_items"
        )

    return (
        timestamp_expr,
        "optimized_for_streaming" in media_columns,
        "deleted_at" in part_columns,
        "deleted_at" in metadata_columns,
    )


def fetch_parts(
    conn: sqlite3.Connection,
    path_maps: list[tuple[str, str]],
    cursor: Cursor | None,
) -> list[Part]:
    timestamp_expr, has_optimized, part_deleted, metadata_deleted = schema_details(conn)
    optimized_expr = (
        "media.optimized_for_streaming"
        if has_optimized
        else "NULL"
    )

    where = [f"{timestamp_expr} IS NOT NULL"]
    params: list[int] = []

    if part_deleted:
        where.append("parts.deleted_at IS NULL")
    if metadata_deleted:
        where.append("metadata.deleted_at IS NULL")

    if cursor is not None:
        where.append(
            f"(CAST({timestamp_expr} AS INTEGER) > ? OR "
            f"(CAST({timestamp_expr} AS INTEGER) = ? AND parts.id > ?))"
        )
        params.extend([cursor.added_at, cursor.added_at, cursor.part_id])

    rows = conn.execute(
        f"""
        SELECT
            parts.id AS part_id,
            parts.file AS file,
            CAST({timestamp_expr} AS INTEGER) AS added_at,
            media.id AS media_id,
            media.container AS container,
            {optimized_expr} AS optimized_for_streaming,
            metadata.id AS metadata_id,
            metadata.title AS title
        FROM media_parts AS parts
        INNER JOIN media_items AS media
            ON media.id = parts.media_item_id
        INNER JOIN metadata_items AS metadata
            ON metadata.id = media.metadata_item_id
        WHERE {' AND '.join(where)}
        ORDER BY CAST({timestamp_expr} AS INTEGER), parts.id
        """,
        params,
    ).fetchall()

    parts: list[Part] = []
    for row in rows:
        optimized = row["optimized_for_streaming"]
        if optimized is None:
            optimized_value = None
        else:
            optimized_value = bool(int(optimized))

        plex_path = str(row["file"] or "")
        parts.append(
            Part(
                part_id=int(row["part_id"]),
                media_id=int(row["media_id"]),
                metadata_id=int(row["metadata_id"]),
                title=str(row["title"] or f"metadata {row['metadata_id']}"),
                plex_path=plex_path,
                local_path=apply_path_maps(plex_path, path_maps),
                added_at=int(row["added_at"]),
                container=str(row["container"] or ""),
                optimized_for_streaming=optimized_value,
            )
        )
    return parts


def latest_cursor(conn: sqlite3.Connection) -> Cursor:
    timestamp_expr, _, part_deleted, metadata_deleted = schema_details(conn)
    where = [f"{timestamp_expr} IS NOT NULL"]
    if part_deleted:
        where.append("parts.deleted_at IS NULL")
    if metadata_deleted:
        where.append("metadata.deleted_at IS NULL")

    row = conn.execute(
        f"""
        SELECT
            CAST({timestamp_expr} AS INTEGER) AS added_at,
            parts.id AS part_id
        FROM media_parts AS parts
        INNER JOIN media_items AS media
            ON media.id = parts.media_item_id
        INNER JOIN metadata_items AS metadata
            ON metadata.id = media.metadata_item_id
        WHERE {' AND '.join(where)}
        ORDER BY CAST({timestamp_expr} AS INTEGER) DESC, parts.id DESC
        LIMIT 1
        """
    ).fetchone()

    if row is None:
        return Cursor(0, 0)
    return Cursor(int(row["added_at"]), int(row["part_id"]))


def load_state(path: Path) -> Cursor | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Cursor(
            int(data["last_added_at"]),
            int(data["last_media_part_id"]),
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read state file {path}: {exc}") from exc


def save_state(path: Path, cursor: Cursor) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_added_at": cursor.added_at,
        "last_media_part_id": cursor.part_id,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def top_level_box_order(path: Path) -> list[str]:
    """Read ISO-BMFF top-level boxes without scanning media payload bytes."""
    file_size = path.stat().st_size
    boxes: list[str] = []

    with path.open("rb") as handle:
        offset = 0
        while offset + 8 <= file_size:
            handle.seek(offset)
            header = handle.read(8)
            if len(header) != 8:
                break

            size32, raw_type = struct.unpack(">I4s", header)
            header_size = 8

            if size32 == 1:
                extended = handle.read(8)
                if len(extended) != 8:
                    raise ValueError(f"Truncated extended MP4 box at offset {offset}")
                box_size = struct.unpack(">Q", extended)[0]
                header_size = 16
            elif size32 == 0:
                box_size = file_size - offset
            else:
                box_size = size32

            if box_size < header_size or offset + box_size > file_size:
                raise ValueError(
                    f"Invalid MP4 box size {box_size} at offset {offset} in {path}"
                )

            box_type = raw_type.decode("ascii", errors="replace")
            boxes.append(box_type)

            if box_size == 0:
                break
            offset += box_size

    return boxes


def faststart_status(path: Path) -> bool | None:
    boxes = top_level_box_order(path)
    try:
        moov_index = boxes.index("moov")
        mdat_index = boxes.index("mdat")
    except ValueError:
        return None
    return moov_index < mdat_index


def is_mp4(part: Part) -> bool:
    return part.local_path.suffix.casefold() == ".mp4"


def probe_signature(ffprobe: str, path: Path) -> dict:
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=index,codec_type,codec_name,width,height,channels,sample_rate",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        raise ValueError(f"ffprobe failed for {path}: {detail}")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"ffprobe returned invalid JSON for {path}: {exc}") from exc

    streams = data.get("streams")
    format_info = data.get("format")
    if not isinstance(streams, list) or not isinstance(format_info, dict):
        raise ValueError(f"Unexpected ffprobe output for {path}")

    normalized_streams = []
    for stream in sorted(streams, key=lambda item: int(item.get("index", 0))):
        normalized_streams.append(
            (
                stream.get("codec_type"),
                stream.get("codec_name"),
                stream.get("width"),
                stream.get("height"),
                stream.get("channels"),
                stream.get("sample_rate"),
            )
        )

    duration = format_info.get("duration")
    return {
        "duration": float(duration) if duration is not None else None,
        "streams": normalized_streams,
    }


def validate_remux(before: dict, after: dict, output_path: Path) -> None:
    if before["streams"] != after["streams"]:
        raise ValueError("remux changed the media stream inventory")

    before_duration = before["duration"]
    after_duration = after["duration"]
    if before_duration is not None and after_duration is not None:
        if abs(before_duration - after_duration) > DURATION_TOLERANCE_SECONDS:
            raise ValueError(
                f"remux duration changed from {before_duration:.3f}s "
                f"to {after_duration:.3f}s"
            )

    if faststart_status(output_path) is not True:
        raise ValueError("remux output is not fast-start (moov before mdat)")


def backup_path_for(path: Path) -> Path:
    candidate = Path(str(path) + ".bak")
    if candidate.exists():
        raise ValueError(f"Backup already exists, refusing to overwrite it: {candidate}")
    return candidate


def normalize_mp4(
    path: Path,
    ffmpeg: str,
    ffprobe: str,
    keep_backup: bool,
) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Media file not found: {path}")

    original_stat = path.stat()
    before = probe_signature(ffprobe, path)

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.stem}.plextools-",
        suffix=".mp4",
        dir=str(path.parent),
    )
    os.close(fd)
    temp_path = Path(temp_name)

    try:
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(path),
            "-map",
            "0",
            "-map_metadata",
            "0",
            "-map_chapters",
            "0",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(temp_path),
        ]
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or f"exit code {result.returncode}"
            raise ValueError(f"ffmpeg remux failed: {detail}")

        after = probe_signature(ffprobe, temp_path)
        validate_remux(before, after, temp_path)

        shutil.copystat(path, temp_path)
        if hasattr(os, "chown"):
            try:
                os.chown(temp_path, original_stat.st_uid, original_stat.st_gid)
            except PermissionError:
                pass

        if keep_backup:
            backup = backup_path_for(path)
            os.replace(path, backup)
            try:
                os.replace(temp_path, path)
            except Exception:
                os.replace(backup, path)
                raise
        else:
            os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def describe_part(part: Part) -> str:
    stamp = datetime.fromtimestamp(part.added_at, tz=timezone.utc).isoformat()
    return f"{part.local_path} [part={part.part_id}, added={stamp}]"


def process_parts(
    parts: list[Part],
    mode: str,
    write: bool,
    state_file: Path,
    ffmpeg: str | None,
    ffprobe: str | None,
    keep_backup: bool,
) -> int:
    errors = 0
    fixed = 0
    skipped = 0
    candidates = 0

    for part in parts:
        cursor = Cursor(part.added_at, part.part_id)

        if not is_mp4(part):
            skipped += 1
            if mode == "run" and write:
                save_state(state_file, cursor)
            continue

        if not part.local_path.is_file():
            print(f"[ERROR] Plex file is missing locally: {describe_part(part)}", file=sys.stderr)
            errors += 1
            if mode == "run" and write:
                break
            continue

        try:
            actual_faststart = faststart_status(part.local_path)
        except (OSError, ValueError) as exc:
            print(f"[ERROR] Could not inspect {part.local_path}: {exc}", file=sys.stderr)
            errors += 1
            if mode == "run" and write:
                break
            continue

        # The file itself is authoritative when Plex metadata is stale.
        if actual_faststart is True:
            print(f"[OK]   already normalized: {describe_part(part)}")
            skipped += 1
            if mode == "run" and write:
                save_state(state_file, cursor)
            continue

        if actual_faststart is None:
            print(f"[REVIEW] no moov/mdat pair found: {describe_part(part)}", file=sys.stderr)
            errors += 1
            if mode == "run" and write:
                break
            continue

        candidates += 1
        plex_flag = (
            "unknown"
            if part.optimized_for_streaming is None
            else str(part.optimized_for_streaming).lower()
        )

        if not write:
            print(
                f"[WOULD NORMALIZE] {describe_part(part)} "
                f"(Plex optimized_for_streaming={plex_flag})"
            )
            continue

        assert ffmpeg is not None and ffprobe is not None
        print(f"[NORMALIZE] {describe_part(part)}")
        try:
            normalize_mp4(
                part.local_path,
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                keep_backup=keep_backup,
            )
        except (OSError, ValueError) as exc:
            print(f"[ERROR] {part.local_path}: {exc}", file=sys.stderr)
            errors += 1
            if mode == "run":
                break
            continue

        fixed += 1
        print(f"[FIXED] {part.local_path}")
        if mode == "run":
            save_state(state_file, cursor)

    print(
        f"Summary: rows={len(parts)} candidates={candidates} "
        f"fixed={fixed} skipped={skipped} errors={errors}"
    )
    return 1 if errors else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize newly-added Plex MP4 files by remuxing them losslessly "
            "with streaming-friendly MP4 structure."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Modes:\n"
            "  run       Process only Plex media parts newer than the saved cursor.\n"
            "            On the first --write run, initialize the cursor at the current\n"
            "            end of the Plex catalogue and process nothing historical.\n"
            "  scan      Preview parts newer than the saved cursor; never changes state.\n"
            "  backfill  Inspect all current Plex media parts; add --write to normalize.\n\n"
            "Task Scheduler example:\n"
            "  python3 plextools.py mp4-normalizer run --write\n\n"
            "Plex's database is always opened read-only. --backup is optional;\n"
            "without it, validated output atomically replaces the original file."
        ),
    )

    parser.add_argument("mode", choices=("run", "scan", "backfill"))
    parser.add_argument(
        "--write",
        action="store_true",
        help="Actually replace eligible MP4 files. Without this, only preview.",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="Keep the original beside the normalized file as FILE.mp4.bak.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Shared PlexTools JSON config. Default: repository-root config.json.",
    )
    parser.add_argument(
        "-d",
        "--database-folder",
        type=Path,
        help="Override configured plex.database_folder.",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        help="Override the incremental cursor state file.",
    )
    parser.add_argument(
        "--path-map",
        action="append",
        default=[],
        type=parse_path_map,
        metavar="FROM=TO",
        help="Override plex.path_maps. May be repeated.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.mode == "scan" and args.write:
        print("[FATAL] scan is read-only; omit --write.", file=sys.stderr)
        return 2
    if args.backup and not args.write:
        print("[FATAL] --backup only makes sense with --write.", file=sys.stderr)
        return 2

    try:
        config = load_config(args.config)
        plex_config = config.get("plex", {})
        media_tools_config = config.get("media_tools", {})
        tools_config = config.get("tools", {})

        if not isinstance(plex_config, dict):
            raise ValueError("'plex' in config must be a JSON object")
        if not isinstance(media_tools_config, dict):
            raise ValueError("'media_tools' in config must be a JSON object")
        if not isinstance(tools_config, dict):
            raise ValueError("'tools' in config must be a JSON object")

        normalizer_config = tools_config.get("mp4_normalizer", {})
        if not isinstance(normalizer_config, dict):
            raise ValueError("'tools.mp4_normalizer' in config must be a JSON object")

        configured_database = plex_config.get("database_folder")
        if args.database_folder is not None:
            database_folder = args.database_folder
        elif configured_database:
            if not isinstance(configured_database, str):
                raise ValueError("'plex.database_folder' in config must be a string")
            database_folder = Path(configured_database)
        else:
            raise ValueError(
                "No Plex database folder configured. Set plex.database_folder "
                "or pass --database-folder."
            )

        if args.state_file is not None:
            state_file = args.state_file
        else:
            configured_state = normalizer_config.get("state_file")
            if configured_state:
                if not isinstance(configured_state, str):
                    raise ValueError(
                        "'tools.mp4_normalizer.state_file' in config must be a string"
                    )
                state_file = Path(configured_state).expanduser()
            else:
                state_file = DEFAULT_STATE

        path_maps = args.path_map if args.path_map else config_path_maps(plex_config)
        ffmpeg = ffprobe = None
        if args.write:
            ffmpeg = resolve_media_tool(media_tools_config, "ffmpeg_path", "ffmpeg")
            ffprobe = resolve_media_tool(media_tools_config, "ffprobe_path", "ffprobe")

    except (ValueError, argparse.ArgumentTypeError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2

    db_path = database_folder / LIBRARY_DB
    try:
        conn = open_readonly(db_path)
    except (OSError, sqlite3.Error) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2

    try:
        if args.mode == "backfill":
            cursor = None
        else:
            cursor = load_state(state_file)

            if cursor is None:
                newest = latest_cursor(conn)
                if args.mode == "run" and args.write:
                    save_state(state_file, newest)
                    print(
                        "Initialized incremental cursor at current Plex catalogue end: "
                        f"{newest.added_at}/{newest.part_id}. "
                        "No historical files were changed; use backfill explicitly."
                    )
                    return 0

                print(
                    "No incremental state exists yet. "
                    f"Current Plex catalogue end is {newest.added_at}/{newest.part_id}.",
                    file=sys.stderr,
                )
                print(
                    "Run once with 'run --write' to initialize safely, "
                    "or use 'backfill' to inspect historical files.",
                    file=sys.stderr,
                )
                return 0

        parts = fetch_parts(conn, path_maps, cursor)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"[FATAL] Plex database query failed: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    if not parts:
        print("No new Plex media parts to inspect." if args.mode != "backfill" else "No Plex media parts found.")
        return 0

    result = process_parts(
        parts,
        mode=args.mode,
        write=args.write,
        state_file=state_file,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        keep_backup=args.backup,
    )

    if args.mode == "backfill" and args.write and result == 0:
        # Backfill establishes a clean baseline for future incremental runs.
        final_cursor = Cursor(parts[-1].added_at, parts[-1].part_id)
        save_state(state_file, final_cursor)
        print(
            f"Incremental cursor set to {final_cursor.added_at}/{final_cursor.part_id}."
        )

    return result


if __name__ == "__main__":
    raise SystemExit(main())
