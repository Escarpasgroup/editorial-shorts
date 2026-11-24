"""Command line for the eight roles. Mechanical steps run here; judgment is recorded here with its reasons.

Exit codes: 0 ok · 1 error · 2 editorial rejection · 3 waiting on a person/agent or missing capability.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

from . import db, editorial, learn, media, publish, research, review
from .config import ConfigError, EXAMPLE_CONFIG, load, now_iso, page as get_page

JUDGMENT_ROLES = {"supervisor", "analyst", "curator", "scriptwriter", "reviewer"}


def _ws(a):
    return Path(a.workspace or os.environ.get("ES_WORKSPACE") or ".").resolve()


def _json_file(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _story(conn, sid):
    s = db.row(conn, "SELECT * FROM stories WHERE id=?", sid)
    if not s:
        raise ConfigError(f"story {sid} does not exist (es.py story list)")
    return s


def _sdir(cfg, sid):
    d = Path(cfg["_workspace"]) / "stories" / sid
    d.mkdir(parents=True, exist_ok=True)
    return d


def _set_status(conn, sid, status):
    conn.execute("UPDATE stories SET status=?, updated_at=? WHERE id=?", (status, now_iso(), sid))


def _event(conn, sid, role, action, payload):
    db.log_event(conn, sid, role, action, payload, now_iso())


def _budget_warning(conn, cfg, sid):
    n = db.row(conn, "SELECT COUNT(*) n FROM events WHERE story_id=? AND role IN (%s)" %
               ",".join("?" * len(JUDGMENT_ROLES)), sid, *JUDGMENT_ROLES)["n"]
    lim = cfg["cost_limits"]["max_judgment_calls_per_story"]
    if n > lim:
        print(f"warning: {n} judgment decisions recorded for {sid} (limit {lim}); consider archiving it")


def latest_script(conn, sid):
    s = db.row(conn, "SELECT * FROM scripts WHERE story_id=? AND verdict='accepted' ORDER BY id DESC LIMIT 1", sid)
    return db.jload(s["data"]) if s else None


def curation_of(conn, sid):
    c = db.row(conn, "SELECT data FROM curations WHERE story_id=?", sid)
    return db.jload(c["data"]) if c else None


def platforms_of(cfg, page_id):
    try:
        return [d["platform"] for d in get_page(cfg, page_id).get("destinations", [])] if page_id else []
    except ConfigError:
        return []


# ---------------- commands ----------------

def cmd_init(a):
    ws = _ws(a)
    ws.mkdir(parents=True, exist_ok=True)
    cfgp = ws / "config.json"
    if cfgp.exists() and not a.force:
        print(f"{cfgp} already exists (use --force to overwrite)")
    else:
        shutil.copyfile(EXAMPLE_CONFIG, cfgp)
        print(f"created {cfgp} from config.example.json (publishing.mode = simulate)")
    for d in ("data", "stories", "reports"):
        (ws / d).mkdir(exist_ok=True)
    db.connect(ws).close()
    print(f"workspace ready: {ws}\nnext: es.py --workspace \"{ws}\" doctor")
    return 0


def doctor_checks(cfg):
    has = shutil.which
    whisper_model = (cfg["transcription"].get("model_path") or "").strip()
    chk = [
        ("ffmpeg + ffprobe", bool(has("ffmpeg") and has("ffprobe")), "editor, reviewer (required)", "install ffmpeg 6+"),
        ("ffmpeg subtitles (libass)", media.has_filter("subtitles"), "editor: burned captions/comments", "use a full ffmpeg build"),
        ("ffmpeg loudnorm/ebur128", media.has_filter("loudnorm") and media.has_filter("ebur128"), "editor, reviewer", "full ffmpeg build"),
        ("transcription (ffmpeg whisper + model)", media.has_filter("whisper") and bool(whisper_model) and Path(whisper_model).exists(),
         "curator: locate speech; editor: accessibility captions (optional)",
         "ffmpeg 8+ with whisper and transcription.model_path -> ggml model"),
        ("speech synthesis", cfg["tts"]["engine"] == "flite" and media.has_filter("flite")
         or cfg["tts"]["engine"] == "command" and bool(cfg["tts"]["command"]),
         "editor: voiced lines in modes B/C (optional: on-screen lines and original audio need none)",
         "tts.engine = flite (English voices) or command [... {text} ... {out}]"),
        ("yt-dlp", bool(has("yt-dlp")), "researcher: public metadata and creator baseline (optional)", "pip install yt-dlp"),
        ("curl with --aws-sigv4", bool(has("curl")), "publisher: R2 upload for Buffer (optional)", "curl 7.75+"),
        ("R2 storage env vars", publish.storage_ready(cfg)[0], "publisher: public video URL for Buffer (optional)",
         "set ES_R2_* environment variables and integrations.storage.type = r2"),
    ]
    adapters = publish.make_adapters(cfg)
    fake = {"platform": None, "meta": "{}"}
    for pg in cfg["pages"]:
        for d in pg.get("destinations", []):
            eff = publish.effective_adapter(cfg, d)
            ok, why = adapters[eff].available(dict(fake, platform=d["platform"]))
            chk.append((f"destination {pg['id']}/{d['platform']}/{d['account']} via {eff}", ok,
                        "publisher", why or ("simulated: nothing leaves this machine" if eff == "mock" else "ready")))
    return chk


def cmd_doctor(a, cfg, conn):
    rows = doctor_checks(cfg)
    print(f"publishing.mode = {cfg['publishing']['mode']}")
    for name, ok, need, fix in rows:
        print(f"[{'ok' if ok else '--'}] {name}\n      used by: {need}" + ("" if ok else f"\n      to enable: {fix}"))
    print("Research, curation, scripting, rendering and local export work without any account or credential.")
    return 0 if rows[0][1] else 3


def cmd_research(a, cfg, conn):
    if a.action == "add":
        data = _json_file(a.file)
        recs = data["references"] if isinstance(data, dict) else data
        n = research.add_refs(conn, cfg, recs)
        print(f"{n} references stored ({len(recs)} read); reposts grouped into clusters")
    elif a.action == "enrich":
        if not research.have_ytdlp():
            print("yt-dlp is not installed: metrics stay as provided (unknown where missing)")
            return 3
        for r in db.rows(conn, "SELECT * FROM refs"):
            if a.ids and r["id"] not in a.ids:
                continue
            d = research.enrich_with_ytdlp(db.jload(r["data"]))
            research.add_refs(conn, cfg, [d])
            print(f"{r['id']}: views={d['metrics'].get('views')} baseline={d.get('creator_baseline')}")
    else:
        s = research.study(conn, cfg)
        out = Path(cfg["_workspace"]) / "reports" / "study.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
        if a.json:
            print(json.dumps(s, ensure_ascii=False, indent=1))
            return 0
        if s["demo_data"]:
            print("DEMONSTRATIVE DATA: these numbers illustrate the method and are not evidence.")
        print(f"{s['total_refs']} references")
        for lim in s["limitations"]:
            print(f"limitation: {lim}")
        for th, t in s["themes"].items():
            pats = [f"{k}={v}" for k in ("angle", "format", "audio_type") for v, x in t["factors"][k].items() if x["pattern"]]
            print(f"- {th}: {t['recommendation']} · {t['why']}" + (f" · repeated: {', '.join(pats)}" if pats else ""))
        print(f"full study: {out}")
    return 0


def cmd_story(a, cfg, conn):
    if a.action == "new":
        seed = "|".join(sorted(a.refs or [])) or a.title.strip().lower()
        sid = "st_" + hashlib.sha1(f"{a.path}|{seed}".encode()).hexdigest()[:10]
        with db.tx(conn):
            if db.row(conn, "SELECT id FROM stories WHERE id=?", sid):
                print(f"{sid} already exists")
                return 0
            conn.execute("INSERT INTO stories(id,title,theme,path,page_id,angle,status,data,created_at,updated_at) "
                         "VALUES(?,?,?,?,?,?,'proposed',?,?,?)",
                         (sid, a.title, a.theme, a.path, a.page, a.angle, db.jdump({"refs": a.refs or []}), now_iso(), now_iso()))
            for r in a.refs or []:
                conn.execute("UPDATE refs SET story_id=? WHERE id=?", (sid, r))
            _event(conn, sid, "researcher", "propose", {"refs": a.refs})
        _sdir(cfg, sid)
        print(sid)
    elif a.action == "decide":
        s = _story(conn, a.id)
        status = {"approve": "approved", "research": "needs_research", "return": "returned", "archive": "archived"}[a.decision]
        if a.decision == "approve":
            get_page(cfg, a.page or s["page_id"] or "")
        with db.tx(conn):
            conn.execute("UPDATE stories SET status=?, page_id=COALESCE(?,page_id), angle=COALESCE(?,angle), "
                         "priority=COALESCE(?,priority), updated_at=? WHERE id=?",
                         (status, a.page, a.angle, a.priority, now_iso(), a.id))
            _event(conn, a.id, "supervisor", a.decision, {"reason": a.reason, "page": a.page, "angle": a.angle})
        print(f"{a.id}: {status} ({a.reason})")
        _budget_warning(conn, cfg, a.id)
    elif a.action == "show":
        s = _story(conn, a.id)
        s["data"] = db.jload(s["data"])
        s["events"] = [dict(e, payload=db.jload(e["payload"])) for e in
                       db.rows(conn, "SELECT role, action, payload, ts FROM events WHERE story_id=? ORDER BY id", a.id)]
        s["renders"] = db.rows(conn, "SELECT id, version, status, sha256, path FROM renders WHERE story_id=?", a.id)
        s["reviews"] = db.rows(conn, "SELECT render_id, decision, valid, notes, reviewer FROM reviews WHERE render_id LIKE ?", a.id + "%")
        s["publications"] = db.rows(conn, "SELECT id, platform, account, state, remote_id, last_error FROM publications WHERE story_id=?", a.id)
        print(json.dumps(s, ensure_ascii=False, indent=1))
    else:
        for s in db.rows(conn, "SELECT id, status, path, page_id, title FROM stories ORDER BY created_at"):
            print(f"{s['id']}  {s['status']:<20} {s['path']:<8} {s['page_id'] or '-':<10} {s['title']}")
    return 0


def cmd_curate(a, cfg, conn):
    s = _story(conn, a.id)
    cur = _json_file(a.file)
    d = _sdir(cfg, a.id)
    src = (cur.get("source") or {}).get("file")
    if src and not Path(src).is_absolute():  # resolve relative to the curation file, store absolute
        cur["source"]["file"] = str((Path(a.file).parent / src).resolve())
    (d / "curation.json").write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
    decision, reasons = editorial.story_gate(s, cur)
    status = {"ready": "curated", "needs_research": "needs_research", "needs_visual_review": "needs_visual_review",
              "return": "returned"}[decision]
    with db.tx(conn):
        conn.execute("INSERT INTO curations(story_id,data,ts) VALUES(?,?,?) ON CONFLICT(story_id) DO UPDATE SET "
                     "data=excluded.data, ts=excluded.ts", (a.id, db.jdump(cur), now_iso()))
        _set_status(conn, a.id, status)
        _event(conn, a.id, "curator", decision, {"reasons": reasons})
    print(f"{a.id}: {status}")
    for r in reasons:
        print(f"  - {r}")
    return 0 if decision == "ready" else 3


def cmd_script(a, cfg, conn):
    s = _story(conn, a.id)
    if s["status"] not in ("curated", "scripted", "rendered", "review_returned", "approved_for_publish", "queued"):
        print(f"{a.id} is '{s['status']}': curation must pass first (es.py curate)")
        return 3
    cur = curation_of(conn, a.id)
    sc = _json_file(a.file)
    verdict, probs = editorial.check_script(sc, cur, cfg)
    sha = hashlib.sha256(db.jdump(sc).encode()).hexdigest()
    with db.tx(conn):
        conn.execute("INSERT INTO scripts(story_id,data,verdict,problems,sha,ts) VALUES(?,?,?,?,?,?)",
                     (a.id, db.jdump(sc), verdict, db.jdump(probs), sha, now_iso()))
        if verdict == "accepted":
            _set_status(conn, a.id, "scripted")
        _event(conn, a.id, "scriptwriter", verdict, {"mode": sc.get("mode"), "lines": len(sc.get("lines", [])), "problems": probs})
    print(f"{a.id}: script {verdict} (mode {sc.get('mode')}, {len(sc.get('lines', []))} line(s))")
    for p in probs:
        where = f"line {p['line']}" if p.get("line") is not None else "script"
        print(f"  - {where}: [{p['rule']}] {p['msg']}")
    return 0 if verdict == "accepted" else 2


def cmd_transcribe(a, cfg, conn):
    cur = curation_of(conn, a.id)
    if not cur:
        print("curate the story first")
        return 3
    out = _sdir(cfg, a.id) / "captions.srt"
    try:
        media.transcribe(cur["source"]["file"], out, cfg)
    except media.CapabilityMissing as e:
        print(f"transcription unavailable: {e}")
        return 3
    cur["captions_srt"] = "captions.srt"
    with db.tx(conn):
        conn.execute("UPDATE curations SET data=? WHERE story_id=?", (db.jdump(cur), a.id))
    print(f"{out} ({len(media.parse_srt(out.read_text(encoding='utf-8')))} cues)")
    return 0


def do_render(conn, cfg, sid):
    s = _story(conn, sid)
    cur, sc = curation_of(conn, sid), latest_script(conn, sid)
    if not cur or not sc or s["status"] in ("returned", "archived", "needs_research", "needs_visual_review"):
        raise publish.Conflict(f"{sid} is '{s['status']}': needs a passing curation and an accepted script before rendering")
    d = _sdir(cfg, sid)
    v = (db.row(conn, "SELECT MAX(version) v FROM renders WHERE story_id=?", sid)["v"] or 0) + 1
    man = media.render(cur["source"]["file"], cur, sc, cfg, d / "renders" / f"v{v}", d)
    man["script_sha"] = hashlib.sha256(db.jdump(sc).encode()).hexdigest()
    rid = f"{sid}-v{v}"
    with db.tx(conn):
        conn.execute("UPDATE renders SET status='superseded' WHERE story_id=? AND status='current'", (sid,))
        conn.execute("UPDATE reviews SET valid=0 WHERE render_id LIKE ?", (sid + "-v%",))
        conn.execute("INSERT INTO renders(id,story_id,version,path,sha256,manifest,status,ts) VALUES(?,?,?,?,?,?,'current',?)",
                     (rid, sid, v, man["file"], media.sha256(man["file"]), db.jdump(man), now_iso()))
        publish.cancel_superseded(conn, sid, rid)
        _set_status(conn, sid, "rendered")
        _event(conn, sid, "editor", "render", {"render": rid, "mode": man["mode"], "duration": man["duration"]})
    return rid, man


def cmd_render(a, cfg, conn):
    try:
        rid, man = do_render(conn, cfg, a.id)
    except media.CapabilityMissing as e:
        print(f"cannot render: {e}")
        return 3
    except publish.Conflict as e:
        print(e)
        return 3
    print(f"{rid}: {man['file']} ({man['duration']}s, mode {man['mode']}, {man['voice_tracks']} voice track(s)); "
          f"any earlier approval is now invalid")
    return 0


def do_auto_review(conn, cfg, sid):
    s = _story(conn, sid)
    rnd = publish.current_render(conn, sid)
    if not rnd:
        raise publish.Conflict("nothing rendered yet")
    sc = db.jload(db.row(conn, "SELECT data FROM scripts WHERE story_id=? AND sha=? ORDER BY id DESC LIMIT 1",
                         sid, rnd["manifest"]["script_sha"])["data"])
    issues, frames = review.auto_review(rnd, curation_of(conn, sid), sc, cfg, platforms_of(cfg, s["page_id"]),
                                        _sdir(cfg, sid) / "review" / rnd["id"])
    with db.tx(conn):
        conn.execute("INSERT INTO reviews(render_id,sha256,decision,notes,auto,reviewer,valid,ts) VALUES(?,?,?,?,?,?,1,?)",
                     (rnd["id"], rnd["sha256"], "auto_pass" if not issues else "auto_fail", "[]", db.jdump(issues),
                      "auto", now_iso()))
    return rnd, issues, frames


def cmd_review(a, cfg, conn):
    if a.action == "auto":
        try:
            rnd, issues, frames = do_auto_review(conn, cfg, a.id)
        except publish.Conflict as e:
            print(e)
            return 3
        print(f"{rnd['id']} sha256 {rnd['sha256'][:12]}…: {'PASS' if not issues else 'FAIL'} automatic checks")
        for i in issues:
            print(f"  - {'%.2fs' % i['t'] if i['t'] is not None else 'file'}: {i['problem']} -> {i['fix']}")
        print("frames for the visual review (open them before deciding):")
        for f in frames:
            print(f"  {f}")
        return 0 if not issues else 2
    rnd = publish.current_render(conn, a.id)
    if not rnd:
        print("nothing rendered yet")
        return 3
    if media.sha256(rnd["path"]) != rnd["sha256"]:
        print("file changed after render: render again")
        return 2
    notes = [review.parse_note(n) for n in (a.note or [])]
    if a.decision == "approve":
        last = db.row(conn, "SELECT decision FROM reviews WHERE render_id=? AND sha256=? AND decision LIKE 'auto%' "
                            "AND valid=1 ORDER BY id DESC LIMIT 1", rnd["id"], rnd["sha256"])
        if not last or last["decision"] != "auto_pass":
            print("approval refused: run `review auto` on this exact file and fix every issue first")
            return 2
    elif not notes:
        print("a return needs at least one --note 'SECONDS|problem|expected fix'")
        return 2
    with db.tx(conn):
        conn.execute("INSERT INTO reviews(render_id,sha256,decision,notes,auto,reviewer,valid,ts) VALUES(?,?,?,?,?,?,1,?)",
                     (rnd["id"], rnd["sha256"], "approved" if a.decision == "approve" else "returned",
                      db.jdump(notes), "[]", a.reviewer, now_iso()))
        _set_status(conn, a.id, "approved_for_publish" if a.decision == "approve" else "review_returned")
        _event(conn, a.id, "reviewer", a.decision, {"render": rnd["id"], "sha256": rnd["sha256"], "notes": notes})
    print(f"{rnd['id']} ({rnd['sha256'][:12]}…): {a.decision} by {a.reviewer}")
    for n in notes:
        print(f"  - {n['t']}s: {n['problem']} -> {n['fix']}")
    return 0


def cmd_publish(a, cfg, conn):
    if a.action == "queue":
        try:
            res = publish.queue(conn, cfg, a.id, a.page or _story(conn, a.id)["page_id"], _json_file(a.meta))
        except (publish.Conflict, ConfigError) as e:
            print(f"not queued: {e}")
            return 2
        for pid, plat, acct, st in res:
            print(f"{pid}  {plat:<10} {acct:<20} {st}")
    elif a.action == "run":
        res = publish.run(conn, cfg, a.worker, a.max)
        for pid, plat, st, info in res:
            print(f"{pid}  {plat:<10} {st:<24} {info}")
        if not res:
            print("nothing queued")
    elif a.action == "reconcile":
        for pid, plat, st, info in publish.reconcile(conn, cfg):
            print(f"{pid}  {plat:<10} {st:<24} {info}")
    elif a.action == "confirm":
        outcome = ("published" if a.remote_id else "uncertain" if a.uncertain else
                   "rejected" if a.rejected else "not_found" if a.not_found else None)
        try:
            publish.confirm(conn, a.id, outcome, a.remote_id, a.url, a.rejected or "")
        except publish.Conflict as e:
            print(e)
            return 2
        print(f"{a.id}: {outcome}")
    elif a.action == "retry":
        try:
            publish.retry(conn, a.id)
        except publish.Conflict as e:
            print(e)
            return 2
        print(f"{a.id}: queued again")
    else:
        for p in db.rows(conn, "SELECT id, story_id, platform, account, adapter, state, attempts, remote_id, last_error "
                               "FROM publications ORDER BY created_at"):
            print(f"{p['id']}  {p['story_id']}  {p['platform']:<9} {p['account']:<16} {p['adapter']:<12} {p['state']:<20} "
                  f"{p['remote_id'] or ''} {p['last_error'] or ''}")
    return 0


def cmd_metrics(a, cfg, conn):
    try:
        learn.add(conn, a.id, a.window, _json_file(a.file), a.demo, a.source)
    except ValueError as e:
        print(e)
        return 2
    print(f"{a.id} {a.window}: stored{' (demo)' if a.demo else ''}")
    return 0


def cmd_learn(a, cfg, conn):
    r = learn.report(conn, cfg)
    out = Path(cfg["_workspace"]) / "reports" / "learn.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0
    if r["demo_data"]:
        print("DEMONSTRATIVE DATA: illustrates the method; not evidence about any real audience.")
    for g in r["groups"]:
        print(f"== {g['page']} · {g['platform']} · {g['window']} ({g['videos']} video(s))")
        for x in g["rows"]:
            print(f"  {x['title']}: mode {x['mode']} · first voice {x['first_voice']} · payoff {x['delivery']}s · "
                  f"stopped {x['stopped']} · stayed {x['stayed']} · shared/reach {x['shared']}")
        for k in ("findings", "hypotheses", "limits"):
            for line in g[k]:
                print(f"  {k[:-1] if k != 'limits' else 'limit'}: {line}")
    if not r["groups"]:
        print("no published video has metrics yet")
    return 0


def cmd_status(a, cfg, conn):
    print("stories:", ", ".join(f"{r['status']} {r['n']}" for r in
                                db.rows(conn, "SELECT status, COUNT(*) n FROM stories GROUP BY status")) or "none")
    print("publications:", ", ".join(f"{r['state']} {r['n']}" for r in
                                     db.rows(conn, "SELECT state, COUNT(*) n FROM publications GROUP BY state")) or "none")
    for p in db.rows(conn, "SELECT id, state, last_error FROM publications WHERE state IN "
                           "('confirmation_pending','awaiting_agent','blocked')"):
        print(f"needs attention: {p['id']} {p['state']} {p['last_error'] or ''}")
    return 0


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="es.py", description="editorial-shorts: research, edit, review, publish, learn")
    ap.add_argument("--workspace", "-w", help="workspace folder (default: $ES_WORKSPACE or current folder)")
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("init"); p.add_argument("--force", action="store_true")
    sp.add_parser("doctor")
    sp.add_parser("status")
    p = sp.add_parser("research"); p.add_argument("action", choices=["add", "enrich", "study"])
    p.add_argument("file", nargs="?"); p.add_argument("--ids", nargs="*"); p.add_argument("--json", action="store_true")
    p = sp.add_parser("story"); p.add_argument("action", choices=["new", "decide", "show", "list"])
    p.add_argument("id", nargs="?"); p.add_argument("decision", nargs="?", choices=["approve", "research", "return", "archive"])
    p.add_argument("--title"); p.add_argument("--theme"); p.add_argument("--path", choices=["cut", "original"], default="cut")
    p.add_argument("--refs", nargs="*"); p.add_argument("--page"); p.add_argument("--angle"); p.add_argument("--priority", type=int)
    p.add_argument("--reason", default="")
    p = sp.add_parser("curate"); p.add_argument("id"); p.add_argument("file")
    p = sp.add_parser("script"); p.add_argument("id"); p.add_argument("file")
    p = sp.add_parser("transcribe"); p.add_argument("id")
    p = sp.add_parser("render"); p.add_argument("id")
    p = sp.add_parser("review"); p.add_argument("action", choices=["auto", "decide"]); p.add_argument("id")
    p.add_argument("decision", nargs="?", choices=["approve", "return"]); p.add_argument("--reviewer", default="reviewer")
    p.add_argument("--note", action="append")
    p = sp.add_parser("publish"); p.add_argument("action", choices=["queue", "run", "reconcile", "confirm", "retry", "list"])
    p.add_argument("id", nargs="?"); p.add_argument("--page"); p.add_argument("--meta"); p.add_argument("--worker", default="worker-1")
    p.add_argument("--max", type=int, default=50); p.add_argument("--remote-id"); p.add_argument("--url")
    p.add_argument("--uncertain", action="store_true"); p.add_argument("--rejected"); p.add_argument("--not-found", action="store_true")
    p = sp.add_parser("metrics"); p.add_argument("id"); p.add_argument("--window", required=True); p.add_argument("--file", required=True)
    p.add_argument("--demo", action="store_true"); p.add_argument("--source", default="manual")
    p = sp.add_parser("learn"); p.add_argument("--json", action="store_true")
    p = sp.add_parser("demo"); p.add_argument("--keep", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "init":
        return cmd_init(a)
    if a.cmd == "demo":
        from .demo import run_demo
        return run_demo(_ws(a))
    conn = None
    try:
        cfg = load(_ws(a))
        conn = db.connect(cfg["_workspace"])
        return globals()[f"cmd_{a.cmd}"](a, cfg, conn)
    except (ConfigError, media.CapabilityMissing) as e:
        print(f"error: {e}")
        return 1
    finally:
        if conn is not None:
            conn.close()
