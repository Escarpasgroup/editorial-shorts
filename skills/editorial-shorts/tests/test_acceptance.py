"""Acceptance tests: one test per criterion of the specification. Synthetic material only; nothing leaves the machine.

Run:  python -m unittest discover -s tests -v      (from the skill folder; needs ffmpeg/ffprobe on PATH)
"""
import contextlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))

from editorial_shorts import config, demo, editorial, media  # noqa: E402
from editorial_shorts.cli import main  # noqa: E402

ES = SKILL / "scripts" / "es.py"


def es(ws, *args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(["--workspace", str(ws), *map(str, args)])
    return code, buf.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="es-test-"))
        es(self.ws, "init")
        self.inp = demo.prepare_inputs(self.ws)

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    # ---- helpers ----
    def cfg(self):
        return config.load(self.ws)

    def edit_cfg(self, fn):
        p = self.ws / "config.json"
        j = json.loads(p.read_text(encoding="utf-8"))
        fn(j)
        p.write_text(json.dumps(j, indent=1), encoding="utf-8")

    def curation(self, **changes):
        c = json.loads((self.inp / "dog_curation.json").read_text(encoding="utf-8"))
        c.update(changes)
        p = self.inp / f"cur_{abs(hash(json.dumps(changes, sort_keys=True)))}.json"
        p.write_text(json.dumps(c), encoding="utf-8")
        return p

    def new_story(self, title="Dog and gate", path="cut"):
        code, out = es(self.ws, "story", "new", "--title", title, "--theme", "pets", "--path", path, "--page", "general")
        sid = out.split()[0]
        self.assertEqual(es(self.ws, "story", "decide", sid, "approve", "--page", "general", "--reason", "test")[0], 0)
        return sid

    def through_review(self, curation=None, script="script_mode_a.json", approve=True, title="Dog and gate"):
        sid = self.new_story(title)
        self.assertEqual(es(self.ws, "curate", sid, curation or self.inp / "dog_curation.json")[0], 0)
        self.assertEqual(es(self.ws, "script", sid, self.inp / script)[0], 0)
        code, out = es(self.ws, "render", sid)
        self.assertEqual(code, 0, out)
        code, out = es(self.ws, "review", "auto", sid)
        if approve:
            self.assertEqual(code, 0, out)
            self.assertEqual(es(self.ws, "review", "decide", sid, "approve", "--reviewer", "t")[0], 0)
        return sid

    def db(self):
        c = sqlite3.connect(str(self.ws / "data" / "editorial.sqlite"))
        c.row_factory = sqlite3.Row
        return c

    def pubs(self):
        with contextlib.closing(self.db()) as c:
            return [dict(r) for r in c.execute("SELECT * FROM publications")]

    def outbox(self):
        return list((self.ws / "sim_outbox").glob("post_*.json"))

    def faults(self, **f):
        (self.ws / "sim_outbox").mkdir(exist_ok=True)
        (self.ws / "sim_outbox" / "_faults.json").write_text(json.dumps(f))

    def manifest(self, sid):
        with contextlib.closing(self.db()) as c:
            r = c.execute("SELECT manifest, path FROM renders WHERE story_id=? AND status='current'", (sid,)).fetchone()
        return json.loads(r["manifest"]), Path(r["path"])


class T01FirstFlow(unittest.TestCase):
    def test_example_config_runs_first_flow_as_a_user_would(self):
        ws = Path(tempfile.mkdtemp(prefix="es-first-"))
        try:
            r = subprocess.run([sys.executable, str(ES), "--workspace", str(ws), "demo"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=900)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-2000:])
            self.assertIn("simulated posts", r.stdout)
            self.assertEqual(len(list((ws / "sim_outbox").glob("post_*.json"))), 1)
            self.assertTrue(list(ws.glob("stories/*/renders/v1/final.mp4")))
        finally:
            shutil.rmtree(ws, ignore_errors=True)


class T02OriginalAudio(Base):
    def test_self_explanatory_scene_keeps_original_audio_with_zero_voice(self):
        sid = self.through_review()
        man, mp4 = self.manifest(sid)
        self.assertEqual(man["mode"], "A")
        self.assertEqual(man["voice_tracks"], 0)
        self.assertEqual(man["lines"], [])
        # the laugh (source 4.6-6.4 s -> output 2.6-4.4 s) is in the final file; before it there is only room tone
        laugh = media.mean_volume(mp4, 2.7, 4.3)
        before = media.mean_volume(mp4, 0.2, 2.4)
        self.assertGreater(laugh, -30)
        self.assertGreater(laugh - before, 15)


class T03VoiceRules(Base):
    PT_VISIBLE = [{"t0": 2.0, "t1": 8.0, "desc": "um cachorro marrom corre atrás da bola vermelha"},
                  {"t0": 3.9, "t1": 5.1, "desc": "o cachorro pula o portão alto"}]

    def pt_curation(self):
        c = json.loads((self.inp / "dog_curation.json").read_text(encoding="utf-8"))
        c["visible"] = self.PT_VISIBLE
        c["details"][0]["desc"] = "a bola passa por baixo do portão enquanto o cachorro pula por cima"
        return c

    def test_descriptive_voice_rejected_specific_remark_accepted_pt(self):
        cfg, cur = self.cfg(), self.pt_curation()
        bad = {"mode": "B", "language": "pt-BR", "lines": [{
            "text": "Neste momento, o cachorro marrom corre atrás da bola. Agora ele vai pular o portão. "
                    "Olha como esse portão é alto! E você, pularia?",
            "at": 6.5, "dur": 1.4, "kind": "voice", "loss_if_removed": "nada", "evidence": []}]}
        verdict, probs = editorial.check_script(bad, cur, cfg)
        rules = {p["rule"] for p in probs}
        self.assertEqual(verdict, "rejected")
        self.assertTrue({"narrating_the_obvious", "anticipating_payoff", "generic_question", "describes_visible",
                         "deletion_test", "no_evidence"} <= rules, rules)

        good = {"mode": "B", "language": "pt-BR", "lines": [{
            "text": "A bola achou um caminho bem mais fácil.", "at": 6.5, "dur": 1.4, "kind": "voice",
            "loss_if_removed": "quase ninguém percebe que a bola passou por baixo enquanto o cachorro pulou",
            "evidence": [{"type": "detail", "ref": "d1"}]}]}
        self.assertEqual(editorial.check_script(good, cur, cfg), ("accepted", []))

        # the same good sentence fails if it covers the laugh, cites nothing, or gives the payoff away early
        over_laugh = json.loads(json.dumps(good)); over_laugh["lines"][0]["at"] = 5.0
        self.assertIn("covers_protected", {p["rule"] for p in editorial.check_script(over_laugh, cur, cfg)[1]})
        no_ev = json.loads(json.dumps(good)); no_ev["lines"][0]["evidence"] = []
        self.assertIn("no_evidence", {p["rule"] for p in editorial.check_script(no_ev, cur, cfg)[1]})
        early = json.loads(json.dumps(good)); early["lines"][0]["at"] = 2.5
        cur_early = self.pt_curation(); cur_early["details"][0]["t0"] = 4.6
        self.assertIn("anticipates_payoff", {p["rule"] for p in editorial.check_script(early, cur_early, cfg)[1]})

    def test_same_rule_through_the_cli_en(self):
        sid = self.new_story()
        es(self.ws, "curate", sid, self.inp / "dog_curation.json")
        code, out = es(self.ws, "script", sid, self.inp / "script_descriptive_bad.json")
        self.assertEqual(code, 2, out)
        self.assertIn("describes_visible", out)
        self.assertEqual(es(self.ws, "script", sid, self.inp / "script_comment_good.json")[0], 0)

    def test_on_screen_comment_renders_after_the_laugh(self):
        sid = self.through_review(script="script_comment_good.json")
        man, _ = self.manifest(sid)
        self.assertEqual(man["voice_tracks"], 0)
        self.assertEqual(len(man["lines"]), 1)
        self.assertGreaterEqual(man["lines"][0]["out_t0"], 4.4)  # after the protected laugh in output time


class T04Curiosity(Base):
    def test_curiosity_without_evidence_is_sent_back(self):
        sid = self.new_story("Octopus hearts", path="original")
        code, out = es(self.ws, "curate", sid, self.inp / "curiosity_curation.json")
        self.assertEqual(code, 3)
        self.assertIn("needs_research", out)
        # confirmed claim but no image that demonstrates it -> returned
        c = json.loads((self.inp / "curiosity_curation.json").read_text(encoding="utf-8"))
        c["facts"][0].update(status="confirmed", sources=["https://example.org/reference"])
        p = self.inp / "c2.json"; p.write_text(json.dumps(c), encoding="utf-8")
        code, out = es(self.ws, "curate", sid, p)
        self.assertEqual(code, 3)
        self.assertIn("returned", out)
        self.assertIn("no image", out)
        # evidence + visual proof + usage condition -> ready
        c["details"] = [{"id": "d9", "t0": 1, "t1": 3, "desc": "diagram of the three hearts", "proves": ["f1"]}]
        c["source"] = {"file": None, "rights": {"status": "licensed", "evidence": "license file"}}
        p.write_text(json.dumps(c), encoding="utf-8")
        self.assertEqual(es(self.ws, "curate", sid, p)[0], 0)


class T05MeaningCut(Base):
    UNITS = [{"id": "u0", "t0": 2.0, "t1": 3.0, "text": "I would never say"},
             {"id": "u1", "t0": 3.0, "t1": 4.5, "text": "this game is bad", "requires": ["u0"]}]

    def test_cut_that_changes_the_meaning_is_rejected(self):
        sid = self.new_story()
        code, out = es(self.ws, "curate", sid, self.curation(meaning_units=self.UNITS, segments=[{"in": 3.0, "out": 8.0}]))
        self.assertEqual(code, 3)
        self.assertIn("without 'I would never say'", out)
        code, out = es(self.ws, "curate", sid, self.curation(meaning_units=self.UNITS, segments=[{"in": 2.5, "out": 8.0}]))
        self.assertEqual(code, 3)
        self.assertIn("keeps 50%", out)
        self.assertEqual(es(self.ws, "curate", sid, self.curation(meaning_units=self.UNITS,
                                                                  segments=[{"in": 2.0, "out": 8.0}]))[0], 0)


class T06Reframe(Base):
    def test_reframe_hiding_the_essential_detail_is_caught_on_the_final_file(self):
        bad = self.curation(framing={"crop_x": 0})
        sid = self.through_review(curation=bad, approve=False)
        code, out = es(self.ws, "review", "auto", sid)
        self.assertEqual(code, 2, out)
        self.assertIn("essential detail", out)
        self.assertRegex(out, r"\d+\.\d\ds: essential detail")
        self.assertEqual(es(self.ws, "review", "decide", sid, "approve")[0], 2)  # cannot approve this file
        code, out = es(self.ws, "review", "decide", sid, "return", "--note", "2.5|ball is outside the frame|crop_x 650")
        self.assertEqual(code, 0)
        self.assertEqual(es(self.ws, "review", "decide", sid, "return")[0], 2)  # a return needs an exact second

    def test_pixel_probe_reads_the_final_file(self):
        sid = self.through_review()
        man, mp4 = self.manifest(sid)
        src, crop = man["source"], man["segments"][0]["crop"]
        ball_src = [900, 905, 50, 50]                       # source 4.5 s
        s = 1920 / crop[3]
        ball_out = [int((900 - crop[0]) * s), int(905 * s), int(50 * s), int(50 * s)]
        same = media.patch_difference(src, 4.5, ball_src, mp4, 2.5, ball_out)
        wrong_time = media.patch_difference(src, 4.5, ball_src, mp4, 0.2, ball_out)  # ball not there yet
        self.assertLess(same, 15)
        self.assertGreater(wrong_time, 40)


class T07VersionBinding(Base):
    def test_approval_is_bound_to_file_and_account(self):
        sid = self.through_review()
        meta = self.inp / "meta.json"
        self.assertEqual(es(self.ws, "publish", "queue", sid, "--page", "general", "--meta", meta)[0], 0)
        v1 = self.pubs()[0]
        # new render: the queued publication is cancelled and the old approval is void
        self.assertEqual(es(self.ws, "render", sid)[0], 0)
        self.assertEqual(self.pubs()[0]["state"], "cancelled")
        code, out = es(self.ws, "publish", "queue", sid, "--page", "general", "--meta", meta)
        self.assertEqual(code, 2)
        self.assertIn("no valid approved review", out)
        self.assertEqual(es(self.ws, "publish", "run")[1].strip(), "nothing queued")
        # review the new file, queue again: the publication now points to v2 and its sha256
        self.assertEqual(es(self.ws, "review", "auto", sid)[0], 0)
        self.assertEqual(es(self.ws, "review", "decide", sid, "approve")[0], 0)
        self.assertEqual(es(self.ws, "publish", "queue", sid, "--page", "general", "--meta", meta)[0], 0)
        v2 = self.pubs()[0]
        self.assertNotEqual(v1["render_id"], v2["render_id"])  # same bytes or not, a new render needed a new review
        # the page's account changes after queueing: the publisher refuses to send
        self.edit_cfg(lambda j: j["pages"][0]["destinations"][0].update(account="someone-else"))
        code, out = es(self.ws, "publish", "run")
        self.assertIn("blocked", out)
        self.assertIn("no longer sends", out)
        self.assertEqual(self.outbox(), [])

    def test_file_changed_after_approval_is_not_published(self):
        sid = self.through_review()
        meta = self.inp / "meta.json"
        self.assertEqual(es(self.ws, "publish", "queue", sid, "--page", "general", "--meta", meta)[0], 0)
        _, mp4 = self.manifest(sid)
        with open(mp4, "ab") as f:
            f.write(b"\0")
        code, out = es(self.ws, "publish", "run")
        self.assertIn("sha256 mismatch", out)
        self.assertEqual(self.outbox(), [])


class T08NoDuplicates(Base):
    def queued(self):
        sid = self.through_review()
        self.assertEqual(es(self.ws, "publish", "queue", sid, "--page", "general", "--meta", self.inp / "meta.json")[0], 0)
        return self.pubs()[0]["id"]

    def test_upload_accepted_but_confirmation_lost(self):
        pid = self.queued()
        self.faults(accept_then_drop=1, lookup_unavailable=1)
        code, out = es(self.ws, "publish", "run")
        self.assertIn("confirmation_pending", out)
        self.assertEqual(len(self.outbox()), 1)          # the platform did receive it
        self.assertEqual(es(self.ws, "publish", "run")[1].strip(), "nothing queued")  # no blind resend
        self.assertEqual(es(self.ws, "publish", "retry", pid)[0], 2)  # retry refused while unverified
        code, out = es(self.ws, "publish", "reconcile")
        self.assertIn("published (reconciled)", out)
        self.assertEqual(self.pubs()[0]["state"], "published")
        self.assertEqual(len(self.outbox()), 1)

    def test_worker_died_while_sending(self):
        pid = self.queued()
        with contextlib.closing(self.db()) as c:
            c.execute("UPDATE publications SET state='sending', lease_until=0 WHERE id=?", (pid,)); c.commit()
        self.assertEqual(es(self.ws, "publish", "run")[1].strip(), "nothing queued")
        code, out = es(self.ws, "publish", "reconcile")
        self.assertIn("confirmed absent", out)       # checked the destination: nothing there, retry is safe
        self.assertEqual(es(self.ws, "publish", "retry", pid)[0], 0)
        es(self.ws, "publish", "run")
        self.assertEqual(len(self.outbox()), 1)

    def test_concurrent_workers_publish_once(self):
        self.queued()
        self.faults(delay_ms=1500)
        procs = [subprocess.Popen([sys.executable, str(ES), "--workspace", str(self.ws), "publish", "run", "--worker", f"w{i}"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(4)]
        outs = [p.communicate(timeout=300) for p in procs]
        self.assertTrue(all(p.returncode == 0 for p in procs), outs)
        self.assertEqual(len(self.outbox()), 1, outs)
        self.assertEqual(self.pubs()[0]["state"], "published")
        self.assertEqual(self.pubs()[0]["attempts"], 1)


class T09MissingCredentials(Base):
    def test_missing_credentials_are_reported_and_other_steps_finish(self):
        self.edit_cfg(lambda j: (j["publishing"].update(mode="live"), j["pages"][0].update(destinations=[
            {"platform": "youtube", "adapter": "buffer", "account": "my-channel"},
            {"platform": "youtube", "adapter": "local_export", "account": "manual-upload"}])))
        code, out = es(self.ws, "doctor")
        self.assertIn("organization_id is empty", out)
        self.assertIn("work without any account or credential", out)
        self.assertEqual(es(self.ws, "research", "add", self.inp / "references.json")[0], 0)
        self.assertEqual(es(self.ws, "research", "study")[0], 0)
        sid = self.through_review()
        self.assertEqual(es(self.ws, "publish", "queue", sid, "--page", "general", "--meta", self.inp / "meta.json")[0], 0)
        code, out = es(self.ws, "publish", "run")
        self.assertIn("blocked", out)
        self.assertIn("organization_id is empty", out)
        states = {p["adapter"]: p["state"] for p in self.pubs()}
        self.assertEqual(states, {"buffer": "blocked", "local_export": "published"})
        exported = list((self.ws / "export").rglob("*.mp4"))
        self.assertEqual(len(exported), 1)
        self.assertTrue(exported[0].with_suffix(".txt").exists())


@unittest.skipUnless(media.has_filter("flite"), "ffmpeg without flite: voiced lines need a configured TTS")
class T11OptionalVoiceAndTranscription(Base):
    def test_voiced_comment_and_transcription(self):
        self.edit_cfg(lambda j: (j["tts"].update(engine="flite"),
                                 j["transcription"].update(model_path=os.environ.get("ES_WHISPER_MODEL", ""))))
        sc = json.loads((self.inp / "script_comment_good.json").read_text(encoding="utf-8"))
        sc["lines"][0]["kind"] = "voice"
        (self.inp / "voiced.json").write_text(json.dumps(sc), encoding="utf-8")
        sid = self.through_review(script="voiced.json")
        man, mp4 = self.manifest(sid)
        self.assertEqual(man["voice_tracks"], 1)
        self.assertGreater(media.mean_volume(mp4, 4.6, 5.6), -35)  # the remark is audible after the laugh
        if not os.environ.get("ES_WHISPER_MODEL") or not media.has_filter("whisper"):
            self.skipTest("set ES_WHISPER_MODEL to a ggml model to also check transcription")
        out = self.ws / "t.srt"
        media.transcribe(mp4, out, self.cfg())
        self.assertRegex(out.read_text(encoding="utf-8").lower(), r"ball|easier|way")


class T10Portable(unittest.TestCase):
    def test_package_has_no_private_paths_and_runs_from_a_copy(self):
        bad = []
        import re
        pat = re.compile(r"([A-Za-z]:\\\\?Users\\\\?[A-Za-z]|/home/[a-z]|/Users/[A-Za-z]|@[a-z0-9_.-]+\.(com|net)\b)", re.I)
        for f in SKILL.rglob("*"):
            if f.is_file() and f.suffix in {".py", ".md", ".json", ".txt"} and "__pycache__" not in f.parts:
                for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                    if pat.search(line) and "noqa-portable" not in line:
                        bad.append(f"{f.relative_to(SKILL)}:{i}: {line.strip()[:100]}")
        self.assertEqual(bad, [])
        tmp = Path(tempfile.mkdtemp(prefix="es-copy-"))
        try:
            copy = tmp / "installed" / "editorial-shorts"
            shutil.copytree(SKILL, copy, ignore=shutil.ignore_patterns("__pycache__"))
            env = {k: v for k, v in os.environ.items() if not k.startswith("ES_")}
            env.update(HOME=str(tmp / "home"), USERPROFILE=str(tmp / "home"))
            r = subprocess.run([sys.executable, str(copy / "scripts" / "es.py"), "--workspace", str(tmp / "ws"), "demo"],
                               cwd=str(tmp), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=900)
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-1000:])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
