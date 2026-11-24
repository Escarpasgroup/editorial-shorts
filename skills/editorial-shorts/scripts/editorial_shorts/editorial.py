"""Editorial rules that can be enforced without a model.

These checks are guardrails around the judgment made by the supervisor, scriptwriter and reviewer roles.
They do not replace watching the material. Every rule returns explicit problems so a human or model can fix them.

Central rule: a voice line or on-screen text must earn its place. "If I delete this sentence, what exactly does
the viewer lose?" If the answer is nothing, the line is removed.
"""
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from .config import lang_code

LEXICON_DIR = Path(__file__).parent / "lexicon"
RIGHTS_OK = {"owned", "licensed", "permission", "public_domain", "creative_commons"}
FACT_OK = {"confirmed"}


def norm(text):
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9?]+", " ", t)
    return " " + re.sub(r"\s+", " ", t).strip() + " "


@lru_cache(maxsize=None)
def lexicon(lang):
    p = LEXICON_DIR / f"{lang}.json"
    if not p.exists():
        p = LEXICON_DIR / "en.json"
    return json.loads(p.read_text(encoding="utf-8"))


def content_tokens(text, lang):
    stop = set(lexicon(lang)["stopwords"])
    toks = [w for w in norm(text).replace("?", " ").split() if len(w) >= 3 and w not in stop]
    return {w[:4] for w in toks}  # crude stem: "pula"/"pular", "portao"/"portoes"


def sentences(text):
    return [s for s in re.split(r"(?<=[.!?…])\s+", text.strip()) if s.strip()]


def banned_hits(text, lang):
    n = norm(text)
    hits = []
    for cat, phrases in lexicon(lang)["banned"].items():
        if cat == "generic_question" and "?" not in text:
            continue
        for ph in phrases:
            if " " + norm(ph).strip().replace("?", "").strip() + " " in n.replace("?", " "):
                hits.append((cat, ph))
                break
    return hits


def _overlaps(a0, a1, b0, b1):
    return min(a1, b1) - max(a0, b0) > 1e-6


def check_line(line, curation, lang, cfg, moments=None):
    """Problems for one voice / on-screen line. Times are in SOURCE seconds (the script is written against
    the source material; the renderer maps them to output time)."""
    probs = []
    text = (line.get("text") or "").strip()
    ed = cfg["editorial"]
    lex = lexicon(lang)
    if not text:
        return [("empty", "line has no text")]

    loss = norm(line.get("loss_if_removed") or "").strip()
    if loss in {norm(x).strip() for x in lex["trivial_loss"]} or len(loss.split()) < 3:
        probs.append(("deletion_test", "'If I delete this line, what does the viewer lose?' has no concrete answer"))

    for cat, ph in banned_hits(text, lang):
        probs.append((cat, f"contains '{ph}'"))

    visible = " ".join(v.get("desc", "") for v in curation.get("visible", []))
    vis_tok = content_tokens(visible, lang)
    limit = ed["visible_overlap_reject"]
    for s in sentences(text):
        st = content_tokens(s, lang)
        if st and vis_tok and len(st & vis_tok) / len(st) >= limit:
            probs.append(("describes_visible", f"'{s}' repeats what is already on screen"))

    if len(norm(text).split()) > ed["max_words_per_line"]:
        probs.append(("too_long", f"more than {ed['max_words_per_line']} words"))

    details = {d["id"]: d for d in curation.get("details", [])}
    facts = {f["id"]: f for f in curation.get("facts", [])}
    ev = line.get("evidence") or []
    if not ev:
        probs.append(("no_evidence", "line cites no detail in the material and no verified fact"))
    for e in ev:
        ref = e.get("ref")
        if e.get("type") == "detail":
            if ref not in details:
                probs.append(("evidence_missing", f"detail {ref!r} is not annotated in the curation"))
        elif e.get("type") == "fact":
            f = facts.get(ref)
            if not f:
                probs.append(("evidence_missing", f"fact {ref!r} is not in the curation"))
            elif f.get("status") not in FACT_OK and not (f.get("status") == "interpretation"
                                                          and line.get("framing") == "interpretation"):
                probs.append(("unverified_fact", f"fact {ref!r} is '{f.get('status')}', not confirmed"))
        else:
            probs.append(("evidence_missing", f"unknown evidence type {e.get('type')!r}"))

    at, dur = float(line.get("at", 0)), float(line.get("dur", 0))
    if dur <= 0:
        probs.append(("timing", "line needs a positive 'dur'"))
    for p in curation.get("protected", []):
        if _overlaps(at, at + dur, p["t0"], p["t1"]):
            probs.append(("covers_protected", f"covers protected moment {p['t0']}-{p['t1']}s ({p.get('why', '')})"))

    main = (moments or {}).get("main")
    if main is not None and at + dur <= main:
        for e in ev:
            d = details.get(e.get("ref")) if e.get("type") == "detail" else None
            if d and d["t0"] >= main:
                probs.append(("anticipates_payoff", f"reveals detail {d['id']} before the main moment at {main}s"))
    return probs


def check_script(script, curation, cfg):
    """Returns (verdict, problems). verdict is 'accepted' or 'rejected'."""
    lang = lang_code(script.get("language") or cfg)
    mode = script.get("mode")
    lines = script.get("lines", [])
    probs = []
    if mode not in ("A", "B", "C"):
        probs.append({"line": None, "rule": "mode", "msg": "mode must be A (original audio), B (spot comment) or C (guided explanation)"})
    if mode == "A" and lines:
        probs.append({"line": None, "rule": "mode_A", "msg": "mode A keeps the original audio: zero voice and zero added on-screen lines"})
    if mode == "B" and not 1 <= len(lines) <= cfg["editorial"]["max_comment_lines"]:
        probs.append({"line": None, "rule": "mode_B", "msg": f"mode B allows 1 to {cfg['editorial']['max_comment_lines']} short lines"})
    if mode == "C":
        if not (script.get("question") or "").strip():
            probs.append({"line": None, "rule": "mode_C", "msg": "mode C needs the legitimate question it answers"})
        if not lines:
            probs.append({"line": None, "rule": "mode_C", "msg": "mode C needs lines"})
    for i, ln in enumerate(lines):
        for rule, msg in check_line(ln, curation, lang, cfg, curation.get("moments")):
            probs.append({"line": i, "rule": rule, "msg": msg, "text": ln.get("text")})
    return ("rejected" if probs else "accepted"), probs


def check_cut(segments, units):
    """A cut must not start/end inside a meaning unit, nor keep a unit without the units it depends on
    (e.g. keeping "this game is bad" without "I would never say")."""
    probs = []

    def kept(u):
        tot = sum(max(0.0, min(s["out"], u["t1"]) - max(s["in"], u["t0"])) for s in segments)
        return tot / max(1e-6, u["t1"] - u["t0"])

    by_id = {u["id"]: u for u in units}
    for u in units:
        k = kept(u)
        if 0.02 < k < 0.98:
            probs.append({"unit": u["id"], "rule": "splits_speech",
                          "msg": f"cut keeps {k:.0%} of '{u.get('text', u['id'])}' ({u['t0']}-{u['t1']}s)"})
        if k >= 0.98:
            for dep in u.get("requires", []):
                d = by_id.get(dep)
                if d is None or kept(d) < 0.98:
                    probs.append({"unit": u["id"], "rule": "changes_meaning",
                                  "msg": f"keeps '{u.get('text', u['id'])}' without '{(d or {}).get('text', dep)}' it depends on"})
    return probs


def story_gate(story, curation):
    """Supervisor helper. Returns (decision, reasons): 'ready', 'needs_research', 'needs_visual_review' or 'return'."""
    reasons = []
    if story["path"] == "original":
        facts = {f["id"]: f for f in curation.get("facts", [])}
        cid = curation.get("central_claim")
        f = facts.get(cid)
        if not cid or not f:
            return "return", ["original story has no central claim recorded"]
        if f.get("status") != "confirmed" or not f.get("sources"):
            return "needs_research", [f"central claim '{f.get('claim')}' is '{f.get('status')}' with "
                                      f"{len(f.get('sources') or [])} source(s): verify or archive"]
        if not any(cid in (d.get("proves") or []) for d in curation.get("details", [])):
            return "return", ["no image in the material demonstrates the central claim: reframe the story honestly or archive"]
    else:
        if not curation.get("context_checked"):
            reasons.append("watch the excerpt inside the original context first (who speaks, to what, what happens next)")
        if not curation.get("segments"):
            reasons.append("no in/out segments marked")
        probs = check_cut(curation.get("segments", []), curation.get("meaning_units", []))
        reasons += [p["msg"] for p in probs]
        if reasons:
            return "return", reasons
        if not curation.get("visual_checked"):
            return "needs_visual_review", ["the visual action was not checked on frames: send to a step that can see it"]
    rights = (curation.get("source") or {}).get("rights", {})
    if rights.get("status") not in RIGHTS_OK:
        return "return", [f"usage condition is '{rights.get('status', 'unknown')}': record owned/licensed/permission with evidence"]
    if not rights.get("evidence"):
        return "return", ["usage condition has no evidence (link, file or note)"]
    return "ready", []


def lint_post_text(text, lang):
    """Titles and captions: no fabricated suspense or habitual calls to action."""
    return [f"{cat}: '{ph}'" for cat, ph in banned_hits(text, lang)
            if cat in ("fabricated_suspense", "habitual_cta", "empty_adjectives")]
