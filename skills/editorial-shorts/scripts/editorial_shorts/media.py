"""ffmpeg/ffprobe helpers: probe, transcription, speech synthesis, vertical render, technical QA, review frames."""
import hashlib
import json
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path


class CapabilityMissing(RuntimeError):
    """A tool or setting the installer must provide. The message says what is missing and how to fix it."""


def need(tool):
    if shutil.which(tool) is None:
        raise CapabilityMissing(f"{tool} not found on PATH (install ffmpeg 6+; 8+ for the whisper filter)")


def run(cmd, cwd=None, timeout=1800):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"command failed ({r.returncode}): {' '.join(map(str, cmd[:6]))} ...\n{r.stderr[-1500:]}")
    return r


@lru_cache(maxsize=None)
def has_filter(name):
    if shutil.which("ffmpeg") is None:
        return False
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, errors="replace").stdout
    return re.search(rf"^\s*\S+\s+{re.escape(name)}\s", out, re.M) is not None


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def probe(path):
    need("ffprobe")
    r = run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,r_frame_rate:format=duration",
             "-of", "json", str(path)])
    j = json.loads(r.stdout)
    v = next((s for s in j["streams"] if s["codec_type"] == "video"), None)
    num, den = (v or {}).get("r_frame_rate", "0/1").split("/")
    return {"width": v and v["width"], "height": v and v["height"], "fps": float(num) / float(den or 1),
            "duration": float(j["format"]["duration"]), "has_audio": any(s["codec_type"] == "audio" for s in j["streams"])}


def loudness(path):
    r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True, errors="replace")
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    return float(m[-1]) if m else None


def mean_volume(path, t0, t1):
    """Mean volume (dB) of the audio between t0 and t1 (output seconds). -91 = silence."""
    r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-ss", f"{t0:.3f}", "-t", f"{max(0.05, t1 - t0):.3f}",
                        "-i", str(path), "-af", "volumedetect", "-vn", "-f", "null", "-"],
                       capture_output=True, text=True, errors="replace")
    m = re.search(r"mean_volume:\s+(-?[\d.]+) dB", r.stderr)
    return float(m.group(1)) if m else -91.0


# ---------- transcription (accessibility captions + locating candidates) ----------

def transcribe(src, out_srt, cfg):
    t = cfg["transcription"]
    model = (t.get("model_path") or "").strip()
    if t.get("engine") != "ffmpeg-whisper":
        raise CapabilityMissing(f"transcription engine {t.get('engine')!r} is not supported by this CLI")
    if not has_filter("whisper"):
        raise CapabilityMissing("ffmpeg has no 'whisper' filter (needs ffmpeg 8+ built with whisper.cpp)")
    if not model or not Path(model).exists():
        raise CapabilityMissing("transcription.model_path must point to a whisper.cpp ggml model "
                                "(e.g. ggml-small.bin from huggingface.co/ggerganov/whisper.cpp)")
    out = Path(out_srt).resolve()
    lang = t.get("language") or "auto"
    if lang == "auto":
        lang = cfg.get("language", "en").split("-")[0]
    # filter options are ':'-separated, so run in the model's folder and pass relative names
    run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(Path(src).resolve()), "-vn", "-af",
         f"aresample=16000,whisper=model={Path(model).name}:language={lang}:queue=3:destination={out.name}:format=srt",
         "-f", "null", "-"], cwd=str(Path(model).parent))
    produced = Path(model).parent / out.name
    if produced != out:
        shutil.move(str(produced), str(out))
    return out


def parse_srt(text):
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        m = re.search(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)\s*\n(.*)", block, re.S)
        if m:
            g = list(map(int, m.groups()[:8]))
            cues.append({"t0": g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000,
                         "t1": g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000,
                         "text": " ".join(m.group(9).split())})
    return cues


# ---------- speech synthesis (optional) ----------

def tts(text, out_wav, cfg):
    t = cfg["tts"]
    out = Path(out_wav)
    if t.get("engine") == "flite":
        if not has_filter("flite"):
            raise CapabilityMissing("tts.engine=flite but this ffmpeg build has no flite filter")
        (out.parent / (out.stem + ".txt")).write_text(text, encoding="utf-8")
        run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
             f"flite=textfile={out.stem}.txt:voice={t.get('flite_voice', 'slt')}", "-ar", "48000", out.name], cwd=str(out.parent))
        return out
    if t.get("engine") == "command" and t.get("command"):
        run([a.replace("{text}", text).replace("{out}", str(out)) for a in t["command"]])
        if not out.exists():
            raise CapabilityMissing("tts command ran but did not write {out}")
        return out
    raise CapabilityMissing("no speech synthesis configured (tts.engine). Use on-screen lines, original audio, "
                            "or provide a voice file per line ('audio' field)")


# ---------- render ----------

def crop_box(src_w, src_h, W, H, crop_x=None):
    cw, ch = int(round(src_h * W / H)) // 2 * 2, src_h
    if cw > src_w:
        cw, ch = src_w // 2 * 2, int(round(src_w * H / W)) // 2 * 2
    x = (src_w - cw) // 2 if crop_x in (None, "center") else max(0, min(int(crop_x), src_w - cw))
    return [x, (src_h - ch) // 2, cw, ch]


def to_output(t, segments):
    """Map a source time to output time using the segment map; None if the time was cut out."""
    for s in segments:
        if s["src_in"] - 1e-6 <= t <= s["src_out"] + 1e-6:
            return round(s["out_t0"] + (t - s["src_in"]), 3)
    return None


def _ass_time(t):
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _ass(cues, cfg):
    r = cfg["render"]
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {r['width']}\nPlayResY: {r['height']}\nWrapStyle: 0\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
            "MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Caption,{r['font']},{r['font_size']},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,5,0,2,80,80,{int(r['height'] * 0.2)},1\n"
            f"Style: Comment,{r['font']},{r['font_size']},&H0000F0FF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,5,0,8,80,80,{int(r['height'] * 0.12)},1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    ev = "".join(f"Dialogue: 0,{_ass_time(c['t0'])},{_ass_time(c['t1'])},{c['style']},,0,0,0,,"
                 f"{c['text'].replace(chr(10), ' ').replace('{', '(').replace('}', ')')}\n" for c in cues)
    return head + ev


def render(src, curation, script, cfg, out_dir, story_dir):
    """Render the vertical cut. Returns the manifest (what the reviewer checks against the file)."""
    need("ffmpeg")
    src = Path(src).resolve()
    info = probe(src)
    R = cfg["render"]
    W, H, FPS = R["width"], R["height"], R["fps"]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    default_x = (curation.get("framing") or {}).get("crop_x")

    segs, t = [], 0.0
    for s in curation["segments"]:
        a, b = float(s["in"]), float(s["out"])
        if not 0 <= a < b <= info["duration"] + 0.05:
            raise ValueError(f"segment {a}-{b}s is outside the source (0-{info['duration']:.2f}s)")
        segs.append({"src_in": a, "src_out": b, "out_t0": round(t, 3), "out_t1": round(t + b - a, 3),
                     "crop": crop_box(info["width"], info["height"], W, H, s.get("crop_x", default_x))})
        t += b - a
    total = t

    inputs = ["-i", str(src)]
    audio_in = "0:a"
    if not info["has_audio"]:
        inputs += ["-f", "lavfi", "-t", f"{info['duration']:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        audio_in = "1:a"
    fc = []
    for i, s in enumerate(segs):
        x, y, cw, ch = s["crop"]
        fc.append(f"[0:v]trim=start={s['src_in']}:end={s['src_out']},setpts=PTS-STARTPTS,crop={cw}:{ch}:{x}:{y},"
                  f"scale={W}:{H}:flags=lanczos,setsar=1,fps={FPS},format=yuv420p[v{i}]")
        fc.append(f"[{audio_in}]atrim=start={s['src_in']}:end={s['src_out']},asetpts=PTS-STARTPTS,"
                  f"aresample=48000,aformat=channel_layouts=stereo[a{i}]")
    fc.append("".join(f"[v{i}][a{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=1:a=1[vc][ac]")

    lines, cues, voice_inputs = [], [], []
    for i, ln in enumerate(script.get("lines", [])):
        o0 = to_output(float(ln["at"]), segs)
        if o0 is None:
            raise ValueError(f"line {i} at {ln['at']}s falls outside the kept segments")
        o1 = round(min(total, o0 + float(ln["dur"])), 3)
        kind = ln.get("kind", "voice")
        lines.append({"text": ln["text"], "kind": kind, "out_t0": o0, "out_t1": o1})
        cues.append({"t0": o0, "t1": o1, "text": ln["text"], "style": "Comment"})
        if kind == "voice":
            wav = Path(ln["audio"]) if ln.get("audio") else tts(ln["text"], out_dir / f"line{i}.wav", cfg)
            voice_inputs.append((str(Path(story_dir, wav).resolve()) if not wav.is_absolute() else str(wav), o0, o1))

    srt = curation.get("captions_srt")
    if srt and Path(story_dir, srt).exists():
        for c in parse_srt(Path(story_dir, srt).read_text(encoding="utf-8")):
            for s in segs:
                a, b = max(c["t0"], s["src_in"]), min(c["t1"], s["src_out"])
                if b - a > 0.05:
                    cues.append({"t0": s["out_t0"] + a - s["src_in"], "t1": s["out_t0"] + b - s["src_in"],
                                 "text": c["text"], "style": "Caption"})

    vlabel = "vc"
    if cues:
        (out_dir / "captions.ass").write_text(_ass(sorted(cues, key=lambda c: c["t0"]), cfg), encoding="utf-8")
        fc.append("[vc]subtitles=captions.ass[vs]")
        vlabel = "vs"

    alabel = "ac"
    if voice_inputs:
        duck = ",".join(f"volume=enable='between(t,{o0},{o1})':volume={R['duck_volume']}" for _, o0, o1 in voice_inputs)
        fc.append(f"[ac]{duck}[acd]")
        base = 1 + (0 if info["has_audio"] else 1)
        mix = ["[acd]"]
        for k, (path, o0, _) in enumerate(voice_inputs):
            inputs += ["-i", path]
            ms = int(o0 * 1000)
            fc.append(f"[{base + k}:a]aresample=48000,aformat=channel_layouts=stereo,adelay={ms}|{ms}[vo{k}]")
            mix.append(f"[vo{k}]")
        fc.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0:duration=first[am]")
        alabel = "am"

    pre = out_dir / "pre.mkv"
    run(["ffmpeg", "-nostdin", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(fc),
         "-map", f"[{vlabel}]", "-map", f"[{alabel}]", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(R["crf"]),
         "-c:a", "pcm_s16le", pre.name], cwd=str(out_dir))

    # Two-pass loudness normalisation (single pass drifts on short clips).
    target = R["loudness_lufs"]
    m = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", pre.name, "-af",
                        f"loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
                       cwd=str(out_dir), capture_output=True, text=True, errors="replace").stderr
    js = json.loads(m[m.rindex("{"):m.rindex("}") + 1])
    if js.get("input_i") in (None, "-inf"):
        af = "anull"  # silent track: nothing to normalise
    else:
        af = (f"loudnorm=I={target}:TP=-1.5:LRA=11:measured_I={js['input_i']}:measured_TP={js['input_tp']}:"
              f"measured_LRA={js['input_lra']}:measured_thresh={js['input_thresh']}:offset={js['target_offset']}:linear=true")
    final = out_dir / "final.mp4"
    run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", pre.name, "-c:v", "copy", "-af", f"{af},aresample=48000",
         "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", final.name], cwd=str(out_dir))
    pre.unlink(missing_ok=True)

    moments = curation.get("moments") or {}
    return {
        "source": str(src), "source_size": [info["width"], info["height"]], "out_size": [W, H], "fps": FPS,
        "duration": round(total, 3), "segments": segs, "lines": lines, "mode": script.get("mode"),
        "voice_tracks": len(voice_inputs), "captions": len(cues),
        "delivery_s": to_output(moments["main"], segs) if "main" in moments else None,
        "first_intervention_s": min((l["out_t0"] for l in lines), default=None),
        "file": str(final),
    }


# ---------- QA and review frames ----------

def qa(path, cfg, platforms):
    """Technical checks on the rendered file. Returns a list of problem strings (empty = pass)."""
    R = cfg["render"]
    p = probe(path)
    probs = []
    if (p["width"], p["height"]) != (R["width"], R["height"]):
        probs.append(f"resolution {p['width']}x{p['height']} (expected {R['width']}x{R['height']})")
    if not p["has_audio"]:
        probs.append("no audio stream")
    for pl in platforms:
        prof = cfg["platform_profiles"].get(pl)
        if prof and not prof["min_s"] <= p["duration"] <= prof["max_s"]:
            probs.append(f"duration {p['duration']:.1f}s outside {pl} profile ({prof['min_s']}-{prof['max_s']}s)")
    lufs = loudness(path)
    if lufs is not None and lufs > -70 and abs(lufs - R["loudness_lufs"]) > R["loudness_tolerance"]:
        probs.append(f"loudness {lufs:.1f} LUFS (target {R['loudness_lufs']} +/- {R['loudness_tolerance']})")
    for label, ss in (("first", 0.0), ("last", max(0.0, p["duration"] - 0.6))):
        r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-ss", f"{ss:.2f}", "-t", "0.5", "-i", str(path),
                            "-vf", "blackdetect=d=0.4:pix_th=0.05", "-an", "-f", "null", "-"],
                           capture_output=True, text=True, errors="replace")
        if "black_start" in r.stderr:
            probs.append(f"{label} half-second is black")
    return probs


def patch(path, t, box, n=16):
    """Grey n x n thumbnail of a region of the frame at time t (bytes 0-255)."""
    x, y, w, h = (int(v) for v in box)
    r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                        "-vf", f"crop={w}:{h}:{x}:{y},scale={n}:{n}:flags=area", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                       capture_output=True)
    if r.returncode != 0 or len(r.stdout) != n * n:
        raise RuntimeError(f"could not read region {box} at {t:.2f}s of {path}")
    return r.stdout


def patch_difference(src, t_src, src_box, out, t_out, out_box):
    """Mean absolute grey difference between a detail in the source and where it should be in the output."""
    a, b = patch(src, t_src, src_box), patch(out, t_out, out_box)
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def frame(path, t, out_jpg, box=None):
    vf = "scale=540:-2"
    if box:
        x, y, w, h = box
        vf = f"drawbox=x={x}:y={y}:w={w}:h={h}:color=yellow@0.9:t=8," + vf
    run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
         "-vf", vf, str(out_jpg)])
    return out_jpg
