"""Beat editor (owner, 2026-11-19): one beat in its own window - the scrubber on that beat, what the narration says there (speech-to-
text timing) and the prompts / searches used for its picture, a choice of ladder rung to render for it (or your own video / still,
dropped or linked; a still gets camera shots - overhead, dolly, pan, zoom), then Discard or Splice. Splice replaces only that beat
in place (edit.apply re-cuts the beat; nothing else is re-rendered).

Built on what the timeline editor already has: the beat's candidates (review workspace), review.start for background work,
edit.preview / edit.start_apply for the render and the splice. While the pipeline is making a video, anything that renders or uses
the GPU waits (the window shows a modal "please wait" with OK): editing must never slow down or interrupt a job.
"""
import json
import shutil
import subprocess
import time
from pathlib import Path

from . import media

# rungs the window offers, in ladder order (cheapest first); "own" is your own file
RUNGS = [
    ("stock", "Stock footage (Pexels, Pixabay ...)", False),
    ("photo", "Real photos of the subject (credited)", False),
    ("imagegen", "AI image services (free: Cloudflare, ChatGPT ...)", False),
    ("storyboard", "Storyboard: a new AI still every ~4 s, one locked style", False),
    ("animate", "Animate this beat's AI still (Meta video / Flow, then WanGP)", True),
    ("wangp", "WanGP still on this PC (GPU)", True),
    ("headline", "Headline card (text summing up the beat)", False),
    ("all", "Every rung (show all options)", True),
    ("own", "Your own video or still (drop, pick or link)", False),
]
HEAVY = {r for r, _, gpu in RUNGS if gpu} | {"shots"}      # GPU / CPU-heavy: wait while the pipeline runs
SHOTS = [("auto", "Mixed shots (overhead, dolly, pan, zoom)"), ("spin", "Overhead (drone, rotating down)"), ("dolly", "Dolly in"),
         ("pan-lr", "Pan left to right"), ("pan-rl", "Pan right to left"), ("push", "Zoom in"), ("pull", "Zoom out (reveal)"),
         ("tilt-down", "Tilt down"), ("wide", "Wide, slow push"), ("none", "Hold (no camera move)")]
MIX = ["spin", "dolly", "pan-lr", "push", "pull", "tilt-down"]
SHOT_SECS = 4.5            # one camera shot per ~4.5 s of the beat in "mixed" mode


def pipeline_busy(studio, jid=None):
    """'' when nothing is being made; else the reason editing has to wait (owner: never interfere with the pipeline)."""
    cur = getattr(studio, "current", None)
    if not cur: return ""
    try:
        s = studio.job(cur).state
        title = s.get("title") or s.get("src_title") or s.get("url") or cur
        stage = s.get("stage") or "working"
    except Exception:
        title, stage = cur, "working"
    same = " This is the video you are editing." if cur == jid else ""
    return (f"Video Studio is making \"{str(title)[:80]}\" right now (stage: {stage}).{same} Rendering or generating for a beat now "
            "would slow it down or interrupt it. Please wait until it has finished, then try again.")


def _layout_beat(job, bid, kind):
    from . import edit
    out = edit.beats(job, kind)
    b = next((x for x in out.get("beats", []) if x["id"] == bid), None)
    if not b: raise ValueError(f"beat {bid} not found")
    return out.get("kind"), b


def _words(job, kind, b):
    """The narration heard in this beat as speech-to-text timings: [(word, t)] with t in seconds from the beat's start, or []."""
    job = Path(job)
    try:
        if kind == "short":
            return []
        from . import edit
        lb = next(x for x in edit.long_layout(job) if x["id"] == b["id"])
        sid = lb["sid"]
        f = job / "captions" / f"words_{sid}.json"
        if not f.exists(): return []
        wt = json.loads(f.read_text(encoding="utf-8"))
        first = min(x["start"] for x in edit.long_layout(job) if x["sid"] == sid)   # the section's narration starts with its first beat
        a, e = lb["start"] - first, lb["start"] - first + lb["dur"]
        return [(w, round(max(0.0, s - a), 2)) for w, s, _ in wt if a - 0.3 <= s < e - 0.05]   # a word starting just before the cut belongs here
    except Exception:
        return []


def _prompts(job, kind, bid):
    """What the beat's pictures were made from: AI image prompt, animation prompt, stock searches, subject."""
    job = Path(job)
    try:
        if kind == "short":
            from .pipeline import Job
            sec = next(s for s in Job(job).short_plan()["sections"] if s["id"] == bid)
        else:
            pf = job / "longedit" / "short" / "plan.json"
            sec = next((s for s in json.loads(pf.read_text(encoding="utf-8"))["sections"] if s["id"] == bid), {}) if pf.exists() else {}
            if not sec:
                from . import edit
                v = next(x for x in edit.long_layout(job) if x["id"] == bid).get("visual") or {}
                sec = {"still_prompt": v.get("prompt", ""), "queries": v.get("queries") or [], "subject": v.get("subject")}
        subj = sec.get("subject")
        return {"still_prompt": sec.get("still_prompt", ""), "motion": sec.get("motion", ""),
                "queries": sec.get("queries") or [], "subject": (subj or {}).get("name") if isinstance(subj, dict) else (subj or "")}
    except Exception:
        return {"still_prompt": "", "motion": "", "queries": [], "subject": ""}


def info(studio, jid, bid, kind=None):
    """Everything the window shows for one beat."""
    from . import edit
    j = studio.job(jid); job = j.dir
    if kind in ("short", "long"):
        try: edit.set_target(job, kind)
        except Exception: pass
    k, b = _layout_beat(job, bid, kind)
    s = j.state
    out_path = s.get("short_output") if k == "short" else s.get("output")
    video = ""
    if out_path and Path(out_path).exists():
        try: video = Path(out_path).resolve().relative_to(job.resolve()).as_posix()
        except ValueError: video = ""
    return {"job": jid, "title": s.get("title") or s.get("src_title") or jid, "kind": k, "space": "short" if k == "short" else "long",
            "beat": {x: b.get(x) for x in ("id", "label", "start", "dur", "vo", "editable", "source", "rungs", "ladder")},
            "words": _words(job, k, b), "prompts": _prompts(job, k, bid), "video": video,
            "rungs": [{"id": r, "name": n, "heavy": r in HEAVY} for r, n, _ in RUNGS], "shots": [{"id": i, "name": n} for i, n in SHOTS],
            "busy": pipeline_busy(studio, jid), "editing": edit.status(job)}


# ---------------------------------------------------------------- single rungs (run by review.start in the background)
def photo_rung(job, bid, cfg, wg, log, payload):
    """Real photos of the beat's subject (credited), as candidates."""
    from . import review, photos, coherence
    sd, _ = review._p(job); sp, sec = review._section(job, bid)
    name = (sec.get("subject") or {}).get("name") if isinstance(sec.get("subject"), dict) else sec.get("subject")
    if not name: raise ValueError("this beat has no named subject to find real photos of (try stock, the image services or your own file)")
    review._busy(job, bid, f"Searching real photos of {name}...")
    pics = photos.find(cfg, sec["subject"], sd / "photos", log, want=10)
    phases = coherence.beat_phases([x for x in sp["sections"] if not x.get("cta")])
    with review._lock(job):
        rv = review.load(job)
        for pc in pics:
            dst = sd / "fallback" / f"{pc['id']}.jpg"; dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists(): shutil.copy2(pc["path"], dst)
            ok = coherence.phase_ok(phases.get(bid, ""), pc.get("phase"))
            review._add(rv, bid, {"type": "image", "src": review._rel(dst, sd), "fit": "frame", "photo": pc["id"], "credit": photos.credit(pc),
                                  "short_credit": photos.short_credit(pc), "tag": "FILE"}, "photo", ok,
                        "real photo of the subject" if ok else f"real photo, but it shows the subject {pc.get('phase') or 'at another moment'}")
        rv["beats"][bid]["msg"] = f"{len(pics)} real photo(s) found." if pics else "No real photos found."
        review.save(job, rv)
    review.thumbs(job, cfg, log)


def imagegen_rung(job, bid, cfg, wg, log, payload):
    """One picture from the free image services for the beat's prompt."""
    from . import review
    sp, sec = review._section(job, bid)
    review._busy(job, bid, "Asking the image services for a picture...")
    gen = review._refine_image(job, sec, f"{bid}_b{int(time.time()) % 100000}", cfg, log)
    with review._lock(job):
        rv = review.load(job)
        if gen: review._add(rv, bid, gen[0], "imagegen", True, f"image service ({gen[1]})")
        rv["beats"][bid]["msg"] = "New picture from the image services." if gen else "The image services made no picture (see the log)."
        review.save(job, rv)
    review.thumbs(job, cfg, log)


def wangp_rung(job, bid, cfg, wg, log, payload):
    """A WanGP still (and clip) on this PC for the beat's prompt."""
    from . import review
    sd, _ = review._p(job); sp, sec = review._section(job, bid)
    key = f"{bid}_w{int(time.time()) % 100000}"
    review._busy(job, bid, "WanGP is making a still on this PC (GPU)...")
    st = review._run_slots(job, sec, [key], cfg, wg, log)
    with review._lock(job):
        rv = review.load(job)
        if key in st: review._slot_cands(rv, sec, key, st[key], sd, True, log)
        rv["beats"][bid]["msg"] = "WanGP made new option(s)." if st.get(key, {}).get("still") else "WanGP made nothing (see the log)."
        review.save(job, rv)
    review.thumbs(job, cfg, log)
    try:   # owner: stop WanGP right after use
        from . import wangp as _w; _w.release(log, "beat editor")
    except Exception: pass


def _render_still_shots(src, seconds, move, out, size):
    """A still as camera shots for the whole beat: one move, or (move 'auto') a different move every ~4.5 s joined by dissolves."""
    from . import camera_moves as cm
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True)
    if move == "none":
        media.ff("-loop", 1, "-i", src, "-t", f"{seconds:.3f}", "-vf", f"scale={size[0]}:{size[1]}:force_original_aspect_ratio=increase,"
                 f"crop={size[0]}:{size[1]},fps=30,format=yuv420p", "-c:v", "libx264", "-crf", 16, out)
        return out
    if move != "auto":
        return cm.render_framed(src, seconds, move, out, size=size)
    n = max(1, min(len(MIX), round(seconds / SHOT_SECS)))
    if n == 1: return cm.render_framed(src, seconds, MIX[0], out, size=size)
    xf = cm.XF / cm.FPS
    each = (seconds + (n - 1) * xf) / n
    parts = [cm.render_framed(src, each, MIX[i], out.with_name(f"{out.stem}_m{i}.mp4"), size=size) for i in range(n)]
    ins = [a for p in parts for a in ("-i", p)]
    chain, last = [], "[0:v]"
    for i in range(1, n):
        off = i * (each - xf)
        tag = f"[x{i}]" if i < n - 1 else "[v]"
        chain.append(f"{last}[{i}:v]xfade=transition=fade:duration={xf:.3f}:offset={off:.3f}{tag}"); last = tag
    media.ff(*ins, "-filter_complex", ";".join(chain), "-map", "[v]", "-c:v", "libx264", "-crf", 16, "-pix_fmt", "yuv420p", "-t", f"{seconds:.3f}", out)
    for p in parts: Path(p).unlink(missing_ok=True)
    return out


def shots(job, bid, cfg, wg, log, payload):
    """Camera shots of one still candidate (yours or any picture) for the beat: a new video candidate."""
    from . import review
    sd, rd = review._p(job)
    cid, move = payload.get("cid"), payload.get("move") or "auto"
    if move not in dict(SHOTS): raise ValueError(f"unknown camera shot {move}")
    with review._lock(job):
        rv = review.load(job); c = rv["cands"].get(cid); secs = float(rv["beats"][bid].get("secs") or 6)
    if not c or c["item"]["type"] != "image": raise ValueError("pick a still (a picture) to make camera shots from")
    review._busy(job, bid, f"Rendering camera shots ({dict(SHOTS)[move].lower()}) for {secs:.1f}s...")
    src = sd / c["item"]["src"]
    out = rd / "shots" / f"shot_{bid}_{move}_{int(time.time()) % 100000}.mp4"
    _render_still_shots(src, secs, move, out, review._fsize(sd))
    it = {"type": "video", "src": review._rel(out, sd), "secs": round(media.dur(out), 2), "rate": 1.0,
          "tag": c["item"].get("tag") or "", "camera": move,
          **({"user": True} if c["item"].get("user") else {}), **({"credit": c["item"]["credit"]} if c["item"].get("credit") else {}),
          **({"short_credit": c["item"]["short_credit"]} if c["item"].get("short_credit") else {})}
    with review._lock(job):
        rv = review.load(job)
        nid = review._add(rv, bid, it, c["rung"] if c["rung"] != "current" else "supplied", True, f"{dict(SHOTS)[move]} of {c['why'][:60]}")
        b = rv["beats"][bid]; b["decision"], b["chosen"] = "chosen", [nid]
        b["msg"] = f"Camera shots ready ({dict(SHOTS)[move]}). Preview it in the scrubber, then Splice or Discard."
        review.save(job, rv)
    review.thumbs(job, cfg, log, [nid])


ACTIONS = {"photo": photo_rung, "imagegen": imagegen_rung, "wangp": wangp_rung, "shots": shots}
