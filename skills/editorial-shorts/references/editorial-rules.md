# Editorial rules: when to speak, when to let the scene breathe, when to give up

## 1. Choose one mode per video
| mode | use it when | lines |
|---|---|---|
| **A. Original audio** | dialogue, reaction, sound or timing carry the story | zero added voice or text (accessibility captions of the original speech are kept) |
| **B. Spot comment** | one short intervention clarifies, reveals a detail, gives a specific reading or adds humour | 1 to `editorial.max_comment_lines` (default 2) |
| **C. Guided explanation** | a legitimate question needs context | the voice leads to an answer built on confirmed facts and pertinent images; `question` is required |

## 2. The deletion test (every line, voice or on-screen)
"If I delete this sentence, what exactly does the viewer lose?" Write the answer in `loss_if_removed`. "Nothing",
"pacing", "filler" or an empty answer rejects the line.

## 3. What the validator rejects (and why)
| rule | example | why |
|---|---|---|
| `describes_visible` | "The brown dog runs after the ball." | repeats what everyone sees (compared with the curator's `visible` annotations) |
| `narrating_the_obvious` | "Right now…", "Look how…", "Neste momento…" | narration habit |
| `fabricated_suspense` | "You won't believe…", "Wait for it" | promise not grounded in the material |
| `anticipating_payoff` | "Now he is going to jump" / a line that reveals a detail before the main moment | spoils the delivery |
| `generic_question` | "Would you jump it?" | question used by habit |
| `habitual_cta` | "Comment below", "Like and subscribe" | call to action by habit |
| `empty_adjectives` | "insane", "incredible" | adds nothing specific |
| `no_evidence` / `unverified_fact` | a claim with no detail or confirmed fact behind it | the AI must not invent intentions or dangers |
| `covers_protected` | voice over the laugh, the decisive line, a funny silence | the main moment must breathe |
| `too_long` | more than `editorial.max_words_per_line` words | spot comments are short |
| mode rules | mode A with lines; mode C without a question | consistency |

The lexicons live in `scripts/editorial_shorts/lexicon/<lang>.json` (pt, en, es shipped). Add a language by copying
`en.json`; unknown languages fall back to English patterns, so review those lines yourself with extra care.
The validator is a guardrail around judgment, not a substitute: a line can pass every rule and still be weak.

## 4. Worked example (same scene, three correct decisions)
Material: a dog runs after a ball and jumps a high gate; the person filming laughs.
- Rejected: "Neste momento, o cachorro marrom corre atrás da bola. Agora ele vai pular o portão. Olha como esse
  portão é alto! E você, pularia?" — describes the image, anticipates the jump, generic question, nothing lost.
- The jump and the laugh are funny on their own → **mode A**, zero words.
- The ball actually went under the gate while the dog went over (annotated detail `d1`) → **mode B**, after the
  laugh: "A bola achou um caminho bem mais fácil." Only if the detail is visible in the material and the line
  improves the moment.
- Nothing funny, no context, no discovery, no reaction → **discard**. Do not invent intent or danger.

## 5. Comments that change understanding
Sports: the audience saw who scored; a replay can show the earlier move that opened the space. Travel: two similar
images can hide different routes, times or prices — explain the difference only when that comparison's data is
confirmed (`facts[].status = confirmed` with sources).

## 6. Production paths
**Cut of existing material:** watch the excerpt inside its original context (who speaks, responding to what, what
happens next) → `context_checked: true`. A transcript locates candidates; visual decisions need frames
(`visual_checked: true`). Mark meaning units of speech: the cut may not start or end inside one, nor keep a unit
without the units it depends on (`requires`). Example: keeping "this game is bad" without "I would never say".

**Original story or curiosity:** start from the question and the verifiable answer (`central_claim` → a fact with
`status: confirmed` and sources). Confirm which images actually demonstrate it (`details[].proves`). No visual proof
→ reframe the story honestly or archive. No evidence → back to research.

## 7. Assembly
- Start at the first image or line that already has value; drop greetings and needless setup, keep the setup the
  joke, reaction or explanation needs.
- Deliver what the opening promises. No fixed intro, mandatory final question, universal duration or minimum
  number of cuts.
- Do not cover a decisive line, a funny silence or an explanatory sound (`protected`).
- Replay, zoom, arrow or slow motion only when they help perceive something specific. In the vertical reframe the
  ball, object, reaction or detail must stay visible (`details[].essential` + bbox/track; the reviewer checks the
  final file geometrically and by pixels).
- Check names, numbers, units, captions, sync, legibility, audio levels, start and end of the file. End when the
  story is complete.
