# Data formats (written by the judgment roles, validated by the CLI)

Times in curation and script files are SOURCE seconds. The renderer maps them to output time and stores the map in
the render manifest. Examples: `assets/demo/*.json`.

## Curation (`curate <story> curation.json`)
```json
{
  "source": {"file": "clip.mp4", "url": "https://…", "rights": {"status": "owned|licensed|permission|public_domain|creative_commons", "evidence": "link, file or note"}},
  "context_checked": true,
  "visual_checked": true,
  "segments": [{"in": 2.0, "out": 8.0, "crop_x": 650}],
  "framing": {"crop_x": "center"},
  "moments": {"setup": 3.0, "main": 4.5, "reaction": 5.0},
  "visible": [{"t0": 2.0, "t1": 8.0, "desc": "what anyone can see"}],
  "details": [{"id": "d1", "t0": 4.0, "t1": 5.0, "desc": "what most viewers miss", "essential": true,
               "track": [{"t": 4.5, "bbox": [x, y, w, h]}], "proves": ["f1"]}],
  "protected": [{"t0": 4.6, "t1": 6.4, "why": "laugh / decisive line / funny silence / explanatory sound"}],
  "meaning_units": [{"id": "u1", "t0": 3.0, "t1": 4.5, "text": "…", "requires": ["u0"]}],
  "facts": [{"id": "f1", "claim": "…", "status": "confirmed|interpretation|uncertain", "sources": ["https://…"]}],
  "central_claim": "f1",
  "captions_srt": "captions.srt"
}
```
`bbox` is in source pixels. `crop_x` is the left edge of the vertical window in source pixels (`center` by default).
Gate results: `ready` · `needs_research` · `needs_visual_review` · `return` (with reasons).

## Script (`script <story> script.json`)
```json
{"mode": "A|B|C", "language": "pt-BR", "question": "mode C only",
 "lines": [{"text": "…", "at": 6.5, "dur": 1.4, "kind": "voice|onscreen", "audio": "optional/file.wav",
            "loss_if_removed": "what the viewer would lose", "framing": "interpretation (optional)",
            "evidence": [{"type": "detail|fact", "ref": "d1"}]}],
 "assembly": "precise editing instructions"}
```

## Post text (`publish queue … --meta post.json`)
```json
{"title": "…", "text": "…", "credit": "@original_creator or source", "hashtags": ["#topic"],
 "per_platform": {"youtube": {"title": "…"}}, "video_url": "optional public URL", "youtube_category": "24"}
```

## Review return notes
`--note "SECONDS|problem|expected fix"`, e.g. `--note "12.4|caption covers the ball|move caption to the top"`.
