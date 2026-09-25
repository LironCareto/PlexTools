# PlexSubsExtractor

Extract subtitle files that Plex Media Server has stored inside its internal subtitle blob database and save them as normal sidecar files next to the corresponding movie or episode.

The main goal is simple: turn subtitles downloaded through Plex into ordinary files such as:

```text
Movies/
└── Alien (1979)/
    ├── Alien (1979).mkv
    └── Alien (1979).eng.srt
```

## Safety first

PlexSubsExtractor is deliberately conservative:

- Plex SQLite databases are opened with `mode=ro`.
- `PRAGMA query_only = ON` adds a second read-only safeguard.
- The default mode is **dry-run**.
- No subtitle file is created unless you explicitly pass `--write`.
- Existing subtitle files are never overwritten by default.
- Before creating a file, the script compares SHA-256 hashes against existing sidecars for the same video/language/forced/format. Identical content is treated as already extracted and skipped.
- If the filename collides but the subtitle content is different, a numbered filename such as `Movie(1).eng.srt`, `Movie(2).eng.srt`, etc. is used, keeping Plex's language/forced suffix intact.
- The script contains no SQL writes or commits to the Plex databases.
- Machine-specific paths can live in a local `config.json`, which is ignored by Git.

In other words, the script reads Plex's databases and writes subtitle sidecar files. It does not modify Plex's databases.

## Requirements

- Python 3.8+
- No third-party Python packages

## Configuration

Copy the example configuration:

```bash
cp config.example.json config.json
```

On PowerShell:

```powershell
Copy-Item config.example.json config.json
```

Then edit `config.json` with the paths that apply to your machine:

```json
{
  "database_folder": "/path/to/Plex Media Server/Plug-in Support/Databases",
  "path_maps": [
    "/plex/media=/local/media"
  ]
}
```

`config.json` is listed in `.gitignore` and should remain local.

The `--write` and `--force` safety switches are intentionally **not** configurable in the file. They must always be passed explicitly on the command line.

## Usage

### 1. Dry-run first

With `config.json` present:

```bash
python3 plex_subs_extractor.py
```

This only shows what would be extracted. Name collisions are also resolved during dry-run, so the planned filenames match what a real run would create.

Example:

```text
[FOUND] /local/media/Movies/Alien (1979)/Alien (1979).mkv
        language=eng codec=srt forced=no
     -> /local/media/Movies/Alien (1979)/Alien (1979).eng.srt
        [DRY RUN: not written]
```

If that file already exists, the next subtitle with the same target becomes:

```text
/local/media/Movies/Alien (1979)/Alien (1979)(1).eng.srt
```

then `Alien (1979)(2).eng.srt`, `Alien (1979)(3).eng.srt`, and so on. Numbering is inserted before the language tag so Plex can still recognize the sidecar subtitle.

### 2. Write the subtitle files

Once the dry-run looks correct:

```bash
python3 plex_subs_extractor.py --write
```

### Repeated and scheduled runs

PlexSubsExtractor is idempotent for already-extracted subtitles. On later runs it calculates the SHA-256 of each Plex subtitle blob and compares it with the matching sidecars already on disk.

If the exact subtitle is already present, it is reported as:

```text
[ALREADY EXTRACTED: identical SHA-256]
```

and no new file is created.

If the sidecar was deleted, it is no longer present to match the hash, so the next run recreates it. No separate state file or list of previously processed blobs is required.

This makes the script suitable for periodic execution by a scheduler.

### Override configuration from the command line

The database folder can still be supplied directly:

```bash
python3 plex_subs_extractor.py \
  --database-folder "/path/to/Databases"
```

Path mappings can also be supplied directly:

```bash
python3 plex_subs_extractor.py \
  --path-map "/plex/media=/local/media"
```

Command-line path mappings replace mappings from `config.json`.

A different config file can be selected with:

```bash
python3 plex_subs_extractor.py --config "/path/to/another-config.json"
```

### Filter by language

```bash
python3 plex_subs_extractor.py --language eng
```

The comparison is made against the language value stored by Plex for the subtitle stream.

### Force overwriting the base filename

By default, existing subtitle files are preserved and collisions get numbered filenames.

If you explicitly want to overwrite the base target instead:

```bash
python3 plex_subs_extractor.py --write --force
```

## How it works

Plex stores on-demand/uploaded subtitle payloads in:

```text
com.plexapp.plugins.library.blobs.db
```

Subtitle blobs use `blob_type = 3`. Their `linked_id` maps to a row in `media_streams` in:

```text
com.plexapp.plugins.library.db
```

The extractor follows that relationship to `media_parts.file`, decompresses the gzip subtitle blob, and creates a sidecar file next to the corresponding video.

Forced subtitles are named in Plex-compatible form:

```text
Movie.eng.forced.srt
```

If that filename collides, numbering is inserted before the language/forced suffix:

```text
Movie(1).eng.forced.srt
```

## Should Plex be stopped first?

The script cannot write to Plex's databases, even if Plex is running.

However, Plex itself may be updating its two databases while they are being read. For the most consistent possible snapshot, stopping Plex briefly before a large extraction is still a sensible precaution.

## What this does not do

- It does not extract subtitle tracks embedded inside MKV/MP4 files.
- It does not modify Plex metadata.
- It does not update Plex's databases.
- It does not call OpenSubtitles or any other external subtitle service.
- It cannot guarantee compatibility with future Plex database schema changes; Plex's internal database structure is not a public stable API.

## Acknowledgements

The database relationship used here is also used by [danrahn/PlexSubtitleExtractor](https://github.com/danrahn/PlexSubtitleExtractor), which was useful as a reference when verifying Plex's subtitle blob layout.

This implementation focuses on a stricter safety model: hard SQLite read-only access, dry-run by default, explicit writes, SHA-256-based idempotence, collision-safe output naming, and local configuration for machine-specific paths.

## License

MIT. See [LICENSE](LICENSE).

---

Plex is a trademark of Plex, Inc. This project is not affiliated with or endorsed by Plex.
