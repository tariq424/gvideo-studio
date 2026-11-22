"""Three.js beat rung (owner, 2026-11-21: "if they look better than claymation ... let us ditch the futurepedia signup").
Our own original 3D styles, rendered locally by Remotion (@remotion/three): free, no credits, no lockouts, deterministic.
It sits just before the card in both ladders - a beat no picture rung could show gets a labelled 3D diorama instead of text.

    make(cfg, vo, secs, w, h, work, log, key) -> (clip | None, why)

`why` always says which of three things happened: "3D diorama (...)" = succeeded, "failed to render (...)" or "timed out (...)".
Styles are FROZEN by version: STYLES maps a style name to its Remotion composition and its version tag; a change to a style's
look means a new version (and the owner's approval), never an edit in place.
"""
import json, subprocess, time
from pathlib import Path

STYLES = {"diorama": {"comp": "Diorama", "version": "diorama-v1 (2026-11-21)"}}
KINDS = ["house", "factory", "tree", "car", "truck", "tower", "person", "box", "windmill", "tank"]
TIME_CAP = 60          # seconds: the whole render; past it the rung reports "timed out" and the ladder moves on to the card
MAX_RENDER_SECS = 10   # longer beats render this much and hold the last frame (the camera has settled by then)

PROMPT = """A narration line needs a picture, and every photo / AI-image route failed. Show it as a small low-poly 3D diorama
(a floating island scene). Pick props ONLY from this list: {kinds}.
- house = a home / shop / place people live or sell; factory = industry, plant, warehouse, production; car = a car, taxi, driving;
  truck = delivery, shipping, freight, logistics; tower = radio / phone mast, signal, network, lighthouse; person = people, a worker,
  a customer; box = goods, parcels, stock, cargo; windmill = energy, wind, farming, power; tank = fuel, water, oil, storage, chemicals;
  tree = nature, a park, forest, land.
- 2 to 5 props, the first ones the most important. Give a "label" (1-3 words, max 18 characters, words from the narration) to the
  2 or 3 props that carry the point; the others have no label.
- "title": 2-6 words summing up the line (words from the narration, no new facts, no numbers that are not in the narration).
- If none of these props can honestly stand for what the line says (it is about a specific person's face, a quote, a feeling,
  a document), reply {{"fit": false, "why": "..."}} instead.
Narration ({secs:.0f} s): "{vo}"
Reply with JSON only: {{"fit": true, "title": "...", "props": [{{"kind": "...", "label": "..."}}, ...]}}"""


def _check(j):
    if j.get("fit") is False: return
    props = j.get("props")
    if not isinstance(props, list) or not props: raise ValueError("props must be a non-empty list")
    bad = [p.get("kind") for p in props if not isinstance(p, dict) or p.get("kind") not in KINDS]
    if bad: raise ValueError(f"unknown prop kinds {bad}; use only {KINDS}")


def spec_for(cfg, vo, secs, log=print, llm=None, key="t3d"):
    """-> (spec | None, why). The script model turns the line into a scene from the fixed prop library."""
    own = llm is None
    if own:
        from .llm import LLM
        llm = LLM(cfg, log, lambda: False).__enter__()
    try:
        j = llm.trace(f"threejs {key}").chat_json("You design simple, honest 3D explainer scenes. Reply with JSON only.",
                                                  PROMPT.format(kinds=", ".join(KINDS), secs=secs, vo=vo.replace('"', "'")[:600]),
                                                  max_tokens=800, check=_check)
    except RuntimeError as e:
        if str(e) == "stopped": raise
        return None, f"no scene from the script model ({str(e)[:80]})"
    finally:
        if own:
            try: llm.__exit__(None, None, None)
            except Exception: pass
    if j.get("fit") is False: return None, f"no 3D scene fits ({str(j.get('why') or '')[:80]})"
    props = [{"kind": p["kind"], **({"label": str(p["label"])[:18]} if p.get("label") else {})} for p in j["props"][:5]]
    seed = sum(ord(c) for c in vo) % 997   # deterministic: the same line always gets the same island
    return {"style": "diorama", "title": str(j.get("title") or "")[:48], "props": props, "seed": seed}, ""


def render(spec, secs, w, h, out, log=print, style="diorama"):
    """-> (clip | None, why). One Remotion render, hard-capped at TIME_CAP seconds."""
    from . import short
    st = STYLES[style]; out = Path(out); out.unlink(missing_ok=True)
    rsecs = max(2.0, min(float(secs), MAX_RENDER_SECS))
    props = out.with_suffix(".props.json")
    props.write_text(json.dumps({"spec": spec, "secs": rsecs, "w": w, "h": h}), encoding="utf-8")
    cmd = [short._npx(), "remotion", "render", "src/Root.tsx", st["comp"], str(out), f"--props={props}", "--gl=angle",
           "--concurrency=4", "--log=error"]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=short._ensure_remotion(log), capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=TIME_CAP)
    except subprocess.TimeoutExpired:
        out.unlink(missing_ok=True)
        return None, f"timed out (render passed the {TIME_CAP} s cap)"
    if p.returncode != 0 or not out.exists() or out.stat().st_size < 10000:
        tail = " ".join((p.stdout + p.stderr).split())[-140:]
        return None, f"failed to render (exit {p.returncode}: {tail})"
    if secs > rsecs + 0.05:   # a long beat holds the settled last frame
        from . import media
        held = out.with_name(out.stem + "_held.mp4")
        media.ff("-i", out, "-vf", f"tpad=stop_mode=clone:stop_duration={secs - rsecs:.2f}", "-an", "-c:v", "libx264", "-preset",
                 "veryfast", "-crf", "16", held, timeout=120)
        held.replace(out)
    return out, f"3D diorama, {st['version']}, rendered in {time.time() - t0:.0f} s"


def make(cfg, vo, secs, w, h, work, log=print, llm=None, key="t3d"):
    if not cfg.get("threejs_on", True): return None, "skipped (threejs_on off)"
    if not (vo or "").strip(): return None, "no narration for the span"
    spec, why = spec_for(cfg, vo, secs, log, llm, key)
    if not spec: return None, why
    d = Path(work) / "threejs"; d.mkdir(parents=True, exist_ok=True)
    clip, why = render(spec, secs, w, h, d / f"{key}.mp4", log)
    if clip:
        (d / f"{key}.spec.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
        why += f": {spec['title'] or ', '.join(p['kind'] for p in spec['props'])}"
    return clip, why
