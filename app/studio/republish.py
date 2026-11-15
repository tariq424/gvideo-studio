"""owner, 2026-11-14: "If I save the edited video, it replaces its predecessor in both publisher and newsroom."

The editor's "Save & replace published" (server: /api/jobs/<jid>/edit_all/save) ends the editing session - a paused
pipeline goes on - and queues this background worker for the job:
  1. Newsroom (42040, /api/edited) copies the edited files over its staged copy. The Publisher reads that staged copy before
     Video Studio's own folder, so nothing is uploaded until the Publisher sees the new file (checked by size).
  2. Every video of the job that is up on YouTube (any channel, the Publisher's records) and was changed after its upload is
     replaced there with the Publisher's Replace: the new version is uploaded first, with the same title, description, tags,
     thumbnail and visibility; chapters move with any piece you inserted. A Short whose explainer was replaced follows it
     (re-uploaded with the new link, and its Related video set to it), so it never points at the old upload.
  3. Newsroom gets the new links. The OLD uploads stay on YouTube until you delete them (Newsroom's player, or the
     Publisher's page) or keep both - never deleted automatically (the standing rule: upload first, delete on a separate
     confirm).
One upload at a time; a busy or stopped Publisher / Newsroom is retried every 10 minutes (and at the next server start).
Settings: republish_on (true), newsroom_url ("http://127.0.0.1:42040"), publisher_url.
"""
import json
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from . import youtube_main as ym

RETRY = 10 * 60
TRIES = 12
LEDGER = "republish.json"
_due, _cv, _thread, _tries = {}, threading.Condition(), None, {}


class Blocked(Exception):
    """Needs you (an older replace still waiting for its delete / keep): not retried until the next Save."""


def _say(job_dir, log):
    def f(msg):
        line = f"[{time.strftime('%H:%M:%S')}] replace published: {msg}"
        try:
            with open(Path(job_dir) / "backup.log", "a", encoding="utf-8") as fh: fh.write(line + "\n")
        except OSError: pass
        if log:
            try: log("Replace published: " + msg)
            except Exception: pass
    return f


def _state(job_dir):
    return ym._state(job_dir)


def _save_state(job_dir, **kw):
    from .pipeline import Job
    Job(job_dir).save(**kw)


def _newsroom(cfg, body, timeout=120):
    url = str(cfg.get("newsroom_url") or "http://127.0.0.1:42040").rstrip("/") + "/api/edited"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def rec_time(r):
    s = str(r.get("timestamp") or "")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try: return datetime.strptime(s, fmt).timestamp()
        except ValueError: continue
    return 0.0


def plan(job_dir, published):
    """-> {channel: [kind, ...]} of the job's uploads to replace, explainer before its Short. A video is replaced when its
    file changed after it was uploaded; a Short also when its explainer on that channel is being replaced, or when its
    description doesn't carry that explainer's current link (a Short left behind by an earlier, partly finished replace)."""
    jid = Path(job_dir).name; vids = dict(ym.outputs(job_dir)); out = {}
    recs = {}
    for r in published:
        if r.get("job") == jid and not r.get("deleted") and r.get("kind") in vids: recs.setdefault(r["channel"], {})[r["kind"]] = r
    for ch, kinds in recs.items():
        todo = [k for k in ("long", "short") if k in kinds and vids[k].stat().st_mtime > rec_time(kinds[k]) + 1]
        if "short" in kinds and "short" not in todo and "long" in kinds:
            lr = kinds["long"]
            if "long" in todo or (lr.get("url") and lr["url"] not in str((kinds["short"].get("metadata") or {}).get("description") or "")):
                todo.append("short")
        if todo: out[ch] = todo
    return out, recs


def shift_chapters_list(chapters, inserts):
    """Chapter list [{seconds, title}] after the pieces inserted since the upload (each moves chapters at / after it)."""
    ch = [dict(c) for c in chapters or []]
    for ins in sorted(inserts, key=lambda x: x.get("time", 0)):
        for c in ch:
            if c["seconds"] > 0 and c["seconds"] >= ins["t"]: c["seconds"] = int(round(c["seconds"] + ins["secs"]))
    return ch


def _body(job_dir, rec, kind, new_long=None):
    """The replace request: the old upload's own metadata (title, description, tags, thumbnail, visibility...)."""
    from . import insert
    b = dict(rec.get("metadata") or {})
    for k in ("mode", "confirm_replace", "confirm_restart", "long_link"): b.pop(k, None)
    b.update(job=Path(job_dir).name, kind=kind, channel=rec["channel"], mode="replace", confirm_replace=True)
    if kind == "long":
        t0 = rec_time(rec)
        ins = [x for x in _state(job_dir).get("inserts", []) if x.get("kind") == "explainer" and x.get("time", 0) > t0]
        if ins:
            b["chapters"] = shift_chapters_list(b.get("chapters"), ins)
            for x in sorted(ins, key=lambda y: y.get("time", 0)): b["description"] = insert.shift_chapters(b.get("description", ""), x["t"], x["secs"])
    if kind == "short" and new_long:
        old = new_long.get("old_url")
        d = str(b.get("description") or "")
        if old and old in d: d = d.replace(old, new_long["url"])
        else: d = "\n".join(l for l in d.splitlines() if not l.startswith("Full breakdown:")).rstrip()   # the publisher adds the new link
        b.update(description=d, long_url=new_long["url"], link_target=new_long["video_id"])
    return b


def _wait(cfg, tid, hours=3):
    t = {}; deadline = time.time() + hours * 3600
    while time.time() < deadline:
        time.sleep(30)
        try: t = ym._http(cfg, f"/publish/status/{tid}")
        except Exception: continue
        if t.get("status") in ("complete", "error", "cancelled"): break
    return t


def process(cfg, job_dir, log=None):
    """-> True when done (or nothing to do / needs you), False to retry later."""
    say = _say(job_dir, log); jid = Path(job_dir).name; st = _state(job_dir)
    if not cfg.get("republish_on", True): return True
    if not st.get("republish_req"): return True
    pend = st.get("republish_notify")   # links from an earlier run that Newsroom hasn't taken yet
    try:
        _newsroom(cfg, {"job": jid, **({"replaced": pend} if pend else {})})
        if pend: _save_state(job_dir, republish_notify=None)
        nr = True
    except Exception as e:
        nr = False; say(f"Newsroom not reachable ({str(e)[:100]}); its staged copy is not refreshed yet")
    try: published = ym._http(cfg, "/api/published")
    except Exception as e:
        say(f"YouTube Publisher not reachable ({str(e)[:100]}); retrying in {RETRY // 60} min"); return False
    todo, recs = plan(job_dir, published)
    if not todo:
        say("nothing on YouTube needs replacing" + ("" if nr else " (Newsroom will be refreshed on a later try)"))
        if nr: _save_state(job_dir, republish_req=None, republish_done=time.time())
        return nr
    vids = dict(ym.outputs(job_dir)); done = []
    try:
        for ch, kinds in todo.items():
            new_long = None
            for kind in kinds:
                size = vids[kind].stat().st_size
                try: seen = ym._http(cfg, f"/api/defaults/{jid}/{kind}/{ch}").get("media") or {}
                except Exception as e: raise RuntimeError(f"Publisher can't read the {kind} ({str(e)[:100]})")
                if int(seen.get("size") or 0) != size:
                    raise RuntimeError(f"the Publisher still sees the old {kind} file ({seen.get('filename')}, {seen.get('size')} bytes, "
                                       f"edited one {size}) - Newsroom's staged copy not refreshed yet")
                old = recs[ch][kind]
                lr = recs[ch].get("long")   # a Short follows the explainer that is up on this channel (new or not)
                nl = new_long or ({"url": lr.get("url"), "video_id": lr.get("video_id"), "old_url": None} if lr and lr.get("url") else None)
                body = _body(job_dir, old, kind, nl if kind == "short" else None)
                try: tid = ym._http(cfg, "/publish", data=body)["task_id"]
                except RuntimeError as e:
                    if "delete it (or keep it) first" in str(e): raise Blocked(f"{kind} on {ch}: {e}")
                    raise
                say(f"replacing the {kind} on {ch} (old {old.get('video_id')}); the old one stays until you delete it")
                t = _wait(cfg, tid); r = t.get("result") or {}
                if t.get("status") != "complete":
                    raise RuntimeError(f"{kind} on {ch}: {t.get('status')} {str(t.get('error') or '')[:150]}")
                entry = {"channel": ch, "kind": kind, "url": r.get("url"), "video_id": r.get("video_id"),
                         "old_url": old.get("url"), "old_video_id": old.get("video_id"), "link_error": r.get("link_error")}
                done.append(entry)
                say(f"{kind} on {ch}: new upload {r.get('url')}" + (f" (link: {r.get('link_error')})" if r.get("link_error") else ""))
                if kind == "long": new_long = entry
    except Blocked as e:
        say(f"needs you: {e}"); _finish(cfg, job_dir, done, say, blocked=str(e)); return True
    except Exception as e:
        say(f"stopped ({str(e)[:200]}); retrying in {RETRY // 60} min"); _finish(cfg, job_dir, done, say, final=False); return False
    _finish(cfg, job_dir, done, say)
    return True


def _finish(cfg, job_dir, done, say, blocked=None, final=True):
    if done:
        try: _newsroom(cfg, {"job": Path(job_dir).name, "replaced": done})
        except Exception as e:
            say(f"Newsroom not told the new links yet ({str(e)[:100]}); told on the next try")
            _save_state(job_dir, republish_notify=(_state(job_dir).get("republish_notify") or []) + done)
        try:
            p = Path(job_dir) / "output" / LEDGER
            led = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
            led[time.strftime("%Y-%m-%dT%H:%M:%S")] = {"replaced": done, **({"blocked": blocked} if blocked else {})}
            p.write_text(json.dumps(led, indent=1), encoding="utf-8")
        except (OSError, ValueError): pass
    if final:
        _save_state(job_dir, republish_req=None, republish_done=time.time(), **({"republish_blocked": blocked} if blocked else {"republish_blocked": None}))
        if done: say("done: " + ", ".join(f"{d['kind']} on {d['channel']}" for d in done) + ". The old uploads are still on YouTube until you delete them.")


def request(job_dir, log=None):
    """The editor's Save: remember the request (survives a restart) and queue the worker."""
    _save_state(job_dir, republish_req=time.time())
    queue(job_dir, log)


def queue(job_dir, log=None, delay=0):
    global _thread
    with _cv:
        _due[str(job_dir)] = (time.time() + delay, log)
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_worker, name="republish", daemon=True); _thread.start()
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
            _say(jd, log)(f"failed ({type(e).__name__}: {str(e)[:200]})"); ok = False
        if ok: _tries.pop(jd, None); continue
        _tries[jd] = _tries.get(jd, 0) + 1
        if _tries[jd] < TRIES: queue(jd, log, delay=RETRY)
        else: _say(jd, log)(f"gave up after {TRIES} tries; tried again at the next Video Studio start")


def sweep(jobs_dir):
    """Server start: jobs whose Save & replace hasn't finished are queued again."""
    n = 0
    for d in sorted(Path(jobs_dir).iterdir()) if Path(jobs_dir).is_dir() else []:
        st = d / "state.json"
        if not st.is_file(): continue
        try: s = json.loads(st.read_text(encoding="utf-8"))
        except (OSError, ValueError): continue
        if s.get("republish_req") or s.get("republish_notify"):
            if not s.get("republish_req"): _save_state(d, republish_req=time.time())
            queue(d, delay=240 + 60 * n); n += 1
    return n
