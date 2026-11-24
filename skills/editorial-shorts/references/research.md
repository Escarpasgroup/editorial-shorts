# Research, study and learning

## Configure the scope
`config.json`: `language`, `region`, `themes`, `research.platforms`, `research.sources`, `research.period_days`.
Pilot (adjustable, not a requirement): 60–100 references over 6–8 themes, including typical performers, not only hits.

## Reference record (`research add <file.json>`)
Required: `url`, `platform`, `creator`, `theme`, `why`. Recommended: `title`, `published_at`, `moment_s` (the second
worth watching), `original_url` (marks a repost), `metrics` {views, likes, comments, shares — `null` when unknown},
`creator_baseline` {median_views, n, same_platform, same_format, age_matched}, `angle`, `format`, `audio_type`
(original | voiceover | music | mixed), `execution`, and `answers`:
| key | question |
|---|---|
| `hook` | What exactly holds attention or raises interest? |
| `supported_at_s` | Do the image or original audio support that reading? At which second? |
| `takeaway` | What information, emotion, humour or utility does the viewer take away? |
| `share_reason` | Who would have a concrete reason to send it to someone? |
| `pattern_elsewhere` | Does the pattern appear in different stories and creators? |
| `more_material` | Is there usable material for more good episodes? |
`demo: true` marks demonstrative data.

## Comparing fairly
`ratio = views / creator's median views` only when the baseline has at least `research.baseline_min_videos`
videos on the same platform and format; otherwise the ratio is "unknown" and the reason is stored. Hashtag growth,
search volume, views and share rate measure different things: do not mix them. Private or unavailable metrics stay
null ("unknown"), never estimated.

`research enrich` (optional, needs `yt-dlp`) fills public views/likes/comments and a baseline from the creator's
latest uploads. Limits: shares are usually not public; the baseline is not age-matched.

## What `research study` recommends per theme
- `dedicated_page`: at least `page_min_independent_stories` independent outlier stories (reposts grouped), from
  `page_min_creators` creators, with `page_min_available_material` leads to usable material.
- `general_page_story`: outlier(s) worth a cut on the general page.
- `archive`: nothing beat its creator's typical result, or no comparable baseline.
Factors (angle, format, audio type) are flagged `pattern` only when they repeat across independent stories from
different creators. "Football goes viral" is not a conclusion; "replays that explain the previous move" is.

## After publishing (`metrics`, `learn`)
Collect what each integration really provides at 24 h and 7 d (`metrics.windows`). Three separate questions:
- **stopped** — 3-second retention, or views/impressions;
- **stayed until the payoff** — completion, or retention at the manifest's `delivery_s`;
- **wanted to share** — shares / reach.
Comparisons stay inside the same page, platform and window. The report also lists, per video, the voice mode, the
first intervention and the payoff second. A retention drop when the voice enters is flagged for review as a
hypothesis. A finding needs `metrics.pattern_min_stories` different stories of real (non-demo) data, e.g.:
"On this page's sports cuts, replays that explain the previous move had more shares per reach in three different
stories. Test two more plays of that kind." Never present numbers like that from demo data.
