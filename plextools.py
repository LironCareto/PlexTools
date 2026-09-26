#!/usr/bin/env python3
"""Small dispatcher that makes the PlexTools command-line tools discoverable."""

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent

TOOLS = {
    "maintainer": (
        ROOT / "tools" / "library_maintainer" / "plex_library_maintainer.py",
        "Plex library normalization, collision handling and duplicate analysis.",
    ),
    "subs": (
        ROOT / "tools" / "subs_extractor" / "plex_subs_extractor.py",
        "Extract subtitle blobs from Plex into sidecar files.",
    ),
    "track": (
        ROOT / "tools" / "track_merger" / "plex_track_merger.py",
        "Inspect, align and transplant media tracks between masters.",
    ),
}


def print_help() -> None:
    print(
        """PlexTools

Usage:
  python3 plextools.py --help
  python3 plextools.py TOOL --help
  python3 plextools.py TOOL [tool arguments...]

Tools:
  maintainer  Plex library normalization, collisions and duplicate analysis
  subs        Plex subtitle blob extraction
  track       Media-master alignment and audio-track transplant

Examples:
  python3 plextools.py maintainer --help
  python3 plextools.py maintainer --report duplicates --probe-media --tsv duplicates.tsv
  python3 plextools.py subs --help
  python3 plextools.py subs --write --language spa
  python3 plextools.py track --help
  python3 plextools.py track --align SOURCE TARGET

Each tool has its own detailed --help with complete examples.
"""
    )


def main() -> int:
    args = sys.argv[1:]

    if not args or args[0] in {"-h", "--help", "help"}:
        if len(args) >= 2 and args[0] == "help":
            tool_name = args[1]
            tool = TOOLS.get(tool_name)
            if tool is None:
                print(f"Unknown tool: {tool_name}", file=sys.stderr)
                print_help()
                return 2
            return subprocess.call(
                [sys.executable, str(tool[0]), "--help"]
            )

        print_help()
        return 0

    tool_name = args[0]
    tool = TOOLS.get(tool_name)
    if tool is None:
        print(f"Unknown tool: {tool_name}", file=sys.stderr)
        print_help()
        return 2

    script, _ = tool
    return subprocess.call([sys.executable, str(script), *args[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
