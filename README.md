# editorial-shorts (Claude Code plugin)

An editorial team for short vertical videos: research what is worth posting, curate the source, write only the lines
that earn their place, render, review the rendered file, publish once and learn from the results. Any language,
theme, page and supported platform.

The plugin contains:
- `skills/editorial-shorts/` — the skill: instructions, references and the CLI (Python standard library + ffmpeg).
  Its own `README.md` explains the CLI, the demo and the tests.
- `agents/` — eight agents, one per role, each named by its function:

| agent | model | role |
|---|---|---|
| `editorial-supervisor` | Claude Fable 5.2, effort max | team lead; runs as the main session and makes the editorial calls |
| `trend-researcher` | Claude Opus 5.5, effort high | collects references and groups reposts |
| `performance-analyst` | Claude Opus 5.5, effort high | studies references against each creator's baseline; reads 24 h / 7 d results |
| `media-curator` | Claude Opus 5.5, effort high | facts, usage condition, in/out, protected moments, essential details |
| `scriptwriter` | Claude Opus 5.5, effort high | mode A / B / C; only lines that pass the deletion test |
| `video-editor` | Claude Opus 5.5, effort high | renders the 1080x1920 cut |
| `video-reviewer` | Claude Opus 5.5, effort high | reviews the file itself; approves bound to sha256 or returns with seconds |
| `publisher` | Claude Opus 5.5, effort high | sends once, reconciles, records the real id |

To change a model or effort, edit the `model:` / `effort:` lines at the top of the agent file.

## Requirements on the machine
1. Claude Code (CLI or desktop app) signed in to an account with access to the models above.
2. Python 3.9+ on PATH (`python` or `py`).
3. ffmpeg and ffprobe 6+ full build (libass, loudnorm, ebur128) on PATH. For transcription: ffmpeg 8+ with the
   `whisper` filter plus a ggml model (see `skills/editorial-shorts/references/integrations.md`).
4. Optional: `yt-dlp` (public metadata), `curl` 7.75+ (R2 upload).
5. Connectors enabled in the app, only when needed: Buffer (real posting and metrics), a voice connector such as
   ElevenLabs (natural voice lines). No other skill is required.

## Install
From GitHub (this repository is a plugin marketplace):
```
/plugin marketplace add <owner>/editorial-shorts
/plugin install editorial-shorts@editorial-shorts
```
For a private repository, sign in to GitHub on that machine first (`gh auth login`).

Or from a local copy: `claude --plugin-dir <path-to-this-folder>`.

## Run
Start the supervisor as the main session:
```
claude --agent editorial-shorts:editorial-supervisor
```
or set `"agent": "editorial-shorts:editorial-supervisor"` in the project's `.claude/settings.json` so every session
in that folder starts with it. Then ask, for example: "create a workspace in ~/shorts for pt-BR pets and run the demo".

First run without any account: the supervisor creates the workspace (`es.py --workspace <folder> init`), runs
`doctor` and `demo`. Publishing stays in `simulate` until you configure and enable your own accounts.

## Em português (resumo)
Plugin com a skill `editorial-shorts` e 8 agentes, um por função. O supervisor roda no Fable 5.2 (esforço máximo)
e os outros sete no Opus 5.5. Para instalar no outro PC: instale Python e ffmpeg, entre no GitHub, rode
`/plugin marketplace add <dono>/editorial-shorts` e `/plugin install editorial-shorts@editorial-shorts`, e comece
com `claude --agent editorial-shorts:editorial-supervisor`. Buffer, R2 e voz são configurados depois; até lá a
publicação fica simulada.

License: MIT (see `LICENSE`).
