# Plex MP4 Normalizer

Incremental, lossless normalization of MP4 files already discovered by Plex.

Plex already watches the media folders, detects new media, and records it in its
library database. This tool deliberately does not add a second watcher. Instead,
it opens `com.plexapp.plugins.library.db` in SQLite read-only mode and uses Plex
as the source of truth for newly-added media.

## Modes

- `run` — process only Plex media parts newer than the saved cursor.
- `scan` — preview the same incremental set without changing files or state.
- `backfill` — inspect all current Plex media parts; add `--write` to normalize.

The incremental cursor is the pair:

```text
(addition timestamp, media_parts.id)
```

Using both fields avoids missing rows when several media parts share the same
timestamp.

The first `run --write` establishes the cursor at the current end of the Plex
catalogue and changes no historical files. This makes installation safe for an
existing library. Use `backfill` explicitly when historical files should also
be considered.

## What gets changed

Only `.mp4` files whose actual top-level MP4 structure has `mdat` before
`moov` are candidates. The tool reads the MP4 box headers directly; it does not
grep arbitrary media bytes.

A candidate is remuxed with:

```text
ffmpeg -map 0 -map_metadata 0 -map_chapters 0 -c copy -movflags +faststart
```

No audio or video stream is re-encoded.

Before replacement, the temporary output is validated with `ffprobe`:

- media stream inventory must remain equivalent;
- duration must remain within a small tolerance;
- the new MP4 must have `moov` before `mdat`.

Only after validation does the temporary file replace the original.

## Backups

No backup is kept by default.

Use `--backup` to retain the original beside the normalized file as
`FILE.mp4.bak`. Backup generation is intentionally opt-in so scheduled runs do
not accumulate duplicate media by default.

## Configuration

The tool uses the shared root `config.json`:

```json
{
  "plex": {
    "database_folder": "/path/to/Plex Media Server/Plug-in Support/Databases",
    "path_maps": [
      "/plex/media=/local/media"
    ]
  },
  "media_tools": {
    "ffprobe_path": "",
    "ffmpeg_path": ""
  },
  "tools": {
    "mp4_normalizer": {
      "state_file": ""
    }
  }
}
```

If `state_file` is empty or omitted, the default is:

```text
~/.local/state/plextools/mp4-normalizer.json
```

FFmpeg/ffprobe are resolved from the configured paths, `PATH`, common system
locations, and Synology package paths below `/var/packages/*/target/bin/`.

## Synology Task Scheduler

Typical hourly command:

```bash
python3 /path/to/PlexTools/plextools.py mp4-normalizer run --write
```

The normal no-op case is cheap: Plex's SQLite database is queried for rows newer
than the saved cursor and the process exits when there is nothing new.

## Examples

Initialize incremental operation without touching the existing library:

```bash
python3 plextools.py mp4-normalizer run --write
```

Preview new Plex additions after initialization:

```bash
python3 plextools.py mp4-normalizer scan
```

Preview the whole current Plex catalogue:

```bash
python3 plextools.py mp4-normalizer backfill
```

Normalize historical candidates too:

```bash
python3 plextools.py mp4-normalizer backfill --write
```

Keep originals during a deliberate run:

```bash
python3 plextools.py mp4-normalizer backfill --write --backup
```

## Safety model

- Plex database: read-only SQLite URI plus `PRAGMA query_only=ON`.
- No custom filesystem watcher.
- No media writes without `--write`.
- No re-encoding.
- Same-directory temporary output.
- Validation before replacement.
- Incremental cursor only advances after each successful/irrelevant row during
  normal `run` processing.
