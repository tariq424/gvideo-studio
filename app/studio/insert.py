"""owner, 2026-11-14: "I can add a beat with my own video or still seamlessly anywhere in the timeline, or at the start or end."

A new piece - your own video clip or still picture - goes into a finished Short or explainer (the Remotion timelines) at a
beat boundary: the very start, before or after any beat, or the very end. Nothing else is re-rendered:
  * the output MP4 is cut at the boundary (a keyframe sits on every beat boundary, edit.ensure_keyframes), and the piece is
    placed between the two halves with a stream copy;
  * the piece is made in the video's own frame size, frame rate and codec: a clip fills the frame (a different shape sits
    on a blurred copy of itself instead of being cropped hard), a still gets a slow push-in;
  * its sound: the clip's own sound (brought to the narration's loudness) or none, with the video's music bed running on
    underneath (lower under the clip's own sound), short fades at both joins - no clicks, no gap;
  * the timeline (teaser.json), the narration track (voice.wav, silence added) and the captions file move everything after
    the piece by its length, so later beat edits and a full re-render still line up; the description's chapters move too.
Undo (the editor's undo stack) takes the piece out again. A full re-render puts recorded pieces back (reapply).
Interview videos have their own pieces (interview_edit.insert_commentary); a still is turned into a short clip for them.
"""
import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import media

VIDEO = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
STILL = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
STILL_SECS = 4.0          # a still's default length
MAX_SECS = 120.0          # one piece at most this long
UNDER_SOUND_DB = -12.0    # the bed sits this much lower under the clip's own sound
FADE = 0.15               # sound fade at both ends of the piece
TAG = "YOUR FOOTAGE"


def _e():
    from . import edit
    return edit


def _probe(p):
    r = subprocess.run([media._bin("ffprobe"), "-v", "error", "-show_entries",
                        "stream=codec_type,width,height,sample_rate,channels:format=duration", "-of", "json", str(p)],
                       capture_output=True, text=True, timeout=120)
    try: j = json.loads(r.stdout or "{}")
    except ValueError: j = {}
    v = next((s for s in j.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in j.get("streams", []) if s.get("codec_type") == "audio"), {})
    try: d = float((j.get("format") or {}).get("duration") or 0)
    except ValueError: d = 0.0
    return {"w": int(v.get("width") or 0), "h": int(v.get("height") or 0), "audio": bool(a),
            "sr": int(a.get("sample_rate") or 48000), "dur": d}


def kind_of(src):
    s = Path(src).suffix.lower()
    return "still" if s in STILL else "video" if s in VIDEO else None


def fit_chain(sw, sh, W, H):
    """ffmpeg filter (one input, one output) that puts a sw x sh picture into a W x H frame: fill and crop when the shapes are
    close; otherwise the whole picture over a blurred, darkened copy of itself filling the frame (no black bars, no hard crop)."""
    if sw <= 0 or sh <= 0 or abs(sw / sh - W / H) / (W / H) < 0.2:
        return f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"
    return (f"split[fa][fb];[fa]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=40,eq=brightness=-0.08[fbg];"
            f"[fb]scale={W}:{H}:force_original_aspect_ratio=decrease[ffg];[fbg][ffg]overlay=(W-w)/2:(H-h)/2,setsar=1")


def shift_chapters(text, t, d):
    """Chapter lines ("1:23 Title" / "1:02:03 Title") at or after t seconds move d seconds later."""
    def fmt(s):
        s = int(round(s)); h, m, x = s // 3600, s % 3600 // 60, s % 60
        return f"{h}:{m:02}:{x:02}" if h else f"{m}:{x:02}"
    def sub(mt):
        parts = [int(x) for x in mt.group(1).split(":")]
        s = sum(v * 60 ** i for i, v in enumerate(reversed(parts)))
        return (fmt(s + d) if s >= t and s > 0 else mt.group(1)) + mt.group(2)
    return re.sub(r"(?m)^((?:\d+:)?\d{1,2}:\d{2})(\s)", sub, text or "")


def _shift_srt(p, t, d):
    def ts(x):
        h, m, r = x.split(":"); s, ms = r.split(",")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
    def fm(v):
        ms = int(round(v * 1000)); return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"
    def sub(mt):
        a, b = ts(mt.group(1)), ts(mt.group(2))
        return f"{fm(a + d if a >= t - 0.01 else a)} --> {fm(b + d if a >= t - 0.01 else b)}"
    p.write_text(re.sub(r"(\d\d:\d\d:\d\d,\d{3}) --> (\d\d:\d\d:\d\d,\d{3})", sub, p.read_text(encoding="utf-8")), encoding="utf-8")


def points(job, k=None):
    """Where a piece can go in the video being edited: [{id, label, t}] - "start", every beat ("before" / "after" it), "end"."""
    e = _e(); job = Path(job); k = k or e.kind(job)
    if k not in e.TL: return []
    tz = e._tl_json(job, k); out = [{"id": "start", "label": "Very start", "t": 0.0}]
    for b in _labelled(job, k, tz): out.append({"id": b["id"], "label": b["label"], "t": b["start"]})
    out.append({"id": "end", "label": "Very end", "t": tz["total"]})
    return out


def _labelled(job, k, tz):
    """The timeline's beats with the names the editor shows."""
    secs = []
    if k == "explainer":
        try:
            from .pipeline import Job
            secs = Job(job).plan()["sections"]
        except Exception: secs = []
    n = 0; out = []
    for b in tz["beats"]:
        if b.get("insert"): lab = "Your insert" + (f" ({b.get('name')})" if b.get("name") else "")
        elif b["id"] == "end": lab = "End card"
        else:
            lab = (secs[n]["title"] if n < len(secs) else b["id"]) if k == "explainer" else f"Beat {n + 1}"
            n += 1
        out.append({"id": b["id"], "label": lab, "start": b["start"], "dur": b.get("dur")})
    return out


def _boundary(job, k, tz, at, where):
    """-> frame number (30 fps) of the cut for a piece placed `where` (before | after) beat `at` ("start" / "end" / beat id /
    explainer slot id)."""
    e = _e()
    total = int(-(-tz["total"] * e.FPS_S // 1))
    if at == "start": return 0
    if at == "end": return total
    ids = [b["id"] for b in tz["beats"]]
    if at not in ids and k == "explainer":   # an explainer slot id (s2_1): its section beat
        lb = next((x for x in e.explainer_layout(job) if x["id"] == at), None)
        at = lb["beat"] if lb else at
    if at not in ids: raise ValueError(f"no beat {at} in this video")
    i = ids.index(at) + (1 if where == "after" else 0)
    if i >= len(ids): return total
    st = tz["beats"][i]["start"]
    return e._fr(st) if st > 0 else 0


def _make_piece(src, n, W, H, out, start=0.0, log=print):
    """The piece's picture: exactly n frames, W x H, the editor's encode (keyframe first, no sound)."""
    e = _e(); src = Path(src); pr = _probe(src); secs = n / e.FPS_S
    chain = fit_chain(pr["w"], pr["h"], W, H)
    enc = [*e.ENC_S, "-frames:v", n, "-force_key_frames", "0", "-forced-idr", 1]
    if kind_of(src) == "still":
        frame = out.with_suffix(".frame.png")
        media.ff("-i", src, "-filter_complex", f"[0:v]{chain}[v]", "-map", "[v]", "-frames:v", 1, frame, timeout=300)
        # slow push-in (6 % over the piece), drawn at twice the size so the motion is smooth
        media.ff("-loop", 1, "-framerate", e.FPS_S, "-i", frame, "-vf",
                 f"scale={W * 2}:{H * 2},zoompan=z='1+0.06*on/{max(1, n)}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={e.FPS_S},setsar=1",
                 "-an", *enc, out, timeout=1800)
        frame.unlink(missing_ok=True)
    else:
        media.ff("-ss", f"{start:.3f}", "-i", src, "-filter_complex",
                 f"[0:v]{chain},fps={e.FPS_S},tpad=stop_mode=clone:stop_duration={secs + 1:.2f}[v]", "-map", "[v]", "-an", *enc, out, timeout=3600)
    got = e._nframes(out)
    if got != n: raise RuntimeError(f"the piece came out {got} frames long, expected {n}")


def _bed(outp):
    """The video's music (music.mix wrote <name>_music.json next to it): (bed file | None, hook seconds)."""
    try: m = json.loads((outp.parent / (outp.stem + "_music.json")).read_text(encoding="utf-8"))
    except (OSError, ValueError): return None, 0.0
    bed = Path(m["bed"]) if m.get("bed") else None
    return (bed if bed and bed.is_file() else None), float(m.get("hook_secs") or 0)


def _piece_audio(src, secs, t, outp, sr, sound, start, cfg, out):
    """The piece's sound, exactly secs long: the clip's own sound (loudness of the narration) and/or the music bed continued
    from where the video is at t; silence when there is neither."""
    from . import music
    own = sound == "own" and kind_of(src) == "video" and _probe(src)["audio"]
    bed, hook = _bed(outp)
    ins, parts, mix = ["-f", "lavfi", "-t", f"{secs:.3f}", "-i", f"anullsrc=r={sr}:cl=stereo"], [], ["[0:a]"]
    if own:
        ins += ["-ss", f"{start:.3f}", "-i", str(src)]
        parts.append(f"[1:a]atrim=0:{secs:.3f},asetpts=PTS-STARTPTS,{music.NORM},aresample={sr},aformat=channel_layouts=stereo,"
                     f"afade=t=in:d={FADE},afade=t=out:st={max(0.0, secs - FADE):.3f}:d={FADE}[own]"); mix.append("[own]")
    if bed:
        k = len([x for x in ins if x == "-i"])
        ins += ["-stream_loop", "-1", "-i", str(bed)]
        off = max(0.0, t - hook)
        db = float(cfg.get("music_bed_db", -6)) + (UNDER_SOUND_DB if own else 0.0)
        parts.append(f"[{k}:a]atrim=start={off:.3f}:end={off + secs + 2:.3f},asetpts=PTS-STARTPTS,{music.NORM},volume={db}dB,"
                     f"aresample={sr},aformat=channel_layouts=stereo,afade=t=in:d=0.4,afade=t=out:st={max(0.0, secs - 0.4):.3f}:d=0.4[bed]"); mix.append("[bed]")
    flt = ";".join(parts + ["".join(mix) + f"amix=inputs={len(mix)}:normalize=0:duration=first,atrim=0:{secs:.3f},apad=whole_dur={secs:.3f}[a]"])
    media.ff(*ins, "-filter_complex", flt, "-map", "[a]", "-c:a", "pcm_s16le", "-ar", sr, "-ac", 2, out, timeout=1800)
    return bool(own), bool(bed)


def _join_audio(full, piece, t, out, sr):
    """full[0, t) + piece + full[t, end), with 10 ms fades on the cut so the joins never click."""
    total = media.dur(full); ins = ["-i", full, "-i", piece]; parts, cat = [], []
    if t > 0.001:
        parts.append(f"[0:a]atrim=0:{t:.6f},asetpts=PTS-STARTPTS,afade=t=out:st={max(0.0, t - 0.01):.6f}:d=0.01[l]"); cat.append("[l]")
    cat.append("[1:a]")
    if t < total - 0.001:
        parts.append(f"[0:a]atrim=start={t:.6f},asetpts=PTS-STARTPTS,afade=t=in:d=0.01[r]"); cat.append("[r]")
    flt = ";".join(parts + ["".join(cat) + f"concat=n={len(cat)}:v=0:a=1[a]"])
    media.ff(*ins, "-filter_complex", flt, "-map", "[a]", "-c:a", "pcm_s16le", "-ar", sr, "-ac", 2, out, timeout=1800)


def _join_video(job, k, outp, a, seg, n, new_frames, log):
    """The output's video track with seg placed at frame a (stream copy at a keyframe; re-encode once if they don't line up)."""
    e = _e(); ed = Path(job) / "edit"; ed.mkdir(exist_ok=True)
    vcopy = ed / "ins_video_only.mp4"; media.ff("-i", outp, "-map", "0:v", "-c", "copy", vcopy)
    before = e._nframes(vcopy); newv = ed / "ins_video_new.mp4"
    parts = ed / "ins_parts"; shutil.rmtree(parts, ignore_errors=True); parts.mkdir()
    if 0 < a < before:
        media.ff("-i", vcopy, "-c", "copy", "-f", "segment", "-segment_times", f"{a / e.FPS_S - 0.001:.6f}", "-reset_timestamps", 1, parts / "p%03d.mp4")
        ps = sorted(parts.glob("p*.mp4")); ok = len(ps) == 2 and e._nframes(ps[0]) == a
        order = [ps[0], seg, ps[1]] if ok else []
    else:
        ok = True; order = [seg, vcopy] if a <= 0 else [vcopy, seg]
    if ok:
        lst = ed / "ins_join.txt"; lst.write_text("".join(f"file '{Path(p).resolve().as_posix()}'\n" for p in order), encoding="utf-8")
        media.ff("-f", "concat", "-safe", 0, "-i", lst, "-c", "copy", newv)
        ok = e._nframes(newv) == before + n
    if not ok:
        log("  keyframes didn't line up; re-encoding the video track once (slower)")
        flt = (f"[0:v]trim=end_frame={a},setpts=PTS-STARTPTS[l];[1:v]setpts=PTS-STARTPTS[m];[0:v]trim=start_frame={a},setpts=PTS-STARTPTS[r];"
               "[l][m][r]concat=n=3:v=1:a=0[v]") if 0 < a < before else \
              ("[1:v][0:v]concat=n=2:v=1:a=0[v]" if a <= 0 else "[0:v][1:v]concat=n=2:v=1:a=0[v]")
        media.ff("-i", vcopy, "-i", seg, "-filter_complex", flt, "-map", "[v]", *e.ENC_S, "-force_key_frames", e._kf_times(new_frames),
                 "-forced-idr", 1, newv, timeout=7200)
        if e._nframes(newv) != before + n: raise RuntimeError("the joined video has the wrong length; the original is unchanged")
    shutil.rmtree(parts, ignore_errors=True); vcopy.unlink(missing_ok=True)
    return newv, before


def _new_id(tz):
    have = {b["id"] for b in tz["beats"]}
    i = 1
    while f"u{i}" in have: i += 1
    return f"u{i}"


def insert(job, k, at, src, cfg, log, where="before", dur=None, start=0.0, sound="own", name="", iid=None, history=True):
    """Put your clip / still into the finished video k (short | explainer) at the boundary before / after beat `at`
    ("start", "end", a beat id). -> the new beat's id. The video is changed in place; undo takes it out."""
    e = _e(); job = Path(job); t0 = time.time()
    from .pipeline import Job
    if k not in e.TL: raise ValueError("pieces go into Shorts and explainers (interview videos: + My commentary here)")
    src = Path(str(src).strip().strip('"'))
    if not src.is_file(): raise ValueError(f"File not found on this PC: {src}")
    kind = kind_of(src)
    if not kind: raise ValueError("Pick a video (.mp4, .mov, .webm) or a picture (.jpg, .png, .webp)")
    where = "after" if where == "after" else "before"
    sound = "mute" if sound == "mute" else "own"
    pr = _probe(src)
    if kind == "video":
        start = max(0.0, min(float(start or 0), max(0.0, pr["dur"] - 0.5)))
        secs = float(dur) if dur else pr["dur"] - start
    else:
        start = 0.0; secs = float(dur) if dur else STILL_SECS
    secs = max(1.0, min(secs, MAX_SECS))
    n = int(round(secs * e.FPS_S)); secs = n / e.FPS_S
    outp = e._tl_out(job, k); pub = e._tl_pub(job, k)
    e.clean_leftovers(job)
    e.ensure_keyframes(job, log, k)
    tz = e._tl_json(job, k)
    a = _boundary(job, k, tz, at, where); t = a / e.FPS_S
    op = _probe(outp); W, H, sr = op["w"], op["h"], op["sr"]
    iid = iid or _new_id(tz)
    log(f"Insert {iid}: your {kind} {src.name} ({secs:.1f}s) {where} {at} at {t:.2f}s of the {'Short' if k == 'short' else 'explainer'}")
    ed = job / "edit"; ed.mkdir(exist_ok=True)
    seg = ed / f"ins_{iid}.mp4"
    _make_piece(src, n, W, H, seg, start, log)
    full = ed / "ins_full.wav"; pa = ed / "ins_piece.wav"; na = ed / "ins_new.wav"
    media.ff("-i", outp, "-vn", "-c:a", "pcm_s16le", "-ar", sr, "-ac", 2, full, timeout=1800)
    own, bed = _piece_audio(src, secs, t, outp, sr, sound, start, cfg, pa)
    _join_audio(full, pa, t, na, sr)
    # the timeline after the piece: every later beat moves by its length (whole frames, so their frame numbers move by n)
    ntz = json.loads(json.dumps(tz))
    for b in ntz["beats"]:
        if (e._fr(b["start"]) if b["start"] > 0 else 0) >= a: b["start"] = round(b["start"] + secs, 6)
    media_dir = pub / "media" / "user"; media_dir.mkdir(parents=True, exist_ok=True)
    keep = media_dir / f"{iid}.mp4"; shutil.copy2(seg, keep)
    beat = {"id": iid, "start": round(t, 6), "dur": round(secs, 6), "words": [], "insert": True, "name": src.stem[:40],
            "bgs": [{"type": "video", "src": f"media/user/{iid}.mp4", "from": 0.0, "rate": 1.0, "tag": TAG, "slot": 0}]}
    pos = next((i for i, b in enumerate(ntz["beats"]) if (e._fr(b["start"]) if b["start"] > 0 else 0) >= a + n), len(ntz["beats"]))
    ntz["beats"].insert(pos, beat); ntz["total"] = round(tz["total"] + secs, 6)
    new_frames = [x for _, x, _ in (e.short_frames(ntz)[0] if k == "short" else e._frames_of(ntz)[0])]
    newv, before = _join_video(job, k, outp, a, seg, n, new_frames, log)
    tmp = outp.with_name("_edit_" + outp.name)
    media.ff("-i", newv, "-i", na, "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", tmp)
    if e._nframes(tmp) != before + n:
        tmp.unlink(missing_ok=True); raise RuntimeError("the new video has the wrong length; the original is unchanged")
    hd = e._hnew(job) if history else None
    try:
        voice = pub / tz.get("audio", "voice.wav")
        srt = [p for p in outp.parent.glob("*.srt")] if k == "explainer" else []
        desc = outp.parent / ("youtube_description.txt" if k == "explainer" else "youtube_short_description.txt")
        if hd is not None:
            shutil.copy2(outp, hd / "old_full.mp4"); shutil.copy2(pub / "teaser.json", hd / "teaser.json")
            if voice.is_file(): shutil.copy2(voice, hd / "voice.wav")
            for p in srt + ([desc] if desc.is_file() else []): shutil.copy2(p, hd / p.name)
        how = e.replace_file(tmp, outp, log)
        if how == "copied": log("  (the video was open elsewhere: written over it in place)")
        (pub / "teaser.json").write_text(json.dumps(ntz, indent=1, ensure_ascii=False), encoding="utf-8")
        if voice.is_file():   # the narration track gets the same gap, so a full re-render stays in step
            vp = _probe(voice); vt = ed / "ins_voice.wav"
            media.ff("-i", voice, "-f", "lavfi", "-t", f"{secs:.6f}", "-i", f"anullsrc=r={vp['sr']}:cl=stereo", "-filter_complex",
                     (f"[0:a]atrim=0:{t:.6f},asetpts=PTS-STARTPTS,aformat=channel_layouts=stereo[l];[1:a]aformat=channel_layouts=stereo[m];"
                      f"[0:a]atrim=start={t:.6f},asetpts=PTS-STARTPTS,aformat=channel_layouts=stereo[r];[l][m][r]concat=n=3:v=0:a=1[a]"),
                     "-map", "[a]", "-c:a", "pcm_s16le", vt, timeout=1800)
            shutil.move(str(vt), str(voice))
        for p in srt: _shift_srt(p, t, secs)
        if desc.is_file(): desc.write_text(shift_chapters(desc.read_text(encoding="utf-8"), t, secs), encoding="utf-8")
        e._stamp_keyframes(job, k)
        if hd is not None:
            meta = {"type": "insert", "beat": iid, "kind": k, "t": time.strftime("%Y-%m-%d %H:%M"), "label": f"your {kind} {src.name}",
                    "after": e._ostamp(outp), "out": str(outp), "files": [p.name for p in srt] + ([desc.name] if desc.is_file() else [])}
            (hd / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    except Exception:
        if hd is not None: shutil.rmtree(hd, ignore_errors=True)
        raise
    finally:
        for p in (full, pa, na, newv, seg, tmp): Path(p).unlink(missing_ok=True)
    j = Job(job)
    rec = {"id": iid, "kind": k, "at": at, "where": where, "src": str(src), "dur": secs, "start": start, "sound": sound,
           "t": t, "secs": secs, "time": time.time()}
    ins = [x for x in j.state.get("inserts", []) if not (x.get("id") == iid and x.get("kind") == k)] + [rec]
    upd = {"inserts": ins}
    if history:
        edits = j.state.get("edits", []) + [{"beat": iid, "fit": "insert", "t": time.time(), "kind": k, "h": int(hd.name), "user": True}]
        upd.update(edits=edits, edit_last={"beat": iid, "ok": True, "msg": f"Your {kind} added ({secs:.1f}s).", "t": time.time()})
    j.save(**upd)
    log(f"Insert {iid}: done in {time.time() - t0:.0f}s - {secs:.1f}s of your {kind}"
        + (", its own sound" if own else ", no sound of its own") + (" over the music bed" if bed else "")
        + f"; the video is now {ntz['total']:.1f}s" + (f" (undo step {int(hd.name)})" if hd is not None else ""))
    return iid


def undo(job, hd, m, log):
    """The editor's undo for a piece: the video, timeline, narration, captions and description from before it."""
    e = _e(); job = Path(job); k = m["kind"]
    from .pipeline import Job
    outp = e._tl_out(job, k); pub = e._tl_pub(job, k)
    tmp = outp.with_name("_edit_" + outp.name); shutil.copy2(hd / "old_full.mp4", tmp)
    e.replace_file(tmp, outp, log)
    shutil.copy2(hd / "teaser.json", pub / "teaser.json")
    tz = e._tl_json(job, k)
    if (hd / "voice.wav").is_file(): shutil.copy2(hd / "voice.wav", pub / tz.get("audio", "voice.wav"))
    for nm in m.get("files") or []:
        if (hd / nm).is_file(): shutil.copy2(hd / nm, outp.parent / nm)
    (pub / "media" / "user" / f"{m['beat']}.mp4").unlink(missing_ok=True)
    e._stamp_keyframes(job, k)
    j = Job(job)
    j.save(inserts=[x for x in j.state.get("inserts", []) if not (x.get("id") == m["beat"] and x.get("kind") == k)])


def reapply(job, cfg, log):
    """After a full re-render: pieces recorded in the job that the new timeline no longer has go back in, in the order they
    were added (each at its beat boundary, as before). Never fails the job."""
    e = _e(); job = Path(job)
    from .pipeline import Job
    for rec in list(Job(job).state.get("inserts", [])):
        k = rec.get("kind")
        try:
            if k not in e.TL or not (e._tl_pub(job, k) / "teaser.json").exists(): continue
            if any(b["id"] == rec["id"] for b in e._tl_json(job, k)["beats"]): continue
            log(f"Putting your insert {rec['id']} back after the re-render")
            insert(job, k, rec["at"], rec["src"], cfg, log, rec.get("where", "before"), rec.get("dur"), rec.get("start", 0.0),
                   rec.get("sound", "own"), iid=rec["id"], history=False)
        except Exception as ex:
            log(f"  insert {rec.get('id')} not put back ({str(ex)[:200]})")


def still_to_clip(src, out, secs=STILL_SECS, W=1920, H=1080):
    """A still as a short silent clip (for interview pieces, which take video files)."""
    e = _e(); n = int(round(max(1.0, min(float(secs), MAX_SECS)) * e.FPS_S))
    _make_piece(src, n, W, H, Path(out))
    return Path(out)


def start(job, k, at, src, cfg, log, **kw):
    """In the background, like an edit apply (the editor shows it busy; the idle release waits). -> "" or why not."""
    e = _e(); key = str(Path(job))
    with e._lk:
        if e._busy.get(key): return "Already applying an edit to this video."
        e._busy[key] = f"Adding your {kind_of(src) or 'file'} to the video..."
    e._busy.pop(key + ":err", None)
    def run():
        from . import gpu_lock, editor_session
        from .pipeline import Job
        jid = Path(job).name
        editor_session.apply_started(jid)
        try:
            with gpu_lock.holder(desc=f"editor-insert:{jid}"):
                insert(job, k, at, src, cfg, log, **kw)
        except Exception as ex:
            msg = e.LOCKED_MSG if isinstance(ex, e.FileLocked) else str(ex)[:300]
            log(f"Insert NOT added: {msg} The video is unchanged.")
            e._busy[key + ":err"] = msg
            try: Job(job).save(edit_last={"beat": "insert", "ok": False, "msg": msg, "t": time.time()})
            except Exception: pass
        finally:
            e._busy[key] = ""
            editor_session.apply_finished(jid)
    threading.Thread(target=run, daemon=True).start()
    return ""
