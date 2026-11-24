"""Trend researcher + performance analyst (before production).

References come from any source the installer can reach: manual entry, an agent's web search, or yt-dlp
metadata. Metrics that a source does not expose stay null and are reported as "unknown".
"""
import hashlib
import json
import shutil
import statistics
import subprocess
from collections import Counter, defaultdict

from . import db
from .config import now_iso
from .editorial import content_tokens

REQUIRED = ("url", "platform", "creator", "theme", "why")


def ref_id(url):
    return "rf_" + hashlib.sha1(url.strip().lower().encode()).hexdigest()[:10]


def validate_ref(r):
    missing = [k for k in REQUIRED if not r.get(k)]
    if missing:
        raise ValueError(f"reference {r.get('url', '?')}: missing {', '.join(missing)}")


def outlier_ratio(r, cfg):
    """views / creator's typical views on the same platform and format. None when not comparable."""
    views = (r.get("metrics") or {}).get("views")
    b = r.get("creator_baseline") or {}
    if views is None or not b.get("median_views"):
        return None, "no views or no creator baseline"
    if (b.get("n") or 0) < cfg["research"]["baseline_min_videos"]:
        return None, f"baseline has {b.get('n') or 0} videos (< {cfg['research']['baseline_min_videos']})"
    if b.get("same_platform") is False or b.get("same_format") is False:
        return None, "baseline is from another platform or format"
    note = "" if b.get("age_matched") else "baseline not matched by post age"
    return round(views / b["median_views"], 2), note


def add_refs(conn, cfg, records):
    added = 0
    with db.tx(conn):
        for r in records:
            validate_ref(r)
            r.setdefault("collected_at", now_iso(cfg))
            ratio, note = outlier_ratio(r, cfg)
            r["ratio"], r["ratio_note"] = ratio, note
            cur = conn.execute(
                "INSERT INTO refs(id,url,data,demo,collected_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(url) DO UPDATE SET data=excluded.data, demo=excluded.demo",
                (ref_id(r["url"]), r["url"], db.jdump(r), int(bool(r.get("demo"))), r["collected_at"]))
            added += cur.rowcount
        _cluster(conn, cfg)
    return added


def _cluster(conn, cfg):
    """Group reposts of the same story: explicit original_url, or near-identical titles in the same theme."""
    refs = [(x["id"], db.jload(x["data"])) for x in db.rows(conn, "SELECT id, data FROM refs")]
    parent = {i: i for i, _ in refs}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    by_url = {r["url"].strip().lower(): i for i, r in refs}
    thr = cfg["research"]["repost_title_similarity"]
    toks = {i: content_tokens(r.get("title", ""), cfg.get("language", "en")[:2]) for i, r in refs}
    for i, r in refs:
        o = (r.get("original_url") or "").strip().lower()
        if o in by_url:
            union(i, by_url[o])
        elif o:
            # two reposts that point to the same original belong together even if the original is not stored
            for j, r2 in refs:
                if j != i and (r2.get("original_url") or "").strip().lower() == o:
                    union(i, j)
    for a in range(len(refs)):
        for b in range(a + 1, len(refs)):
            (i, r1), (j, r2) = refs[a], refs[b]
            t1, t2 = toks[i], toks[j]
            if r1["theme"] == r2["theme"] and t1 and t2 and len(t1 & t2) / len(t1 | t2) >= thr:
                union(i, j)
    for i, _ in refs:
        conn.execute("UPDATE refs SET cluster=? WHERE id=?", (find(i), i))


def study(conn, cfg):
    rc = cfg["research"]
    refs = [dict(db.jload(x["data"]), id=x["id"], cluster=x["cluster"], demo=x["demo"])
            for x in db.rows(conn, "SELECT * FROM refs")]
    out = {"total_refs": len(refs), "themes": {}, "limitations": [], "demo_data": any(r["demo"] for r in refs)}
    themes = sorted({r["theme"] for r in refs})
    p = rc["pilot"]
    if not (p["references_min"] <= len(refs) <= p["references_max"]) or not (p["themes_min"] <= len(themes) <= p["themes_max"]):
        out["limitations"].append(
            f"pilot target is {p['references_min']}-{p['references_max']} references over {p['themes_min']}-{p['themes_max']} "
            f"themes; this sample has {len(refs)} over {len(themes)} (conclusions are weaker)")
    unknown = sum(1 for r in refs if r.get("ratio") is None)
    if unknown:
        out["limitations"].append(f"{unknown}/{len(refs)} references have no comparable creator baseline (ratio unknown)")
    for th in themes:
        rs = [r for r in refs if r["theme"] == th]
        hot = [r for r in rs if r.get("ratio") is not None and r["ratio"] >= rc["outlier_ratio"]]
        typical = [r for r in rs if r.get("ratio") is not None and r["ratio"] < rc["outlier_ratio"]]
        clusters = defaultdict(list)
        for r in hot:
            clusters[r["cluster"]].append(r)
        # credit the original creator of each story, not the pages that reposted it
        creators = set()
        for members in clusters.values():
            orig = [m for m in members if not m.get("original_url")] or members
            creators |= {m["creator"].lower() for m in orig}
        material = sum(1 for r in rs if (r.get("answers") or {}).get("more_material"))
        factors = {}
        for key in ("angle", "format", "audio_type"):
            # A factor is a pattern only if it shows up in independent stories from different creators.
            seen = defaultdict(set)
            for cid, members in clusters.items():
                for r in members:
                    if r.get(key):
                        seen[r[key]].add((cid, r["creator"].lower()))
            factors[key] = {
                v: {"stories": len({c for c, _ in s}), "creators": len({cr for _, cr in s}),
                    "pattern": len({c for c, _ in s}) >= 2 and len({cr for _, cr in s}) >= 2}
                for v, s in seen.items()}
            factors[key + "_typical"] = dict(Counter(r.get(key) for r in typical if r.get(key)))
        ratios = [r["ratio"] for r in rs if r.get("ratio") is not None]
        if (len(clusters) >= rc["page_min_independent_stories"] and len(creators) >= rc["page_min_creators"]
                and material >= rc["page_min_available_material"]):
            rec = "dedicated_page"
            why = (f"{len(clusters)} independent stories from {len(creators)} creators beat their own baseline by "
                   f">= {rc['outlier_ratio']}x and {material} references point to more usable material")
        elif clusters:
            rec = "general_page_story"
            why = (f"{len(clusters)} independent outlier stor{'y' if len(clusters) == 1 else 'ies'}: enough for a cut, "
                   f"not for a whole page (needs {rc['page_min_independent_stories']} stories, "
                   f"{rc['page_min_creators']} creators, {rc['page_min_available_material']} material leads)")
        else:
            rec = "archive"
            why = "no reference beat its creator's typical performance (or no comparable baseline)"
        out["themes"][th] = {
            "refs": len(rs), "with_ratio": len(ratios), "typical": len(typical), "outliers": len(hot),
            "independent_outlier_stories": len(clusters), "outlier_creators": len(creators),
            "median_ratio": statistics.median(ratios) if ratios else None, "material_leads": material,
            "factors": factors, "recommendation": rec, "why": why,
            "stories": [{"cluster": c, "titles": [m.get("title") for m in ms], "best_moment_s": ms[0].get("moment_s"),
                         "why": ms[0].get("why"), "answers": ms[0].get("answers")} for c, ms in clusters.items()],
        }
    return out


# ---------- optional source: yt-dlp metadata (no download) ----------

def have_ytdlp():
    return shutil.which("yt-dlp") is not None


def _ytdlp_json(args, timeout=120):
    r = subprocess.run(["yt-dlp", "-J", "--skip-download", "--no-warnings", *args],
                       capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[-400:])
    return json.loads(r.stdout)


def enrich_with_ytdlp(ref, baseline_n=15):
    """Fill metrics and a creator baseline from public metadata. Leaves anything missing as null."""
    info = _ytdlp_json([ref["url"]])
    ref.setdefault("metrics", {})
    for src, dst in (("view_count", "views"), ("like_count", "likes"), ("comment_count", "comments")):
        ref["metrics"][dst] = info.get(src)
    ref["metrics"].setdefault("shares", None)  # not exposed publicly by most platforms
    ref["published_at"] = ref.get("published_at") or info.get("upload_date")
    ref["creator"] = ref.get("creator") or info.get("channel") or info.get("uploader")
    ref["duration_s"] = info.get("duration")
    ch = info.get("channel_url") or info.get("uploader_url")
    if not ch:
        ref["creator_baseline"] = None
        return ref
    short = (info.get("duration") or 999) <= 180 and "youtube" in (info.get("extractor") or "")
    feed = ch.rstrip("/") + ("/shorts" if short else "/videos")
    try:
        pl = _ytdlp_json(["--flat-playlist", "--playlist-end", str(baseline_n), feed])
        views = [e.get("view_count") for e in pl.get("entries", []) if e.get("id") != info.get("id")]
        views = [v for v in views if v is not None]
        ref["creator_baseline"] = {"median_views": statistics.median(views) if views else None, "n": len(views),
                                   "same_platform": True, "same_format": True, "age_matched": False,
                                   "source": f"yt-dlp {feed} latest {baseline_n}"}
    except Exception as e:  # baseline is optional; the reference stays usable
        ref["creator_baseline"] = None
        ref["baseline_error"] = str(e)[:200]
    return ref
