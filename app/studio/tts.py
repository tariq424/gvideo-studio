"""Narration engine switch: config "tts_engine" = "wanGP" (default: the cloned voice through WanGP, unchanged) | "kokoro".

Kokoro (hexgrad/Kokoro-82M, Apache-2.0) is a small dedicated TTS model: preset voices only (no cloning), tens of times faster
than real time on the GPU, so a Short's narration takes seconds instead of the 10-15 minutes WanGP needs.

It runs in its OWN Python environment (app/kenv, made by pinokio/install_kokoro.js) through app/kokoro_worker.py, never inside
Video Studio's env: Kokoro needs PyTorch (CUDA 12.8 build for the RTX 50-series), which Video Studio's env doesn't have, and
installing it there could shift numpy / other packages under the running app. One worker call per job (the model loads once)
renders every narration chunk; each comes back as a 24 kHz mono WAV that the callers convert to 48 kHz stereo exactly like
WanGP output, so everything downstream (word timings, splitting, captions, mixing) is unchanged.

Config:
  tts_engine      "wanGP" | "kokoro"   (default "wanGP")
  kokoro_voice    preset voice id      (default "am_michael"; any of Kokoro's voices, e.g. af_heart, am_fenrir, bm_george)
  kokoro_speed    0.5 - 2.0            (default 1.0)
  kokoro_wpm      words per minute used to size Shorts (default 140: am_michael at speed 1.0, measured 2026-10-12)
  kokoro_online   false = HF_HUB_OFFLINE for the worker (the model is cached at install); true if the cache is ever cleared
  kokoro_python   path to the Kokoro env's python ("" = app/kenv/Scripts/python.exe on Windows, app/kenv/bin/python elsewhere)
  kokoro_device   "cuda" | "cpu" | ""  ("" = cuda when available)
"""
import json, os, re, subprocess, sys, tempfile, time
from pathlib import Path
from . import config, media

APP = Path(__file__).resolve().parent.parent
WORKER = APP / "kokoro_worker.py"
DEFAULT_VOICE = "am_michael"

# Owner, 2026-11-07: until the owner's own voice is wired in (OpenVoice tone-colour conversion over Kokoro), each job gets a
# Kokoro voice that suits its subject. All American English. pace = words per minute relative to am_michael, measured on one
# 82-word paragraph at speed 1.0 (michael 179.9, fenrir 240.0, puck 248.5, heart 193.1, bella 192.8, sarah 197.0), so a
# Short's length budget (kokoro_wpm x pace) stays right whichever voice reads it.
VOICE_MENU = {
    "am_michael": {"sound": "steady, authoritative male", "for": "politics, world news, diplomacy, current affairs", "pace": 1.00},
    "am_fenrir": {"sound": "deep, grave male", "for": "war, conflict, disasters, crime, investigations", "pace": 1.334, "speed": 0.85},
    "am_puck": {"sound": "lively, playful male", "for": "comedy, satire, pop culture, sports, entertainment", "pace": 1.381, "speed": 0.82},
    "af_heart": {"sound": "warm, rich female", "for": "science, technology, AI, the future, health, human interest, education, nature", "pace": 1.073},
    "af_bella": {"sound": "bright, light female (thin low end)", "for": "lifestyle, travel, upbeat consumer topics", "pace": 1.072},
    "af_sarah": {"sound": "calm, measured female", "for": "finance, economy, history, explainers", "pace": 1.095},
}
PICK = """Pick the narrator voice that best suits this video's subject and tone. Voices:
{menu}
Video title: "{title}"
Opening of the source: "{text}"
Reply with JSON only: {{"voice": "<one id from the list>", "why": "a few words"}}"""


def pick_voice(cfg, title, text, log=print):
    """-> a VOICE_MENU id for this job's subject (voice_by_subject, default on), or the configured kokoro_voice. Never raises."""
    base = cfg.get("kokoro_voice") or DEFAULT_VOICE
    if not cfg.get("voice_by_subject", True) or engine(cfg) != "kokoro": return base
    allowed = [v for v in (cfg.get("voice_menu") or list(VOICE_MENU)) if v in VOICE_MENU]
    if not allowed: return base
    menu = "\n".join(f'- {v}: {VOICE_MENU[v]["sound"]}; suits {VOICE_MENU[v]["for"]}' for v in allowed)
    try:
        from .llm import LLM
        with LLM(cfg, log, lambda: False) as llm:
            j = llm.chat_json("You are a careful video producer. Reply with JSON only.",
                              PICK.format(menu=menu, title=str(title or "")[:200].replace('"', "'"), text=str(text or "")[:1500].replace('"', "'")),
                              max_tokens=80)
        v = str(j.get("voice") or "").strip()
        if v in allowed:
            log(f"Narrator voice: {v} ({VOICE_MENU[v]['sound']}; {str(j.get('why') or '')[:80]})"); return v
        log(f"Narrator voice: the script AI answered {v[:30]!r}; keeping {base}")
    except Exception as e:
        log(f"Narrator voice: pick failed ({str(e)[:100]}); keeping {base}")
    return base


def engine(cfg):
    selected = str(cfg.get("tts_engine", "kokoro")).strip().lower()
    return selected if selected in ("kokoro", "my_original", "my_american") else "wanGP"


def is_local(cfg):
    return engine(cfg) in ("kokoro", "my_original", "my_american")


def try_kokoro(cfg, items, log, tag, stop):
    """Kokoro for these items. -> True when done; False when it failed and tts_fallback lets WanGP take over (logged).
    Raises when it failed and fallback is off (or the job was cancelled)."""
    try:
        if engine(cfg) in ("my_original", "my_american"):
            cloned_batch(cfg, items, log, tag, stop)
        else:
            kokoro_batch(cfg, items, log, tag, stop)
        return True
    except Exception as e:
        if engine(cfg) in ("my_original", "my_american") or str(e) == "cancelled" or not cfg.get("tts_fallback", True): raise
        log(f"  {tag}: Kokoro failed ({str(e)[:200]}); falling back to WanGP narration (tts_fallback)")
        return False


# owner, 2026-11-20 ("roboticsfinally", "crunchit's", "capacityrobotics", "deemauss", "three dollars five billions"): text went to
# the voice through NFKD + ascii-ignore, which DELETED every em dash ("robotics—finally" -> "roboticsfinally"), and Kokoro reads
# "demos" as the Greek word (dee-moss). ascii_text() keeps the words apart; speakable() also re-spells words the voice gets wrong.
TYPO = {"\u2014": ", ", "\u2013": ", ", "\u2012": ", ", "\u2015": ", ", "\u2212": "-", "\u2026": "...", "\u2018": "'", "\u2019": "'",
        "\u201a": "'", "\u201b": "'", "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u00a0": " ", "\u2009": " ", "\u200a": " ",
        "\u202f": " ", "\u00b7": " ", "\u2022": ", ", "\u00d7": " times ", "\u2192": " to ", "\u00b0": " degrees", "%": " percent"}
SAY = {"demos": "demoes", "Demos": "Demoes", "DEMOS": "DEMOES"}   # the voice only; captions keep the real spelling


def ascii_text(text):
    """Plain ASCII with every word kept apart (dashes become pauses, curly quotes straight) - for the voice and its captions."""
    import re, unicodedata
    t = str(text or "")
    for k, v in TYPO.items():
        if k == "%": continue
        t = t.replace(k, v)
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    t = re.sub(r"\s+,", ",", re.sub(r",\s*,", ",", t))
    return re.sub(r"[ \t]{2,}", " ", t).strip()


def speakable(text, cfg=None):
    """ascii_text + the re-spellings the voice needs (settings 'pronounce': {"word": "spoken as"} adds more)."""
    import re
    t = ascii_text(text)
    say = dict(SAY, **((cfg or {}).get("pronounce") or {}))
    for w, r in say.items():
        t = re.sub(rf"\b{re.escape(w)}\b", r, t)
    return t


def voice_speed(voice):
    """owner, 2026-11-20 ("the narrator speaks in a very hurried voice"): am_puck read at ~193 wpm and am_fenrir ~187 against ~155
    for a calm narrator; those two are slowed to a normal narration pace."""
    return float(VOICE_MENU.get(voice, {}).get("speed", 1.0))


def _pace_key(cfg):
    return engine(cfg) + ("/" + (cfg.get("kokoro_voice") or DEFAULT_VOICE) if engine(cfg) == "kokoro" else "")


def _pace_file(cfg):
    return Path(cfg.get("jobs_dir") or ".").parent / "voice_pace.json"


def learn_pace(cfg, words, secs, log=print):
    """Record the pace a voice really read at (words of narration / seconds spoken), averaged over jobs."""
    if words < 80 or secs < 20: return
    f = _pace_file(cfg); key = _pace_key(cfg)
    try: d = json.loads(f.read_text(encoding="utf-8"))
    except Exception: d = {}
    now = words / secs * 60; old = d.get(key, {}).get("wpm")
    new = round(now if not old else 0.6 * old + 0.4 * now, 1)
    d[key] = {"wpm": new, "n": d.get(key, {}).get("n", 0) + 1}
    f.write_text(json.dumps(d, indent=1), encoding="utf-8")
    log(f"  voice pace: {key} read at {now:.0f} wpm; next Shorts are sized at {new:.0f} wpm")


def wpm(cfg):
    """Speaking pace used to size a Short's script: the pace this voice was measured at in earlier jobs (learn_pace), else the
    cloned narrator's voice_wpm, or kokoro_wpm x the voice's table pace for Kokoro."""
    try:
        got = json.loads(_pace_file(cfg).read_text(encoding="utf-8")).get(_pace_key(cfg), {}).get("wpm")
        if got: return float(got)
    except Exception: pass
    if engine(cfg) in CLONED: return float(cfg.get(engine(cfg) + "_wpm") or CLONED[engine(cfg)]["wpm"])
    if engine(cfg) != "kokoro": return float(cfg.get("voice_wpm", 155))
    v = VOICE_MENU.get(cfg.get("kokoro_voice") or DEFAULT_VOICE, {})
    pace = v.get("pace", 1.0) * v.get("speed", 1.0)   # a faster voice fits more words (fast voices are slowed: voice_speed)
    return float(cfg.get("kokoro_wpm", 140)) * pace


def kokoro_python(cfg):
    p = (cfg.get("kokoro_python") or "").strip()
    if p: return Path(p)
    env = APP / "kenv"
    return env / "Scripts" / "python.exe" if os.name == "nt" else env / "bin" / "python"


def available(cfg):
    """-> "" when Kokoro can run, else why not (shown in the job log)."""
    if engine(cfg) in ("my_original", "my_american"): return cloned_ready(cfg)
    py = kokoro_python(cfg)
    if not py.exists(): return f"the Kokoro environment isn't installed ({py} missing; run install_kokoro in Pinokio)"
    if not WORKER.exists(): return f"{WORKER.name} missing"
    return ""


def lang_of(voice):
    """Kokoro voice ids start with the language letter: a = American English, b = British English (af_/am_/bf_/bm_)."""
    return (voice or "a")[0] if (voice or "a")[0] in "abefhijpz" else "a"


def kokoro_batch(cfg, items, log, tag="voice", stop=lambda: False):
    """items: [(text, out_wav)]. Renders every item that isn't already there in ONE worker process, then converts each to
    48 kHz stereo in place (same format WanGP narration is normalised to). Raises RuntimeError on failure."""
    todo = [(t, Path(o)) for t, o in items if not media.ok(o)]
    if not todo: return
    why = available(cfg)
    if why: raise RuntimeError(f"tts_engine is kokoro but {why}")
    if stop(): raise RuntimeError("cancelled")
    voice = cfg.get("kokoro_voice") or DEFAULT_VOICE
    req = {"voice": voice, "lang": lang_of(voice), "speed": float(cfg.get("kokoro_speed", 1.0) or 1.0) * voice_speed(voice),
           "device": cfg.get("kokoro_device", ""), "items": [{"text": t, "out": str(o.with_suffix(".k24.wav"))} for t, o in todo]}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(req, f); jf = f.name
    t0 = time.time()
    try:
        env = dict(os.environ)
        if not cfg.get("kokoro_online", False): env["HF_HUB_OFFLINE"] = "1"   # model cached by the install: no Hugging Face check per job
        r = subprocess.run([str(kokoro_python(cfg)), str(WORKER), jf], capture_output=True, text=True, timeout=1800,
                           encoding="utf-8", errors="replace", env=env)
    finally:
        try: os.unlink(jf)
        except OSError: pass
    if r.returncode != 0:
        raise RuntimeError(f"Kokoro narration failed: {(r.stderr or r.stdout).strip()[-400:]}")
    for t, o in todo:
        raw = o.with_suffix(".k24.wav")
        if not media.ok(raw, 0.2): raise RuntimeError(f"Kokoro produced no audio for {o.name}")
        media.ff("-i", raw, "-ar", 48000, "-ac", 2, o)
        raw.unlink(missing_ok=True)
    secs = sum(media.dur(o) for _, o in todo)
    log(f"  {tag}: Kokoro ({voice}) rendered {secs:.0f}s of narration in {time.time() - t0:.0f}s ({len(todo)} part(s))")


# ---------------------------------------------------------------- the owner's own voice with a US accent (owner, 2026-11-09)
# "my voice" = Kokoro reads the text in a US voice (my_voice_kokoro), then OpenVoice turns that voice's timbre into the owner's
# (tone-colour conversion from a 30-60 s clean sample, my_voice_ref). Until OpenVoice is installed - its own env, like Kokoro's
# kenv - the Kokoro US voice reads it unchanged. Config:
#   my_voice_ref       a clean recording of the owner speaking (wav / mp4 / mov), 30-60 s
#   my_voice_kokoro    the Kokoro voice under the conversion (default am_michael)
#   openvoice_python   the OpenVoice env's python ("" = app/ovenv/Scripts/python.exe on Windows, app/ovenv/bin/python elsewhere)
# The worker (app/openvoice_worker.py) takes a JSON file {"ref", "device", "items": [{"src", "out"}]} and writes each out wav.
OPENVOICE_WORKER = APP / "openvoice_worker.py"


def openvoice_python(cfg):
    p = cfg.get("openvoice_python") or ""
    if p: return Path(p)
    return APP / "ovenv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def openvoice_ready(cfg):
    """"" when the owner's voice can be made; else why not (shown in the log)."""
    ref = str(cfg.get("my_voice_ref") or "")
    if not ref: return "no sample of your voice set (my_voice_ref)"
    if not Path(ref).is_file(): return f"your voice sample is missing ({ref})"
    if not OPENVOICE_WORKER.exists() or not openvoice_python(cfg).exists(): return "OpenVoice is not installed"
    return ""


def my_voice_id(cfg):
    """What reads the owner's lines right now - stamped next to cached narration so a change re-voices it."""
    kv = cfg.get("my_voice_kokoro") or DEFAULT_VOICE
    if openvoice_ready(cfg): return f"kokoro:{kv}"
    return f"openvoice:{kv}:{Path(str(cfg['my_voice_ref'])).name}:{int(Path(str(cfg['my_voice_ref'])).stat().st_mtime)}"


def my_voice(cfg, items, log, tag="your voice", stop=lambda: False):
    """items: [(text, out_wav)] read in the owner's voice with a US accent (48 kHz stereo, like kokoro_batch)."""
    todo = [(t, Path(o)) for t, o in items if not media.ok(o)]
    if not todo: return
    kcfg = dict(cfg, kokoro_voice=cfg.get("my_voice_kokoro") or DEFAULT_VOICE)
    why = openvoice_ready(cfg)
    if why:
        log(f"  {tag}: {why}; the Kokoro US voice ({kcfg['kokoro_voice']}) reads it")
        kokoro_batch(kcfg, todo, log, tag, stop); return
    base = [(t, o.with_suffix(".base.wav")) for t, o in todo]
    kokoro_batch(kcfg, base, log, tag, stop)
    req = {"ref": str(cfg["my_voice_ref"]), "device": cfg.get("openvoice_device", ""), "tau": float(cfg.get("openvoice_tau", 0.3)),
           "items": [{"src": str(b), "out": str(o.with_suffix(".ov.wav"))} for (_, b), (_, o) in zip(base, todo)]}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(req, f); jf = f.name
    t0 = time.time()
    try:
        r = subprocess.run([str(openvoice_python(cfg)), str(OPENVOICE_WORKER), jf], capture_output=True, text=True, timeout=1800,
                           encoding="utf-8", errors="replace")
    finally:
        try: os.unlink(jf)
        except OSError: pass
    if r.returncode != 0:   # never fail a job over the voice: the Kokoro US voice is still a US voice
        log(f"  {tag}: OpenVoice failed ({(r.stderr or r.stdout).strip()[-200:]}); the Kokoro US voice reads it")
        for (_, b), (_, o) in zip(base, todo): media.ff("-i", b, "-ar", 48000, "-ac", 2, o); b.unlink(missing_ok=True)
        return
    for (_, b), (_, o) in zip(base, todo):
        ov = o.with_suffix(".ov.wav")
        media.ff("-i", ov if media.ok(ov, 0.2) else b, "-ar", 48000, "-ac", 2, o)
        ov.unlink(missing_ok=True); b.unlink(missing_ok=True)
    log(f"  {tag}: your voice (OpenVoice) on {len(todo)} part(s) in {time.time() - t0:.0f}s")


# ---------------------------------------------------------------- the owner's cloned voices (owner, 2026-11-19)
# Two narrator choices, both local and free (no credits), picked per video in the Narration menu:
#   my_original  "My Voice - Original Accent": VoiceStudio's VoxCPM2 clone of the owner (profile "My Voice - YouTube Narration")
#                through its local OpenAI-style API (voicestudio_url). VoiceStudio is started if it is closed, and its model
#                is unloaded after the narration (no model stays in VRAM).
#   my_american  "My Voice - American Accent": MeloTTS U.S. English + OpenVoice V2 tone conversion to the owner's sample, the
#                American Narrator app's models (narrator_cache) run by app/cloned_voice_worker.py in VoiceStudio's VoxCPM2
#                Python env (voxcpm_python); models load once per batch and the process exits after it.
# Settings (defaults below): voicestudio_url, voicestudio_exe, voicestudio_profile, narrator_cache, narrator_ref, voxcpm_python,
# my_original_wpm / my_american_wpm (pace used to size Shorts). A missing piece stops the job with the reason (never silently
# another voice: the owner chose this voice for this video).
CLONED = {"my_original": {"label": "My Voice - Original Accent", "wpm": 137},    # measured 2026-11-19: 136-139 wpm, ~3.4x real time
          "my_american": {"label": "My Voice - American Accent", "wpm": 165}}    # measured 2026-11-19: 165 wpm, ~1x real time
CLONED_WORKER = APP / "cloned_voice_worker.py"


def _vs_url(cfg): return str(cfg.get("voicestudio_url") or "http://127.0.0.1:3900").rstrip("/")
def _appdata(): return Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming")
def _localappdata(): return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")


def voxcpm_python(cfg):
    return Path(cfg.get("voxcpm_python") or _appdata() / "OmniVoice/engines/voxcpm2/voxcpm2/.venv/Scripts/python.exe")


def narrator_cache(cfg): return Path(cfg.get("narrator_cache") or r"F:\audio-transcriber\.openvoice-test")
def narrator_ref(cfg): return Path(cfg.get("narrator_ref") or Path.home() / "OneDrive/Documents/Sound Recordings/my voice clone.m4a")


def _vs_get(cfg, path, timeout=5):
    import urllib.request
    with urllib.request.urlopen(_vs_url(cfg) + path, timeout=timeout) as r: return json.loads(r.read().decode("utf-8"))


def _vs_post(cfg, path, body=None, timeout=600):
    import urllib.request
    req = urllib.request.Request(_vs_url(cfg) + path, data=json.dumps(body or {}).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r: return r.read()


def _voicestudio_up(cfg, start=True, wait=120):
    """True when VoiceStudio's API answers; when closed and start=True, open it and wait for it."""
    try: _vs_get(cfg, "/health"); return True
    except Exception: pass
    if not start: return False
    exe = Path(cfg.get("voicestudio_exe") or _localappdata() / "Programs/VoiceStudio/VoiceStudio.exe")
    if not exe.is_file(): return False
    subprocess.Popen([str(exe)], close_fds=True, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    t0 = time.time()
    while time.time() - t0 < wait:
        time.sleep(3)
        try: _vs_get(cfg, "/health"); return True
        except Exception: pass
    return False


def memory_headroom():
    """-> (free RAM GB, free commit GB). Commit = what Windows can still promise (RAM + page file); when it runs out,
    allocations are refused and PyTorch crashes with an access violation (VoxCPM2 2026-11-14 and 11-19)."""
    try:
        import ctypes
        class MS(ctypes.Structure):
            _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong), ("phys", ctypes.c_ulonglong), ("aphys", ctypes.c_ulonglong),
                        ("page", ctypes.c_ulonglong), ("apage", ctypes.c_ulonglong), ("virt", ctypes.c_ulonglong),
                        ("avirt", ctypes.c_ulonglong), ("aext", ctypes.c_ulonglong)]
        m = MS(); m.len = ctypes.sizeof(MS); ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.aphys / 2**30, m.apage / 2**30
    except Exception:
        try:
            import psutil; v = psutil.virtual_memory(); sw = psutil.swap_memory()
            return v.available / 2**30, (v.available + sw.free) / 2**30
        except Exception: return 99.0, 99.0


def memory_hogs(n=5):
    """The biggest memory users by name (for the owner: what to close)."""
    try:
        import psutil
        tot = {}
        for pr in psutil.process_iter(["name", "memory_info"]):
            try: tot[pr.info["name"]] = tot.get(pr.info["name"], 0) + pr.info["memory_info"].rss
            except Exception: pass
        return ", ".join(f"{k} {v / 2**30:.1f} GB" for k, v in sorted(tot.items(), key=lambda x: -x[1])[:n])
    except Exception: return ""


def wait_for_memory(need_ram, need_commit, log, what, stop=lambda: False, wait=300):
    """Wait (up to `wait` s) until free RAM and commit cover `what`; else raise with what to close (owner: tell me when
    resources are low)."""
    t0, said = time.time(), False
    while True:
        ram, com = memory_headroom()
        if ram >= need_ram and com >= need_commit: return
        if not said:
            log(f"  LOW RESOURCES: {what} needs {need_ram:.0f} GB free RAM and {need_commit:.0f} GB free commit; now {ram:.1f} / "
                f"{com:.1f} GB. Waiting up to {wait // 60} min. Biggest users: {memory_hogs()}"); said = True
        if stop(): raise RuntimeError("cancelled")
        if time.time() - t0 > wait:
            raise RuntimeError(f"Low resources: {what} needs {need_ram:.0f} GB free RAM and {need_commit:.0f} GB free commit "
                               f"(now {ram:.1f} / {com:.1f} GB). Close some apps ({memory_hogs()}), then press Resume.")
        time.sleep(15)


def cloned_ready(cfg, start=True):
    """"" when the chosen own-voice narrator can run, else why not (stops the job before the narration)."""
    e = engine(cfg)
    if e == "my_original":
        if not _voicestudio_up(cfg, start): return "VoiceStudio is not running and could not be started (needed for My Voice - Original Accent)"
        name = cfg.get("voicestudio_profile") or "My Voice - YouTube Narration"
        try:
            d = _vs_get(cfg, "/profiles"); profs = d.get("profiles", d) if isinstance(d, dict) else d
            if not any((x.get("name") or "") == name for x in profs): return f'VoiceStudio has no voice profile named "{name}"'
        except Exception as ex: return f"VoiceStudio's profiles could not be read ({str(ex)[:120]})"
        return ""
    if not CLONED_WORKER.is_file(): return f"{CLONED_WORKER.name} missing"
    if not voxcpm_python(cfg).is_file(): return f"VoiceStudio's VoxCPM2 Python environment is missing ({voxcpm_python(cfg)})"
    if not (narrator_cache(cfg) / "models" / "melo" / "checkpoint.pth").is_file(): return f"the American Narrator model cache is missing ({narrator_cache(cfg)})"
    if not narrator_ref(cfg).is_file(): return f"your voice sample is missing ({narrator_ref(cfg)})"
    return ""


def _vs_unload(cfg, log):
    """Free VoiceStudio's VRAM after the narration (owner: no model stays loaded after use)."""
    try:
        for m in _vs_get(cfg, "/model/loaded").get("models") or []:
            mid = m.get("id") or m.get("model_id") or m.get("name")
            if mid: _vs_post(cfg, f"/model/unload/{mid}", timeout=60)
        _vs_post(cfg, "/system/flush-memory", timeout=60)
    except Exception as ex:
        log(f"  VoiceStudio model not unloaded ({str(ex)[:120]})")


def _takes(text, limit=3800):
    """Text in pieces of at most `limit` characters, split between sentences (VoxCPM2's per-take limit is 4,096)."""
    if len(text) <= limit: return [text]
    out, cur = [], ""
    for sent in re.findall(r"[^.?!]+[.?!]+[\"')\]]*\s*|[^.?!]+$", text):
        if cur and len(cur) + len(sent) > limit: out.append(cur.strip()); cur = ""
        cur += sent
    if cur.strip(): out.append(cur.strip())
    return out


def cloned_batch(cfg, items, log, tag="voice", stop=lambda: False):
    """items: [(text, out_wav)] in the owner's voice (engine my_original / my_american); 48 kHz stereo like kokoro_batch."""
    todo = [(t, Path(o)) for t, o in items if not media.ok(o)]
    if not todo: return
    e = engine(cfg)
    why = cloned_ready(cfg)
    if why: raise RuntimeError(f"{CLONED[e]['label']}: {why}")
    t0 = time.time()
    if e == "my_original":
        name = cfg.get("voicestudio_profile") or "My Voice - YouTube Narration"
        # VoxCPM2 loads a 4.6 GB checkpoint through RAM; short commit memory kills its process mid-load (no error, a crash)
        wait_for_memory(float(cfg.get("voxcpm_need_ram_gb", 6)), float(cfg.get("voxcpm_need_commit_gb", 12)), log,
                        "My Voice - Original Accent", stop)
        try:
            for i, (t, o) in enumerate(todo, 1):
                if stop(): raise RuntimeError("cancelled")
                raws = []
                for k, piece in enumerate(_takes(t)):   # VoxCPM2 takes at most 4,096 characters: split at sentences, never cut
                    body = {"model": "voxcpm2", "voice": name, "input": piece, "response_format": "wav", "language": "en"}
                    for attempt in (1, 2):   # its model process can die once (memory); VoiceStudio respawns it on the next call
                        try: audio = _vs_post(cfg, "/v1/audio/speech", body); break
                        except Exception as ex:
                            detail = ex.read().decode("utf-8", "replace")[-300:] if hasattr(ex, "read") else str(ex)[:300]
                            if attempt == 2 or stop(): raise RuntimeError(f"VoiceStudio could not read part {i} ({detail})")
                            ram, com = memory_headroom()
                            log(f"  {tag}: VoiceStudio failed on part {i} ({detail[:120]}); free RAM {ram:.1f} GB, commit {com:.1f} GB;"
                                " retrying once")
                            wait_for_memory(float(cfg.get("voxcpm_need_ram_gb", 6)), float(cfg.get("voxcpm_need_commit_gb", 12)),
                                            log, "My Voice - Original Accent", stop)
                            time.sleep(10)
                    if audio[:4] != b"RIFF" or audio[8:12] != b"WAVE": raise RuntimeError(f"VoiceStudio returned no WAV for part {i}")
                    raw = o.with_suffix(f".vs{k}.wav"); raw.write_bytes(audio); raws.append(raw)
                if len(raws) == 1: media.ff("-i", raws[0], "-ar", 48000, "-ac", 2, o)
                else:
                    ins = [a for r in raws for a in ("-i", r)]
                    media.ff(*ins, "-filter_complex", "".join(f"[{n}:a]" for n in range(len(raws))) + f"concat=n={len(raws)}:v=0:a=1",
                             "-ar", 48000, "-ac", 2, o)
                for r in raws: r.unlink(missing_ok=True)
                if i % 10 == 0: log(f"  {tag}: {i}/{len(todo)} part(s) in your original voice")
        finally:
            _vs_unload(cfg, log)
    else:
        ref = Path(tempfile.gettempdir()) / f"studio_voice_ref_{os.getpid()}.wav"
        media.ff("-i", narrator_ref(cfg), "-map", "0:a:0", "-ac", 1, "-c:a", "pcm_s16le", ref)
        req = {"cache": str(narrator_cache(cfg)), "ref": str(ref), "speed": float(cfg.get("narrator_speed", 0.94)),
               "tau": float(cfg.get("narrator_tau", 0.12)),
               "items": [{"text": t, "out": str(o.with_suffix(".am.wav"))} for t, o in todo]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(req, f); jf = f.name
        try:
            env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHONIOENCODING": "utf-8"}
            p = subprocess.Popen([str(voxcpm_python(cfg)), str(CLONED_WORKER), jf], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8", errors="replace", env=env)
            tail = []
            for line in p.stdout:
                tail = (tail + [line.rstrip()])[-30:]
                if line.startswith("pronounced by letter-to-sound:"): log(f"  {tag}: {line.strip()} (not in the dictionary)")
                if stop(): p.kill(); raise RuntimeError("cancelled")
                if line.startswith("part ") and line.split()[1].split("/")[0].isdigit() and int(line.split()[1].split("/")[0]) % 10 == 0:
                    log(f"  {tag}: {line.strip()} in your American-accent voice")
            if p.wait() != 0: raise RuntimeError(f"American Accent narration failed: {' | '.join(tail[-6:])[-500:]}")
        finally:
            for x in (jf, ref):
                try: os.unlink(x)
                except OSError: pass
        for t, o in todo:
            raw = o.with_suffix(".am.wav")
            if not media.ok(raw, 0.2): raise RuntimeError(f"American Accent produced no audio for {o.name}")
            media.ff("-i", raw, "-ar", 48000, "-ac", 2, o); raw.unlink(missing_ok=True)
    for t, o in todo:
        if not media.ok(o, 0.2): raise RuntimeError(f"{CLONED[e]['label']} produced no valid audio for {o.name}")
    secs = sum(media.dur(o) for _, o in todo)
    log(f"  {tag}: {CLONED[e]['label']} read {secs:.0f}s of narration in {time.time() - t0:.0f}s ({len(todo)} part(s))")
