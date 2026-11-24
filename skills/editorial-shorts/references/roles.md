# The eight roles

Roles are logical. One agent session can play all of them. When the skill is installed as the `editorial-shorts`
plugin, each role also has its own agent (`agents/` in the plugin): run the supervisor as the main session
(`claude --agent editorial-shorts:editorial-supervisor`) and it delegates every other role to its specialist.
Mechanical steps are CLI commands (no model). Judgment steps are done by the most capable model available
(`models.judgment`), or by a person, and are recorded with reasons. `cost_limits.max_judgment_calls_per_story`
warns when a story eats too many decisions: archive it rather than polishing a weak cut.

| role | input | output | done when | judgment or mechanical |
|---|---|---|---|---|
| 1 Supervisor | study, references, analyst findings, curator gate | `story new/decide`: page, angle, priority; approve / research / return / archive | every story has a recorded decision and reason | judgment |
| 2 Trend researcher | configured language, region, sources, period, platforms | references JSON → `research add` (origin, date, platform, why, link, moment) | candidates stored, reposts grouped | judgment to find, mechanical to store/enrich |
| 3 Performance analyst | references; later metrics | `research study`; `learn` | conclusions with sample size and limitations | mechanical numbers, judgment for conclusions |
| 4 Curator | approved story, source material | curation JSON → `curate` (facts with status, usage condition, context, in/out, moments, protected, details, meaning units) | gate `ready` | judgment (needs to see frames) |
| 5 Scriptwriter | curation | script JSON → `script` (mode, lines with `loss_if_removed` and evidence, assembly notes) | validator accepts | judgment |
| 6 Editor | curation + accepted script | `render` → `renders/vN/final.mp4` + manifest | file rendered; earlier approvals voided | mechanical (ffmpeg), judgment for crop/timing choices |
| 7 Reviewer | the rendered file | `review auto` (QA, framing by geometry and pixels, protected audio, script rules, review frames) then `review decide approve` or `return --note "SECONDS|problem|fix"` | approval bound to sha256, or return with exact seconds | judgment (must look at the frames) |
| 8 Publisher | approved render, page config, post text | `publish queue` → `publish run` → (`reconcile`/`confirm`) | real remote id recorded | mechanical; agent calls connectors when the adapter needs them |

## Supervisor decisions
- `approve` — worth producing for the page (needs a configured page).
- `research` — promising but the central information is not verified.
- `return` — fixable problem (angle, material, usage condition).
- `archive` — not worth it. Archiving is a good outcome when the material is weak.
A single viral can justify a cut. A dedicated page needs repeated performance across independent stories and
enough usable material (`research study` says `dedicated_page` only then).

## Reviewer checklist (in addition to `review auto`)
1. Understandable for someone who never saw the original?
2. Does the file deliver what the opening promises?
3. Does the cut respect the context and the meaning of what was said?
4. Captions in sync and legible; names, numbers, units right; audio levels; clean start and end.
A return names the exact second, the problem and the expected fix. Any change → new render → new review.
