# PlexTools

A collection of conservative, specialized tools for maintaining and improving Plex media libraries.

This monorepo consolidates two existing projects:

- [PlexLibraryMaintainer](https://github.com/LironCareto/PlexLibraryMaintainer)
- [PlexSubsExtractor](https://github.com/LironCareto/PlexSubsExtractor)

The original repositories remain available with their original commit histories. Their current code is migrated here as independent tools so future shared infrastructure, analysis features, and an optional local web interface can evolve in one place.

Imported snapshots:

- PlexLibraryMaintainer: `bd4d9431e54b8c70e115a210256c079298905b7c`
- PlexSubsExtractor: `ab54f068e2877a50feb1927b6b7d1700073c4cc1`

## Tools

- `tools/library_maintainer/` — conservative Plex library maintenance and duplicate-media analysis.
- `tools/subs_extractor/` — extraction of subtitle blobs stored by Plex into sidecar files.
- `tools/track_merger/` — track transplant/alignment tool under development.
- `tools/mp4_normalizer/` — incremental, lossless MP4 normalization driven by Plex's own catalogue.

## Shared configuration

PlexTools uses one machine-local `config.json` at the repository root. Copy `config.example.json` to `config.json` and fill in the paths and settings for the machine running the tools.

The shared sections contain Plex database/path settings and media-tool executables. Tool-specific settings live below `tools`.

`config.json` is ignored by Git and must not be committed. Individual tools still accept `--config` when an alternate configuration file is required.

## MP4 normalizer

The MP4 normalizer deliberately does **not** implement its own filesystem watcher. Plex already performs media discovery, so the normalizer treats Plex as the catalogue of truth and queries the Plex library database read-only.

Normal operation is incremental: it stores a high-water mark made of Plex's addition timestamp plus `media_parts.id`, then only considers media parts Plex added after that cursor. This is intended for a lightweight hourly Synology Task Scheduler job:

```bash
python3 plextools.py mp4-normalizer run --write
```

Eligible MP4 files that are not already fast-start/streaming normalized are remuxed losslessly with stream copy and `+faststart`, validated, and atomically replaced. Backups are **not** kept by default; `--backup` is opt-in.

The first `run --write` initializes the cursor at the current end of the Plex catalogue and changes no historical files. Use `backfill` explicitly to inspect or normalize existing library contents.

## Safety

Plex databases are always treated as read-only. Write-capable tools use explicit write flags, machine-specific configuration belongs in the ignored root `config.json`, and the MP4 normalizer validates its temporary remux before replacing the original media file.

## License

MIT. See [LICENSE](LICENSE).

---

Plex is a trademark of Plex, Inc. This project is not affiliated with or endorsed by Plex.
