# editorial-shorts

An agent skill that researches content opportunities, selects or produces short vertical videos, edits, reviews,
publishes them and learns from the results — for any language, theme, page and supported platform.

It is built around one editorial rule: **a voice line must earn its place**. The skill knows when to keep the
original sound, when a single specific remark helps, when a guided explanation is justified — and when to drop a
cut. A batch may end with zero approved videos.

## Install
1. Requirements: Python 3.9+ and ffmpeg/ffprobe 6+ (full build with libass) on PATH. Optional: ffmpeg 8+ with the
   `whisper` filter and a ggml model, `yt-dlp`, a TTS command, the Buffer connector plus public storage.
2. Copy this folder to your agent's skills folder, e.g. for Claude Code:
   - personal: `~/.claude/skills/editorial-shorts/`
   - per project: `<project>/.claude/skills/editorial-shorts/`
3. Create a workspace anywhere (your data never lives inside the skill folder):
   ```
   python scripts/es.py --workspace ~/shorts-workspace init
   python scripts/es.py --workspace ~/shorts-workspace doctor
   ```
4. Edit `~/shorts-workspace/config.json`: language, region, themes, pages and their destinations, models, limits.
   Keep `publishing.mode` at `simulate` until you have configured and tested your own accounts.

## First run (synthetic material, simulated destination)
```
python scripts/es.py --workspace ~/shorts-demo demo
```
It generates a 10-second synthetic clip, loads demonstrative references, studies them, rejects a narration that
describes the image, accepts a specific remark, chooses original audio, renders a 1080x1920 cut, reviews the file,
approves it, publishes once to a simulated destination, sends a curiosity without evidence back to research, and
prints a learning report labelled as demonstrative. Exit code 0 means every step behaved as expected.

## Tests
```
cd <skill folder>
python -m unittest discover -s tests -v
```
One test per acceptance criterion (original audio with zero voice, voice rules in pt/en, curiosity without
evidence, meaning-changing cut, reframe hiding the detail, approval bound to file and account, lost confirmation
and concurrent workers without duplicates, missing credentials, portable package). Optional: set `ES_WHISPER_MODEL`
to a ggml model path to also test transcription.

## Layout
- `SKILL.md` — instructions for the agent (roles, flow, hard rules)
- `references/` — editorial rules, roles, research and learning, publishing, integrations, data formats
- `scripts/es.py` + `scripts/editorial_shorts/` — the CLI (standard library only)
- `assets/demo/` — demonstrative fixtures for the first run
- `config.example.json` — every setting, no secrets

## Em português (resumo)
Instale copiando a pasta para `~/.claude/skills/editorial-shorts/`, crie um workspace com
`python scripts/es.py --workspace <pasta> init`, rode `doctor` e depois `demo`. Ajuste `language: "pt-BR"`,
`region`, temas e páginas no `config.json`. A publicação real fica desligada (`simulate`) até você configurar as
suas contas. A regra central: toda fala precisa responder "se eu apagar esta frase, o que o espectador perde?".

License: MIT (see `LICENSE`).
