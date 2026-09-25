# PlexTools

A collection of conservative tools for maintaining and improving Plex media libraries.

This monorepo consolidates two existing projects:

- [PlexLibraryMaintainer](https://github.com/LironCareto/PlexLibraryMaintainer)
- [PlexSubsExtractor](https://github.com/LironCareto/PlexSubsExtractor)

The original repositories remain available with their original commit histories. Their current code is migrated here as independent tools so future shared infrastructure, analysis features, and an optional local web interface can evolve in one place.

## Tools

- `tools/library_maintainer/` — conservative Plex library maintenance and duplicate-media analysis.
- `tools/subs_extractor/` — extraction of subtitle blobs stored by Plex into sidecar files.

## Safety

The tools retain their existing safety models: Plex databases are treated as read-only, destructive actions are avoided, and machine-specific configuration belongs in local ignored files.

## License

MIT. See [LICENSE](LICENSE).

---

Plex is a trademark of Plex, Inc. This project is not affiliated with or endorsed by Plex.
