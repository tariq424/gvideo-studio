"""Background music: a suspense cue under the hook, then a quiet bed under the rest, ducked whenever the narrator speaks.

Tracks come from your own folder (no downloads, no licences guessed): music_dir (default <jobs folder parent>\\music) with
    hook\\   short suspense / tension cues   (played under the hook: the Short's first beat, the explainer's first seconds)
    bed\\    calm background tracks, e.g. lo-fi (looped under the rest of the video)
Use tracks you are allowed to put on YouTube (YouTube Studio > Audio Library is free and safe). A random track is picked
per video. No tracks -> no music, the video is unchanged. Any failure leaves the video as it was (never fails the job).

Settings: music_on (true), music_dir, music_hook_db (-4), music_bed_db (-6): the level in the narrator's pauses (breathers); under speech it sits ~18 dB below the voice (music_duck_threshold 0.015, music_duck_ratio 20, music_duck_boost 4),
music_long (true: explainers get it too)."""
import json
import random
import time
from pathlib import Path

from . import media

# every track is first brought to the narration's loudness (-14 LUFS), so the dB settings below mean "this much under the voice"
# (2026-10-23: raw -14/-24 dB plus a hard 8:1 dip left the music 20-30 dB under the voice: inaudible)
NORM = "loudnorm=I=-14:TP=-2:LRA=11"
AUDIO = (".mp3", ".m4a", ".wav", ".ogg", ".aac", ".flac")


def folder(cfg):
    return Path(cfg.get("music_dir") or (Path(cfg.get("jobs_dir") or ".").parent / "music"))


MOODS = {   # mood -> how we describe a new track of that mood to Gemini (kind: hook = 15-20 s cue, bed = loopable background)
    "tense":       ("hook", "a tense cinematic build-up, low pulsing strings and a ticking pulse, 15-20 seconds, ending on a hit"),
    "dramatic":    ("hook", "a dramatic orchestral opener, big drums and brass swell, 15-20 seconds"),
    "epic":        ("hook", "a huge orchestral swell: a low sustained note rising into a brass fanfare, pounding timpani and a full-orchestra climax, 15-20 seconds"),
    "playful":     ("hook", "a playful, cheeky pizzicato and woodblock sting, bright and quick, 15-20 seconds"),
    "classical":   ("bed", "light classical chamber music, string quartet and piano, gentle and elegant, steady tempo, loopable"),
    "light-dance": ("bed", "light, bouncy dance groove, soft four-on-the-floor beat, warm synths, unobtrusive, loopable"),
    "cheerful":    ("bed", "cheerful, sunny acoustic pop instrumental, ukulele, claps and glockenspiel, loopable"),
    "calm":        ("bed", "calm lo-fi hip hop instrumental, mellow keys and soft vinyl texture, relaxed, loopable"),
    "inspiring":   ("bed", "inspiring, hopeful cinematic underscore, piano and soft strings, slowly rising, loopable"),
    "upbeat":      ("bed", "upbeat modern news-style background, light percussion and plucked synths, energetic but quiet, loopable"),
    "somber":      ("bed", "somber, reflective ambient underscore, soft piano and pads, slow, loopable"),
}
PICK = """Pick background music for this news video. Hook = the opening cue; bed = quiet music under the rest.
Hook moods: tense, dramatic, playful. Bed moods: classical, light-dance, cheerful, calm, inspiring, upbeat, somber.
Match the story's tone (a light tech story -> cheerful or light-dance; serious harm or loss -> somber; never cheerful
music under bad news). VIDEO: {text}
Reply with JSON only: {{"hook": "...", "bed": "..."}}"""


def library(cfg):
    """[{file, kind, mood, desc, source}] from library.json, plus any file you dropped in hook/ or bed/ (mood 'any')."""
    root = folder(cfg); idx = root / "library.json"
    try: lib = json.loads(idx.read_text(encoding="utf-8"))
    except (OSError, ValueError): lib = []
    known = {x["file"] for x in lib}
    for kind in ("hook", "bed"):
        d = root / kind
        for f in (sorted(d.glob("*")) if d.is_dir() else []):
            rel = f"{kind}/{f.name}"
            if f.suffix.lower() in AUDIO and rel not in known: lib.append({"file": rel, "kind": kind, "mood": "any", "desc": "", "source": "yours"})
    return [x for x in lib if (root / x["file"]).exists()]


def add_to_library(cfg, f, kind, mood, desc, source="gemini"):
    root = folder(cfg); idx = root / "library.json"
    try: lib = json.loads(idx.read_text(encoding="utf-8"))
    except (OSError, ValueError): lib = []
    rel = Path(f).resolve().relative_to(root.resolve()).as_posix()
    lib = [x for x in lib if x["file"] != rel] + [{"file": rel, "kind": kind, "mood": mood, "desc": desc, "source": source, "at": time.time()}]
    idx.write_text(json.dumps(lib, indent=1), encoding="utf-8")


def moods_for(cfg, text, log):
    """The script AI picks a hook mood and a bed mood for the story (default: tense + calm)."""
    if not text: return "tense", "calm"
    try:
        from .llm import LLM
        def ok(j):
            if j.get("hook") not in MOODS or MOODS[j["hook"]][0] != "hook" or j.get("bed") not in MOODS or MOODS[j["bed"]][0] != "bed":
                raise ValueError("pick one hook mood and one bed mood from the lists")
        with LLM(dict(cfg), log, lambda: False) as llm:
            j = llm.chat_json("You pick music moods. Reply with JSON only.", PICK.format(text=text[:1500].replace('"', "'")), max_tokens=100, check=ok)
        return j["hook"], j["bed"]
    except Exception as e:
        log(f"  music: mood pick failed ({str(e)[:80]}); tense + calm"); return "tense", "calm"


def choose(cfg, kind, mood, rng, log):
    """A track of this kind and mood from the library; a new one from Gemini only if none fits and today's cap allows;
    else any track of this kind (yours included)."""
    lib = [x for x in library(cfg) if x["kind"] == kind]
    fit = [x for x in lib if x["mood"] == mood]
    if fit: return folder(cfg) / rng.choice(fit)["file"]
    from . import music_gen
    if music_gen.allowed(cfg):
        desc = MOODS[mood][1]
        out = folder(cfg) / kind / f"{mood}_{time.strftime('%Y%m%d_%H%M%S')}.mp3"
        try:
            music_gen.generate(cfg, desc, out, log, evidence_dir=folder(cfg))
            add_to_library(cfg, out, kind, mood, desc); log(f"  music: new {mood} {kind} track added to the library")
            return out
        except Exception as e:
            log(f"  music: couldn't make a new {mood} track ({' '.join(str(e).split())[:150]}); using another from the library")
    any_ = lib   # nothing fits and no new track: any track of this kind (yours included)
    return folder(cfg) / rng.choice(any_)["file"] if any_ else None


def speech_spans(timeline):
    """[(start, end)] of the spoken words in a Remotion timeline (short/public or build/public teaser.json), merged,
    in seconds of the video. None if it can't be read."""
    try:
        tj = json.loads(Path(timeline).read_text(encoding="utf-8"))
        spans = sorted((float(b["start"]) + float(s), float(b["start"]) + float(e)) for b in tj["beats"] for _, s, e in b.get("words") or [])
    except Exception:
        return None
    out = []
    for s, e in spans:
        if out and s <= out[-1][1] + 0.05: out[-1] = (out[-1][0], max(out[-1][1], e))
        else: out.append((s, e))
    return out or None


def fade_curve(spans, total, cfg):
    """An ffmpeg volume expression (gain, 1 = the set level) that keeps the music low under speech and lifts it in every
    pause of at least music_pause_min secs: it fades UP over music_fade_up secs after the narrator stops and DOWN over
    music_fade_down secs so it is already low when the next word starts (we know when that is). -> (expr, pauses)"""
    duck = 10 ** (float(cfg.get("music_under_voice_db", -18)) / 20)
    up, down, gap = float(cfg.get("music_fade_up", 1.2)), float(cfg.get("music_fade_down", 1.0)), float(cfg.get("music_pause_min", 1.0))
    pauses = [(0.0, spans[0][0])] if spans[0][0] >= gap else []          # before the first word
    pauses += [(a[1], b[0]) for a, b in zip(spans, spans[1:]) if b[0] - a[1] >= gap]
    if total - spans[-1][1] >= 0.5: pauses.append((spans[-1][1], total + 5))   # after the last word: music comes up to the end
    terms = []
    for i, (s, e) in enumerate(pauses):
        rise = f"(t-{s:.3f})/{up}" if s > 0.01 else "1"                   # the very start needs no fade-in from 'ducked'
        fall = f"({e:.3f}-t)/{down}"
        terms.append(f"clip(min({rise},{fall}),0,1)")
    lift = "+".join(terms) or "0"
    return f"{duck:.4f}+{1 - duck:.4f}*min(1,{lift})", pauses


def mix(mp4, cfg, log, hook_secs=6.0, seed=None, text="", timeline=None):
    """Lay music under the narration of mp4 (in place). -> True if music was added.
    timeline: the video's teaser.json; with it the music follows the word timings (smooth fades into and out of every
    pause), without it a compressor ducks the music under the voice."""
    if not cfg.get("music_on", True): return False
    mp4 = Path(mp4); rng = random.Random(seed)
    was = mp4.parent / (mp4.stem + "_music.json")   # owner, 2026-11-09: an edit keeps the video's music (each re-assembly
    try: was = json.loads(was.read_text(encoding="utf-8")) if cfg.get("music_keep", True) and was.exists() else None   # had
    except (OSError, ValueError): was = None                                                                            # re-picked it)
    if was and (not was.get("hook") or Path(was["hook"]).exists()) and (not was.get("bed") or Path(was["bed"]).exists()):
        hm, bm = (list(was.get("moods") or []) + ["", ""])[:2]
        hook, bed = (Path(was["hook"]) if was.get("hook") else None), (Path(was["bed"]) if was.get("bed") else None)
        log(f"  music: kept from the last cut ({hook.name if hook else '-'} + {bed.name if bed else '-'})")
    else:
        hm, bm = moods_for(cfg, text, log)
        log(f"  music: moods hook={hm}, bed={bm}")
        hook, bed = choose(cfg, "hook", hm, rng, log), choose(cfg, "bed", bm, rng, log)
    if not hook and not bed:
        log(f"  music: no tracks in the library ({folder(cfg)}); no music added"); return False
    swells_used = ([], None)
    try:
        total = media.dur(mp4); h = max(0.0, min(float(hook_secs), total)) if hook else 0.0
        hdb, bdb = float(cfg.get("music_hook_db", -4)), float(cfg.get("music_bed_db", -6))
        ins, parts = ["-i", mp4], []
        if hook:
            ins += ["-i", hook]
            # the cue plays under the hook and fades out over its last second
            parts.append(f"[{len(ins) // 2 - 1}:a]atrim=0:{h + 1:.2f},asetpts=PTS-STARTPTS,{NORM},volume={hdb}dB,afade=t=out:st={max(0, h - 0.5):.2f}:d=1.5[mh]")
        if bed and total - h > 2:
            ins += ["-stream_loop", "-1", "-i", bed]
            k = len([x for x in ins if x == "-i"]) - 1
            parts.append(f"[{k}:a]atrim=0:{total - h + 1:.2f},asetpts=PTS-STARTPTS,{NORM},volume={bdb}dB,afade=t=in:st=0:d=1.5,"
                         f"afade=t=out:st={max(0, total - h - 2):.2f}:d=2,adelay={int(h * 1000)}|{int(h * 1000)}[mb]")
        tracks = [t for t, ok in (("[mh]", hook), ("[mb]", bed and total - h > 2)) if ok]
        music = "".join(tracks) + (f"amix=inputs={len(tracks)}:normalize=0:duration=longest" if len(tracks) > 1 else "anull") + ",aformat=channel_layouts=stereo[m];"
        spans = speech_spans(timeline) if timeline else None
        if spans and spans[-1][1] <= total + 1.0:
            # the music follows the narration's own word timings: low under speech, smooth fades up into each pause and
            # back down before the next word (no compressor guessing, so no sudden jumps)
            expr, pauses = fade_curve(spans, total, cfg)
            sw = [tuple(x) for x in was["swells"]] if was and "swells" in was else pick_swells(cfg, timeline, total, log)
            swt = Path(was["swell_track"]) if was and was.get("swell_track") and Path(was["swell_track"]).exists() else None
            if sw and not swt:   # an epic cue from the library, else a dramatic one, else a new epic one, else the hook's
                have = {x["mood"] for x in library(cfg) if x["kind"] == "hook"}
                swt = choose(cfg, "hook", "epic" if "epic" in have or "dramatic" not in have else "dramatic", rng, log) or hook
            sins, sparts, slabels = swell_filters(sw, swt, len([x for x in ins if x == "-i"]), cfg) if sw and swt else ([], [], [])
            ins += sins; parts += sparts
            flt = ";".join(parts) + ";" + music.replace("[m];", f",volume='{expr}':eval=frame[md];") + \
                  f"[0:a][md]{''.join(slabels)}amix=inputs={2 + len(slabels)}:normalize=0:duration=first[a]"
            if slabels: swells_used = (sw, swt)
            log(f"  music: fades with the narration ({len(pauses)} pause(s) where it comes up)")
        else:
            # no word timings: duck the music under the voice (sidechain); the voice level is untouched
            flt = ";".join(parts) + ";" + music + \
                  f"[0:a]asplit=2[v][key];[m][key]sidechaincompress=threshold={float(cfg.get('music_duck_threshold', 0.015))}:ratio={float(cfg.get('music_duck_ratio', 20))}:level_sc={float(cfg.get('music_duck_boost', 4))}:attack=20:release=900[md];" \
                  "[v][md]amix=inputs=2:normalize=0:duration=first[a]"
        tmp = mp4.with_name("_music_" + mp4.name)
        media.ff(*ins, "-filter_complex", flt, "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                 "-movflags", "+faststart", "-t", f"{total:.3f}", tmp)
        if media.dur(tmp) < total - 0.5: raise RuntimeError("the result came out shorter")
        # owner, 2026-11-20: a player, Explorer's preview or the backup can hold the video open for a moment; Windows then refuses
        # the swap ("Access is denied") and the Short went out with no music - its musical pauses as dead air. Retry for 2 min.
        for i in range(40):
            try: tmp.replace(mp4); break
            except PermissionError:
                if i == 39: raise RuntimeError(f"{mp4.name} stayed open in another program for 2 minutes (close any player or "
                                               f"preview showing it); the music version is kept as {tmp.name}")
                if i == 0: log(f"  {mp4.name} is open in another program; waiting to swap in the music version...")
                time.sleep(3)
        (mp4.parent / (mp4.stem + "_music.json")).write_text(json.dumps({"hook": str(hook or ""), "bed": str(bed or ""), "hook_secs": h,
                                                                         "moods": [hm, bm], "swells": [list(x) for x in swells_used[0]],
                                                                         "swell_track": str(swells_used[1] or "")}, indent=1), encoding="utf-8")
        log(f"Music added: {'hook ' + hook.name + f' ({h:.1f}s)' if hook else ''}{' + ' if hook and bed else ''}{'bed ' + bed.name if bed else ''}")
        return True
    except Exception as e:
        log(f"  music skipped ({' '.join(str(e).split())[:250]}); the video is unchanged")
        return False


def hook_secs_short(job):
    """The Short's hook = its first beat (from the timeline)."""
    try:
        b0 = json.loads((Path(job) / "short" / "public" / "teaser.json").read_text(encoding="utf-8"))["beats"][0]
        return float(b0["start"]) + float(b0["dur"])
    except Exception:
        return 6.0


# ---------------------------------------------------------------- swells at the dramatic moments (owner, 2026-11-19)
# "At dramatic events we want some loud music as in space odyssey 2001." The script AI picks the few most dramatic lines
# (a reveal, the stakes, a turning point); at each, an epic cue's climax rises under the last words, plays at full level in
# the pause after the line and sinks back under the next words. Our own library tracks only (the "epic" mood, else
# "dramatic"); the famous recording itself is not used. Settings: music_swells (true), music_swell_db (0: as loud as the
# voice), music_swell_every (one per this many seconds, at most music_swell_max).
SWELL_PICK = """These are lines of a video's narration with the second each one ends. Pick the {n} most DRAMATIC moments:
a big reveal, the stakes made plain, a turning point, a shocking number - the lines after which a film would hit a loud
orchestral swell. Never a joke, a question to the viewer or the sign-off. Lines:
{lines}
Reply with JSON only: {{"picks": [line numbers]}}"""
SWELL_PRE, SWELL_POST = 2.0, 2.5


def swell_candidates(timeline, min_pause=0.6):
    """[(end_second, next_word_second, sentence)] of the narration's sentences followed by a pause of at least min_pause."""
    try: tj = json.loads(Path(timeline).read_text(encoding="utf-8"))
    except Exception: return []
    words = sorted((float(b["start"]) + float(st), float(b["start"]) + float(en), str(w)) for b in tj["beats"] for w, st, en in b.get("words") or [])
    out, cur = [], []
    for i, (st, en, w) in enumerate(words):
        cur.append(w)
        if not w.rstrip("\"'").endswith((".", "!", "?")): continue
        nxt = words[i + 1][0] if i + 1 < len(words) else en + 5
        if nxt - en >= min_pause: out.append((round(en, 2), round(nxt, 2), " ".join(cur)))
        cur = []
    return out


def pick_swells(cfg, timeline, total, log):
    """-> [(end_second, next_word_second)] of the dramatic moments (none when off or nothing fits)."""
    if not cfg.get("music_swells", True) or not timeline: return []
    cands = [c for c in swell_candidates(timeline) if 20 < c[0] < total - 20]
    if not cands: return []
    n = max(1, min(int(cfg.get("music_swell_max", 4)), int(total // float(cfg.get("music_swell_every", 300)))))
    lines = "\n".join(f"[{i}] ({c[0]:.0f}s) {c[2][:220]}" for i, c in enumerate(cands))
    try:
        from .llm import LLM
        def ok(j):
            if not isinstance(j.get("picks"), list) or not j["picks"]: raise ValueError("picks: a list of line numbers")
            if any(not isinstance(x, int) or not 0 <= x < len(cands) for x in j["picks"]): raise ValueError("line numbers out of range")
        with LLM(dict(cfg), log, lambda: False) as llm:
            j = llm.chat_json("You are a film music editor. Reply with JSON only.", SWELL_PICK.format(n=n, lines=lines[:30000]), max_tokens=120, check=ok)
    except Exception as e:
        log(f"  music: no dramatic swells ({str(e)[:100]})"); return []
    out = []
    for i in sorted({int(x) for x in j["picks"]})[:n]:   # never two swells within a minute of each other
        if not out or cands[i][0] - out[-1][0] > 60: out.append((cands[i][0], cands[i][1]))
    for t, _ in out: log(f"  music: dramatic swell at {t:.0f}s")
    return out


def climax(track, secs):
    """-> the offset (s) in track where its loudest `secs`-long stretch begins, less the rise (the cue's climax)."""
    import re, subprocess
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(track), "-af", "asetnsamples=n=44100,astats=metadata=1:reset=1,"
                            "ametadata=print:key=lavfi.astats.Overall.RMS_level", "-f", "null", "-"], capture_output=True, text=True, timeout=120)
        lv = [-120.0 if "inf" in x else float(x) for x in re.findall(r"RMS_level=(-?inf|-?[\d.]+)", r.stderr)]
    except Exception: return 0.0
    w = max(1, int(round(secs)))
    if len(lv) <= w: return 0.0
    best = max(range(len(lv) - w), key=lambda i: sum(lv[i:i + w]))
    return max(0.0, best - SWELL_PRE)


def swell_filters(swells, track, first_input, cfg):
    """-> (extra ffmpeg inputs, filter parts, labels) laying the cue's climax over each swell."""
    db = float(cfg.get("music_swell_db", 0))
    ins, parts, labels = [], [], []
    for i, (t, nxt) in enumerate(swells):
        hold = max(0.5, nxt - t)
        d = SWELL_PRE + hold + SWELL_POST
        off = climax(track, d)
        st = max(0.0, t - SWELL_PRE)
        ins += ["-i", str(track)]; k = first_input + i
        parts.append(f"[{k}:a]atrim={off:.2f}:{off + d:.2f},asetpts=PTS-STARTPTS,{NORM},volume={db}dB,"
                     f"afade=t=in:st=0:d={SWELL_PRE},afade=t=out:st={SWELL_PRE + hold:.2f}:d={SWELL_POST},"
                     f"aformat=channel_layouts=stereo,adelay={int(st * 1000)}|{int(st * 1000)}[sw{i}]")
        labels.append(f"[sw{i}]")
    return ins, parts, labels
