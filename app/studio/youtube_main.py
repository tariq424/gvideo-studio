"""owner, 2026-11-12: "every new video only that was successfully created and if short too then short has a link to the
video. always a private save." Every job that finishes successfully is saved to the owner's main YouTube channel (the
publisher's `tariqhasnain` channel), Private, through the YouTube Publisher on 42030: the long video first, then the Short,
whose Related video and description link point to it (/publish/pair). Runs in the background like the Google Photos backup:
it never holds up or fails a job; a busy or stopped publisher is retried later, and at the next server start.

Only new videos: a job that already has a record on the main channel is skipped (an editor re-assemble is not re-uploaded).
Settings: youtube_main_on (true), youtube_main_channel ("tariqhasnain"), publisher_url ("http://127.0.0.1:42030").
"""
import json, threading, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

SINCE = 1794520800          # 2026-11-12 17:00: jobs finished from here on are saved by the start-up sweep
RETRY = 10 * 60             # publisher busy or down: try again in 10 minutes
TRIES = 12                  # then stop until the next server start (a permanent error must not loop all day)
LEDGER = "youtube_main.json"
_due, _cv, _thread, _tries = {}, threading.Condition(), None, {}


def _log_to(job_dir, log):
    def f(msg):
        line = f"[{time.strftime('%H:%M:%S')}] main channel: {msg}"
        try:
            with open(Path(job_dir) / "backup.log", "a", encoding="utf-8") as fh: fh.write(line + "\n")
        except OSError: pass
        if log:
            try: log("main channel: " + msg)
            except Exception: pass
    return f


def _state(job_dir):
    try: return json.loads((Path(job_dir) / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError): return {}


def outputs(job_dir):
    """The finished job's videos: [("long", path), ("short", path)] for those that exist; [] unless the job finished."""
    s = _state(job_dir)
    if s.get("status") != "finished": return []
    out = [(k, Path(s[f])) for k, f in (("long", "output"), ("short", "short_output")) if s.get(f)]
    return [(k, p) for k, p in out if p.is_file() and p.stat().st_size > 0]


def _http(cfg, path, data=None, form=None, timeout=60):
    url = str(cfg.get("publisher_url") or "http://127.0.0.1:42030").rstrip("/") + path
    if form is not None:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST")
    elif data is not None:
        req = urllib.request.Request(url, data=json.dumps(data).encode(), headers={"Content-Type": "application/json"}, method="POST")
    else: req = urllib.request.Request(url)
    try: return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except urllib.error.HTTPError as e:
        try: msg = json.loads(e.read()).get("error") or str(e)
        except Exception: msg = str(e)
        raise RuntimeError(msg) from None


AI_WORDS = ("AI-", "AI ", "generated", "GENERATED")


def _get_bytes(cfg, path, timeout=60):
    url = str(cfg.get("publisher_url") or "http://127.0.0.1:42030").rstrip("/") + path
    return urllib.request.urlopen(url, timeout=timeout).read()


def _post_file(cfg, path, fields, data, name="thumb.jpg", timeout=60):
    """multipart/form-data POST of one image (the publisher's /api/thumbnail takes an uploaded file)."""
    import uuid
    b = uuid.uuid4().hex
    body = b"".join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items())
    body += f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\nContent-Type: image/jpeg\r\n\r\n'.encode() + data + f"\r\n--{b}--\r\n".encode()
    url = str(cfg.get("publisher_url") or "http://127.0.0.1:42030").rstrip("/") + path
    req = urllib.request.Request(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={b}"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def _face_frame(job_dir):
    """A long video's thumbnail frame: the guest on camera - partway into the first soundbite after the opening seconds
    (a frame from a set time can land on a generated picture or a title card). None when the job has no manifest."""
    try: m = json.loads((Path(job_dir) / "iedit" / "long.json").read_text(encoding="utf-8"))
    except (OSError, ValueError): return None
    from . import media
    t = 0.0
    for part in m.get("parts") or []:
        try: d = media.dur(part["file"])
        except Exception: d = float(part.get("dur") or 0)
        if (part.get("m") or {}).get("type") == "bite" and d >= 4 and t >= 5:
            covered = [(float(c[0]), float(c[1])) for c in part.get("credits") or [] if isinstance(c, (list, tuple)) and len(c) > 1]
            for x in [1.5 + 0.5 * i for i in range(int((d - 2) / 0.5))]:   # the guest's own picture, no overlay on it
                if not any(a - 0.5 <= x <= b + 0.5 for a, b in covered): return t + x
        t += d
    return None


def manifest_meta(job_dir, kind):
    """An interview job's own metadata from its edit manifest (iedit/long.json / short.json): title, chapters at the real
    piece lengths, picture credits, and whether any picture is AI-made. {} when the job has no manifest."""
    p = Path(job_dir) / "iedit" / f"{kind}.json"
    try: m = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError): return {}
    from . import media
    t, chapters, credits, ai = 0.0, [], [], False
    for part in m.get("parts") or []:
        if kind == "long" and part.get("chapter"): chapters.append({"seconds": int(t), "title": str(part["chapter"])[:100]})
        for c in part.get("credits") or []:
            who = str(c[2] if isinstance(c, (list, tuple)) and len(c) > 2 else c)
            if any(w in who for w in AI_WORDS): ai = True
            elif who and who not in credits: credits.append(who)
        mt = (part.get("m") or {})
        if mt.get("type") == "fact":   # an insert's picture: the ladder rung that made it sits beside the piece
            try:
                rung = str(json.loads(Path(part["file"]).with_suffix(".json").read_text(encoding="utf-8")).get("rung") or "")
                if rung.startswith("ai") or "gen" in rung: ai = True
            except (OSError, ValueError, KeyError): pass
        try: t += media.dur(part["file"])
        except Exception: t += float(part.get("dur") or 0)
    gen = Path(job_dir) / "iwork" / "imagegen"   # pictures this job generated (disclosure: over-flagging only adds a label)
    if gen.is_dir() and any(f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".mp4") for f in gen.rglob("*")): ai = True
    if chapters and chapters[0]["seconds"] > 0: chapters.insert(0, {"seconds": 0, "title": "Coming up"})
    if len(chapters) < 3: chapters = []        # YouTube needs at least three
    try: guest = str(json.loads((Path(job_dir) / "iwork" / "triage.json").read_text(encoding="utf-8")).get("guest") or "").strip()
    except (OSError, ValueError): guest = ""
    title = str(m.get("title") or "").strip()
    desc = [title] + ([f"A conversation with {guest}."] if guest else [])
    if ai: desc.append("Some pictures in this video are AI-generated illustrations.")
    if credits: desc.append("Credits:\n" + "\n".join(credits))
    return dict(title=title, description="\n\n".join(x for x in desc if x), chapters=chapters, altered_content=ai)


def _draft(cfg, job, kind, channel, title, length, job_dir=None, published=()):
    """The publisher's defaults for this job, filled in: metadata already checked on another channel for this same video
    wins; else an interview job's own manifest; else the job's title (the default falls back to the file name). A frame
    thumbnail when the job made none."""
    d = _http(cfg, f"/api/defaults/{job}/{kind}/{channel}")["data"]
    seen = next((r for r in published if r.get("job") == job and r.get("kind") == kind and r.get("channel") != channel
                 and not r.get("deleted") and r.get("metadata")), None)
    if seen:
        md = seen["metadata"]
        d.update({k: md[k] for k in ("title", "description", "tags", "chapters", "altered_content", "category", "language") if k in md})
        if kind == "short":   # the publisher puts this channel's own video link back in
            d["description"] = "\n".join(l for l in d["description"].splitlines() if not l.startswith("Full breakdown:")).rstrip()
    else:
        mm = manifest_meta(job_dir, kind) if job_dir else {}
        if mm.get("title"): title = mm["title"]
        d.update({k: v for k, v in mm.items() if k != "title" and (v or k == "altered_content")})
        if title:
            d["title"] = (title if kind == "long" or "#shorts" in title else (title[:90] + " #shorts"))[:100]
    if seen and seen["metadata"].get("thumbnail") and not d.get("thumbnail"):   # the thumbnail already chosen for this video
        try:
            img = _get_bytes(cfg, f"/api/thumbnail/{seen['channel']}/{job}/{kind}/{seen['metadata']['thumbnail']}")
            d["thumbnail"] = _post_file(cfg, "/api/thumbnail", dict(job=job, channel=channel, kind=kind), img)["thumbnail"]
        except Exception: pass
    if not d.get("thumbnail"):
        at = 2.5 if kind == "short" else (_face_frame(job_dir) if job_dir else None) or max(1.0, min(60.0, length * 0.1))
        d["thumbnail"] = _http(cfg, "/api/thumbnail", form=dict(job=job, channel=channel, kind=kind, seconds=f"{at:.2f}"))["thumbnail"]
    d.update(visibility="Private", channel=channel)   # always a private save
    return d


def process(cfg, job_dir, log=None):
    """Save this job's videos to the main channel. -> True when done (or nothing to do), False to retry later."""
    say = _log_to(job_dir, log)
    if not cfg.get("youtube_main_on", True): return True
    vids = dict(outputs(job_dir))
    if "long" not in vids:
        if vids: say("only a Short was made; nothing saved (a Short needs its video to link to)")
        return True
    job, channel = Path(job_dir).name, str(cfg.get("youtube_main_channel") or "tariqhasnain")
    try: published = _http(cfg, "/api/published")
    except Exception as e:
        say(f"YouTube Publisher not reachable ({str(e)[:120]}); retrying in {RETRY // 60} min"); return False
    mine = {r.get("kind"): r for r in published if r.get("channel") == channel and r.get("job") == job and not r.get("deleted")}
    if "long" in mine and "short" in mine and mine["short"].get("related_video_id") != mine["long"].get("video_id"):
        return _link_only(cfg, mine, say)   # both up, the Short's Related video not set yet (see _link_only)
    if "long" in mine and ("short" not in vids or "short" in mine):
        return True   # already saved: only new videos are uploaded
    s = _state(job_dir)
    title = str(s.get("title") or "").strip()
    try:
        from . import media
        long_d = _draft(cfg, job, "long", channel, title, media.dur(vids["long"]), job_dir, published)
        if "short" in vids:
            short_d = _draft(cfg, job, "short", channel, title, media.dur(vids["short"]), job_dir, published)
            if "long" in mine:   # the video is up, its Short is not: the Short alone, linked to it
                short_d.update(link_target=mine["long"]["video_id"], long_url=mine["long"]["url"])
                ids = {"short_task": _http(cfg, "/publish", data=short_d)["task_id"]}
            else:
                ids = _http(cfg, "/publish/pair", data={"long": long_d, "short": short_d})
        else:
            ids = {"long_task": _http(cfg, "/publish", data=long_d)["task_id"]}
    except Exception as e:
        msg = str(e)[:200]
        busy = "in progress" in msg or "shared Chrome profile" in msg
        say(("publisher busy" if busy else f"not saved ({msg})") + f"; retrying in {RETRY // 60} min")
        return False
    say(f"saving Private to {channel}: " + ", ".join(sorted(k.replace('_task', '') for k in ids)))
    last = ids.get("short_task") or ids.get("long_task")
    deadline = time.time() + 3 * 3600
    while time.time() < deadline:
        time.sleep(30)
        try: t = _http(cfg, f"/publish/status/{last}")
        except Exception: continue
        if t.get("status") in ("complete", "error", "cancelled"): break
    rows = {}
    for k, tid in ids.items():
        try: t = _http(cfg, f"/publish/status/{tid}")
        except Exception as e: t = {"status": "unknown", "error": str(e)[:120]}
        r = t.get("result") or {}
        rows[k.replace("_task", "")] = dict(status=t.get("status"), error=t.get("error"), url=r.get("url"),
                                            related=r.get("related_video_id"), link_error=r.get("link_error"))
        say(f"{k.replace('_task', '')}: {t.get('status')} {r.get('url') or t.get('error') or ''}"
            + (f" (link: {r.get('link_error')})" if r.get("link_error") else ""))
    try:
        p = Path(job_dir) / "output" / LEDGER
        led = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        led[time.strftime("%Y-%m-%dT%H:%M:%S")] = dict(channel=channel, **rows)
        p.write_text(json.dumps(led, indent=1), encoding="utf-8")
    except (OSError, ValueError): pass
    # a Short whose Related video couldn't be set yet counts as not done: the retry sets only the link (_link_only)
    return all(v["status"] == "complete" for v in rows.values()) and not any(v.get("link_error") for v in rows.values())


def _link_only(cfg, mine, say):
    """YouTube offers a long video as a Short's Related video only some time after its upload (minutes to hours: it is not
    in Studio's picker yet), so the pair's own link step often fails. Retried here: the Short is reopened (not re-uploaded)
    and only its Related video is set. -> True once it points at the long video."""
    target = mine["long"]["video_id"]
    body = dict(mine["short"].get("metadata") or {}, mode="resume", link_target=target)
    body.pop("confirm_replace", None)
    try: tid = _http(cfg, "/publish", data=body)["task_id"]
    except Exception as e:
        say(f"Short link not set yet ({str(e)[:120]})"); return False
    t = {}
    for _ in range(60):
        time.sleep(15)
        try: t = _http(cfg, f"/publish/status/{tid}")
        except Exception: continue
        if t.get("status") in ("complete", "error", "cancelled"): break
    r = (t or {}).get("result") or {}
    ok = r.get("related_video_id") == target
    say(f"Short's Related video -> {target}: " + ("set" if ok else f"not yet ({str(r.get('link_error') or t.get('error'))[:100]}); retrying later"))
    return ok


def queue(job_dir, log=None, delay=0):
    """Ask the worker to save this job (after `delay` s; a later queue of the same job moves the time)."""
    global _thread
    with _cv:
        _due[str(job_dir)] = (time.time() + delay, log)
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_worker, name="youtube-main", daemon=True); _thread.start()
        _cv.notify()


def _worker():
    from . import config
    while True:
        with _cv:
            while not _due: _cv.wait()
            jd, (t, log) = min(_due.items(), key=lambda kv: kv[1][0])
            if t > time.time(): _cv.wait(timeout=min(60, t - time.time())); continue
            _due.pop(jd)
        try: ok = process(config.load(), jd, log)
        except Exception as e:
            _log_to(jd, log)(f"failed ({type(e).__name__}: {str(e)[:200]})"); ok = False
        if ok: _tries.pop(jd, None); continue
        _tries[jd] = _tries.get(jd, 0) + 1
        if _tries[jd] < TRIES: queue(jd, log, delay=RETRY)
        else: _log_to(jd, log)(f"gave up after {TRIES} tries; retried at the next Video Studio start")


def sweep(jobs_dir):
    """Server start: queue every job finished since SINCE (its state saved since then) that may not be on the main channel."""
    n = 0
    for d in sorted(Path(jobs_dir).iterdir()) if Path(jobs_dir).is_dir() else []:
        st = d / "state.json"
        if st.is_file() and st.stat().st_mtime >= SINCE and outputs(d):
            queue(d, delay=180 + 60 * n); n += 1
    return n
