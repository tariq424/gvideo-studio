"""Explainer picture walk. Every rung logs `  {id}: {name}: failed|skipped|fired: {reason}`.

The shared order is SHARED_RUNG_NAMES. Illustration is a terminal floor, logged
after three.js and before the card. A flight-path slot calls Unreal (T_DataWall)
before motion. A cabin prompt does not. Motion, three.js, and illustration fail
when the picture is the wrong side of the subject (an interior prompt does not
accept an exterior cartoon).
"""
import re
import shutil
from pathlib import Path

from . import coherence
from .rungs import SHARED_RUNG_NAMES

# A shot of the flight itself, inside a narration that already names the diversion.
FLIGHTISH = re.compile(
    r"\b(descent|descended|dive|dived|diving|aerial|in flight|flying|sky|cruise|approach|"
    r"runway|airliner|aircraft|jet|plane|altitude|flight path)\b",
    re.I,
)
EXTERIOR_SCENES = {"sky", "airport", "sea", "city"}
INTERIOR_SCENES = {"cockpit", "cabin"}
PATHISH = re.compile(r"\b(T_DataWall|flight[- ]path|route|descent path)\b", re.I)
OFF_REASON = re.compile(r"\b(off|not set up|not configured|not on)\b", re.I)


def fmt(slot_id, name, status, why):
    why = re.sub(r"\s+", " ", str(why or "").strip())
    return f"  {slot_id}: {name}: {status}: {why}"


def walk_names():
    """Shared rungs, with illustration logged once, immediately before the card."""
    names = list(SHARED_RUNG_NAMES)
    if "illustration" not in names:
        names.insert(names.index("card"), "illustration")
    return names


def asked_place(text):
    """'interior' or 'exterior' when the prompt names one side and not the other."""
    blob = text or ""
    inside = bool(coherence.INTERIOR.search(blob))
    outside = bool(coherence.EXTERIOR.search(blob))
    if inside and not outside:
        return "interior"
    if outside and not inside:
        return "exterior"
    return None


def is_flight_path(prompt, section_vo=""):
    """A descent / flight-path shot. A cabin (or any interior) prompt is not one."""
    if asked_place(prompt) == "interior":
        return False
    from . import flight_path
    if flight_path.for_narration(prompt or ""):
        return True
    if not flight_path.for_narration(section_vo or ""):
        return False
    return bool(FLIGHTISH.search(prompt or ""))


def _picture_bits(picture):
    if picture is None:
        return {}, ""
    if isinstance(picture, dict):
        g = picture.get("g") if isinstance(picture.get("g"), dict) else picture
        text = " ".join(str(picture.get(k) or "") for k in ("how", "template", "why", "kind"))
        text += " " + " ".join(str(g.get(k) or "") for k in ("scene", "kind", "shape", "aircraft", "template"))
        return g, text
    return {}, str(picture)


def _exterior_picture(picture):
    g, text = _picture_bits(picture)
    scene = str(g.get("scene") or "")
    if scene in EXTERIOR_SCENES:
        return True
    if g.get("kind") == "silhouette" and str(g.get("shape") or "") in ("airliner", "jet", "aircraft"):
        return True
    if PATHISH.search(text):
        return True
    return bool(coherence.EXTERIOR.search(text) and not coherence.INTERIOR.search(text))


def _interior_picture(picture):
    g, text = _picture_bits(picture)
    scene = str(g.get("scene") or "")
    if scene in INTERIOR_SCENES:
        return True
    if g.get("kind") in ("cabin",) or g.get("shape") in ("cabin",):
        return True
    return bool(coherence.INTERIOR.search(text) and not coherence.EXTERIOR.search(text) and not PATHISH.search(text))


def picture_matches(prompt, picture):
    """(ok, why). An interior prompt rejects an exterior cartoon. An unseen picture fails closed."""
    want = asked_place(prompt)
    if not want:
        return True, ""
    g, text = _picture_bits(picture)
    scene = str(g.get("scene") or g.get("shape") or "")
    if want == "interior" and _exterior_picture(picture):
        return False, f"interior prompt, exterior cartoon (scene {scene or 'exterior'})"
    if want == "exterior" and _interior_picture(picture):
        return False, f"exterior prompt, interior picture (scene {scene or 'interior'})"
    if isinstance(picture, dict) and picture.get("subject_ok") and not (
        (want == "interior" and _exterior_picture(picture)) or (want == "exterior" and _interior_picture(picture))
    ):
        return True, ""
    if want == "interior" and _interior_picture(picture):
        return True, ""
    if want == "exterior" and _exterior_picture(picture):
        return True, ""
    return False, "picture was not checked against the subject"


def slot_illustration(ctx):
    """The illustration for this slot. The slot prompt decides the scene, not the whole section."""
    if "illustration" in ctx:
        return ctx.get("illustration")
    from . import illustrate
    prompt = ctx.get("prompt") or ""
    sec = {"id": str(ctx.get("slot_id") or ""), "vo": prompt, "still_prompt": prompt}
    opts = illustrate.options(sec, ctx.get("ents") or [])
    return opts[0] if opts else None


def climb(slot_id, prompt, section_vo, attempt, log, stop=None, names=None):
    """Try each rung. After one fires, the rest are logged skipped by name. -> the name that fired, or None."""
    stop = stop or (lambda: False)
    fired = None
    for name in (names or walk_names()):
        if stop():
            raise RuntimeError("cancelled")
        if fired:
            log(fmt(slot_id, name, "skipped", f"{fired} already fired"))
            continue
        try:
            status, why, payload = attempt(name)
        except Exception as e:
            if str(e) == "cancelled":
                raise
            status, why, payload = "failed", f"{type(e).__name__}: {str(e)[:120]}", None
        if status not in ("failed", "skipped", "fired"):
            status, why = "failed", why or "no result"
        if status == "fired" and name in ("illustration", "motion", "threejs"):
            ok, reason = picture_matches(prompt, payload if payload is not None else {"how": why})
            if not ok:
                status, why = "failed", reason
        log(fmt(slot_id, name, status, why))
        if status == "fired":
            fired = name
    return fired


def _apply_motion(ctx, clip, tag):
    v = ctx.get("visual")
    job = ctx.get("job")
    if job and clip and v is not None:
        dest = Path(job) / "motion" / f"{v['id']}.mp4"
        dest.parent.mkdir(parents=True, exist_ok=True)
        src = Path(clip)
        if src.resolve() != dest.resolve():
            shutil.copy(src, dest)
    if v is not None:
        v["type"] = "motion"
        v.pop("g", None)
        v["tag"] = tag


def _apply_card(ctx):
    v = ctx.get("visual")
    if v is None:
        return
    text = " ".join((ctx.get("prompt") or ctx.get("section_vo") or "").split()[:12]) or "..."
    v["type"] = "motion"
    v["g"] = {"kind": "headline", "kicker": "", "text": text, "sub": ""}
    v["tag"] = "HEADLINE"


def _repair_ctx(ctx):
    work = Path(ctx["work"])
    work.mkdir(parents=True, exist_ok=True)
    return {
        "cfg": ctx.get("cfg") or {}, "log": ctx.get("log") or (lambda _m: None),
        "vo": ctx.get("prompt") or "", "job": ctx.get("job") or work, "work": work,
        "mp4": work / "none.mp4", "vert": False, "W": 1920, "H": 1080,
        "a": 0.0, "b": 6.0, "need": 6.0, "key": str(ctx.get("slot_id") or "slot"),
        "images": ctx.setdefault("images", []),
    }


def _qa_image(ctx, img):
    from . import qa
    from .llm import qa_available
    if not img or not qa_available(ctx.get("cfg") or {}):
        return False, "picture was not checked against the subject"
    good, issue = qa.check(ctx.get("cfg") or {}, img, ctx.get("prompt") or "")
    if str(issue or "").startswith("qa-"):
        return False, "picture was not checked against the subject"
    if not good:
        return False, issue or "picture does not match the subject"
    return True, ""


def _try_wangp(ctx):
    """WanGP still for this slot's prompt. The held worker is reused. A place prompt that was not checked does not pass."""
    from .illustrate import wangp_lock
    prompt = ctx.get("prompt") or ""
    lock = wangp_lock({"vo": prompt, "still_prompt": prompt}, ctx.get("ents") or [])
    if lock:
        return "skipped", lock, None
    wg = ctx.get("wg")
    if wg is None:
        return "failed", "WanGP is not running", None
    from .render import _still
    work = Path(ctx["work"])
    work.mkdir(parents=True, exist_ok=True)
    png = work / "wangp.png"
    try:
        ok = _still(ctx.get("cfg") or {}, wg, ctx.get("log") or (lambda _m: None), prompt, png, 4242,
                    ctx.get("stop") or (lambda: False), f"WanGP {ctx.get('slot_id')}")
    except Exception as e:
        if str(e) == "cancelled":
            raise
        return "failed", f"WanGP unavailable ({str(e)[:80]})", None
    if not ok or not png.exists():
        return "failed", "WanGP made no usable still", None
    if asked_place(prompt):
        ok_pic, reason = _qa_image(ctx, png)
        if not ok_pic:
            return "failed", reason, {"image": str(png)}
    v = ctx.get("visual")
    job = ctx.get("job")
    if job and v is not None:
        dest = Path(job) / "stills" / f"{v['id']}.jpg"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(png, dest)
        v["type"] = "still"
        v.pop("g", None)
        v["tag"] = "GENERATED"
    return "fired", "WanGP still", {"how": "WanGP still", "subject_ok": bool(asked_place(prompt)), "image": str(png)}


def _qc_handler(name, ctx):
    """Call the shared rung. The slot prompt is the narration, so a cabin is not drawn from the section's jet."""
    from . import qc_repair
    fn = dict(qc_repair.RUNGS).get(name)
    if fn is None:
        return "failed", f"{name} is not on the shared ladder", None
    clip, img, why = fn(_repair_ctx(ctx))
    if not clip:
        why = why or "nothing produced"
        status = "skipped" if OFF_REASON.search(str(why)) else "failed"
        return status, why, None
    payload = {"how": why or name, "image": str(img) if img else ""}
    ok, reason = picture_matches(ctx.get("prompt") or "", payload)
    if not ok and str(reason).startswith("picture was not checked") and img:
        ok, reason = _qa_image(ctx, img)
        if ok:
            payload["subject_ok"] = True
    if not ok:
        return "failed", reason, payload
    _apply_motion(ctx, clip, str(name).upper())
    return "fired", why or name, payload


def _switched_off(name, cfg):
    """(skipped, reason) when this rung's switch is off. None when it should be called."""
    flags = {
        "meta_chain": ("meta_chain_on", True, "Meta image chain is not on"),
        "ai_image": ("imagegen_on", True, "image generation is off"),
        "blender": ("blender_on", True, "blender is off"),
        "marble": ("marble_on", False, "World Labs Marble not set up (marble_on is off)"),
        "studio3d": ("studio3d_on", True, "studio3d is off"),
        "motion": ("motion_rung", True, "motion graphics are off"),
        "hyperframes": ("hyperframes_on", True, "hyperframes is off"),
        "threejs": ("threejs_on", True, "threejs is off"),
    }
    if name == "video" and not cfg.get("meta_video_on", True) and not cfg.get("flow_on", False):
        return "skipped", "no video provider is on"
    row = flags.get(name)
    if row and not cfg.get(row[0], row[1]):
        return "skipped", row[2]
    return None


def production_attempt(name, ctx):
    """One rung of the explainer walk. -> (status, why, payload)."""
    cfg = ctx.get("cfg") or {}
    prompt = ctx.get("prompt") or ""
    section = ctx.get("section_vo") or ""
    if name == "stock":
        return "failed", ctx.get("stock_why") or "nothing suitable", None
    if name == "unreal":
        if not cfg.get("unreal_on", True):
            return "skipped", "unreal is off", None
        if not is_flight_path(prompt, section):
            return "skipped", "not a route or flight-path beat", None
        fn = ctx.get("render_unreal")
        if not fn:
            return "failed", "Unreal was not called", None
        clip, why = fn(ctx)
        if not clip:
            return "failed", why or "Unreal MRQ produced nothing", None
        payload = {"template": "T_DataWall", "how": why or "T_DataWall", "clip": str(clip)}
        ok, reason = picture_matches(prompt, payload)
        if not ok:
            return "failed", reason, payload
        _apply_motion(ctx, clip, "UNREAL")
        return "fired", why or "T_DataWall", payload
    if name == "illustration":
        item = slot_illustration(ctx)
        if not item:
            return "failed", "no illustration fits this prompt", None
        ok, why = picture_matches(prompt, item)
        if not ok:
            return "failed", why, item
        paint = ctx.get("render_illustration")
        if not paint:
            return "failed", "illustration was not rendered", None
        if not paint(ctx, item):
            return "failed", "illustration render failed", item
        scene = (item.get("g") or {}).get("scene") or (item.get("g") or {}).get("shape") or item.get("rung") or "illustration"
        return "fired", f"scene {scene}", item
    if name == "card":
        _apply_card(ctx)
        return "fired", "headline card", {"how": "headline card", "kind": "headline"}
    custom = (ctx.get("handlers") or {}).get(name)
    if custom:
        return custom(ctx)
    if name == "wangp":
        return _try_wangp(ctx)
    off = _switched_off(name, cfg)
    if off:
        return off[0], off[1], None
    try:
        return _qc_handler(name, ctx)
    except Exception as e:
        if str(e) == "cancelled":
            raise
        return "failed", f"{type(e).__name__}: {str(e)[:120]}", None
