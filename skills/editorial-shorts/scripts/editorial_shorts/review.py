"""Reviewer: checks the RENDERED FILE (bound to its sha256), not the plan.

Automatic checks cover what can be measured: file identity, technical QA, framing of essential details,
voice over protected moments, mode A integrity, audibility of protected moments. The reviewer role (a person or a
model that can see frames) then decides approve/return with exact output timestamps.
"""
import re
from pathlib import Path

from . import media
from .editorial import check_script


def _inside(box, crop):
    x, y, w, h = box
    cx, cy, cw, ch = crop
    ix = max(0, min(x + w, cx + cw) - max(x, cx))
    iy = max(0, min(y + h, cy + ch) - max(y, cy))
    return (ix * iy) / max(1, w * h)


def _to_out_box(box, crop, out_size):
    x, y, w, h = box
    cx, cy, cw, ch = crop
    sx, sy = out_size[0] / cw, out_size[1] / ch
    return [int((x - cx) * sx), int((y - cy) * sy), int(w * sx), int(h * sy)]


def detail_keyframes(d):
    if d.get("track"):
        return [(float(k["t"]), k["bbox"]) for k in d["track"]]
    if d.get("bbox"):
        return [(float(d["t0"]), d["bbox"]), (float(d["t1"]), d["bbox"])]
    return []


def auto_review(render, curation, script, cfg, platforms, review_dir):
    m = render["manifest"]
    path = Path(render["path"])
    issues = []  # each: {"t": output seconds or None, "problem": str, "fix": str}
    if not path.exists():
        return [{"t": None, "problem": "rendered file is missing", "fix": "render again"}], []
    if media.sha256(path) != render["sha256"]:
        return [{"t": None, "problem": "file changed after render (sha256 mismatch)",
                 "fix": "render again through the CLI; edited files need a new review"}], []

    for p in media.qa(path, cfg, platforms):
        issues.append({"t": None, "problem": p, "fix": "adjust render settings or segments"})

    verdict, sprobs = check_script(script, curation, cfg)
    for p in sprobs:
        line = script["lines"][p["line"]] if p.get("line") is not None else None
        t = media.to_output(float(line["at"]), m["segments"]) if line else None
        issues.append({"t": t, "problem": f"script rule {p['rule']}: {p['msg']}", "fix": "delete or rewrite the line"})

    if m.get("mode") == "A" and (m.get("voice_tracks") or m["lines"]):
        issues.append({"t": None, "problem": "mode A file carries added voice or text", "fix": "remove added lines"})

    for pr in curation.get("protected", []):
        o0, o1 = media.to_output(pr["t0"], m["segments"]), media.to_output(pr["t1"], m["segments"])
        if o0 is None or o1 is None:
            continue
        for l in m["lines"]:
            if l["kind"] == "voice" and min(o1, l["out_t1"]) - max(o0, l["out_t0"]) > 1e-6:
                issues.append({"t": max(o0, l["out_t0"]), "problem": f"voice covers protected moment ({pr.get('why', '')})",
                               "fix": f"move the line after {o1:.2f}s or delete it"})
        vol = media.mean_volume(path, o0, o1)
        if vol < -50:
            issues.append({"t": o0, "problem": f"protected moment is nearly silent ({vol:.0f} dB)",
                           "fix": "keep the original audio of this moment"})

    need = cfg["render"]["detail_min_visible"]
    frames = []
    review_dir = Path(review_dir)
    review_dir.mkdir(parents=True, exist_ok=True)
    for d in curation.get("details", []):
        if not d.get("essential"):
            continue
        kfs = detail_keyframes(d)
        shown = False
        for t, box in kfs:
            seg = next((s for s in m["segments"] if s["src_in"] - 1e-6 <= t <= s["src_out"] + 1e-6), None)
            if seg is None:
                continue
            shown = True
            o = media.to_output(t, m["segments"])
            frac = _inside(box, seg["crop"])
            out_box = _to_out_box(box, seg["crop"], m["out_size"])
            jpg = media.frame(path, min(o, m["duration"] - 0.05), review_dir / f"{d['id']}_{o:06.2f}.jpg",
                              out_box if frac > 0 else None)
            frames.append(str(jpg))
            if frac < need:
                cx = int(box[0] + box[2] / 2 - seg["crop"][2] / 2)
                issues.append({"t": o, "problem": f"essential detail '{d.get('desc', d['id'])}' is {frac:.0%} inside the "
                                                  f"vertical frame (needs {need:.0%})",
                               "fix": f"set crop_x near {max(0, cx)} for the segment {seg['src_in']}-{seg['src_out']}s"})
                continue
            # Geometry says it should be there; confirm on the pixels of the final file (catches covered or
            # mis-annotated details, wrong timing, captions on top of the detail).
            W, H = m["out_size"]
            ob = [max(0, out_box[0]), max(0, out_box[1])]
            ob += [max(2, min(out_box[2], W - ob[0])), max(2, min(out_box[3], H - ob[1]))]
            diff = media.patch_difference(m["source"], t, box, path, min(o, m["duration"] - 0.05), ob)
            if diff > cfg["render"]["detail_max_difference"]:
                issues.append({"t": o, "problem": f"essential detail '{d.get('desc', d['id'])}' does not match the source "
                                                  f"in the final frame (difference {diff:.0f}/255)",
                               "fix": "check what covers it (text, crop, timing) or correct the detail annotation"})
        if kfs and not shown:
            issues.append({"t": None, "problem": f"essential detail '{d.get('desc', d['id'])}' was cut out",
                           "fix": "extend the segments to include it"})
    for key in ("main", "reaction"):
        t = (curation.get("moments") or {}).get(key)
        if t is not None:
            o = media.to_output(t, m["segments"])
            if o is None:
                issues.append({"t": None, "problem": f"the {key} moment ({t}s) is not in the cut", "fix": "include it"})
            else:
                frames.append(str(media.frame(path, min(o, m["duration"] - 0.05), review_dir / f"{key}_{o:06.2f}.jpg")))
    return issues, frames


NOTE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)s?\s*\|\s*([^|]+?)\s*\|\s*(.+?)\s*$")


def parse_note(s):
    """'12.4|what is wrong|what to do' -> dict. Returns must point to an exact second."""
    m = NOTE_RE.match(s)
    if not m:
        raise ValueError(f"review note must be 'SECONDS|problem|expected fix', got: {s!r}")
    return {"t": float(m.group(1)), "problem": m.group(2), "fix": m.group(3)}
