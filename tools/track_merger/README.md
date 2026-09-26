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

Implemented as a read-only prototype.

With `--align`, the tool:

- chooses visual anchors across the movie while avoiding the extreme beginning/end;
- extracts tiny normalized grayscale frames with ffmpeg;
- builds perceptual gradient fingerprints so exact binary frame equality is not required;
- searches a target window around each predicted correspondence;
- fits a robust affine mapping of the form `target_time = a * source_time + b`;
- performs a second validation pass at more anchors and higher temporal resolution;
- reports median/max residuals and refuses to call the mapping reliable when the visual evidence is inconsistent.

The first pass uses frame-rate/duration information only to define a search neighborhood. The final mapping comes from visual correspondences.

### M3 — Alignment validation

Partially implemented by the M2 validation pass. The next step is stronger discontinuity detection and piecewise mapping when a movie contains inserted/removed material.

### M4 — Track transplant

Implemented for audio.

With `--merge`, the tool reruns visual alignment, requires a consistent global affine mapping, rejects detected tail discontinuities, applies pitch-preserving tempo correction plus the measured timeline offset to one explicitly selected source audio stream, and creates a new output file.

All original target streams are stream-copied. Only the transplanted audio is decoded and re-encoded. Existing files are never overwritten and both inputs remain untouched.

## Configuration

PlexTrackMerger uses the shared repository-root `config.json`. Inventory uses `media_tools.ffprobe_path`; visual alignment also uses `media_tools.ffmpeg_path` when those binaries are not already discoverable.

No tool-specific configuration is required yet.

## Usage

From the PlexTools repository root:

```bash
python3 tools/track_merger/plex_track_merger.py \
  "/path/to/source.mkv" \
  "/path/to/target.mkv"
```

Without `--align`, the tool only reports M1 inventory information.

To run visual alignment:

```bash
python3 tools/track_merger/plex_track_merger.py --align \
  "/path/to/source.mkv" \
  "/path/to/target.mkv"
```

Alignment remains read-only. No track is extracted, retimed, or remuxed.

To transplant one source audio track after validated alignment:

```bash
python3 tools/track_merger/plex_track_merger.py --merge \
  --source-audio 1 \
  --language spa \
  --title "Spanish" \
  --output "/path/to/new-output.mkv" \
  "/path/to/source.mkv" \
  "/path/to/target.mkv"
```

`--source-audio` is the absolute stream index shown in the source inventory. The output path must not already exist. The target video, existing audio, subtitles, attachments, chapters and metadata are preserved where the container permits; only the transplanted audio is retimed and re-encoded.

## License

MIT. See the repository root [LICENSE](../../LICENSE).
