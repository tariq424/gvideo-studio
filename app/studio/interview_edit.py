"""Interview jobs in the Video Studio editor (owner, 2026-10-25): the interview edit (interview.py) opened as a job, so the
owner can scrub it, open any piece (a soundbite, a fact, a narration line), see other pictures for it, preview, apply, undo,
and chat about it - instead of another round of automatic versions.

A job made from an interview edit has state kind="interview" and two manifests, iedit/short.json and iedit/long.json
(written by interview.build: every piece in order with its file, captions, and where its picture came from).
Editing a piece changes only its PICTURE: his voice / the narration stays. Candidates for a piece:
  - in the video now; the same, blurred
  - the same clip at other moments that are clean as filmed (no close-up of the camera holder, no finger, not sideways)
  - the creator's other clips filmed at the same place the same day (catalogue GPS + time): the outside of the building,
    the greetings inside, the third floor ... (labelled with the catalogue's description)
  - a file the owner supplies (path on this PC)
Applying re-renders the piece and splices it in (only changed pieces are re-encoded; the first edit of an older video dresses every piece once, ~20 min for the
documentary. "Stop if not sure": pieces whose picture was a guess (blurred, covered from another clip, a fact over the
scene) are marked "check" on the timeline."""
import csv
import json
import math
import re
import shutil
import threading
import time
from pathlib import Path

from . import media

VIDEO = {".mov", ".mp4", ".m4v"}
PHOTO = {".jpg", ".jpeg", ".png"}


def _guess(m, p):
    """A piece whose picture was a guess (mostly blurred / turned on its side) is marked "check" (owner: stop if not sure)."""
    from . import shots
    mm = p.get("m") or {}
    if mm.get("type") not in ("bite", "narr") or mm.get("si") is None: return False
    try:
        an_dir = m["sources"][int(mm["si"])][2]
        an = json.loads((Path(an_dir) / "faces.json").read_text(encoding="utf-8"))
    except Exception: return False
    by = {x["t"]: shots.kind_keep(x) for x in an.get("secs") or []}
    t0 = float(mm.get("t0") or 0); t1 = float(mm.get("t1") or t0 + float(p.get("dur") or 0))
    secs = list(range(int(t0), max(int(t0) + 1, int(t1))))
    bad = sum(1 for t in secs if by.get(t, "none") in ("blur",) or str(by.get(t, "")).startswith("turn"))
    return bad / max(1, len(secs)) >= 0.3


def adopt(job, manifests, log=print):
    """A finished interview build -> this job's outputs and editor manifests. manifests: {"short": parts.json, "long": parts.json}
    (either may be missing). Videos are hard-linked (copied across drives). -> {"short": path, "long": path}"""
    import os
    job = Path(job); outs = {}
    for k, mp in manifests.items():
        if not mp or not Path(mp).exists(): continue
        m = json.loads(Path(mp).read_text(encoding="utf-8"))
        if not Path(m["out"]).exists(): continue
        dst = job / "output" / Path(m["out"]).name; dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and not os.path.samefile(dst, m["out"]):   # a re-run made a new cut: it replaces the old one (Nov 8: output kept the old cut)
            dst.unlink()
        if not dst.exists():
            try: os.link(m["out"], dst)
            except OSError: shutil.copy2(m["out"], dst)
        for p in m["parts"]:
            p["dur"] = media.dur(p["file"]); p["check"] = _guess(m, p)
        (job / "iedit").mkdir(parents=True, exist_ok=True)
        _save_man(job, k, m); outs[k] = str(dst)
        log(f"  {k}: {len(m['parts'])} pieces, {sum(1 for p in m['parts'] if p['check'])} marked check")
    return outs


def is_interview(job):
    from .pipeline import Job
    return Job(job).state.get("kind") == "interview"


def _k(job):
    from .pipeline import Job
    t = Job(job).state.get("edit_target") or "short"
    return "long" if t == "long" else "short"


def _man_p(job, k): return Path(job) / "iedit" / f"{k}.json"
def _man(job, k=None): return json.loads(_man_p(job, k or _k(job)).read_text(encoding="utf-8"))
def _save_man(job, k, m): _man_p(job, k).write_text(json.dumps(m, indent=1, ensure_ascii=False), encoding="utf-8")


def outputs(job):
    return [k for k in ("short", "long") if _man_p(job, k).exists()]


def kind(job): return _k(job)


def set_target(job, target):
    from .pipeline import Job
    if target not in ("short", "long") or not _man_p(job, target).exists(): raise ValueError(f"this job has no {target} video")
    Job(job).save(edit_target=target)


def workspace(job, k=None):
    return Path(job) if (k or _k(job)) == "short" else Path(job) / "longedit"


def _rd(job, k=None):
    sd = workspace(job, k) / "short"; rd = sd / "review"; rd.mkdir(parents=True, exist_ok=True)
    return sd, rd


def _label(p, i):
    m = p.get("m") or {}; t = m.get("type", "")
    if t == "bite": lab = "Soundbite" + (f" · {p['chapter']}" if p.get("chapter") else "")
    elif t == "fact": lab = f"Fact · {p.get('card') or ''}".strip(" ·")
    elif t == "narr": lab = "Narration"
    elif t == "comment": lab = "Your commentary"
    else: lab = "Title / end card"
    return ("⚠ check · " if p.get("check") else "") + lab


def _vo(p):
    m = p.get("m") or {}
    return m.get("text") or " ".join(w[0] for w in (p.get("words") or []))


def _pic_rungs(p):
    """The scrubber's "Made by" for an interview piece: your footage, a title card, a photo, or the ladder rung of an insert."""
    src = _pic_source(p) or ""
    head = src.split(" · ")
    if head[0] == "Ladder" and len(head) > 1:
        from .edit import RUNG_NAMES
        r = head[1].split(": ", 1)[-1]   # "ai_image: AI image (chatgpt) -> image-to-video" -> the readable part
        return [RUNG_NAMES.get(head[1], r)]
    if len(head) == 1 and "_narr" in head[0]: return ["Narration over a picture"]
    return [{"Card": "Title / end card", "Picture": "Narration picture", "Insert": "Insert over blurred footage"}.get(head[0], head[0])] if head[0] else []


def _pic_source(p):
    """Editor-only note (owner, 2026-10-27): where this piece's picture came from - the ladder rung that
    supplied it (read from the sibling <k>_insert.json the build wrote), a photo credit, or the footage
    file. Shown in the editor only; never rendered into the video."""
    m = p.get("m") or {}
    t = m.get("type", "")
    f = Path(p.get("file") or "")
    try:
        if t == "bite":
            return "Your footage · " + Path(m.get("src") or f).name
        if t == "narr":
            if m.get("src"):
                return "Your footage · " + Path(m["src"]).name
            return "Picture · " + f.name if f.name else ""
        if t == "fact":
            rung = credit = ""
            try:
                ins = json.loads(f.with_suffix(".json").read_text(encoding="utf-8"))
                rung, credit = str(ins.get("rung") or ""), str(ins.get("credit") or "")
            except Exception:
                pass
            credit = credit or str(p.get("credit") or "")
            if rung:
                return f"Ladder · {rung} · {f.name}"
            if credit:
                return f"Photo · {credit} · {f.name}"
            return f"Insert · {f.name} (over blurred footage)"
        if t == "card":
            return "Card · " + f.name
        if t == "comment":
            return "Your commentary · " + Path(m.get("src") or f).name
    except Exception:
        pass
    return f.name


def _built_stretches(f):
    """What the build recorded for one piece file: [(a, b, how, credit)] in the piece's own time, or None if nothing is recorded.
    A soundbite keeps NN_bite.json (each covered stretch and the rung that made it); a narration line its NN_narr[_re]_cov.json;
    a fact its NN_insert.json."""
    f = Path(f); stem = f.stem
    try:
        if re.fullmatch(r"\d+_(bite|pip)", stem):
            j = f.with_name(stem.split("_")[0] + "_bite.json")
            if j.exists(): return [tuple(x) for x in json.loads(j.read_text(encoding="utf-8"))]
        if re.fullmatch(r"\d+_narr", stem):
            for j in (f.with_name(stem + "_re_cov.json"), f.with_name(stem + "_cov.json")):
                if j.exists():
                    x = json.loads(j.read_text(encoding="utf-8")); return [(0.0, 9e9, x.get("how", ""), x.get("credit", ""))]
            if f.with_name(stem + "_cov.mp4").exists() or f.with_name(stem + "_re_cov.mp4").exists():
                return [(0.0, 9e9, "a ladder picture (rung not recorded)", "")]
        if f.with_suffix(".json").exists() and stem.endswith("_insert"):
            x = json.loads(f.with_suffix(".json").read_text(encoding="utf-8"))
            if x.get("rung") or x.get("credit"): return [(0.0, 9e9, str(x.get("rung") or "photo"), str(x.get("credit") or ""))]
    except Exception: pass
    return None


_LOGS = {}


def _rung_log(job):
    """What the job's build logs say about each ladder picture (builds before Nov 10 kept only the rung's name):
    [(piece stem or "", source second or None, {"detail", "prompt", "parallax"})] in log order."""
    out, key = [], []
    files = [Path(job) / n for n in ("log.txt", "refresh.log") if (Path(job) / n).exists()]
    sig = tuple((str(f), f.stat().st_mtime_ns) for f in files)
    if _LOGS.get(str(job), (None,))[0] == sig: return _LOGS[str(job)][1]
    for f in files:
        cur, ctx = "", {}
        try: lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError: continue
        for ln in lines:
            s = re.sub(r"^\[[0-9:]+\]\s*", "", ln).strip()
            mm = re.match(r"(\d\d_(?:bite|narr|pip|insert))\.mp4[: ]", s)
            if mm: cur = mm.group(1)
            mm = re.match(r"(\w+): prompt \(\d+ words\): \"(.*)\"?$", s)
            if mm: ctx["prompt"] = mm.group(2).rstrip('"'); ctx["provider"] = mm.group(1); continue
            mm = re.match(r"parallax: (.*)$", s)
            if mm: ctx["parallax"] = mm.group(1); continue
            mm = re.match(r"ladder (\w+): OK - (.*)$", s)
            if mm: ctx["detail"] = mm.group(2); ctx["rung"] = mm.group(1); continue
            if re.match(r"ladder \w+: no - |picture dropped|sketch failed", s): ctx = {}; continue   # (a failed try's prompt is not the picture's)
            mm = re.match(r"(\d\d_narr)\.mp4 -> (.+)$", s)   # a narration line's picture: whatever made it
            if mm:
                r = re.match(r"rung (\w+)", mm.group(2))
                d = dict(ctx) if r and ctx.get("rung") == r.group(1) else {}
                d["how"] = ("rung " + r.group(1)) if r else mm.group(2)
                out.append((mm.group(1), None, d)); ctx = {}; continue
            mm = re.match(r"(\d+)-(\d+)s(?:: [^>]*)?\s*-> rung (\w+)", s)
            if mm:
                if ctx.get("detail") and ctx.get("rung") == mm.group(3): out.append((cur, float(mm.group(1)), dict(ctx)))
                ctx = {}
    _LOGS[str(job)] = (sig, out)
    return out


def _log_detail(job, stem, src_t):
    """The latest logged detail for a ladder picture of piece `stem` at source second `src_t` (None: the whole piece)."""
    if src_t is None:
        return next((d for cur, t, d in reversed(_rung_log(job)) if cur == stem and t is None), None)
    near = [(cur, d) for cur, t, d in _rung_log(job) if t is not None and abs(t - src_t) <= 1.5]
    return next((d for cur, d in reversed(near) if cur == stem), near[-1][1] if near else None)   # (this piece's line first)


def _stretches(m, i, p, d, job=None):
    """Owner, 2026-11-10 ("I don't see the source of the clip, e.g. stock video or a particular rung that was fired"): each
    stretch of a piece with how its picture was made -> [{"a", "b", "how", "credit"}] (piece time). Follows the piece back through
    its edits to the build's record; a picture picked in the editor covers the whole piece; a defect-report fix its stretch."""
    bid = f"p{i:02}"
    hist = [h for h in (m.get("history") or []) if h.get("bid") in (bid, None) and not h.get("op")]
    rec, f, fixes = None, p["file"], []
    for h in reversed(hist):   # newest first: what each edit replaced (Undo pops the history, so this stays true)
        if h.get("new") != f: continue
        why = str(h.get("why") or "")
        if not why:   # a picture picked in the editor: the whole piece
            rec = [(0.0, d, "picked in the editor", "")]; break
        if h.get("stretch"): fixes.append(h["stretch"])   # a defect-report fix; any other scripted change: a splice over the piece
        f = h.get("old") or f
    if rec is None: rec = _built_stretches(f) or []
    out = [{"a": round(max(0.0, float(a)), 2), "b": round(min(d, float(b)), 2), "how": str(how), "credit": str(cr)} for a, b, how, cr in rec
           if float(b) > float(a)]
    for a, b, c in [tuple(x[:3]) for x in p.get("credits") or []]:   # credits the record doesn't explain (a splice laid by hand)
        if not any(x["a"] < float(b) and x["b"] > float(a) for x in out):
            out.append({"a": round(float(a), 2), "b": round(min(d, float(b)), 2), "how": str(c), "credit": str(c)})
    for s in reversed(fixes):   # defect-report fixes, oldest first, laid over what was there
        out = [x for x in out if not (x["a"] < s["b"] and x["b"] > s["a"])] + [dict(s)]
    t0 = (p.get("m") or {}).get("t0")
    for x in out:   # owner, Nov 10: "was it Cloudflare? Google Flow? parallax from a snapped frame?" - the provider and method
        how = x["how"]
        if how.endswith("(rung not recorded)") and job is not None:   # a narration line built before its rung was saved
            got = _log_detail(job, Path(f).stem, None)
            if got:
                x.update({k: got[k] for k in ("detail", "prompt", "parallax", "provider", "rung") if got.get(k)}); x["how"] = got["how"]
                if not got["how"].startswith("rung "): x["credit"] = (re.search(r"\((.*)$", got["how"]) or [None, ""])[1].rstrip(")")
            continue
        if ":" in how and how.startswith("rung "):
            x["rung"], x["detail"] = how[5:].split(":", 1)[0], how.split(":", 1)[1].strip()
        elif how.startswith("rung ") and job is not None:
            stem = Path(f).stem
            got = _log_detail(job, stem, None) if stem.endswith("_narr") else _log_detail(job, stem, float(t0) + x["a"]) if t0 is not None else None
            if got: x.update(rung=how[5:], detail=got.get("detail", ""), **{k: got[k] for k in ("prompt", "parallax", "provider") if got.get(k)})
    return sorted(out, key=lambda x: x["a"])


def beats(job):
    k = _k(job); m = _man(job, k); out, t = [], 0.0
    for i, p in enumerate(m["parts"]):
        d = float(p.get("dur") or media.dur(p["file"]))
        out.append({"id": f"p{i:02}", "n": i + 1, "start": round(t, 3), "dur": round(d, 3), "label": _label(p, i), "vo": _vo(p)[:600],
                    "stretches": _stretches(m, i, p, d, job),
                    "editable": (p.get("m") or {}).get("type") in ("bite", "fact", "narr"), "own": True, "source": _pic_source(p),
                    "rungs": _pic_rungs(p), "fade": p.get("fade") or {},
                    **({"voice": p.get("voice") or {}} if (p.get("m") or {}).get("type") == "comment" else {})})
        b = out[-1]; ss = b["stretches"]
        if (p.get("m") or {}).get("type") == "narr" and ss and ss[0]["a"] <= 0.5 and ss[-1]["b"] >= d - 0.5:   # (Nov 10: a Commons
            s = ss[0]; b["source"] = "Ladder · " + (s.get("credit") or s.get("detail") or s["how"])            # photo said "Your footage")
            b["rungs"] = [s["how"]]
        t += d
    try:
        from .pipeline import Job
        mine = bool((Job(job).state.get("interview") or {}).get("my_voice"))
    except Exception: mine = False
    return {"kind": "short" if k == "short" else "long", "beats": out, "my_voice": mine}


def _part(job, bid, k=None):
    m = _man(job, k); i = int(bid[1:]); return m, i, m["parts"][i]


# ---------------------------------------------------------------- candidates
def _keep_kinds(an_dir):
    from . import shots
    try: an = json.loads((Path(an_dir) / "faces.json").read_text(encoding="utf-8"))
    except Exception: return {}
    return {x["t"]: shots.kind_keep(x) for x in an.get("secs") or []}


def _same_clip(m, p, d, n=5, reach=150.0):
    """Clean windows of the piece's own clip near it: [(start, label)]."""
    mm = p.get("m") or {}
    if mm.get("src") is None or mm.get("si") is None: return []
    src, fl, an_dir = m["sources"][int(mm["si"])]
    by = _keep_kinds(an_dir)
    if not by: return []
    dur = media.dur(src); t0 = float(mm.get("t0") or 0); t1 = float(mm.get("t1") or t0 + d)
    ok = lambda a: a >= 0 and a + d <= dur - 0.2 and not (a < t1 and a + d > t0) and all(by.get(t, "none") in ("none", "guest") for t in range(int(a), int(a + d) + 1))
    out, step = [], 0
    while len(out) < n and step <= reach:
        for a in (t0 - d - step, t1 + step):
            if ok(a) and all(abs(a - x) > d for x, _ in out): out.append((a, f"Same clip at {int(a // 60)}:{int(a % 60):02}, clean as filmed"))
        step += 5
    return [(src, fl, a, lab) for a, lab in out[:n]]


def _place_clips(m, p, n=14, radius=250.0):
    """The creator's other clips at the same place the same day, from the folder's catalogue."""
    mm = p.get("m") or {}
    src = Path(mm["src"]) if mm.get("src") else Path(m["sources"][0][0])
    lab_dir = Path(m["sources"][0][0]).parent / "labeled"
    if not (lab_dir / "index.csv").exists() or not m.get("gps"): return []
    la, lo = m["gps"]
    desc = {}
    try:
        for r in csv.DictReader(open(lab_dir / "catalog.csv", encoding="utf-8-sig")):
            desc[Path(r["original"].replace("\\", "/")).name] = r
    except Exception: pass
    rows = list(csv.DictReader(open(lab_dir / "index.csv", encoding="utf-8-sig")))
    day = next((r["taken"][:10] for r in rows if r["file"] == Path(m["sources"][0][0]).name), "")
    used = {Path(x[0]).name for x in m["sources"]}
    out, seen = [], set()
    for r in rows:
        f = Path(m["sources"][0][0]).parent / r["file"]
        if f.suffix.lower() not in VIDEO | PHOTO or f.name == src.name or (day and r["taken"][:10] != day): continue
        try: dist = 6371000 * math.hypot(math.radians(float(r["lat"]) - la), math.radians(float(r["lon"]) - lo) * math.cos(math.radians(la)))
        except ValueError: continue
        if dist > radius: continue
        c = desc.get(r["file"], {})
        if str(c.get("face_closeup", "")).lower() == "true": continue
        key = (r["taken"][:16], r["seconds"])
        if key in seen: continue   # an IMG_ copy and its named copy
        seen.add(key)
        d = (c.get("description") or r.get("place") or "").split(" / ")[0][:90]
        flip = f.name in used and next((bool(x[1]) for x in m["sources"] if Path(x[0]).name == f.name), False)
        out.append((f, flip, 1.0 if f.suffix.lower() in VIDEO else 0.0, f"{r['taken'][11:16]} · {f.name} · {d}"))
    return out[:n]


def _thumb(item, out, kind):
    from . import shots
    W, H = (270, 480) if kind == "short" else (480, 270)
    f = Path(item["file"])
    if f.suffix.lower() in PHOTO:
        media.ff("-i", f, "-frames:v", 1, "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}", out, timeout=60)
    else:
        vf = shots.vf_frame("short" if kind == "short" else "documentary", None, 0.0, bool(item.get("flip")), blur=bool(item.get("blur")))
        media.ff("-ss", f"{float(item.get('start', 0)) + min(1.0, float(item.get('dur', 2)) / 2):.2f}", "-i", f, "-frames:v", 1, "-vf", vf + f",scale={W}:{H}", out, timeout=60)


def open_beat(job, bid, cfg, log):
    from . import review
    job = Path(job); k = _k(job); ws = workspace(job, k); sd, rd = _rd(job, k)
    with review._lock(ws):
        rv = review.load(ws) or {"mode": "edit", "status": "closed", "next": 1, "beats": {}, "cands": {}}
        m, i, p = _part(job, bid, k)
        d = float(p.get("dur") or media.dur(p["file"]))
        b = rv["beats"].get(bid)
        if not b or b.get("file") != p["file"]:   # first open, or the piece changed: build its candidates
            b = rv["beats"][bid] = {"vo": _vo(p), "secs": d, "auto": [], "cands": [], "decision": "auto", "chosen": [], "uncertain": bool(p.get("check")),
                                    "why": "", "history": [], "busy": "", "msg": "", "notes": [], "file": p["file"], "current": []}
            def add(rung, item, why):
                cid = f"{bid}_{len(b['cands'])}"
                th = rd / "thumbs" / f"{cid}.jpg"; th.parent.mkdir(exist_ok=True)
                try: _thumb(dict(item, dur=d), th, k)
                except Exception: return None
                rv["cands"][cid] = {"id": cid, "beat": bid, "rung": rung, "ok": True, "why": why, "item": item, "thumb": review._rel(th, sd),
                                    "secs": d, "key": f"{item.get('file')}|{item.get('start', 0)}|{item.get('blur', False)}"}
                b["cands"].append(cid); return cid
            cur = add("current", {"file": p["file"], "start": 0.0, "flip": False, "part": True}, "in the video now")
            b["current"] = [cur] if cur else []; b["auto"] = list(b["current"])
            add("own", {"file": p["file"], "start": 0.0, "flip": False, "part": True, "blur": True}, "the same, blurred")
            for f, fl, a, lab in _same_clip(m, p, d): add("own", {"file": str(f), "start": a, "flip": fl}, lab)
            for f, fl, a, lab in _place_clips(m, p): add("own", {"file": str(f), "start": a, "flip": fl}, lab)
            review.save(ws, rv)
        return rv


def supply(job, bid, src, space=None):
    from . import review
    job = Path(job); k = _k(job); ws = workspace(job, k); sd, rd = _rd(job, k)
    f = Path(src.strip().strip('"'))
    if not f.is_file(): raise ValueError(f"File not found on this PC: {f}")
    rv = open_beat(job, bid, {}, print); b = rv["beats"][bid]
    cid = f"{bid}_u{len(b['cands'])}"; th = rd / "thumbs" / f"{cid}.jpg"; th.parent.mkdir(exist_ok=True)
    item = {"file": str(f), "start": 0.0, "flip": False, "user": True}
    _thumb(dict(item, dur=b["secs"]), th, k)
    rv["cands"][cid] = {"id": cid, "beat": bid, "rung": "supplied", "ok": True, "why": "your file", "item": item, "thumb": review._rel(th, sd), "secs": b["secs"], "key": str(f)}
    b["cands"].append(cid); b["chosen"] = [cid]
    review.save(ws, rv)
    return rv


# ---------------------------------------------------------------- rendering a piece with a new picture
SLOW_MAX = 1.5   # a clip a little short of its slot is slowed down to fill it, at most this much


def _user_turn(f, work):
    """Degrees clockwise that stand your own clip up (0 = as it is). Three frames, each shown to the local vision model turned
    four ways side by side ("which copy is the right way up?" - far surer than asking about one frame); two must agree.
    Cached per file."""
    from collections import Counter
    from . import shots, interview, footage, config
    from .llm import parse_json
    f = Path(f); cache = Path(work) / "user_turns.json"
    try: memo = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    except (OSError, ValueError): memo = {}
    key = f"{f}|{f.stat().st_size}"
    if key in memo: return memo[key]
    cv2, np = shots._cv(); cfg = config.load(); picks = []
    if cv2 is None: return 0
    d = media.dur(f) or 1.0; fi, gi = Path(work) / "_ut.jpg", Path(work) / "_ut4.jpg"
    for k in range(3):
        try:
            media.ff("-ss", f"{d * (k + 0.5) / 3:.2f}", "-i", f, "-frames:v", 1, "-vf", "scale=640:-2", fi, timeout=120)
            im = cv2.imread(str(fi))
            if im is None: continue
            if shots._faces(cv2, cv2.cvtColor(im, cv2.COLOR_BGR2GRAY), im): picks.append(0); continue   # a face reads as it is: upright
            order = [0, 90, 180, 270][k % 4:] + [0, 90, 180, 270][:k % 4]
            cv2.imwrite(str(gi), interview._four_turns(cv2, np, im, order))
            pick = str(parse_json(footage._vision(dict(cfg, vet_provider="local"), gi, interview.Ladder.ORIENT)).get("upright", "")).strip()[:1].upper()
            if pick in ("A", "B", "C", "D"): picks.append(order["ABCD".index(pick)])
        except Exception as e:
            if str(e) == "cancelled": raise
    deg, n = Counter(picks).most_common(1)[0] if picks else (0, 0)
    deg = deg if n >= 3 else 0   # (a wrong turn ruins a good clip: all three must agree - Nov 10, an upright selfie read as sideways)
    memo[key] = deg
    try: cache.write_text(json.dumps(memo, indent=1), encoding="utf-8")
    except OSError: pass
    return deg


def _lens_blob(cv2, np, im):
    """1.0 when a big skin-coloured, out-of-focus blob touches the frame edge (a finger over the lens, the filmer's chin), else
    0.0 - grading by how much of the frame it covers (6 % starts to count, 20 % is full)."""
    ycc = cv2.cvtColor(im, cv2.COLOR_BGR2YCrCb)
    skin = cv2.inRange(ycc, (20, 135, 80), (235, 180, 135))
    g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    soft = (cv2.blur(np.abs(cv2.Laplacian(g, cv2.CV_32F)), (7, 7)) < 6).astype("uint8") * 255   # little texture: out of focus
    m = cv2.morphologyEx(cv2.bitwise_and(skin, soft), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m)
    h, w = m.shape; worst = 0.0
    for i in range(1, n):
        x, y, bw, bh, a = st[i]
        if x == 0 or y == 0 or x + bw >= w or y + bh >= h: worst = max(worst, a / float(h * w))
    return max(0.0, min(1.0, (worst - 0.06) / 0.14))


def _best_window(f, share, step=0.5):
    """Owner, 2026-11-10 ("vid gets cut to fit intelligently"): where in a clip longer than its slot to start -> seconds.
    Frames every `step` s are scored for sharpness, light and steadiness; the first / last half-second (pressing record,
    lowering the phone) count against; the window with the best average wins."""
    from . import shots
    dur = media.dur(f)
    if dur <= share + 0.2: return 0.0
    cv2, np = shots._cv()
    if cv2 is None: return min(0.5, dur - share)
    n = int(dur / step); step = dur / max(1, n) if n > 240 else step; n = min(n, 240)
    cap = cv2.VideoCapture(str(f)); sc, prev = [], None
    for k in range(n):
        cap.set(cv2.CAP_PROP_POS_MSEC, k * step * 1000); ok, im = cap.read()
        if not ok: sc.append(-1.0); continue
        g = cv2.cvtColor(cv2.resize(im, (160, 90)), cv2.COLOR_BGR2GRAY).astype("float32")
        sharp = min(1.0, float(cv2.Laplacian(g, cv2.CV_32F).var()) / 300.0)
        light = 1.0 - abs(float(g.mean()) - 120.0) / 120.0
        shake = 0.0 if prev is None else min(1.0, float(np.abs(g - prev).mean()) / 40.0)
        prev = g; edge = 0.6 if k * step < 0.5 or k * step > dur - 0.5 else 0.0
        sc.append(0.45 * sharp + 0.35 * max(0.0, light) - 0.35 * shake - edge - 0.9 * _lens_blob(cv2, np, cv2.resize(im, (160, 90))))
    cap.release()
    w = max(1, int(round(share / step)))
    if len(sc) <= w: return 0.0
    cs = np.cumsum([0.0] + sc); best = max(range(len(sc) - w + 1), key=lambda i: (cs[i + w] - cs[i]) / w)
    return round(min(best * step, dur - share), 2)


def _fit_clip(f, share, st, vf, seg, scale_ok=True):
    """A user clip fitted to exactly `share` seconds, muted: its best window if longer; slowed (up to SLOW_MAX) if a little
    short; played forward then back (no jump) if much shorter."""
    dur = media.dur(f) or share
    enc = ["-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
    if dur >= share - 0.05:
        media.ff("-ss", f"{st:.2f}", "-i", f, "-t", f"{share:.3f}", "-vf", vf, *enc, seg, timeout=900); return "best window"
    if share / dur <= SLOW_MAX:
        media.ff("-i", f, "-vf", f"setpts={share / dur:.4f}*PTS," + vf, "-t", f"{share:.3f}", "-r", "30", *enc, seg, timeout=900)
        return f"slowed {share / dur:.2f}x"
    # (Nov 10: the app's ffmpeg ignores a phone's rotation tag inside -filter_complex - an upright selfie came out upside down.
    # The clip is framed once with -vf, which honours it, and the joins are made from that copy.)
    base = Path(seg).with_name(Path(seg).stem + "_base.mp4")
    media.ff("-i", f, "-vf", vf + ",fps=30", *enc, base, timeout=900)
    f, vf = base, "null"
    if dur <= 6.0:   # (a reverse holds every frame in memory: short clips only)
        media.ff("-i", f, "-filter_complex", f"[0:v]{vf},split[a][b];[b]reverse[r];[a][r]concat=n=2:v=1:a=0,loop=-1:size=32767:start=0[v]",
                 "-map", "[v]", "-t", f"{share:.3f}", *enc, seg, timeout=900)
        base.unlink(missing_ok=True)
        return "forward and back"
    x = 0.6; n = math.ceil((share - x) / (dur - x)) + 1   # longer: played again with a soft dissolve at each join
    fc = "".join(f"[{i}:v]{vf},fps=30,settb=AVTB[c{i}];" for i in range(n))
    last, off = "c0", dur - x
    for i in range(1, n):
        fc += f"[{last}][c{i}]xfade=transition=fade:duration={x}:offset={off:.3f}[x{i}];"; last = f"x{i}"; off += dur - x
    media.ff(*[a for _ in range(n) for a in ("-i", f)], "-filter_complex", fc.rstrip(";"), "-map", f"[{last}]", "-t", f"{share:.3f}", *enc, seg, timeout=900)
    base.unlink(missing_ok=True)
    return "repeated with dissolves"


def _picture(job, items, d, out, kind, scale=1.0):
    """Video-only file of length d from the chosen items (played in order, each an equal share)."""
    from . import shots
    W, H = (1080, 1920) if kind == "short" else (1920, 1080)
    W, H = int(W * scale) // 2 * 2, int(H * scale) // 2 * 2
    segs, share = [], d / max(1, len(items))
    for n, it in enumerate(items):
        f = Path(it["file"]); seg = Path(out).with_name(Path(out).stem + f"_{n}.mp4")
        if f.suffix.lower() in PHOTO:
            nfr = int(share * 30) + 1
            vf = (f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
                  f"zoompan=z='min(zoom+0.0006,1.10)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={nfr}:s={W}x{H}:fps=30,format=yuv420p,setsar=1")
            media.ff("-loop", 1, "-i", f, "-t", f"{share:.2f}", "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", seg, timeout=900)
        else:
            rot = "" if it.get("part") or it.get("flip") else shots.orient(f, Path(job) / "iedit", print)
            if it.get("user") and not rot:   # your own clip filmed upside down / sideways with no rotation tag: one look to stand it up
                rot = {90: "transpose=1", 270: "transpose=2", 180: "hflip,vflip"}.get(_user_turn(f, Path(job) / "iedit"), "")
            vf = (rot + "," if rot else "") + shots.vf_frame("short" if kind == "short" else "documentary", None, 0.0, bool(it.get("flip")), blur=bool(it.get("blur")))
            if scale != 1.0: vf += f",scale={W}:{H}"
            if it.get("user"):   # your own clip: the best part of it, exactly the slot, its sound off (the narration stays)
                _fit_clip(f, share, _best_window(f, share), vf, seg); segs.append(seg); continue
            st = float(it.get("start", 0)); loop = ["-stream_loop", "-1"] if media.dur(f) < st + share else []
            media.ff(*loop, "-ss", f"{st:.2f}", "-i", f, "-t", f"{share:.2f}", "-an", "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                     "-pix_fmt", "yuv420p", seg, timeout=900)
        segs.append(seg)
    if len(segs) == 1: shutil.move(str(segs[0]), str(out)); return out
    lst = Path(out).with_suffix(".txt"); lst.write_text("".join(f"file '{s.as_posix()}'\n" for s in segs), encoding="utf-8")
    media.ff("-f", "concat", "-safe", 0, "-i", lst, "-c", "copy", out, timeout=900)
    for s in segs: s.unlink(missing_ok=True)
    return out


def _render_piece(job, bid, cids, out, scale=1.0):
    from . import review
    k = _k(job); ws = workspace(job, k); rv = review.load(ws)
    m, i, p = _part(job, bid, k); d = float(p.get("dur") or media.dur(p["file"]))
    items = [rv["cands"][c]["item"] for c in cids if c in rv["cands"]]
    if not items: raise ValueError("pick at least one picture")
    if len(items) == 1 and items[0].get("part") and not items[0].get("blur"): shutil.copy2(p["file"], out); return out, d
    pic = Path(out).with_name(Path(out).stem + "_pic.mp4")
    _picture(job, items, d, pic, k, scale)
    media.ff("-i", pic, "-i", p["file"], "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "copy", "-t", f"{d:.3f}", out, timeout=900)
    pic.unlink(missing_ok=True)
    return out, d


def preview_cand(job, cid, space=None):
    """A 2-second look at one candidate (the Candidates pane's thumbnail click)."""
    from . import review
    k = "long" if space == "long" else "short"; ws = workspace(job, k); sd, rd = _rd(job, k); rv = review.load(ws); c = rv["cands"][cid]
    out = rd / "prev" / f"{cid}.mp4"; out.parent.mkdir(exist_ok=True)
    if not media.ok(out, 0.3): _picture(job, [dict(c["item"], start=float(c["item"].get("start", 0)))], 2.0, out, k, 0.5)
    return {"video": review._rel(out, sd)}


def preview(job, bid, cids, fit, cfg, log):
    out = Path(job) / "iedit" / "prev" / f"{_k(job)}_{bid}.mp4"; out.parent.mkdir(parents=True, exist_ok=True)
    _render_piece(job, bid, cids, out, scale=0.5)
    return {"video": str(out.relative_to(Path(job))).replace("\\", "/"), "fit": "as filmed", "why": "his voice / the narration stays; only the picture changes"}


def _eta(job, k):
    """How long applying takes: only the changed piece is re-encoded once every piece has been dressed (work/dressed)."""
    d = Path(job) / "iedit" / f"{k}_work" / "dressed"
    if d.is_dir() and any(d.glob("*.mp4")): return " (only the changed piece is re-encoded: about a minute)"
    return " (the first edit dresses every piece once: ~20 min for the documentary; later edits about a minute)" if k == "long" else " (about a minute)"


def _assemble(job, k, cfg, log):
    from .pipeline import Job
    from . import interview
    m = _man(job, k); s = Job(job).state
    out = Path(s["short_output" if k == "short" else "output"])
    work = Path(job) / "iedit" / f"{k}_work"; work.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".new.mp4")
    parts = [dict(p, words=[tuple(w) for w in p.get("words") or []], titles=[tuple(t) for t in p.get("titles") or []],
                  credits=[tuple(c) for c in p.get("credits") or []]) for p in m["parts"]]
    interview.assemble(parts, tmp, "short" if k == "short" else "documentary", work, dict(cfg, **(m.get("cfg") or {})), log, m.get("title", ""))
    prev = out.with_suffix(".prev")
    if out.exists(): shutil.copy2(out, prev)
    for n in range(20):
        try: tmp.replace(out); break
        except PermissionError:
            if n == 19: raise RuntimeError("Can't replace the video file — it's open in another app. Close the player and retry.")
            time.sleep(1)
    try:   # owner, 2026-11-09: the edited video goes to Google Photos too, once the edits settle
        from . import cloud_backup
        cloud_backup.queue(job, delay=cloud_backup.EDIT_SETTLE)
    except Exception: pass


def start_apply(job, bid, cids, fit, cfg, log):
    from . import edit
    key = str(Path(job))
    with edit._lk:
        if edit._busy.get(key): return "Already applying an edit to this video."
        k = _k(job)
        edit._busy[key] = f"Applying the new picture to {bid}" + _eta(job, k)
        edit._busy.pop(key + ":err", None)
    def run():
        from .pipeline import Job
        from . import review, gpu_lock, editor_session
        jid = Path(job).name
        editor_session.apply_started(jid)   # the 15-min idle release never fires mid-apply
        gpu_lock.acquire(desc=f"editor-apply:{jid}:{bid}")   # one GPU user machine-wide (released in finally)
        try:
            m, i, p = _part(job, bid, k)
            n = len(list((Path(job) / "iedit" / "parts").glob(f"{k}_{bid}_*.mp4"))) if (Path(job) / "iedit" / "parts").exists() else 0
            new = Path(job) / "iedit" / "parts" / f"{k}_{bid}_{n + 1}.mp4"; new.parent.mkdir(parents=True, exist_ok=True)
            _render_piece(job, bid, cids, new)
            hist = m.setdefault("history", []); hist.append({"bid": bid, "old": p["file"], "new": str(new), "t": time.time()})
            p["file"] = str(new); p["check"] = False; m["parts"][i] = p; _save_man(job, k, m)
            _assemble(job, k, cfg, log)
            ws = workspace(job, k); rv = review.load(ws)
            if rv and bid in rv["beats"]: rv["beats"].pop(bid); review.save(ws, rv)   # candidates rebuilt around the new piece on next open
            Job(job).save(edit_last={"beat": bid, "ok": True, "msg": "Applied.", "t": time.time()})
            log(f"Edit {bid} applied ({k})")
        except Exception as e:
            msg = str(e)[:300]; edit._busy[key + ":err"] = msg; log(f"Edit {bid} NOT applied: {msg}")
            try: Job(job).save(edit_last={"beat": bid, "ok": False, "msg": msg, "t": time.time()})
            except Exception: pass
        finally:
            edit._busy.pop(key, None)
            gpu_lock.release()
            editor_session.apply_finished(jid)
    threading.Thread(target=run, daemon=True).start()
    return ""


def can_undo(job):
    try: return bool(_man(job).get("history"))
    except Exception: return False


def undo(job, log, cfg=None):
    from . import config
    k = _k(job); m = _man(job, k); h = m.get("history") or []
    if not h: raise ValueError("nothing to undo")
    last = h.pop(); i = int(last["bid"][1:])
    if last.get("old_part"):   # a whole piece came back (your voice on / off)
        m["parts"][i] = last["old_part"]
    elif last.get("op") == "insert":   # your commentary out again; the pieces after it move back
        m["parts"].pop(i)
        for e in h:
            if int(e["bid"][1:]) > i: e["bid"] = f"p{int(e['bid'][1:]) - 1:02}"
    else:
        m["parts"][i]["file"] = last["old"]
        if "fade_old" in last: m["parts"][i]["fade"] = last["fade_old"]
    _save_man(job, k, m)
    _assemble(job, k, cfg or config.load(), log)


# ---------------------------------------------------------------- chat about a piece
CHAT = """You help the owner edit one piece of his interview video in an editor. The piece's audio (his voice or a narration
line) stays; only the picture can change. The words in this piece: {vo}
Pictures available for it (id: what it is):
{cands}
In the video now: {cur}
The owner says: {text}
Earlier in this conversation: {past}
Reply with JSON only: {{"reply": "short plain answer", "select": [ids to show, in order, or []], "apply": true|false}}
- "select" only ids from the list; pick the ones that match what he asks (e.g. "the outside of the building" -> the clip whose
  description says building / exterior; "where I greet people" -> greetings / salaam / prayer hall with people).
- "apply": true only when he clearly says to apply / use it / do it now. If nothing in the list fits, say so and suggest
  he supply a file (path on the PC) - never pretend."""


def chat(job, bid, text, cfg):
    from .pipeline import Job
    from . import review
    from .llm import LLM
    j = Job(job); k = _k(job); rv = open_beat(job, bid, cfg, j.log); b = rv["beats"][bid]
    cands = "\n".join(f"{c}: {rv['cands'][c]['why']}" for c in b["cands"])
    past = [e for e in (j.state.get("debug") or {}).get("chat", []) if e.get("beat") == bid][-4:]
    with LLM(dict(cfg), j.log, lambda: False) as llm:
        r = llm.chat_json("You are a careful video editor's assistant. Reply with JSON only.",
                          CHAT.format(vo=b["vo"][:600], cands=cands, cur=", ".join(b["current"]), text=text,
                                      past=" | ".join(f"you: {e['you']} -> {e['reply']}" for e in past) or "(none)"), max_tokens=400)
    sel = [c for c in (r.get("select") or []) if c in rv["cands"]]
    action = "none"
    reply = str(r.get("reply") or "")
    if sel and r.get("apply"):
        err = start_apply(job, bid, sel, "auto", cfg, j.log)
        action = "apply"
        if err: reply += f"\n(Couldn't do it: {err})"
    elif sel: action = "select"
    ent = {"t": time.strftime("%H:%M"), "beat": bid, "you": text, "reply": reply, "backend": "deepseek", "action": action, "kind": "interview"}
    if sel: ent["select"] = sel
    d = j.state.get("debug") or {}; d.setdefault("chat", []).append(ent); j.save(debug=d)
    return ent


# ---------------------------------------------------------------- your own commentary, fades (owner, 2026-11-09)
FADE = 0.6   # seconds of a piece's fade in / out (picture from / to black, sound with it)


def _background(job, busy, bid, work, cfg, log):
    """Run work() (changes the manifest), then re-assemble the video, in the background like an applied picture."""
    from . import edit
    key = str(Path(job))
    with edit._lk:
        if edit._busy.get(key): return "Already applying an edit to this video."
        edit._busy[key] = busy + _eta(job, _k(job))
        edit._busy.pop(key + ":err", None)
    def run():
        from .pipeline import Job
        from . import gpu_lock, editor_session
        jid = Path(job).name
        editor_session.apply_started(jid)
        gpu_lock.acquire(desc=f"editor-apply:{jid}:{bid}")
        try:
            msg = work()
            _assemble(job, _k(job), cfg, log)
            Job(job).save(edit_last={"beat": bid, "ok": True, "msg": msg, "t": time.time()})
            log(f"Edit {bid}: {msg}")
        except Exception as e:
            msg = str(e)[:300]; edit._busy[key + ":err"] = msg; log(f"Edit {bid} NOT applied: {msg}")
            try: Job(job).save(edit_last={"beat": bid, "ok": False, "msg": msg, "t": time.time()})
            except Exception: pass
        finally:
            edit._busy.pop(key, None); gpu_lock.release(); editor_session.apply_finished(jid)
    threading.Thread(target=run, daemon=True).start()
    return ""


def _has_audio(f):
    import subprocess
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(f)],
                           capture_output=True, text=True, timeout=60)
        return bool(r.stdout.strip())
    except Exception:
        return True


ON_CAMERA = 0.3   # share of a recording's seconds with a face in it for "you are on screen"


def _on_camera(clip, log=print):
    """Is the owner on screen in this recording? (faces in >= ON_CAMERA of its seconds)"""
    from . import shots
    try:
        wd = Path(clip).with_suffix(""); wd.mkdir(exist_ok=True)
        secs = shots.analyze(clip, wd, False, log).get("secs") or []
        return bool(secs) and sum(1 for s in secs if s.get("faces")) >= ON_CAMERA * len(secs)
    except Exception as e:
        log(f"  commentary: face check failed ({str(e)[:80]}); taken as on camera"); return True


def voice_label(on_camera, words):
    """Owner, 2026-11-09, audio routing: off (not on screen: narration / B-roll) | on_silent (on screen, not speaking) |
    on_speaking (on screen and speaking)."""
    if not on_camera: return "off"
    return "on_speaking" if words else "on_silent"


def _revoice(cam, text, out, k, cfg, log=print):
    """The voiceover override (rules 1 and 3): your words read in your US voice over the recording's picture, the camera
    audio dropped, no lip-sync attempted; the picture holds its last frame if the voice runs longer. -> caption words."""
    from . import interview, tts, captions
    wav = Path(out).with_suffix(".me.wav")
    tts.my_voice(cfg, [(text, wav)], log, "your commentary")
    d = media.dur(wav) + 0.3; extra = max(0.0, d - media.dur(cam)) + 0.1
    media.ff("-i", cam, "-i", wav, "-map", "0:v:0", "-map", "1:a:0", "-vf", f"tpad=stop_mode=clone:stop_duration={extra:.2f}",
             "-af", "apad", "-t", f"{d:.2f}", *interview.ENC, out, timeout=1800)
    try: return [tuple(w) for w in captions.word_times(wav, text)]
    except Exception as e:
        log(f"  commentary: no captions for the voiceover ({str(e)[:80]})"); return []


def _commentary_clip(job, src, out, k, log=print, synthetic=False, cfg=None):
    """Your recording / file in the video's frame and codecs (so it joins the other pieces), its words for captions, and its
    voice routing. A phone's front-camera file is un-mirrored like the interview clips; a browser recording is not mirrored.
    -> (words, voice) with voice = {"label", "synthetic", "camera": the piece with your camera audio, "words": its words}"""
    from . import interview, shots
    f = Path(src); out = Path(out)
    cam = out.with_name(out.stem + "_camera.mp4")
    flip = f.suffix.lower() != ".webm" and shots.camera(f) == "front"
    vf = shots.vf_frame("short" if k == "short" else "documentary", None, 0.0, flip)
    if _has_audio(f):
        media.ff("-i", f, "-vf", vf, "-r", 30, *interview.ENC, cam, timeout=1800)
    else:
        media.ff("-i", f, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-shortest", "-vf", vf, "-r", 30, *interview.ENC, cam, timeout=1800)
    words = []
    try:
        wd = cam.with_suffix(""); wd.mkdir(exist_ok=True)
        segs = interview.transcribe(cam, wd, log)
        words = [tuple(w) for x in segs for w in x["words"]]
    except Exception as e:
        log(f"  commentary: no captions ({str(e)[:100]})")
    voice = {"label": voice_label(_on_camera(cam, log), words), "synthetic": False, "camera": str(cam), "words": [list(w) for w in words]}
    if synthetic and words:
        new = _revoice(cam, " ".join(w[0] for w in words), out, k, cfg or {}, log)
        voice["synthetic"] = True
        log(f"  commentary ({voice['label']}): your US voice over the picture, camera audio dropped")
        return new, voice
    shutil.copy2(cam, out)
    return words, voice


def insert_commentary(job, bid, src, cfg, log, where="after", synthetic=False):
    """Your own commentary as a new piece right after (or before) the piece at bid: a camera recording made in the editor or
    a video file on this PC. Undo takes it out again."""
    job = Path(job); k = _k(job)
    f = Path(str(src).strip().strip('"'))
    if not f.is_file(): raise ValueError(f"File not found on this PC: {f}")
    if f.suffix.lower() not in VIDEO | {".webm", ".mkv"}: raise ValueError("Pick a video file (.mp4, .mov, .webm)")
    i = int(bid[1:]) + (1 if where == "after" else 0)
    if not 0 <= i <= len(_man(job, k)["parts"]): raise ValueError("no such place in the video")
    def work():
        d = job / "iedit" / "commentary"; d.mkdir(parents=True, exist_ok=True)
        out = d / f"{k}_{int(time.time())}.mp4"
        words, voice = _commentary_clip(job, f, out, k, log, synthetic, cfg)
        mm = _man(job, k)
        part = {"file": str(out), "words": [list(w) for w in words], "style": "Cap", "dur": media.dur(out), "voice": voice,
                "m": {"type": "comment", "src": str(f), "text": " ".join(w[0] for w in voice["words"])}}
        near = mm["parts"][i - 1] if i > 0 else (mm["parts"][0] if mm["parts"] else {})
        if near.get("banner"): part["banner"] = near["banner"]   # the topic banner runs on through it
        hist = mm.setdefault("history", [])
        for e in hist:   # the pieces after it move up one
            if int(e["bid"][1:]) >= i: e["bid"] = f"p{int(e['bid'][1:]) + 1:02}"
        mm["parts"].insert(i, part)
        hist.append({"op": "insert", "bid": f"p{i:02}", "old": "", "new": str(out), "t": time.time()})
        _save_man(job, k, mm)
        return f"your commentary added (piece {i + 1})"
    return _background(job, f"Adding your commentary {where} {bid}", bid, work, cfg, log)


def _faded(src, out, d, fin, fout):
    from . import interview
    vf, af = [], []
    if fin: vf.append(f"fade=t=in:st=0:d={FADE}"); af.append(f"afade=t=in:st=0:d={FADE}")
    if fout: vf.append(f"fade=t=out:st={max(0.0, d - FADE):.3f}:d={FADE}"); af.append(f"afade=t=out:st={max(0.0, d - FADE):.3f}:d={FADE}")
    media.ff("-i", src, "-vf", ",".join(vf) or "null", "-af", ",".join(af) or "anull", "-t", f"{d:.3f}", *interview.ENC, out, timeout=1800)
    return out


def set_fade(job, bid, fin, fout, cfg, log):
    """Fade the piece at bid in from black and / or out to black (picture and sound). Both off = the piece as it was."""
    job = Path(job); k = _k(job)
    m, i, p = _part(job, bid, k)
    old = p.get("fade") or {}
    if bool(old.get("in")) == bool(fin) and bool(old.get("out")) == bool(fout): return ""
    def work():
        mm, ii, pp = _part(job, bid, k)
        base = pp.get("unfaded") or pp["file"]
        d = float(pp.get("dur") or media.dur(base))
        if fin or fout:
            n = len(list((job / "iedit" / "parts").glob(f"{k}_{bid}_*.mp4"))) if (job / "iedit" / "parts").exists() else 0
            new = job / "iedit" / "parts" / f"{k}_{bid}_{n + 1}.mp4"; new.parent.mkdir(parents=True, exist_ok=True)
            _faded(base, new, d, fin, fout)
        else:
            new = Path(base)
        mm.setdefault("history", []).append({"op": "fade", "bid": bid, "old": pp["file"], "new": str(new), "fade_old": pp.get("fade") or {}, "t": time.time()})
        pp["unfaded"] = base; pp["file"] = str(new); pp["fade"] = {"in": bool(fin), "out": bool(fout)}
        mm["parts"][ii] = pp; _save_man(job, k, mm)
        return "fade " + (" and ".join(x for x, on in (("in", fin), ("out", fout)) if on) or "removed")
    return _background(job, f"Fading {bid}", bid, work, cfg, log)


def set_voice(job, bid, on, cfg, log):
    """Your commentary piece read in your own US voice over its picture (camera audio dropped) - or back to the camera audio."""
    job = Path(job); k = _k(job)
    m, i, p = _part(job, bid, k)
    v = p.get("voice") or {}
    if (p.get("m") or {}).get("type") != "comment" or not v.get("camera"): raise ValueError("only your commentary pieces have this")
    if bool(v.get("synthetic")) == bool(on): return ""
    if on and not v.get("words"): raise ValueError("nothing is said in this piece, so there is nothing to re-voice")
    def work():
        import copy
        mm, ii, pp = _part(job, bid, k); old = copy.deepcopy(pp)
        cam = Path(pp["voice"]["camera"])
        if on:
            n = len(list((job / "iedit" / "parts").glob(f"{k}_{bid}_*.mp4"))) if (job / "iedit" / "parts").exists() else 0
            new = job / "iedit" / "parts" / f"{k}_{bid}_{n + 1}.mp4"; new.parent.mkdir(parents=True, exist_ok=True)
            words = _revoice(cam, " ".join(w[0] for w in pp["voice"]["words"]), new, k, cfg, log)
        else:
            new, words = cam, [tuple(w) for w in pp["voice"]["words"]]
        pp.update(file=str(new), words=[list(w) for w in words], dur=media.dur(new), fade={})
        pp.pop("unfaded", None)
        pp["voice"] = dict(pp["voice"], synthetic=bool(on))
        mm.setdefault("history", []).append({"op": "voice", "bid": bid, "old": old["file"], "new": str(new), "old_part": old, "t": time.time()})
        mm["parts"][ii] = pp; _save_man(job, k, mm)
        return "your US voice over it (camera audio dropped)" if on else "your camera audio back"
    return _background(job, f"Re-voicing {bid}", bid, work, cfg, log)



# ---------------------------------------------------------------- defect report from the Vision Checker (owner, 2026-11-10)
# The editor no longer judges every second of every picture while it builds (that took ~16 h on the Clinton cut). A separate
# post-render Vision Checker watches the finished video and writes a defect report:
#   {"mismatches": [{"start": "26:52", "end": "27:04", "defect_type": "wrong_concept", "note": "..."}]}
# Loading it fixes only those stretches and re-assembles with every other approved piece as it is. AI is a rung like any other:
# a stretch is never re-made because its picture is AI - only for what the report says is wrong with it.
DEFECT_TYPES = ("wrong_concept", "bad_framing", "artifact")

REWORK = """A documentary shows a picture for {d:.0f} seconds while these words are heard: "{text}"
It was filmed at: {where}. The picture on screen there now: {was}.
A reviewer flagged it as {kind}: "{note}"
{task}
Never name or picture a specific person they talk with or about (an invented face of a real person is worse than none): show the
place they are in, or the thing talked about.
Reply with JSON only: {{"visual": "a 4-12 word picture description", "fix": "for artifact only: what must be drawn right, else empty"}}"""
REWORK_TASK = {
    "wrong_concept": "The picture is the wrong idea. Give a completely new picture idea that shows what the words are about and "
                     "answers the reviewer's note - never the idea on screen now. A real photo, stock footage or an AI picture are all fine.",
    "bad_framing": "The idea is right but it is framed far too close. Give the same subject as a WIDE shot: the whole room, building, "
                   "street or scene - never a collar, a hand, a chest or a cropped face.",
    "artifact": "The idea is right but the picture has a rendering flaw (melted hands, warped faces, broken lines, garbled text). "
                "Keep the same idea; in \"fix\" say what must be drawn correctly (e.g. \"natural hands with five fingers\").",
}


def _secs(v):
    """'26:52' / '1:02:03.5' / 93.5 -> seconds."""
    if isinstance(v, (int, float)): return float(v)
    s = str(v).strip()
    if not s: raise ValueError("a mismatch has no time")
    n = 0.0
    for x in s.split(":"): n = n * 60 + float(x)
    return n


def parse_defects(report):
    """A defect report (dict, JSON text - code fences allowed - or a path to a .json file) -> [{n, start, end, type, note}]."""
    if isinstance(report, (str, Path)) and not str(report).lstrip().startswith(("{", "[", "`")):
        p = Path(str(report).strip().strip('"'))
        if not p.is_file(): raise ValueError(f"no such file: {p}")
        report = p.read_text(encoding="utf-8")
    if isinstance(report, str):
        t = re.sub(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$", "", report.strip())
        try: report = json.loads(t)
        except ValueError as e: raise ValueError(f"the report is not valid JSON ({e})")
    items = report.get("mismatches") if isinstance(report, dict) else report
    if not isinstance(items, list) or not items: raise ValueError('the report has no "mismatches"')
    out = []
    for n, x in enumerate(items):
        a, b = _secs(x.get("start")), _secs(x.get("end", x.get("start")))
        if b < a: a, b = b, a
        kind = str(x.get("defect_type") or "wrong_concept").strip().lower()
        if kind not in DEFECT_TYPES: kind = "wrong_concept"   # (an unknown kind: treated as the wrong picture)
        out.append({"n": n + 1, "start": a, "end": max(b, a + 0.5), "type": kind, "note": str(x.get("note") or "")[:400]})
    return out


def defect_plan(job, report, k=None):
    """Where each mismatch lands: [{n, type, note, bid, part, a, b (in the piece), start, end (in the video), skip}]."""
    k = k or _k(job); m = _man(job, k); ds = parse_defects(report)
    spans, t = [], 0.0
    for i, p in enumerate(m["parts"]):
        d = float(p.get("dur") or media.dur(p["file"])); spans.append((t, t + d, i, p)); t += d
    plan = []
    for x in ds:
        hit = False
        ov = [min(x["end"], s1) - max(x["start"], s0) for s0, s1, _i, _p in spans]
        most = max(ov) if ov else 0.0
        for (s0, s1, i, p), o in zip(spans, ov):
            a, b = max(x["start"], s0) - s0, min(x["end"], s1) - s0
            if o < 0.3 or (o < 1.0 and o < most): continue   # (a sliver spilling into the next piece: the flagged picture is the other one)
            hit = True
            d = s1 - s0
            if b - a < 2.0:   # (a picture shorter than 2 s flashes by: widened inside the piece)
                c = (a + b) / 2; a = max(0.0, min(c - 1.0, d - 2.0)); b = min(d, a + 2.0)
            typ = (p.get("m") or {}).get("type", "")
            plan.append({"n": x["n"], "type": x["type"], "note": x["note"], "bid": f"p{i:02}", "part": i, "label": _label(p, i),
                         "a": round(a, 2), "b": round(b, 2), "start": round(s0 + a, 2), "end": round(s0 + b, 2),
                         "skip": "a title / end card" if typ == "card" else ""})
        if not hit:
            plan.append({"n": x["n"], "type": x["type"], "note": x["note"], "bid": "", "part": -1, "label": "", "a": 0, "b": 0,
                         "start": x["start"], "end": x["end"], "skip": f"past the end of the video ({t:.0f} s)"})
    return plan


def _rework_ladder(job, m, p, k, cfg, log):
    from .pipeline import Job
    from . import interview
    iv = Job(job).state.get("interview") or {}
    mm = p.get("m") or {}
    src = mm.get("src") or iv.get("src") or p["file"]
    kd = Path(job) / "iwork" / "rework"; kd.mkdir(parents=True, exist_ok=True)
    lad = interview.Ladder(dict(cfg, interview_content_audit=False), Path(src), bool(mm.get("flip")), kd,
                           "short" if k == "short" else "documentary", log)
    lad.context = iv.get("context", "") or ""
    g = m.get("gps"); lad.gps = tuple(g) if g else None
    return lad


def _rework_idea(cfg, x, text, was, where, log):
    """The ladder's next picture idea for a flagged stretch -> (visual, fix)."""
    from .llm import LLM
    try:
        with LLM(dict(cfg), log, lambda: False) as llm:
            j = llm.chat_json("You pick pictures for a documentary. Reply with JSON only.",
                              REWORK.format(d=x["b"] - x["a"], text=(text or "-")[:700], where=(where or "-")[:300], was=was or "unknown",
                                            kind=x["type"].replace("_", " "), note=x["note"] or "-", task=REWORK_TASK[x["type"]]), max_tokens=200)
        v, fix = str(j.get("visual") or "").strip()[:120], str(j.get("fix") or "").strip()[:160]
    except Exception as e:
        if str(e) == "cancelled" or type(e).__name__ == "Paused": raise
        log(f"    idea query failed ({str(e)[:80]}); the note itself"); v, fix = "", ""
    v = v or (x["note"] or text)[:100]
    if x["type"] == "bad_framing" and not re.search(r"\bwide\b|establishing|full[- ]length", v, re.I): v = "wide shot of " + v
    return v, fix


def _overlay(src, pic, at, d, out, k="long", fade=0.3):
    """`pic` (silent, d s) laid over `src` from `at`, dissolving in and out; src's audio untouched."""
    W, H = (1080, 1920) if k == "short" else (1920, 1080)
    media.ff("-i", src, "-i", pic, "-filter_complex",
             f"[1:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps=30,format=yuva420p,setsar=1,trim=0:{d:.3f},"
             f"fade=t=in:st=0:d={fade}:alpha=1,fade=t=out:st={max(0.0, d - fade):.3f}:d={fade}:alpha=1,"
             f"setpts=PTS-STARTPTS+{at:.3f}/TB[w];[0:v][w]overlay=enable='between(t,{at:.3f},{at + d:.3f})':eof_action=pass,format=yuv420p[v]",
             "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "copy", out, timeout=900)


def rework_defects(job, report, cfg, log, k=None, assemble=True):
    """Fix only the stretches a defect report names, then re-assemble (every other piece as it is). -> summary dict.
    wrong_concept: a new picture idea from the note, down the whole ladder (photo, stock, AI ...);
    bad_framing: the same subject as a wide shot, down the whole ladder; artifact: the same idea with the prompt fixed, AI rungs
    first. A stretch the ladder can't fill is left as it was (a lockout never fails the job)."""
    t_all = time.time()
    k = k or _k(job); plan = defect_plan(job, report, k)
    rd = Path(job) / "iedit" / "parts"; rd.mkdir(parents=True, exist_ok=True)
    rungs = cfg.get("interview_rungs"); rungs = tuple(("ai_image", "chatgpt_chain", "static_image") if rungs is None else rungs)
    done, lads = [], {}
    log(f"Defect report: {len(plan)} stretch(es) in {len({x['bid'] for x in plan if x['bid']})} piece(s)")
    for x in plan:
        t0 = time.time()
        if x["skip"]:
            log(f"  #{x['n']} {x['type']} at {x['start']:.0f}s: skipped ({x['skip']})"); done.append(dict(x, result="skipped")); continue
        m = _man(job, k); i = x["part"]; p = m["parts"][i]
        a, b = x["a"], x["b"]; d = b - a
        text = " ".join(w[0] for w in p.get("words") or [] if float(w[2]) > a and float(w[1]) < b) or (p.get("m") or {}).get("text", "")
        was = "; ".join(str(c[2]) for c in p.get("credits") or [] if float(c[0]) < b and float(c[1]) > a) or _pic_source(p)
        lad = lads.get(i) or _rework_ladder(job, m, p, k, cfg, log); lads[i] = lad
        visual, fix = _rework_idea(cfg, x, text, was, lad.context, log)
        log(f"  #{x['n']} {x['type']} {x['bid']} {a:.1f}-{b:.1f}s: was {was[:60]!r} -> {visual!r}" + (f" (fix: {fix})" if fix else ""))
        n = len(list(rd.glob(f"{k}_{x['bid']}_*.mp4")))
        pic = Path(job) / "iwork" / "rework" / f"{k}_{x['bid']}_{n + 1}_pic.mp4"
        got = None
        if x["type"] == "artifact":   # the same idea drawn again, the prompt fixed - the AI rungs first
            v2 = f"{visual}, {fix}" if fix else f"{visual}, clean natural detail, no distortions"
            for r in rungs:
                got = lad._rungs(text, v2, d, pic, (r,))
                if got and lad.fits(got[0], v2, text): break
                got = None
        if not got:
            got = lad.media(visual, text, d, pic)   # the whole ladder: photo, stock, AI pictures, sketch, card
        if not got:
            log(f"    #{x['n']}: the ladder found nothing; left as it was")
            done.append(dict(x, result="unchanged", secs=round(time.time() - t0, 1))); continue
        new = rd / f"{k}_{x['bid']}_{n + 1}.mp4"
        _overlay(p["file"], got[0], a, d, new, k)
        cr = [c for c in p.get("credits") or [] if not (float(c[0]) < b and float(c[1]) > a)]
        whole = p.pop("credit", "")   # (a fact's one credit for the whole piece: kept only around the re-made span)
        if whole:
            dd = float(p.get("dur") or media.dur(p["file"]))
            cr += [x for x in ([0.0, a, whole], [b, dd, whole]) if x[1] - x[0] >= 0.5]
        if got[2]: cr.append([a, b, got[2]])
        m.setdefault("history", []).append({"bid": x["bid"], "old": p["file"], "new": str(new), "t": time.time(),
                                            "why": f"defect report #{x['n']}: {x['type']} - {x['note'][:80]}",
                                            "stretch": {"a": a, "b": b, "how": got[1], "credit": got[2] or "", "fix": x["type"]}})
        p.update(file=str(new), credits=sorted(cr, key=lambda c: float(c[0])), check=False); m["parts"][i] = p; _save_man(job, k, m)
        secs = round(time.time() - t0, 1)
        log(f"    #{x['n']} -> {got[1]}" + (f" ({got[2]})" if got[2] else "") + f" in {secs:.0f} s")
        done.append(dict(x, result=got[1], credit=got[2], visual=visual, secs=secs))
    t_fix = time.time() - t_all
    if assemble and any(r.get("result") not in ("skipped", "unchanged") for r in done):
        _assemble(job, k, cfg, log)
    res = {"k": k, "items": done, "fix_secs": round(t_fix, 1), "total_secs": round(time.time() - t_all, 1)}
    (Path(job) / "iedit" / f"defects_{int(t_all)}.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    log(f"Defect report done: {sum(1 for r in done if r.get('result') not in ('skipped', 'unchanged'))} of {len(done)} fixed, "
        f"{res['fix_secs']:.0f} s fixing" + (f" + {res['total_secs'] - res['fix_secs']:.0f} s assembling" if assemble else ""))
    return res


def start_rework(job, report, cfg, log):
    """The defect report applied in the background like an edit (one GPU user; Undo takes back one stretch at a time)."""
    plan = defect_plan(job, report)   # (a bad report is refused before anything starts)
    todo = [x for x in plan if not x["skip"]]
    if not todo: return "Nothing in the report lands on a piece that can be re-made."
    def work():
        r = rework_defects(job, report, cfg, log, assemble=False)
        return f"defect report: {sum(1 for x in r['items'] if x.get('result') not in ('skipped', 'unchanged'))} of {len(r['items'])} stretches re-made"
    return _background(job, f"Fixing {len(todo)} flagged stretch(es) from the defect report", "defects", work, cfg, log)
