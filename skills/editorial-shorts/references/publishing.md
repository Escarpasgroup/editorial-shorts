# Publishing safely

## Destinations
Each page lists `destinations`: `{platform, adapter, account}`. `account` is the label of the installer's own
account for that page. In `publishing.mode: "simulate"` every destination is forced to the `mock` adapter
(`WS/sim_outbox/`). Switch to `"live"` only when the user asks, for their own accounts.

| adapter | what it does | needs |
|---|---|---|
| `mock` | simulated, non-idempotent platform (each call creates a post); supports fault injection | nothing |
| `local_export` | copies the approved file + ready-to-paste text to `WS/export/<page>/<platform>/` | nothing |
| `buffer` | writes `WS/handoff/<publication>.json` with the `create_post` request; the agent calls the Buffer connector and reports back | Buffer connector in the agent, `integrations.buffer.organization_id` + `channels.<platform>` (from `get_account` / `list_channels`; never guess ids), a public video URL (R2 env vars or `meta.video_url`) |
The Buffer request mirrors the shape documented for the connector (reel/feed for Instagram, title for TikTok and
YouTube). Check it against the connector's current tool schema before the first live post.

## Flow
1. `publish queue <story> --page <page> --meta post.json` — refuses unless the current render has a valid approved
   review for its exact sha256 and the file on disk still matches. Post text is checked per platform (title and text
   length, hashtags, no fabricated suspense or habitual calls to action).
2. `publish run --worker <name>` — claims one publication at a time inside a database write lock, runs preflight
   (render not superseded, sha256, review valid, page still sends this platform to this account, adapter available),
   marks `sending`, then calls the adapter once.
3. Outcomes: `published` (real id stored) · `failed` (definitive rejection) · `awaiting_agent` (handoff written) ·
   `confirmation_pending` (timeout / dropped connection) · `blocked` (preflight problem, with the reason).
4. Agent-mediated send: run the connector call from the handoff file, then
   `publish confirm <pub> --remote-id <id>` · `--uncertain` (timeout) · `--rejected "<error>"`.

## The critical case: accepted upload, lost confirmation
State `confirmation_pending`. `publish reconcile` (or the next `run`) checks the destination when the adapter can
(mock/export do; Buffer → the agent lists posts and looks for the video URL that contains the publication's
`idem_key`). Found → `published` with the real id. Confirmed absent → `failed`, and only then `publish retry` is
allowed. Cannot verify → stays pending and is reported. A worker that died while `sending` becomes
`confirmation_pending` after its lease expires — never re-queued blindly. Two workers cannot hold the same
publication: claiming is a single locked transaction and the (story, page, platform, account) pair is unique.

## After publishing
Store metrics with `metrics <pub> --window 24h --file m.json` (add `--demo` for illustrative numbers). Fields:
impressions, reach, views, avg_view_s, avg_view_pct, completion_rate, retention_3s, shares, saves, comments, likes,
retention_curve `[[second, percent], ...]`. Leave unknown values out or null.
