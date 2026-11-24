r"""Publisher: queue approved renders, claim work atomically, send once, reconcile unknown outcomes.

State machine (per story + page + platform + account):

  queued -> claimed -> sending -> published
                    \          -> failed                (definitive rejection; `retry` re-queues)
                     \         -> confirmation_pending  (timeout / connection lost after upload)
                      \        -> awaiting_agent        (agent-mediated adapters such as Buffer via MCP)
                       -> blocked                       (preflight failed: stale render, account changed, no credentials)
  confirmation_pending -> published | failed            (only after checking the destination: never a blind resend)
  queued/claimed/blocked/failed -> cancelled            (a new render superseded the approved one)

A timeout is never permission to post again. Only `failed` and `blocked` can go back to `queued`.
"""
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from . import db, media
from .config import ConfigError, lang_code, now_iso, page as get_page
from .editorial import lint_post_text

ACTIVE = ("queued", "claimed", "sending", "awaiting_agent", "confirmation_pending", "published")


class Conflict(RuntimeError):
    pass


def _move(conn, pub_id, from_states, to, actor=None, note="", **fields):
    sets = ", ".join(f"{k}=?" for k in fields)
    sql = (f"UPDATE publications SET state=?, updated_at=?{', ' + sets if sets else ''} "
           f"WHERE id=? AND state IN ({','.join('?' * len(from_states))})")
    cur = conn.execute(sql, (to, now_iso(), *fields.values(), pub_id, *from_states))
    if cur.rowcount != 1:
        st = db.row(conn, "SELECT state FROM publications WHERE id=?", pub_id)
        raise Conflict(f"{pub_id}: cannot move {st and st['state']} -> {to}")
    conn.execute("INSERT INTO pub_log(pub_id, from_state, to_state, worker, note, ts) VALUES(?,?,?,?,?,?)",
                 (pub_id, "|".join(from_states), to, actor, note, now_iso()))


def current_render(conn, story_id):
    r = db.row(conn, "SELECT * FROM renders WHERE story_id=? AND status='current'", story_id)
    if r:
        r["manifest"] = db.jload(r["manifest"])
    return r


def approved_review(conn, render):
    return db.row(conn, "SELECT * FROM reviews WHERE render_id=? AND sha256=? AND decision='approved' AND valid=1 "
                        "ORDER BY id DESC LIMIT 1", render["id"], render["sha256"])


def effective_adapter(cfg, dest):
    return "mock" if cfg["publishing"]["mode"] == "simulate" else dest.get("adapter", "mock")


def compose_text(meta, platform):
    pp = (meta.get("per_platform") or {}).get(platform, {})
    title = pp.get("title", meta.get("title", "")).strip()
    body = pp.get("text", meta.get("text", "")).strip()
    tail = "\n".join(x for x in [meta.get("credit", "").strip(), " ".join(meta.get("hashtags", []))] if x)
    return title, body, tail


def check_meta(meta, cfg, platform, lang):
    prof = cfg["platform_profiles"].get(platform, {})
    title, body, tail = compose_text(meta, platform)
    probs = []
    if not title:
        probs.append("title is empty")
    if prof and len(title) > prof["title_max"]:
        probs.append(f"title longer than {prof['title_max']} characters for {platform}")
    if prof and len("\n\n".join([title, body, tail])) > prof["text_max"]:
        probs.append(f"text longer than {prof['text_max']} characters for {platform}")
    if prof and len(meta.get("hashtags", [])) > prof["hashtags_max"]:
        probs.append(f"more than {prof['hashtags_max']} hashtags for {platform}")
    probs += lint_post_text(title + "\n" + body, lang)
    return probs


def queue(conn, cfg, story_id, page_id, meta):
    story = db.row(conn, "SELECT * FROM stories WHERE id=?", story_id)
    if not story:
        raise ConfigError(f"story {story_id} does not exist")
    rnd = current_render(conn, story_id)
    if not rnd:
        raise Conflict("no current render for this story")
    if not approved_review(conn, rnd):
        raise Conflict(f"render {rnd['id']} has no valid approved review: review the file that will be published")
    if media.sha256(rnd["path"]) != rnd["sha256"]:
        raise Conflict("rendered file changed after review (sha256 mismatch): render and review again")
    pg = get_page(cfg, page_id)
    lang = lang_code(pg.get("language") or cfg)
    meta = dict(meta, _file=rnd["path"])
    out = []
    with db.tx(conn):
        for d in pg.get("destinations", []):
            plat, acct = d["platform"], d["account"]
            probs = check_meta(meta, cfg, plat, lang)
            if probs:
                raise Conflict(f"{plat}: post text rejected: " + "; ".join(probs))
            key = hashlib.sha1(f"{story_id}|{page_id}|{plat}|{acct}|{rnd['sha256']}".encode()).hexdigest()[:16]
            pid = "pb_" + hashlib.sha1(f"{story_id}|{page_id}|{plat}|{acct}".encode()).hexdigest()[:10]
            ex = db.row(conn, "SELECT * FROM publications WHERE id=?", pid)
            fields = dict(render_id=rnd["id"], sha256=rnd["sha256"], idem_key=key, adapter=effective_adapter(cfg, d),
                          meta=db.jdump(meta), worker=None, lease_until=None, last_error=None)
            if ex is None:
                conn.execute("INSERT INTO publications(id, story_id, page_id, platform, account, state, created_at, updated_at) "
                             "VALUES(?,?,?,?,?,'new',?,?)", (pid, story_id, page_id, plat, acct, now_iso(), now_iso()))
                _move(conn, pid, ("new",), "queued", note="queued", **fields)
                out.append((pid, plat, acct, "queued"))
            elif ex["state"] in ("failed", "blocked", "cancelled"):
                _move(conn, pid, (ex["state"],), "queued", note="re-queued", **fields)
                out.append((pid, plat, acct, "re-queued"))
            else:
                out.append((pid, plat, acct, f"already {ex['state']} (not queued again)"))
        conn.execute("UPDATE stories SET status='queued', updated_at=? WHERE id=?", (now_iso(), story_id))
    return out


def claim(conn, worker, lease_s):
    now = time.time()
    with db.tx(conn):
        p = db.row(conn, "SELECT * FROM publications WHERE state='queued' OR (state='claimed' AND lease_until<?) "
                         "ORDER BY created_at, id LIMIT 1", now)
        if not p:
            return None
        _move(conn, p["id"], ("queued", "claimed"), "claimed", worker, "claimed", worker=worker, lease_until=now + lease_s)
    return db.row(conn, "SELECT * FROM publications WHERE id=?", p["id"])


def preflight(conn, cfg, pub, adapters):
    probs = []
    rnd = current_render(conn, pub["story_id"])
    if not rnd or rnd["id"] != pub["render_id"]:
        probs.append("approved render was superseded: review the new render and queue again")
    else:
        if not Path(rnd["path"]).exists() or media.sha256(rnd["path"]) != pub["sha256"]:
            probs.append("file on disk is not the approved version (sha256 mismatch)")
        if not approved_review(conn, rnd):
            probs.append("approved review is no longer valid")
    try:
        pg = get_page(cfg, pub["page_id"])
        d = next((d for d in pg.get("destinations", [])
                  if d["platform"] == pub["platform"] and d["account"] == pub["account"]), None)
        if d is None:
            probs.append(f"page {pub['page_id']} no longer sends {pub['platform']} to account {pub['account']}")
        elif effective_adapter(cfg, d) != pub["adapter"]:
            probs.append(f"destination changed from {pub['adapter']} to {effective_adapter(cfg, d)}: queue again")
    except ConfigError as e:
        probs.append(str(e))
    if not probs:
        ok, why = adapters[pub["adapter"]].available(pub)
        if not ok:
            probs.append(why)
    return probs


def run(conn, cfg, worker, max_items=50):
    adapters = make_adapters(cfg)
    report = []
    for _ in range(max_items):
        pub = claim(conn, worker, cfg["publishing"]["lease_seconds"])
        if not pub:
            break
        probs = preflight(conn, cfg, pub, adapters)
        if probs:
            with db.tx(conn):
                _move(conn, pub["id"], ("claimed",), "blocked", worker, "; ".join(probs), last_error="; ".join(probs))
            report.append((pub["id"], pub["platform"], "blocked", "; ".join(probs)))
            continue
        with db.tx(conn):
            _move(conn, pub["id"], ("claimed",), "sending", worker, "sending",
                  attempts=pub["attempts"] + 1, lease_until=time.time() + cfg["publishing"]["lease_seconds"])
        try:
            res = adapters[pub["adapter"]].publish(pub)
        except Exception as e:  # anything unexpected after the send started = unknown outcome
            res = {"status": "unknown", "error": f"{type(e).__name__}: {e}"}
        report.append(_apply(conn, pub, res, worker, adapters))
    return report


def _apply(conn, pub, res, worker, adapters):
    st = res["status"]
    with db.tx(conn):
        if st == "published":
            _move(conn, pub["id"], ("sending",), "published", worker, "confirmed by destination",
                  remote_id=res["remote_id"], remote_url=res.get("url"))
        elif st == "rejected":
            _move(conn, pub["id"], ("sending",), "failed", worker, res.get("error", ""), last_error=res.get("error"))
        elif st == "handoff":
            _move(conn, pub["id"], ("sending",), "awaiting_agent", worker, res.get("handoff", ""), last_error=None)
        else:
            _move(conn, pub["id"], ("sending",), "confirmation_pending", worker, res.get("error", "no confirmation"),
                  last_error=res.get("error"))
    if st == "unknown":
        return reconcile_one(conn, db.row(conn, "SELECT * FROM publications WHERE id=?", pub["id"]), adapters, worker)
    return (pub["id"], pub["platform"], {"handoff": "awaiting_agent"}.get(st, st), res.get("remote_id") or res.get("error") or res.get("handoff", ""))


def reconcile_one(conn, pub, adapters, worker="reconcile"):
    look = adapters[pub["adapter"]].lookup(pub)
    with db.tx(conn):
        if look["status"] == "found":
            _move(conn, pub["id"], ("confirmation_pending",), "published", worker, "found at destination after uncertain send",
                  remote_id=look["remote_id"], remote_url=look.get("url"), last_error=None)
            return (pub["id"], pub["platform"], "published (reconciled)", look["remote_id"])
        if look["status"] == "absent":
            _move(conn, pub["id"], ("confirmation_pending",), "failed", worker, "confirmed absent at destination",
                  last_error="confirmed absent at destination; `retry` is safe")
            return (pub["id"], pub["platform"], "failed (confirmed absent)", "retry is safe")
    return (pub["id"], pub["platform"], "confirmation_pending", look.get("note", "cannot verify: leave pending"))


def reconcile(conn, cfg):
    adapters = make_adapters(cfg)
    out = []
    with db.tx(conn):  # a worker that died while sending: outcome unknown, never re-queue blindly
        for p in db.rows(conn, "SELECT id FROM publications WHERE state='sending' AND lease_until<?", time.time()):
            _move(conn, p["id"], ("sending",), "confirmation_pending", "reconcile", "worker lease expired while sending")
    for p in db.rows(conn, "SELECT * FROM publications WHERE state='confirmation_pending'"):
        out.append(reconcile_one(conn, p, adapters))
    return out


def confirm(conn, pub_id, outcome, remote_id=None, url=None, note=""):
    """Outcome reported by an agent that called an external tool (e.g. an MCP connector)."""
    with db.tx(conn):
        if outcome == "published":
            if not remote_id:
                raise Conflict("published needs the real remote id")
            _move(conn, pub_id, ("awaiting_agent", "confirmation_pending"), "published", "agent", note,
                  remote_id=remote_id, remote_url=url, last_error=None)
        elif outcome == "uncertain":
            _move(conn, pub_id, ("awaiting_agent",), "confirmation_pending", "agent", note or "agent lost the response",
                  last_error=note or "uncertain")
        elif outcome == "rejected":
            _move(conn, pub_id, ("awaiting_agent",), "failed", "agent", note, last_error=note)
        elif outcome == "not_found":
            _move(conn, pub_id, ("confirmation_pending",), "failed", "agent", "agent confirmed no post exists",
                  last_error="confirmed absent at destination; `retry` is safe")
        else:
            raise Conflict(f"unknown outcome {outcome}")


def retry(conn, pub_id):
    with db.tx(conn):
        _move(conn, pub_id, ("failed", "blocked"), "queued", None, "manual retry", worker=None, lease_until=None)


def cancel_superseded(conn, story_id, new_render_id):
    for p in db.rows(conn, "SELECT id, state FROM publications WHERE story_id=? AND render_id<>? "
                           "AND state IN ('queued','claimed','blocked','failed')", story_id, new_render_id):
        _move(conn, p["id"], (p["state"],), "cancelled", None, f"superseded by {new_render_id}")


# ---------------- adapters ----------------

class MockAdapter:
    """Simulated destination that behaves like a real, NON-idempotent platform: every publish call creates a
    new post. Faults can be injected through <workspace>/sim_outbox/_faults.json, e.g.
    {"accept_then_drop": 1, "lookup_unavailable": 0, "reject": 0} (counters are consumed)."""

    def __init__(self, cfg):
        self.dir = Path(cfg["_workspace"]) / "sim_outbox"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _fault(self, name):
        f = self.dir / "_faults.json"
        if not f.exists():
            return False
        lock = self.dir / "_faults.lock"
        for _ in range(200):
            try:
                os.mkdir(lock)
                break
            except FileExistsError:
                time.sleep(0.02)
        try:
            j = json.loads(f.read_text())
            if j.get(name, 0) > 0:
                j[name] -= 1
                f.write_text(json.dumps(j))
                return True
            return False
        finally:
            os.rmdir(lock)

    def available(self, pub):
        return True, ""

    def publish(self, pub):
        f = self.dir / "_faults.json"
        delay = json.loads(f.read_text()).get("delay_ms", 0) if f.exists() else 0  # not consumed: widens race windows
        time.sleep(delay / 1000)
        if self._fault("reject"):
            return {"status": "rejected", "error": "simulated rejection: video format not accepted"}
        # unique per call, so a duplicate send would show up as two files instead of overwriting one
        rid = f"sim-{time.time_ns()}-{os.getpid()}"
        (self.dir / f"post_{rid}.json").write_text(json.dumps({
            "remote_id": rid, "idem_key": pub["idem_key"], "platform": pub["platform"], "account": pub["account"],
            "sha256": pub["sha256"], "meta": json.loads(pub["meta"])}, ensure_ascii=False, indent=1), encoding="utf-8")
        if self._fault("accept_then_drop"):
            raise ConnectionError("simulated: upload accepted, connection dropped before confirmation")
        return {"status": "published", "remote_id": rid, "url": f"sim://{pub['platform']}/{rid}"}

    def lookup(self, pub):
        if self._fault("lookup_unavailable"):
            return {"status": "unknown", "note": "destination lookup unavailable: leave pending"}
        for f in self.dir.glob("post_*.json"):
            j = json.loads(f.read_text(encoding="utf-8"))
            if j["idem_key"] == pub["idem_key"]:
                return {"status": "found", "remote_id": j["remote_id"], "url": f"sim://{pub['platform']}/{j['remote_id']}"}
        return {"status": "absent"}


class LocalExportAdapter:
    """Writes the approved file plus ready-to-paste metadata to a folder for manual upload."""

    def __init__(self, cfg):
        self.root = Path(cfg["_workspace"]) / cfg["publishing"]["export_dir"]

    def _target(self, pub):
        return self.root / pub["page_id"] / pub["platform"] / f"{pub['story_id']}_{pub['idem_key']}.mp4"

    def available(self, pub):
        return True, ""

    def publish(self, pub):
        t = self._target(pub)
        t.parent.mkdir(parents=True, exist_ok=True)
        rnd_path = db.jload(pub["meta"]).get("_file")
        tmp = t.with_suffix(".part")
        shutil.copyfile(rnd_path, tmp)
        os.replace(tmp, t)
        title, body, tail = compose_text(json.loads(pub["meta"]), pub["platform"])
        t.with_suffix(".txt").write_text("\n\n".join(x for x in (title, body, tail) if x), encoding="utf-8")
        return {"status": "published", "remote_id": str(t.relative_to(self.root)), "url": t.as_uri()}

    def lookup(self, pub):
        t = self._target(pub)
        if t.exists() and media.sha256(t) == pub["sha256"]:
            return {"status": "found", "remote_id": str(t.relative_to(self.root)), "url": t.as_uri()}
        return {"status": "absent"}


class BufferHandoffAdapter:
    """Buffer is reached through an MCP connector owned by the agent session, not by this CLI.
    publish() uploads the video to public storage (if configured) and writes a handoff file with the exact
    create_post request. The agent calls the connector and reports back with `es.py publish confirm`."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.dir = Path(cfg["_workspace"]) / "handoff"

    def available(self, pub):
        b = self.cfg["integrations"]["buffer"]
        if not b.get("organization_id"):
            return False, "buffer: integrations.buffer.organization_id is empty (get it with the Buffer connector's get_account)"
        if not (b.get("channels") or {}).get(pub["platform"]):
            return False, f"buffer: no channel id for {pub['platform']} in integrations.buffer.channels (list_channels)"
        if not json.loads(pub["meta"]).get("video_url") and not storage_ready(self.cfg)[0]:
            return False, "buffer needs a public video URL: set storage (R2 env vars) or meta.video_url"
        return True, ""

    def publish(self, pub):
        meta = json.loads(pub["meta"])
        url = meta.get("video_url") or upload_r2(meta["_file"], pub["idem_key"])
        title, body, tail = compose_text(meta, pub["platform"])
        full = "\n\n".join(x for x in (title, body, tail) if x)
        req = {"organizationId": self.cfg["integrations"]["buffer"]["organization_id"],
               "channelId": self.cfg["integrations"]["buffer"]["channels"][pub["platform"]],
               "mode": "addToQueue", "schedulingType": "automatic", "assets": [{"video": {"url": url}}], "text": full}
        p = pub["platform"]
        if p == "instagram":
            req["metadata"] = {"instagram": {"type": "reel", "shouldShareToFeed": True}}
        elif p == "tiktok":
            req["metadata"] = {"tiktok": {"title": title}}
        elif p == "youtube":
            req["text"] = "\n\n".join(x for x in (body, tail) if x)
            req["metadata"] = {"youtube": {"title": title, "categoryId": str(meta.get("youtube_category", "24")),
                                           "privacy": "public", "madeForKids": False}}
        elif p == "facebook":
            req["metadata"] = {"facebook": {"type": "reel"}}
        self.dir.mkdir(parents=True, exist_ok=True)
        f = self.dir / f"{pub['id']}.json"
        f.write_text(json.dumps({"publication": pub["id"], "idem_marker": pub["idem_key"], "tool": "buffer create_post",
                                 "request": req, "after": f"es.py publish confirm {pub['id']} --remote-id <post id> "
                                                          f"(or --uncertain on timeout, --rejected '<error>')"},
                                ensure_ascii=False, indent=1), encoding="utf-8")
        return {"status": "handoff", "handoff": str(f)}

    def lookup(self, pub):
        return {"status": "unknown",
                "note": f"ask the agent: Buffer list_posts, look for a post whose video URL contains {pub['idem_key']}; "
                        f"then `publish confirm {pub['id']} --remote-id <id>` or `--not-found`"}


R2_VARS = ("ES_R2_ACCOUNT_ID", "ES_R2_ACCESS_KEY_ID", "ES_R2_SECRET_ACCESS_KEY", "ES_R2_BUCKET", "ES_R2_PUBLIC_URL")


def storage_ready(cfg):
    if cfg["integrations"]["storage"].get("type") != "r2":
        return False, "storage.type is not r2"
    missing = [v for v in R2_VARS if not os.environ.get(v)]
    return (not missing), ("missing env: " + ", ".join(missing) if missing else "")


def upload_r2(path, key):
    """S3-compatible PUT with curl --aws-sigv4 (curl >= 7.75). Credentials go through stdin, not argv."""
    e = os.environ
    obj = f"{key}_{Path(path).name}"
    cfg = f'user = "{e["ES_R2_ACCESS_KEY_ID"]}:{e["ES_R2_SECRET_ACCESS_KEY"]}"\n'
    subprocess.run(["curl", "-sS", "--fail-with-body", "-K", "-", "-X", "PUT", "-T", str(path),
                    "--aws-sigv4", "aws:amz:auto:s3", "-H", "x-amz-content-sha256: UNSIGNED-PAYLOAD",
                    "-H", "Content-Type: video/mp4",
                    f"https://{e['ES_R2_ACCOUNT_ID']}.r2.cloudflarestorage.com/{e['ES_R2_BUCKET']}/{obj}"],
                   input=cfg, text=True, check=True, capture_output=True)
    url = e["ES_R2_PUBLIC_URL"].rstrip("/") + "/" + obj
    subprocess.run(["curl", "-sSfI", url], check=True, capture_output=True)
    return url


def make_adapters(cfg):
    return {"mock": MockAdapter(cfg), "local_export": LocalExportAdapter(cfg), "buffer": BufferHandoffAdapter(cfg)}
