"""One machine-wide editor session (DeepSeek draft via ai-exchange; Claude fixes: Windows-safe pid check, apply counter).

The editor holds a priority reservation, not the raw GPU lock. The raw
GPU lock is taken only for each apply / preview / undo / editor re-render.
"""
import json, os, time
from pathlib import Path

IDLE_LIMIT = 15 * 60

def _settings():
    try:
        from . import config
        return config.load()
    except Exception:
        return {}

def reservation_path(namespace=None):
    p = _settings().get("editor_reservation_file") or os.environ.get("GPU_RESERVATION_FILE") or r"F:\code\gpu.reservation.json"
    p = Path(p)
    if namespace:
        return p.with_name(f"{p.stem}.{namespace}{p.suffix}")
    return p

def _write(data, namespace=None):
    p = reservation_path(namespace)
    p.parent.mkdir(parents=True, exist_ok=True)
    # a temp name of its own per writer: two writers sharing one ".tmp" raced (2026-11-19: job 20261119_061158 crashed on
    # WinError 32/2 when the server and a job thread cleared the reservation at the same moment); Windows may hold the
    # target open for a reader a moment, so the swap is retried
    import threading, uuid
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    for i in range(20):
        try:
            os.replace(tmp, p); return
        except PermissionError:
            time.sleep(0.05 * (i + 1))
    try: tmp.unlink()
    except OSError: pass
    raise PermissionError(f"could not update {p} (held open by another process)")

def _pid_alive(pid):
    """Is that process alive? NEVER os.kill(pid, 0) on Windows: Python maps it to TerminateProcess (it would kill the
    live Video Studio). Windows: OpenProcess + GetExitCodeProcess (STILL_ACTIVE = 259)."""
    try: pid = int(pid)
    except (TypeError, ValueError): return False
    if pid <= 0: return False
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h: return False
        try:
            code = ctypes.c_ulong()
            return bool(k.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259
        finally:
            k.CloseHandle(h)
    try:
        os.kill(pid, 0); return True
    except ProcessLookupError: return False
    except PermissionError: return True
    except OSError: return False


def _read(namespace=None):
    p = reservation_path(namespace)
    if not p.exists():
        return None

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    if not _pid_alive(data.get("pid")) or (int(data.get("active_applies", 0)) == 0 and
                                          time.time() - float(data.get("last_activity", time.time())) > IDLE_LIMIT):
        try: _write({}, namespace)   # clearing a stale reservation is housekeeping: a race here must never fail a job
        except OSError: pass
        return None

    return data

def active(namespace=None):
    return _read(namespace)

def state(namespace=None):
    cur = _read(namespace)
    if not cur:
        return None
    cur = dict(cur)
    cur["age"] = max(0.0, time.time() - float(cur.get("last_activity", time.time())))
    return cur

def acquire(jid, pid, port, sid="", active_applies=0, namespace=None):
    now = time.time()
    cur = _read(namespace)

    if cur:
        if cur.get("jid") != jid or (cur.get("sid") and sid and cur.get("sid") != sid):
            return {
                "ok": False,
                "waiting": True,
                "holder_jid": cur.get("jid"),
                "holder_port": cur.get("port"),
                "age": max(0.0, now - float(cur.get("last_activity", now))),
            }
        cur.update({
            "pid": pid,
            "port": port,
            "last_activity": now,
            "active_applies": int(active_applies or cur.get("active_applies", 0)),
        })
        if sid:
            cur["sid"] = sid
        _write(cur, namespace)
        return {"ok": True, "owner": True, "age": 0.0}

    data = {
        "jid": jid,
        "pid": pid,
        "port": port,
        "last_activity": now,
        "active_applies": int(active_applies or 0),
    }
    if sid:
        data["sid"] = sid
    _write(data, namespace)
    return {"ok": True, "owner": True, "age": 0.0}

def is_owner(jid, pid, port, sid="", namespace=None):
    cur = _read(namespace)
    if not cur:
        return False
    return (
        cur.get("jid") == jid
        and str(cur.get("pid")) == str(pid)
        and str(cur.get("port")) == str(port)
        and (not cur.get("sid") or not sid or cur.get("sid") == sid)
    )

def heartbeat(jid, pid, port, sid="", active_applies=None, namespace=None):
    cur = _read(namespace)
    if not cur or cur.get("jid") != jid:
        return {"ok": False, "error": "not the active editor session"}
    if cur.get("sid") and sid and cur.get("sid") != sid:
        return {"ok": False, "error": "not the active editor session"}

    cur.update({"pid": pid, "port": port, "last_activity": time.time()})
    if active_applies is not None:
        cur["active_applies"] = int(active_applies)
    _write(cur, namespace)
    return {"ok": True}

def release(jid=None, pid=None, port=None, sid="", force=False, namespace=None):
    cur = _read(namespace)
    if not cur:
        return True

    jid_ok = jid is None or cur.get("jid") == jid
    pid_ok = pid is None or str(cur.get("pid")) == str(pid)
    port_ok = port is None or str(cur.get("port")) == str(port)
    sid_ok = not sid or not cur.get("sid") or cur.get("sid") == sid

    if force or (jid_ok and pid_ok and port_ok and sid_ok):
        _write({}, namespace)
        return True
    return False

def _reset(namespace=None):
    _write({}, namespace)


def apply_started(jid, namespace=None):
    """Count an in-flight apply/undo/re-render: the idle release never fires while active_applies > 0."""
    cur = _read(namespace)
    if cur and cur.get("jid") == jid:
        cur["active_applies"] = int(cur.get("active_applies", 0)) + 1; cur["last_activity"] = time.time(); _write(cur, namespace)


def apply_finished(jid, namespace=None):
    p = reservation_path(namespace)
    try: cur = json.loads(p.read_text(encoding="utf-8"))
    except Exception: return
    if isinstance(cur, dict) and cur.get("jid") == jid:
        cur["active_applies"] = max(0, int(cur.get("active_applies", 0)) - 1); cur["last_activity"] = time.time(); _write(cur, namespace)

