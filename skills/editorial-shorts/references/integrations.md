# Integrations: what each role needs

| capability | required for | optional for | provided by | if missing |
|---|---|---|---|---|
| Python 3.9+ | everything (CLI) | — | python.org / system | nothing runs |
| ffmpeg + ffprobe (with libass, loudnorm, ebur128) | editor, reviewer | — | ffmpeg 6+ full build | no render or review; research and curation still work |
| Whisper transcription | — | curator (locate speech), editor (accessibility captions) | ffmpeg 8+ `whisper` filter + `transcription.model_path` (ggml model) | supply `captions_srt` yourself or work from frames |
| Speech synthesis | — | modes B/C with voiced lines | `tts.engine: flite` (English voices, built into some ffmpeg builds) or `command` (e.g. piper, a cloud TTS CLI) with `{text}` and `{out}`; or a per-line `audio` file produced by the agent's voice connector | use on-screen lines or mode A |
| Model with vision | curator and reviewer judgment | — | the agent running the skill | mark `visual_checked: false`; the gate holds the story |
| yt-dlp | — | researcher (public metadata, creator baseline) | `pip install yt-dlp` | enter metrics manually or leave unknown |
| Web search / browsing | — | researcher | the agent's tools | user-provided lists |
| Buffer connector (MCP) + public storage | — | publisher (real posting) | installer's Buffer account; Cloudflare R2 (or any public URL) | `local_export` for manual upload, or `mock` |
| curl 7.75+ | — | R2 upload | system curl | provide `meta.video_url` |

## Connectors the agent uses (enable them in the agent app, not in this package)
| connector | used by | needed for | without it |
|---|---|---|---|
| Buffer (MCP connector) | publisher, performance analyst | real posting (`create_post`, `list_posts`) and post metrics | `mock` or `local_export`; metrics entered by hand |
| A voice connector (for example ElevenLabs) | video editor | natural voice lines in modes B/C (per-line `audio` file) | flite or a TTS command, or on-screen lines |
| Web search and browsing (built into the agent) | trend researcher | finding references | user-provided lists |

No other skill is required. Agents run with the agent app's own tools (Bash, Read, web search, connectors).

## Whisper model
Download a ggml model once (for example `ggml-small.bin`, about 470 MB, from
`https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin`) into a normal folder and set
`transcription.model_path` in `config.json` to its full path.

## Environment variables
Environment variables for R2 (never in config): `ES_R2_ACCOUNT_ID`, `ES_R2_ACCESS_KEY_ID`,
`ES_R2_SECRET_ACCESS_KEY`, `ES_R2_BUCKET`, `ES_R2_PUBLIC_URL`, plus `integrations.storage.type: "r2"`.
`config.json` refuses keys that look like secrets.

Platform metrics differ: what an integration does not return is reported as a limit in `learn`, never estimated.

Windows note: a Python installed from the Microsoft Store runs in a package sandbox and may not see files under
`%LOCALAPPDATA%` created by other programs. Keep models and media in a normal folder (e.g. Documents).
