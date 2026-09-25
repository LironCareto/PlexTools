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

## Shared configuration

PlexTools uses one machine-local `config.json` at the repository root. Copy `config.example.json` to `config.json` and fill in the paths and settings for the machine running the tools.

The shared sections contain Plex database/path settings and media-tool executables. Tool-specific settings live below `tools`.

`config.json` is ignored by Git and must not be committed. Individual tools still accept `--config` when an alternate configuration file is required.

## Safety

The tools retain their existing safety models: Plex databases are treated as read-only, destructive actions are avoided, and machine-specific configuration belongs in the ignored root `config.json`.

## License

MIT. See [LICENSE](LICENSE).

---

Plex is a trademark of Plex, Inc. This project is not affiliated with or endorsed by Plex.
