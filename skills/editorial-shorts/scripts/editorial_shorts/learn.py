"""Performance analyst (after publication): did people stop, stay until the payoff, and want to share?

Compares only videos of the same page and platform in the same window. A single coincidence is a hypothesis;
a finding needs repetition across different stories. Demo data is always labelled and never counts as evidence.
"""
import statistics
from collections import defaultdict

from . import db
from .config import now_iso

FIELDS = ("impressions", "reach", "views", "avg_view_s", "avg_view_pct", "completion_rate", "retention_3s",
          "shares", "saves", "comments", "likes", "retention_curve")


def add(conn, pub_id, win, data, demo=False, source="manual"):
    p = db.row(conn, "SELECT state FROM publications WHERE id=?", pub_id)
    if not p or p["state"] != "published":
        raise ValueError(f"{pub_id} is not a published publication")
    unknown_keys = set(data) - set(FIELDS) - {"note"}
    if unknown_keys:
        raise ValueError(f"unknown metric fields: {', '.join(sorted(unknown_keys))} (allowed: {', '.join(FIELDS)})")
    row = {k: data.get(k) for k in FIELDS}
    row["note"] = data.get("note")
    with db.tx(conn):
        conn.execute("INSERT INTO metrics(pub_id, win, data, demo, source, ts) VALUES(?,?,?,?,?,?) "
                     "ON CONFLICT(pub_id, win, source) DO UPDATE SET data=excluded.data, demo=excluded.demo, ts=excluded.ts",
                     (pub_id, win, db.jdump(row), int(demo), source, now_iso()))


def _rate(a, b):
    return round(a / b, 4) if a is not None and b else None


def _curve_at(curve, t):
    if not curve or t is None:
        return None
    pts = sorted(curve)
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t0 <= t <= t1:
            return v0 + (v1 - v0) * (t - t0) / max(1e-6, t1 - t0)
    return None


def voice_drop(curve, t, factor):
    """Retention drop per second around the first voice entry vs. the video's typical drop per second."""
    if not curve or t is None or len(curve) < 4:
        return None
    pts = sorted(curve)
    slopes = [(v0 - v1) / max(1e-6, t1 - t0) for (t0, v0), (t1, v1) in zip(pts, pts[1:])]
    typical = statistics.median(slopes)
    a, b = _curve_at(pts, max(pts[0][0], t - 0.5)), _curve_at(pts, min(pts[-1][0], t + 1.5))
    if a is None or b is None:
        return None
    local = (a - b) / 2.0
    return {"local_drop_per_s": round(local, 2), "typical_drop_per_s": round(typical, 2),
            "flag": typical > 0 and local > factor * typical}


def report(conn, cfg):
    q = """SELECT p.id pub, p.story_id, p.page_id, p.platform, p.remote_id, s.angle, s.theme, s.title,
                  r.manifest, m.win, m.data, m.demo, m.source
           FROM publications p JOIN stories s ON s.id=p.story_id JOIN renders r ON r.id=p.render_id
           JOIN metrics m ON m.pub_id=p.id WHERE p.state='published'"""
    rows = db.rows(conn, q)
    groups = defaultdict(list)
    for r in rows:
        man, d = db.jload(r["manifest"]), db.jload(r["data"])
        reach = d.get("reach") or d.get("impressions") or d.get("views")
        r.update(mode=man.get("mode"), first_voice=man.get("first_intervention_s"), delivery=man.get("delivery_s"),
                 stopped=d.get("retention_3s") if d.get("retention_3s") is not None else _rate(d.get("views"), d.get("impressions")),
                 stayed=d.get("completion_rate") if d.get("completion_rate") is not None else
                 (_curve_at(d.get("retention_curve"), man.get("delivery_s")) or d.get("avg_view_pct")),
                 shared=_rate(d.get("shares"), reach),
                 unknown=[k for k in FIELDS if d.get(k) is None],
                 drop=voice_drop(d.get("retention_curve"), man.get("first_intervention_s"), cfg["metrics"]["voice_drop_factor"]))
        groups[(r["page_id"], r["platform"], r["win"])].append(r)

    out = {"generated_at": now_iso(cfg), "groups": [], "demo_data": any(r["demo"] for r in rows)}
    need = cfg["metrics"]["pattern_min_stories"]
    for (page, plat, win), rs in sorted(groups.items()):
        g = {"page": page, "platform": plat, "window": win, "videos": len(rs),
             "demonstrative": any(r["demo"] for r in rs), "rows": [], "findings": [], "hypotheses": [], "limits": []}
        for r in rs:
            g["rows"].append({k: r[k] for k in ("pub", "title", "mode", "first_voice", "delivery", "stopped", "stayed",
                                                 "shared", "remote_id")})
            if r["drop"] and r["drop"]["flag"]:
                g["hypotheses"].append(
                    f"{r['title']}: retention fell {r['drop']['local_drop_per_s']} pts/s when the voice entered at "
                    f"{r['first_voice']}s (typical {r['drop']['typical_drop_per_s']} pts/s). Review that segment; "
                    f"treat as a hypothesis until it repeats in other videos.")
        always_unknown = set.intersection(*(set(r["unknown"]) for r in rs))
        if always_unknown:
            g["limits"].append(f"not provided by this integration/platform: {', '.join(sorted(always_unknown))}")
        for feature in ("mode", "angle"):
            by = defaultdict(list)
            for r in rs:
                if r["shared"] is not None and r[feature]:
                    by[r[feature]].append(r)
            if len(by) < 2:
                continue
            med = {k: statistics.median(x["shared"] for x in v) for k, v in by.items()}
            best = max(med, key=med.get)
            others = [k for k in med if k != best]
            stories = len({x["story_id"] for x in by[best]})
            line = (f"{feature}={best}: median shares/reach {med[best]:.4f} vs "
                    + ", ".join(f"{k} {med[k]:.4f}" for k in others) + f" across {stories} different stories")
            if stories >= need and not g["demonstrative"]:
                g["findings"].append(line + f". Test more {feature}={best} videos on this page.")
            else:
                g["hypotheses"].append(line + f" (needs {need} stories of real data to count as a finding)")
        out["groups"].append(g)
    return out
