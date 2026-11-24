"""First demonstrable flow, end to end, with synthetic material and a simulated destination.

Everything happens inside the workspace. Nothing is sent outside the machine. The study and metrics use
demonstrative data and are labelled as such. The "judgment" files (curation, scripts, review decision) stand in
for what the supervisor, curator, scriptwriter and reviewer roles would write after watching the material.
"""
import json
import shutil
from pathlib import Path

from . import media
from .config import SKILL_DIR

DEMO = SKILL_DIR / "assets" / "demo"

# Synthetic 10 s clip, 1920x1080: a brown "dog" jumps a grey gate while a red "ball" rolls under it;
# a laugh-like sound (the person filming) plays from 4.6 s to 6.4 s.
# Moving objects use overlay (its x/y are evaluated per frame; drawbox's are not in every ffmpeg build).
DOG_GATE_VIDEO = (
    "color=c=0x5f8f58:s=1920x1080:d=10:r=30,"
    "drawbox=x=0:y=960:w=1920:h=120:color=0x3b2f22:t=fill,"
    "drawbox=x=900:y=520:w=40:h=380:color=0x888888:t=fill[bg];"
    "color=c=red:s=50x50:d=10:r=30[ball];"
    "color=c=0x8b5a2b:s=160x120:d=10:r=30[dog];"
    "[bg][ball]overlay=x='if(lt(t,3),300,if(gt(t,6),1500,300+400*(t-3)))':y=905[b1];"
    "[b1][dog]overlay=x='100+180*t':y='840-if(between(t,3.9,5.1),450*sin(PI*(t-3.9)/1.2),0)'[v]"
)
DOG_GATE_AUDIO = ("aevalsrc='0.03*(random(0)-0.5)+0.5*sin(2*PI*520*t)*between(t,4.6,6.4)*(0.5+0.5*sin(2*PI*7*t))'"
                  ":s=48000:d=10")


def make_dog_gate(path):
    path = Path(path)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    media.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", DOG_GATE_AUDIO,
               "-filter_complex", DOG_GATE_VIDEO, "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-preset", "veryfast",
               "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", "10", str(path)])
    return path


def prepare_inputs(ws):
    """Copy the demo fixtures into the workspace and generate the synthetic clip next to them."""
    dst = Path(ws) / "demo_inputs"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(DEMO, dst)
    make_dog_gate(dst / "dog_gate.mp4")
    return dst


def run_demo(ws):
    from .cli import main
    ws = Path(ws).resolve()
    if not (ws / "config.json").exists():
        main(["--workspace", str(ws), "init"])
    cfg = json.loads((ws / "config.json").read_text(encoding="utf-8"))
    if cfg.get("publishing", {}).get("mode", "simulate") != "simulate":
        print("demo refused: publishing.mode must be 'simulate' in this workspace")
        return 1
    inp = prepare_inputs(ws)
    W = ["--workspace", str(ws)]
    results = []

    def step(role, title, args, expect=0):
        print(f"\n### [{role}] {title}\n$ es.py {' '.join(args)}")
        code = main(W + args)
        ok = code == expect
        results.append((role, title, code, expect, ok))
        if not ok:
            print(f"!! expected exit {expect}, got {code}")
        return code

    print("\n### [all] capabilities available on this machine")
    main(W + ["doctor"])
    step("researcher+analyst", "load demonstrative references", ["research", "add", str(inp / "references.json")])
    step("analyst", "study: which themes deserve a page, a one-off story, or archiving", ["research", "study"])

    sid = _capture(main, W + ["story", "new", "--title", "Dog clears the gate, ball takes the low road",
                               "--theme", "pets", "--path", "cut", "--page", "general"])
    step("supervisor", "approve the story for the general page", ["story", "decide", sid, "approve", "--page", "general",
                                                                  "--angle", "the detail most viewers miss",
                                                                  "--reason", "self-explanatory scene with a real reaction"])
    step("curator", "context, usage condition, in/out, protected reaction, essential detail",
         ["curate", sid, str(inp / "dog_curation.json")])
    step("scriptwriter", "REJECTED: narration that describes what is on screen",
         ["script", sid, str(inp / "script_descriptive_bad.json")], expect=2)
    step("scriptwriter", "ACCEPTED (validation only): one specific remark backed by the material",
         ["script", sid, str(inp / "script_comment_good.json")])
    step("scriptwriter", "CHOSEN: mode A, original audio, zero added voice",
         ["script", sid, str(inp / "script_mode_a.json")])
    step("editor", "render the vertical cut", ["render", sid])
    step("reviewer", "automatic checks on the rendered file", ["review", "auto", sid])
    step("reviewer", "approve this exact file (sha256-bound)", ["review", "decide", sid, "approve", "--reviewer", "demo-reviewer"])
    step("publisher", "queue for the page's configured account", ["publish", "queue", sid, "--page", "general",
                                                                  "--meta", str(inp / "meta.json")])
    step("publisher", "send (simulated destination)", ["publish", "run", "--worker", "demo-worker"])
    step("publisher", "run again: nothing is sent twice", ["publish", "run", "--worker", "demo-worker-2"])
    step("publisher", "publication log", ["publish", "list"])

    cid = _capture(main, W + ["story", "new", "--title", "Why octopuses have three hearts", "--theme", "science",
                               "--path", "original", "--page", "general"])
    step("supervisor", "approve the question for research", ["story", "decide", cid, "approve", "--page", "general",
                                                             "--reason", "legitimate question, needs verification"])
    step("curator", "curiosity without evidence for its central claim goes back",
         ["curate", cid, str(inp / "curiosity_curation.json")], expect=3)

    pub = _capture(main, W + ["publish", "list"]).split()[0]
    step("analyst", "store demonstrative 24h metrics", ["metrics", pub, "--window", "24h",
                                                         "--file", str(inp / "metrics_24h.json"), "--demo"])
    step("analyst", "learning report", ["learn"])

    print("\n=== demo summary ===")
    bad = [r for r in results if not r[4]]
    for role, title, code, expect, ok in results:
        print(f"[{'ok' if ok else 'FAIL'}] {role}: {title} (exit {code})")
    outbox = list((ws / "sim_outbox").glob("post_*.json"))
    print(f"simulated posts in {ws / 'sim_outbox'}: {len(outbox)}")
    return 0 if not bad and len(outbox) == 1 else 1


def _capture(fn, argv):
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(argv)
    out = buf.getvalue().strip()
    print(f"$ es.py {' '.join(argv[2:])}\n{out}")
    return out.splitlines()[-1].split()[0] if out else ""
