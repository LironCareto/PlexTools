#!/usr/bin/env python3
"""Inspect, align, and safely transplant media tracks between two masters.

Inventory and alignment are read-only. Audio transplantation always creates a
new output file and never modifies either input file.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config.json"

THUMB_WIDTH = 32
THUMB_HEIGHT = 18
THUMB_BYTES = THUMB_WIDTH * THUMB_HEIGHT


@dataclass(frozen=True)
class Fingerprint:
    bits: int
    bit_count: int
    contrast: float


@dataclass(frozen=True)
class Match:
    source_time: float
    target_time: float
    distance: float


@dataclass(frozen=True)
class AlignmentModel:
    slope: float
    intercept: float
    matches: tuple[Match, ...]
    residuals: tuple[float, ...]


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


def resolve_media_tool(config: dict, key: str, executable: str) -> str:
    media_tools = config.get("media_tools", {})
    if not isinstance(media_tools, dict):
        raise ValueError("'media_tools' in config must be a JSON object")

    configured = configured_executable(
        media_tools.get(key),
        f"media_tools.{key}",
    )
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
    candidates.extend(
        sorted(Path("/var/packages").glob(f"*/target/bin/{executable}"))
    )

    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    raise ValueError(
        f"{executable} is required. Configure media_tools.{key} in config.json "
        f"or make {executable} available in PATH."
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


def parse_rate(value):
    if not value or value == "0/0":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if "/" in str(value):
        numerator, denominator = str(value).split("/", 1)
        try:
            denominator_value = float(denominator)
            if denominator_value == 0:
                return None
            return float(numerator) / denominator_value
        except ValueError:
            return None
    return float_value(value)


def primary_video_stream(probe: dict):
    for stream in probe["streams"]:
        if stream.get("codec_type") == "video":
            return stream
    return None


def video_frame_rate(probe: dict):
    stream = primary_video_stream(probe)
    if stream is None:
        return None
    return parse_rate(stream.get("avg_frame_rate") or stream.get("r_frame_rate"))


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


def format_signed_seconds(seconds: float) -> str:
    return f"{seconds:+.3f} s"


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

    source_fps = video_frame_rate(source)
    target_fps = video_frame_rate(target)
    if source_fps and target_fps:
        print(f"Source frame rate     : {source_fps:.6f} fps")
        print(f"Target frame rate     : {target_fps:.6f} fps")
        print(f"Frame-rate ratio      : {source_fps / target_fps:.8f}")

    print("Alignment status      : NOT EVALUATED")
    print(
        "Note                  : duration alone cannot prove that the files use "
        "the same cut or establish sync."
    )


def frame_filter() -> str:
    return f"scale={THUMB_WIDTH}:{THUMB_HEIGHT}:flags=area,format=gray"


def fingerprint(frame: bytes) -> Fingerprint:
    if len(frame) != THUMB_BYTES:
        raise ValueError("Invalid thumbnail frame size")

    values = list(frame)
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    contrast = math.sqrt(variance)

    bits = 0
    bit_count = 0

    # Ignore the outermost columns and the top/bottom rows. This reduces the
    # influence of borders, logos and burned-in subtitle placement.
    for y in range(1, THUMB_HEIGHT - 2):
        row = y * THUMB_WIDTH
        for x in range(1, THUMB_WIDTH - 2):
            bits = (bits << 1) | int(values[row + x] < values[row + x + 1])
            bit_count += 1

    for y in range(1, THUMB_HEIGHT - 3):
        row = y * THUMB_WIDTH
        next_row = (y + 1) * THUMB_WIDTH
        for x in range(1, THUMB_WIDTH - 1):
            bits = (bits << 1) | int(values[row + x] < values[next_row + x])
            bit_count += 1

    return Fingerprint(bits=bits, bit_count=bit_count, contrast=contrast)


def population_count(value: int) -> int:
    """Count set bits without requiring int.bit_count()."""
    bit_count = getattr(int, "bit_count", None)
    if bit_count is not None:
        return bit_count(value)
    return bin(value).count("1")


def fingerprint_distance(left: Fingerprint, right: Fingerprint) -> float:
    if left.bit_count != right.bit_count:
        raise ValueError("Fingerprint sizes do not match")
    return population_count(left.bits ^ right.bits) / left.bit_count


def run_ffmpeg_raw(command: list[str], context: str) -> bytes:
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"ffmpeg failed while {context}: {detail or result.returncode}")
    return result.stdout


def extract_frame(ffmpeg: str, path: Path, timestamp: float) -> bytes | None:
    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, timestamp):.6f}",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-vf",
        frame_filter(),
        "-an",
        "-sn",
        "-dn",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "pipe:1",
    ]
    raw = run_ffmpeg_raw(command, f"extracting a frame from {path}")
    if len(raw) < THUMB_BYTES:
        return None
    return raw[:THUMB_BYTES]


def extract_useful_anchor(
    ffmpeg: str,
    path: Path,
    timestamp: float,
    duration: float,
) -> tuple[float, Fingerprint] | None:
    for offset in (0.0, 3.0, -3.0, 7.0, -7.0):
        candidate_time = min(max(0.0, timestamp + offset), max(0.0, duration - 0.1))
        frame = extract_frame(ffmpeg, path, candidate_time)
        if frame is None:
            continue
        fp = fingerprint(frame)
        if fp.contrast >= 10.0:
            return candidate_time, fp
    return None


def extract_window(
    ffmpeg: str,
    path: Path,
    start: float,
    length: float,
    rate: float,
) -> list[tuple[float, Fingerprint]]:
    start = max(0.0, start)
    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start:.6f}",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-t",
        f"{max(0.1, length):.6f}",
        "-vf",
        f"fps={rate:.8f},{frame_filter()}",
        "-an",
        "-sn",
        "-dn",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "pipe:1",
    ]
    raw = run_ffmpeg_raw(command, f"scanning a visual window in {path}")

    frames = []
    frame_count = len(raw) // THUMB_BYTES
    for index in range(frame_count):
        frame = raw[index * THUMB_BYTES : (index + 1) * THUMB_BYTES]
        fp = fingerprint(frame)
        if fp.contrast < 8.0:
            continue
        timestamp = start + (index / rate)
        frames.append((timestamp, fp))
    return frames


def best_window_match(
    source_fp: Fingerprint,
    target_frames: list[tuple[float, Fingerprint]],
    ambiguity_separation: float = 2.0,
) -> tuple[float, float, float | None] | None:
    if not target_frames:
        return None

    scored = [
        (fingerprint_distance(source_fp, target_fp), timestamp)
        for timestamp, target_fp in target_frames
    ]
    scored.sort()

    best_distance, best_time = scored[0]
    second_distance = None
    for distance, timestamp in scored[1:]:
        if abs(timestamp - best_time) >= ambiguity_separation:
            second_distance = distance
            break

    margin = (
        second_distance - best_distance
        if second_distance is not None
        else None
    )
    return best_time, best_distance, margin


def conservative_alignment_end(duration: float) -> float:
    """Return the end of the high-trust region used to fit the global model."""
    end_margin = max(600.0, duration * 0.08)
    end_margin = min(end_margin, duration * 0.20)
    return max(0.0, duration - end_margin)


def anchor_times(
    duration: float,
    count: int,
    content_end: float | None = None,
) -> list[float]:
    if count < 3:
        count = 3

    start_margin = min(180.0, duration * 0.08)
    start = start_margin
    end = (
        min(duration, content_end)
        if content_end is not None
        else conservative_alignment_end(duration)
    )

    if end <= start:
        start = duration * 0.1
        end = duration * 0.9

    step = (end - start) / (count - 1)
    return [start + (step * index) for index in range(count)]


def initial_slope(source: dict, target: dict) -> float:
    source_fps = video_frame_rate(source)
    target_fps = video_frame_rate(target)
    if source_fps and target_fps:
        ratio = source_fps / target_fps
        if 0.85 <= ratio <= 1.15:
            return ratio

    source_duration = media_duration(source)
    target_duration = media_duration(target)
    if source_duration and target_duration and source_duration > 0:
        return target_duration / source_duration
    return 1.0


def least_squares(matches: list[Match]) -> tuple[float, float]:
    if len(matches) < 2:
        raise ValueError("At least two visual matches are required")

    xs = [match.source_time for match in matches]
    ys = [match.target_time for match in matches]
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)

    denominator = sum((x - x_mean) ** 2 for x in xs)
    if denominator == 0:
        raise ValueError("Visual matches do not span enough source time")

    slope = sum(
        (x - x_mean) * (y - y_mean)
        for x, y in zip(xs, ys)
    ) / denominator
    intercept = y_mean - (slope * x_mean)
    return slope, intercept


def fit_robust_model(
    matches: list[Match],
    residual_limit: float,
    minimum_inliers: int = 3,
    expected_slope: float | None = None,
) -> AlignmentModel:
    if len(matches) < minimum_inliers:
        raise ValueError(
            f"At least {minimum_inliers} visual matches are required for alignment"
        )

    best_inliers: list[Match] = []
    best_score = None

    for left_index in range(len(matches)):
        for right_index in range(left_index + 1, len(matches)):
            left = matches[left_index]
            right = matches[right_index]
            source_delta = right.source_time - left.source_time
            if abs(source_delta) < 1.0:
                continue

            slope = (right.target_time - left.target_time) / source_delta
            if not 0.85 <= slope <= 1.15:
                continue
            intercept = left.target_time - (slope * left.source_time)

            inliers = [
                match
                for match in matches
                if abs(
                    match.target_time
                    - ((slope * match.source_time) + intercept)
                ) <= residual_limit
            ]
            if len(inliers) < minimum_inliers:
                continue

            distance_score = sum(match.distance for match in inliers)
            slope_penalty = (
                abs(slope - expected_slope)
                if expected_slope is not None
                else 0.0
            )
            score = (len(inliers), -slope_penalty, -distance_score)
            if best_score is None or score > best_score:
                best_score = score
                best_inliers = inliers

    if len(best_inliers) < minimum_inliers:
        raise ValueError("Could not find a consistent visual time mapping")

    slope, intercept = least_squares(best_inliers)
    residuals = [
        match.target_time - ((slope * match.source_time) + intercept)
        for match in best_inliers
    ]

    refined_inliers = [
        match
        for match, residual in zip(best_inliers, residuals)
        if abs(residual) <= residual_limit
    ]
    if (
        len(refined_inliers) >= minimum_inliers
        and len(refined_inliers) != len(best_inliers)
    ):
        slope, intercept = least_squares(refined_inliers)
        best_inliers = refined_inliers
        residuals = [
            match.target_time - ((slope * match.source_time) + intercept)
            for match in best_inliers
        ]

    return AlignmentModel(
        slope=slope,
        intercept=intercept,
        matches=tuple(best_inliers),
        residuals=tuple(residuals),
    )


def median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def coarse_alignment(
    ffmpeg: str,
    source: dict,
    target: dict,
    count: int,
    window_radius: float,
    source_content_end: float | None = None,
) -> tuple[AlignmentModel, list[Match]]:
    source_duration = media_duration(source)
    target_duration = media_duration(target)
    if source_duration is None or target_duration is None:
        raise ValueError("Both media files need known durations for visual alignment")

    slope_guess = initial_slope(source, target)
    duration_ratio = target_duration / source_duration
    # The duration ratio gives a useful estimate of the net offset even when
    # frame-rate ratio is the better slope estimate.
    intercept_guess = (
        (target_duration - (slope_guess * source_duration)) / 2.0
    )

    anchors = anchor_times(source_duration, count, source_content_end)
    matches: list[Match] = []

    print()
    print("Coarse visual search")
    print("====================")
    print(f"Anchors              : {len(anchors)}")
    print(f"Initial slope guess  : {slope_guess:.8f}")
    print(f"Duration ratio       : {duration_ratio:.8f}")
    print(f"Search radius        : +/- {window_radius:.0f} s")
    print()

    for index, requested_time in enumerate(anchors, start=1):
        source_anchor = extract_useful_anchor(
            ffmpeg,
            source["path"],
            requested_time,
            source_duration,
        )
        if source_anchor is None:
            print(f"[{index}/{len(anchors)}] source {format_duration(requested_time)} -> no useful frame")
            continue

        source_time, source_fp = source_anchor
        predicted_target = (slope_guess * source_time) + intercept_guess
        search_start = max(0.0, predicted_target - window_radius)
        search_end = min(target_duration, predicted_target + window_radius)
        target_frames = extract_window(
            ffmpeg,
            target["path"],
            search_start,
            max(1.0, search_end - search_start),
            rate=2.0,
        )
        best = best_window_match(source_fp, target_frames)
        if best is None:
            print(f"[{index}/{len(anchors)}] source {format_duration(source_time)} -> no target frames")
            continue

        target_time, distance, uniqueness_margin = best
        if uniqueness_margin is not None and uniqueness_margin < 0.025:
            print(
                f"[{index}/{len(anchors)}] source {format_duration(source_time)} "
                f"-> ambiguous target match distance={distance:.3f} "
                f"margin={uniqueness_margin:.3f}"
            )
            continue
        if distance > 0.24:
            print(
                f"[{index}/{len(anchors)}] source {format_duration(source_time)} "
                f"-> weak match {format_duration(target_time)} distance={distance:.3f}"
            )
            continue

        match = Match(source_time, target_time, distance)
        matches.append(match)
        print(
            f"[{index}/{len(anchors)}] source {format_duration(source_time)} "
            f"-> target {format_duration(target_time)} distance={distance:.3f}"
        )

    # The coarse phase may legitimately produce only two high-confidence
    # correspondences. Two points are enough to seed an affine mapping; the
    # denser refined pass is responsible for validating or rejecting it.
    model = fit_robust_model(
        matches,
        residual_limit=4.0,
        minimum_inliers=2,
        expected_slope=slope_guess,
    )
    return model, matches


def refined_alignment(
    ffmpeg: str,
    source: dict,
    target: dict,
    coarse_model: AlignmentModel,
    count: int,
    source_content_end: float | None = None,
) -> tuple[AlignmentModel, list[Match]]:
    source_duration = media_duration(source)
    target_duration = media_duration(target)
    if source_duration is None or target_duration is None:
        raise ValueError("Both media files need known durations for visual alignment")

    anchors = anchor_times(source_duration, count, source_content_end)
    matches: list[Match] = []

    print()
    print("Refined visual validation")
    print("=========================")
    print(f"Anchors              : {len(anchors)}")
    print("Target search        : +/- 3 s at 12 fps")
    print()

    for index, requested_time in enumerate(anchors, start=1):
        source_anchor = extract_useful_anchor(
            ffmpeg,
            source["path"],
            requested_time,
            source_duration,
        )
        if source_anchor is None:
            print(f"[{index}/{len(anchors)}] source {format_duration(requested_time)} -> no useful frame")
            continue

        source_time, source_fp = source_anchor
        predicted_target = (
            coarse_model.slope * source_time
            + coarse_model.intercept
        )
        search_start = max(0.0, predicted_target - 3.0)
        search_end = min(target_duration, predicted_target + 3.0)
        target_frames = extract_window(
            ffmpeg,
            target["path"],
            search_start,
            max(0.5, search_end - search_start),
            rate=12.0,
        )
        best = best_window_match(source_fp, target_frames)
        if best is None:
            print(f"[{index}/{len(anchors)}] source {format_duration(source_time)} -> no target frames")
            continue

        target_time, distance, uniqueness_margin = best
        if uniqueness_margin is not None and uniqueness_margin < 0.015:
            print(
                f"[{index}/{len(anchors)}] source {format_duration(source_time)} "
                f"-> ambiguous target match distance={distance:.3f} "
                f"margin={uniqueness_margin:.3f}"
            )
            continue
        # The refined search is only +/- 3 seconds around a model prediction,
        # so it can safely tolerate a much weaker perceptual match than the
        # wide coarse search. Residual validation below still rejects temporal
        # inconsistencies.
        if distance > 0.45:
            print(
                f"[{index}/{len(anchors)}] source {format_duration(source_time)} "
                f"-> weak match {format_duration(target_time)} distance={distance:.3f}"
            )
            continue

        match = Match(source_time, target_time, distance)
        matches.append(match)
        print(
            f"[{index}/{len(anchors)}] source {format_duration(source_time)} "
            f"-> target {format_duration(target_time)} distance={distance:.3f}"
        )

    model = fit_robust_model(
        matches,
        residual_limit=0.75,
        minimum_inliers=3,
        expected_slope=coarse_model.slope,
    )
    return model, matches


def tail_discontinuity_scan(
    ffmpeg: str,
    source: dict,
    target: dict,
    model: AlignmentModel,
    source_content_end: float | None = None,
) -> list[tuple[Match, float]]:
    source_duration = media_duration(source)
    target_duration = media_duration(target)
    if source_duration is None or target_duration is None:
        raise ValueError("Both media files need known durations for discontinuity scan")

    effective_end = (
        min(source_duration, source_content_end)
        if source_content_end is not None
        else source_duration
    )
    scan_start = max(180.0, effective_end - 1800.0)
    scan_end = max(scan_start, effective_end - 60.0)
    step = 120.0

    requested_times = []
    current = scan_start
    while current <= scan_end:
        requested_times.append(current)
        current += step

    results: list[tuple[Match, float]] = []

    print()
    print("Tail discontinuity scan")
    print("=======================")
    print(
        f"Source region         : {format_duration(scan_start)} "
        f"to {format_duration(scan_end)}"
    )
    print(f"Anchors              : {len(requested_times)}")
    print("Target search        : +/- 8 s at 12 fps around global mapping")
    print()

    for index, requested_time in enumerate(requested_times, start=1):
        source_anchor = extract_useful_anchor(
            ffmpeg,
            source["path"],
            requested_time,
            source_duration,
        )
        if source_anchor is None:
            print(
                f"[{index}/{len(requested_times)}] "
                f"source {format_duration(requested_time)} -> no useful frame"
            )
            continue

        source_time, source_fp = source_anchor
        predicted_target = (model.slope * source_time) + model.intercept
        search_start = max(0.0, predicted_target - 8.0)
        search_end = min(target_duration, predicted_target + 8.0)
        target_frames = extract_window(
            ffmpeg,
            target["path"],
            search_start,
            max(0.5, search_end - search_start),
            rate=12.0,
        )
        best = best_window_match(source_fp, target_frames)
        if best is None:
            print(
                f"[{index}/{len(requested_times)}] "
                f"source {format_duration(source_time)} -> no target frames"
            )
            continue

        target_time, distance, uniqueness_margin = best
        if uniqueness_margin is not None and uniqueness_margin < 0.015:
            print(
                f"[{index}/{len(requested_times)}] "
                f"source {format_duration(source_time)} -> ambiguous target match "
                f"distance={distance:.3f} margin={uniqueness_margin:.3f}"
            )
            continue
        if distance > 0.45:
            print(
                f"[{index}/{len(requested_times)}] "
                f"source {format_duration(source_time)} -> weak match "
                f"{format_duration(target_time)} distance={distance:.3f}"
            )
            continue

        residual = target_time - predicted_target
        match = Match(source_time, target_time, distance)
        results.append((match, residual))
        print(
            f"[{index}/{len(requested_times)}] "
            f"source {format_duration(source_time)} "
            f"-> target {format_duration(target_time)} "
            f"residual={residual:+.3f}s distance={distance:.3f}"
        )

    print()
    if not results:
        print("Tail scan result      : no usable visual matches")
        return results

    stable = [
        (match, residual)
        for match, residual in results
        if abs(residual) <= 0.75
    ]
    shifted = [
        (match, residual)
        for match, residual in results
        if abs(residual) >= 1.5
    ]

    print(f"Tail matches          : {len(results)}")
    print(f"Within +/-0.75 s      : {len(stable)}")
    print(f"Shifted >= 1.5 s      : {len(shifted)}")

    if shifted:
        first_match, first_residual = shifted[0]
        print(
            "First clear deviation : "
            f"source {format_duration(first_match.source_time)} "
            f"(residual {first_residual:+.3f} s)"
        )
        later = [
            residual
            for match, residual in results
            if match.source_time >= first_match.source_time
            and abs(residual) >= 1.5
        ]
        if len(later) >= 2:
            print(
                "Tail scan result      : persistent timeline shift detected "
                "near the end"
            )
        else:
            print(
                "Tail scan result      : isolated late deviation; "
                "needs another local check"
            )
    else:
        print("Tail scan result      : no clear late discontinuity detected")

    return results


def print_alignment_result(
    source: dict,
    target: dict,
    coarse_model: AlignmentModel,
    refined_model: AlignmentModel,
    refined_candidates: list[Match],
) -> None:
    residuals = [abs(value) for value in refined_model.residuals]
    max_residual = max(residuals) if residuals else float("inf")
    median_residual = median(residuals)

    print()
    print("Visual alignment result")
    print("=======================")
    print(
        "Mapping              : "
        f"target_time = {refined_model.slope:.10f} * source_time "
        f"{refined_model.intercept:+.6f}"
    )
    print(f"Timeline scale       : {refined_model.slope:.10f}")
    print(f"Source speed factor  : {1.0 / refined_model.slope:.10f}")
    print(f"Target offset at t=0 : {format_signed_seconds(refined_model.intercept)}")
    print(
        f"Coarse inliers       : {len(coarse_model.matches)}"
    )
    print(
        f"Refined inliers      : {len(refined_model.matches)}/{len(refined_candidates)}"
    )
    print(f"Median residual      : {median_residual:.3f} s")
    print(f"Maximum residual     : {max_residual:.3f} s")

    source_fps = video_frame_rate(source)
    target_fps = video_frame_rate(target)
    if source_fps and target_fps:
        frame_ratio = source_fps / target_fps
        difference = abs(refined_model.slope - frame_ratio)
        print(f"Frame-rate ratio     : {frame_ratio:.10f}")
        print(f"Slope vs fps ratio   : {difference:.10f}")

    if (
        len(refined_model.matches) >= 7
        and max_residual <= 0.50
        and median_residual <= 0.20
    ):
        status = "CONSISTENT GLOBAL AFFINE ALIGNMENT"
    elif (
        len(refined_model.matches) >= 5
        and max_residual <= 0.75
    ):
        status = "POSSIBLE GLOBAL AFFINE ALIGNMENT - REVIEW"
    else:
        status = "ALIGNMENT NOT RELIABLE"

    print(f"Alignment status     : {status}")
    print(
        "Write status         : READ ONLY - no track has been extracted, "
        "retimed or remuxed."
    )


def perform_visual_alignment(
    ffmpeg: str,
    source: dict,
    target: dict,
    coarse_anchors: int,
    validation_anchors: int,
    search_radius: float,
) -> int:
    try:
        source_duration = media_duration(source)
        target_duration = media_duration(target)
        if source_duration is None or target_duration is None:
            raise ValueError("Both media files need known durations for visual alignment")

        trusted_end = conservative_alignment_end(source_duration)

        print()
        print("Alignment sampling window")
        print("=========================")
        print(f"Primary fit source end : {format_duration(trusted_end)}")
        print(
            "Tail policy            : excluded from model fit; "
            "checked separately for unique visual matches"
        )
        print(
            "Reason                 : end credits and other tail material are "
            "not identified semantically"
        )

        coarse_model, _ = coarse_alignment(
            ffmpeg,
            source,
            target,
            count=coarse_anchors,
            window_radius=search_radius,
            source_content_end=trusted_end,
        )
        refined_model, refined_candidates = refined_alignment(
            ffmpeg,
            source,
            target,
            coarse_model,
            count=validation_anchors,
            source_content_end=trusted_end,
        )
        tail_discontinuity_scan(
            ffmpeg,
            source,
            target,
            refined_model,
            source_content_end=None,
        )
    except ValueError as exc:
        print()
        print(f"[ALIGNMENT FAILED] {exc}", file=sys.stderr)
        return 1

    print_alignment_result(
        source,
        target,
        coarse_model,
        refined_model,
        refined_candidates,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect two media files as candidates for track transplantation. "
            "Use --align for read-only visual timeline alignment."
        )
    )
    parser.add_argument(
        "source",
        type=Path,
        help="Media file containing the track to transplant.",
    )
    parser.add_argument(
        "target",
        type=Path,
        help="Media file that would receive the track.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Shared PlexTools configuration file. Defaults to repository-root config.json.",
    )
    parser.add_argument(
        "--align",
        action="store_true",
        help=(
            "Run read-only visual alignment after the media inventory. "
            "This can take several minutes on a NAS."
        ),
    )
    parser.add_argument(
        "--coarse-anchors",
        type=int,
        default=7,
        metavar="N",
        help="Number of coarse visual anchors. Default: 7.",
    )
    parser.add_argument(
        "--validation-anchors",
        type=int,
        default=11,
        metavar="N",
        help="Number of refined validation anchors. Default: 11.",
    )
    parser.add_argument(
        "--search-radius",
        type=float,
        default=120.0,
        metavar="SECONDS",
        help="Coarse target search radius around each prediction. Default: 120.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.coarse_anchors < 3:
        print("[FATAL] --coarse-anchors must be at least 3", file=sys.stderr)
        return 2
    if args.validation_anchors < 3:
        print("[FATAL] --validation-anchors must be at least 3", file=sys.stderr)
        return 2
    if args.search_radius <= 0:
        print("[FATAL] --search-radius must be greater than zero", file=sys.stderr)
        return 2

    try:
        config = load_config(args.config)
        ffprobe = resolve_media_tool(config, "ffprobe_path", "ffprobe")
        source = probe_media(ffprobe, args.source)
        target = probe_media(ffprobe, args.target)
        ffmpeg = (
            resolve_media_tool(config, "ffmpeg_path", "ffmpeg")
            if args.align
            else None
        )
    except ValueError as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2

    print("PlexTrackMerger")
    print("===============")
    print("Mode      : READ ONLY")
    print(f"Config    : {args.config}")
    print(f"ffprobe   : {ffprobe}")
    if ffmpeg is not None:
        print(f"ffmpeg    : {ffmpeg}")
    print()

    print_inventory("Source", source)
    print_inventory("Target", target)
    print_timing_comparison(source, target)

    if not args.align:
        return 0

    return perform_visual_alignment(
        ffmpeg,
        source,
        target,
        coarse_anchors=args.coarse_anchors,
        validation_anchors=args.validation_anchors,
        search_radius=args.search_radius,
    )


if __name__ == "__main__":
    raise SystemExit(main())
