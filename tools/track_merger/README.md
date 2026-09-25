# PlexTrackMerger

PlexTrackMerger is a specialized PlexTools utility for transplanting a track from one version of a movie into another while preserving the better master.

The first target use case is audio: keep the better video master and add an audio track that exists only in a poorer copy.

## Design principles

- Originals are never modified.
- Analysis comes before any write operation.
- A similar total duration is not treated as proof of synchronization.
- Different encodes, resolutions, frame rates, compression levels, logos, intros, and PAL-style speed changes must be handled explicitly rather than guessed.
- Later milestones will use visual evidence across the movie to determine the temporal relationship between masters.
- The tool is intentionally narrow: track transplantation and the alignment required to make it safe.

## Milestones

### M1 — Media inventory

Implemented.

M1 is read-only. Given a source file and a target file, it:

- probes both with `ffprobe`;
- reports container duration;
- inventories video streams;
- inventories audio tracks with language, codec, channels, title and disposition;
- inventories subtitle tracks;
- reports the duration delta and ratio as diagnostic information only;
- explicitly leaves alignment as **NOT EVALUATED**.

M1 never creates or modifies a media file.

### M2 — Visual alignment

Planned.

Sample visual evidence across both masters, generate perceptual fingerprints that tolerate different encodes/resolutions, find candidate correspondences, and fit a robust time mapping.

### M3 — Alignment validation

Planned.

Validate the mapping across the full runtime, detect discontinuities or differing cuts, and refuse transplantation when confidence is insufficient.

### M4 — Track transplant

Planned.

Extract the selected source track, apply the validated temporal transformation, and remux it into a new output file. Originals remain untouched.

## Configuration

PlexTrackMerger uses the shared repository-root `config.json`. M1 only needs `media_tools.ffprobe_path` when ffprobe is not already discoverable.

No tool-specific configuration is required yet.

## Usage

From the PlexTools repository root:

```bash
python3 tools/track_merger/plex_track_merger.py \
  "/path/to/source.mkv" \
  "/path/to/target.mkv"
```

M1 only reports information. It does not perform a merge.

## License

MIT. See the repository root [LICENSE](../../LICENSE).
