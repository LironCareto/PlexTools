#!/usr/bin/env python3
"""Inspect two media files before transplanting tracks between them.

M1 is deliberately read-only. It probes both files with ffprobe, inventories
video/audio/subtitle streams, and reports timing information that later
alignment milestones can use. It never modifies or creates media files.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config.json"


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


def configured_executable(value, key: str):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError(f"'{key}' in config must be a string")

    path = Path(value)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError(f"Configured {key} is not an executable file: {path}")
    return str(path)


def resolve_ffprobe(config: dict) -> str:
    media_tools = config.get("media_tools", {})
    if not isinstance(media_tools, dict):
        raise ValueError("'media_tools' in config must be a JSON object")

    configured = configured_executable(
        media_tools.get("ffprobe_path"),
        "media_tools.ffprobe_path",
    )
    if configured:
        return configured

    found = shutil.which("ffprobe")
    if found:
        return found

    candidates = [
        Path("/bin/ffprobe"),
        Path("/usr/bin/ffprobe"),
        Path("/usr/local/bin/ffprobe"),
    ]
    candidates.extend(sorted(Path("/var/packages").glob("*/target/bin/ffprobe")))

    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    raise ValueError(
        "ffprobe is required. Configure media_tools.ffprobe_path in config.json "
        "or make ffprobe available in PATH."
    )


def probe_media(ffprobe: str, path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"Media file not found: {path}")

    command = [
        ffprobe,
        "-v",
        "error",
        "-show_format",
        "-show_streams",
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

    streams = data.get("streams", [])
    format_info = data.get("format", {})
    if not isinstance(streams, list) or not isinstance(format_info, dict):
        raise ValueError(f"Unexpected ffprobe output for {path}")

    return {"path": path, "format": format_info, "streams": streams}


def float_value(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def stream_language(stream: dict) -> str:
    tags = stream.get("tags") or {}
    if not isinstance(tags, dict):
        return "und"
    return str(tags.get("language") or "und")


def stream_title(stream: dict) -> str:
    tags = stream.get("tags") or {}
    if not isinstance(tags, dict):
        return ""
    return str(tags.get("title") or "")


def disposition_flag(stream: dict, key: str) -> bool:
    disposition = stream.get("disposition") or {}
    return bool(disposition.get(key)) if isinstance(disposition, dict) else False


def format_duration(seconds) -> str:
    if seconds is None:
        return "unknown"
    total_ms = round(seconds * 1000)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def media_duration(probe: dict):
    duration = float_value(probe["format"].get("duration"))
    if duration is not None:
        return duration

    stream_durations = [
        float_value(stream.get("duration"))
        for stream in probe["streams"]
    ]
    known = [value for value in stream_durations if value is not None]
    return max(known) if known else None


def video_summary(stream: dict) -> str:
    width = stream.get("width")
    height = stream.get("height")
    resolution = f"{width}x{height}" if width and height else "unknown"
    codec = stream.get("codec_name") or "unknown"
    profile = stream.get("profile") or ""
    fps = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "?"
    bits = stream.get("bits_per_raw_sample") or ""
    parts = [codec]
    if profile:
        parts.append(str(profile))
    parts.append(resolution)
    if bits:
        parts.append(f"{bits}-bit")
    parts.append(f"{fps} fps")
    return " | ".join(parts)


def audio_summary(stream: dict) -> str:
    index = stream.get("index", "?")
    codec = stream.get("codec_name") or "unknown"
    channels = stream.get("channels") or "?"
    layout = stream.get("channel_layout") or ""
    language = stream_language(stream)
    title = stream_title(stream)
    flags = []
    if disposition_flag(stream, "default"):
        flags.append("default")
    if disposition_flag(stream, "forced"):
        flags.append("forced")

    detail = f"stream {index}: {language} | {codec} | {channels}ch"
    if layout:
        detail += f" {layout}"
    if title:
        detail += f" | {title}"
    if flags:
        detail += " | " + ",".join(flags)
    return detail


def subtitle_summary(stream: dict) -> str:
    index = stream.get("index", "?")
    codec = stream.get("codec_name") or "unknown"
    language = stream_language(stream)
    title = stream_title(stream)
    flags = []
    if disposition_flag(stream, "default"):
        flags.append("default")
    if disposition_flag(stream, "forced"):
        flags.append("forced")
    if disposition_flag(stream, "hearing_impaired"):
        flags.append("hearing-impaired")

    detail = f"stream {index}: {language} | {codec}"
    if title:
        detail += f" | {title}"
    if flags:
        detail += " | " + ",".join(flags)
    return detail


def print_inventory(label: str, probe: dict) -> None:
    path = probe["path"]
    duration = media_duration(probe)
    videos = [s for s in probe["streams"] if s.get("codec_type") == "video"]
    audios = [s for s in probe["streams"] if s.get("codec_type") == "audio"]
    subtitles = [s for s in probe["streams"] if s.get("codec_type") == "subtitle"]

    print(label)
    print("=" * len(label))
    print(f"File      : {path}")
    print(f"Duration  : {format_duration(duration)}")
    print(f"Video     : {len(videos)} stream(s)")
    for stream in videos:
        print(f"            stream {stream.get('index', '?')}: {video_summary(stream)}")

    print(f"Audio     : {len(audios)} track(s)")
    for stream in audios:
        print(f"            {audio_summary(stream)}")

    print(f"Subtitles : {len(subtitles)} track(s)")
    for stream in subtitles:
        print(f"            {subtitle_summary(stream)}")
    print()


def print_timing_comparison(source: dict, target: dict) -> None:
    source_duration = media_duration(source)
    target_duration = media_duration(target)

    print("Timing comparison")
    print("=================")
    if source_duration is None or target_duration is None:
        print("Duration relationship : unavailable")
        print("Alignment status      : NOT EVALUATED")
        return

    delta = target_duration - source_duration
    ratio = target_duration / source_duration if source_duration else None

    print(f"Source duration       : {format_duration(source_duration)}")
    print(f"Target duration       : {format_duration(target_duration)}")
    print(f"Target - source       : {delta:+.3f} s")
    if ratio is not None:
        print(f"Duration ratio        : {ratio:.8f}")
        print(f"Implied speed factor  : {1.0 / ratio:.8f}")

    print("Alignment status      : NOT EVALUATED")
    print(
        "Note                  : duration alone cannot prove that the files use "
        "the same cut or establish sync."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect two media files as candidates for track transplantation. "
            "M1 is read-only and performs no merge."
        )
    )
    parser.add_argument("source", type=Path, help="Media file containing the track to transplant.")
    parser.add_argument("target", type=Path, help="Media file that would receive the track.")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Shared PlexTools configuration file. Defaults to repository-root config.json.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    try:
        config = load_config(args.config)
        ffprobe = resolve_ffprobe(config)
        source = probe_media(ffprobe, args.source)
        target = probe_media(ffprobe, args.target)
    except ValueError as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2

    print("PlexTrackMerger M1")
    print("==================")
    print("Mode      : READ ONLY")
    print(f"Config    : {args.config}")
    print(f"ffprobe   : {ffprobe}")
    print()

    print_inventory("Source", source)
    print_inventory("Target", target)
    print_timing_comparison(source, target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
