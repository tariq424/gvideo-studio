"""Finished videos -> the owner's Google Photos, and his own footage's day -> the media database (owner, 2026-11-09).

    "I want any video completed to be automatically uploaded as private ... otherwise I may lose local F: drive data
     which is USB drive."  (Google Photos chosen over YouTube: the original file, private, and no clash with publishing.)
    "As a part of any of my own videos, check meta data, then look at my google photos for any meta data matching that
     location. If found then index all data for that matching date into our database for future use."

One background worker, one job at a time, with the pipeline's own signed-in Photos profile (gphotos.PROFILE):
  1. backup - every finished video of the job (state output / short_output) not yet uploaded at its current size and time
     goes up with Create -> Import photos; output/gphotos_backup.json records each upload and its Photos link. A video the
     editor re-assembles is uploaded again once the edits settle (queue(delay=...)); the older copy stays in Photos.
  2. index  - for each of the job's own source videos (an interview's footage and extras, or an uploaded own-footage
     video): the filming place (GPS tag) and day. Photos is searched for that day; when its items are at that place (the
     info panel's place / pin), every item of the day gets a media_db row (link, file name, date, time, place, pin).
     A day is indexed once (media_db folder / indexed_days.json); rows already complete are not opened again.
Never fails a job: anything that goes wrong is logged and retried on the next queue / server start. A Google sign-in or
check page stops the worker (never typed or clicked through)."""
import json
import math
import re
import subprocess
import threading
import time
from pathlib import Path

from . import gphotos, media_db

SINCE = 1794263000          # 2026-11-09 17:30: jobs finished from here on are backed up by the start-up sweep
LEDGER = "gphotos_backup.json"
EDIT_SETTLE = 15 * 60       # an editor re-assemble is uploaded after 15 quiet minutes
VIDEO = {".mov", ".mp4", ".m4v", ".mkv", ".avi", ".webm", ".3gp"}
MONTHS = "January February March April May June July August September October November December".split()

_cv = threading.Condition()
_due = {}                   # job dir -> time it may run
_thread = None


def _log_to(job_dir, log):
    if log: return log
    def w(msg):
        try:
            with open(Path(job_dir) / "backup.log", "a", encoding="utf-8") as f: f.write(time.strftime("%H:%M:%S ") + msg + "\n")
        except OSError: pass
    return w


def outputs(job_dir):
    """The job's finished videos (long and Short) that exist."""
    try: s = json.loads((Path(job_dir) / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError): return []
    if s.get("status") != "finished": return []
    return [Path(s[k]) for k in ("output", "short_output") if s.get(k) and Path(s[k]).is_file()]


def _ledger(job_dir):
    f = Path(job_dir) / "output" / LEDGER
    try: return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError): return {}


def _stamp(f):
    st = Path(f).stat(); return f"{st.st_size}:{int(st.st_mtime)}"


def pending(job_dir):
    led = _ledger(job_dir)
    return [f for f in outputs(job_dir) if (led.get(f.name) or {}).get("stamp") != _stamp(f)]


def own_sources(job_dir):
    """The owner's own footage behind the job: an interview's main video and extras, or an uploaded video of his."""
    jd = Path(job_dir)
    try: s = json.loads((jd / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError): return []
    out = []
    iv = s.get("interview") or {}
    for e in [iv.get("src")] + list(iv.get("extras") or []):
        p = e if isinstance(e, str) else (e or {}).get("src")
        if p and Path(p).is_file(): out.append(Path(p))
    try: own = json.loads((jd / "src" / "meta.json").read_text(encoding="utf-8")).get("own_footage")
    except (OSError, ValueError): own = False
    if own: out += [p for p in sorted((jd / "src").iterdir()) if p.suffix.lower() in VIDEO]
    return out


def where_when(f):
    """(lat, lon, 'YYYY-MM-DD' local day) from a phone video's tags, or None."""
    try:
        t = json.loads(subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(f)],
                                      capture_output=True, text=True, timeout=30).stdout or "{}").get("format", {}).get("tags", {})
    except Exception: return None
    m = re.match(r"([+-]\d+\.\d+)([+-]\d+\.\d+)", t.get("com.apple.quicktime.location.ISO6709") or t.get("location") or "")
    d = re.match(r"(\d{4}-\d\d-\d\d)", t.get("com.apple.quicktime.creationdate") or t.get("creation_time") or "")
    return (float(m[1]), float(m[2]), d[1]) if m and d else None


def place_of(lat, lon):
    """{'city', 'country'} for a pin (OpenStreetMap Nominatim, one request; generic client name)."""
    try:
        import requests
        j = requests.get("https://nominatim.openstreetmap.org/reverse", timeout=30, headers={"User-Agent": "VideoStudio/1.0 (personal media index)"},
                         params={"lat": lat, "lon": lon, "format": "json", "zoom": 10}).json()
        a = j.get("address", {})
        return {"city": a.get("city") or a.get("town") or a.get("village") or a.get("county") or "", "country": a.get("country") or ""}
    except Exception:
        return {"city": "", "country": ""}


def photos_day(day):
    """'2025-11-26' -> ('November 26, 2025' search, 'Nov 26, 2025' as the info panel writes it)."""
    y, m, d = (int(x) for x in day.split("-"))
    return f"{MONTHS[m - 1]} {d}, {y}", f"{MONTHS[m - 1][:3]} {d}, {y}"


def _km(a, b):
    return 6371 * math.hypot(math.radians(a[0] - b[0]), math.radians(a[1] - b[1]) * math.cos(math.radians(a[0])))


def at_place(row, pin, place):
    """Is a Photos item (its info panel) at the filming place? Its pin within 30 km, or its place line naming the town / country."""
    if row.get("lat") not in (None, "") and row.get("lon") not in (None, ""):
        try: return _km((float(row["lat"]), float(row["lon"])), pin) <= 30
        except ValueError: pass
    where = str(row.get("place") or "").lower()
    return bool(where) and any(n and n.lower() in where for n in (place.get("city"), place.get("country")))


def _days_file(cfg):
    return media_db.path(cfg).parent / "indexed_days.json"


def index_day(page, db, cfg, pin, day, place, why, log=print, probe=12):
    """Index every Photos item of `day` when the day's items are at `pin`. -> rows written (0 when the place didn't match)."""
    q, label = photos_day(day)
    hits = gphotos.harvest(page, q, log=log)
    log(f"  Google Photos: '{q}' -> {len(hits)} item(s)")
    if not hits: return 0
    have = {u for u, fn, dt in db.execute("SELECT url, file_name, date FROM items") if fn and dt}
    topic = ", ".join(x for x in (place.get("city"), place.get("country")) if x)
    seen_here, held, rows = False, [], 0   # items read before the place is confirmed wait in `held`
    for k, h in enumerate(hits):
        u = media_db.canon(h["url"])
        if u in have:
            r = dict(zip(media_db.COLS, db.execute("SELECT * FROM items WHERE url=?", (u,)).fetchone()))
            seen_here = seen_here or (r.get("date") == label and at_place(r, pin, place))
            continue
        if not seen_here and k >= probe:   # none of the first items is from there: this day's photos are elsewhere
            log(f"  none of the first {probe} items of {label} is at {topic or pin}; that day is not indexed")
            return 0
        d = gphotos.details(page, h["url"])
        time.sleep(2)
        if d.get("date") != label: continue   # the search also brings near matches
        held.append(dict(d, found_by=why, group_date=h.get("group", ""), topic=topic))
        seen_here = seen_here or at_place(d, pin, place)
        if seen_here:
            for r in held: media_db.upsert(db, r)
            rows += len(held); held = []
            if rows and rows % 20 < 1: log(f"    {rows} item(s) of {label} indexed")
    return rows


def index_own(page, cfg, job_dir, log):
    srcs = own_sources(job_dir)
    if not srcs: return
    done = {}
    try: done = json.loads(_days_file(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError): pass
    db = media_db.connect(cfg)
    todo = {}
    for f in srcs:
        ww = where_when(f)
        if not ww: log(f"  {f.name}: no location / date tag; nothing to match in Google Photos"); continue
        lat, lon, day = ww
        key = f"{day}|{lat:.1f},{lon:.1f}"
        if key not in done and key not in todo: todo[key] = (lat, lon, day, f)
    for key, (lat, lon, day, f) in todo.items():
        place = place_of(lat, lon)
        log(f"  your footage {f.name}: filmed {day} at {place.get('city') or '?'}, {place.get('country') or '?'} ({lat:.4f},{lon:.4f})")
        n = index_day(page, db, cfg, (lat, lon), day, place, f"own video {f.name}: {photos_day(day)[0]}", log)
        done[key] = {"items": n, "place": place, "when": time.strftime("%Y-%m-%dT%H:%M:%S"), "from": str(f)}
        _days_file(cfg).write_text(json.dumps(done, indent=1, ensure_ascii=False), encoding="utf-8")
        log(f"  media database: {n} item(s) of {day} added / completed")


def process(cfg, job_dir, log=None):
    """Back up the job's finished videos, then index its own footage's day. Safe to repeat."""
    log = _log_to(job_dir, log)
    ups = pending(job_dir) if cfg.get("gphotos_backup", True) else []
    want_index = cfg.get("gphotos_index_own", True) and own_sources(job_dir)
    if not ups and not want_index: return
    from playwright.sync_api import sync_playwright
    with gphotos.LOCK, sync_playwright() as pw:
        ctx, page = gphotos._launch(pw, cfg)
        try:
            if ups:
                log(f"Backing up {len(ups)} finished video(s) to Google Photos: {', '.join(f.name for f in ups)}")
                links = gphotos.upload(page, ups, log)
                led = _ledger(job_dir)
                for f in ups:
                    led[f.name] = {"stamp": _stamp(f), "url": links.get(f.name, ""), "uploaded": time.strftime("%Y-%m-%dT%H:%M:%S"), "path": str(f)}
                    log(f"  {f.name} -> {links.get(f.name) or 'in Google Photos (link not found in Recently added)'}")
                (Path(job_dir) / "output" / LEDGER).write_text(json.dumps(led, indent=1), encoding="utf-8")
            if want_index: index_own(page, cfg, job_dir, log)
        except gphotos.Stop as e:
            log(f"Google Photos stopped: {e}")
        finally:
            try: ctx.close()
            except Exception: pass


def queue(job_dir, log=None, delay=0):
    """Ask the worker to back up / index this job (after `delay` s; a later queue of the same job moves the time)."""
    global _thread
    with _cv:
        _due[str(job_dir)] = (time.time() + delay, log)
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_worker, name="gphotos-backup", daemon=True); _thread.start()
        _cv.notify()


def _worker():
    from . import config
    while True:
        with _cv:
            while not _due: _cv.wait()
            jd, (t, log) = min(_due.items(), key=lambda kv: kv[1][0])
            if t > time.time(): _cv.wait(timeout=min(60, t - time.time())); continue
            _due.pop(jd)
        try: process(config.load(), jd, log)
        except Exception as e:
            _log_to(jd, log)(f"Google Photos backup failed ({type(e).__name__}: {str(e)[:200]}); retried at the next start")


def sweep(jobs_dir):
    """Server start: queue every job finished since SINCE whose videos aren't backed up yet."""
    n = 0
    for d in sorted(Path(jobs_dir).iterdir()) if Path(jobs_dir).is_dir() else []:
        fs = [f for f in pending(d) if f.stat().st_mtime >= SINCE]
        if fs: queue(d, delay=120 + 30 * n); n += 1
    return n
