"""HyperFrames rung (owner, 2026-11-18: "For video add this to the rung").

HyperFrames (heygen-com/hyperframes, Apache-2.0) renders an HTML/CSS/GSAP composition to a deterministic MP4. Here the
script model (the same key as the narration and jokes) writes one composition for the narration line - kinetic type, numbers
counting, shapes, a simple chart or diagram - and the CLI renders it on this PC.

  * local only: `hyperframes render` (CPU + headless Chrome + FFmpeg). Never `cloud render`, never a HeyGen account or credits.
  * telemetry off (HYPERFRAMES_NO_TELEMETRY / DO_NOT_TRACK), no skills installed, versions pinned in <repo>/hyperframes.
  * the composition gets no network: GSAP is copied in from the pinned install, no images, no web fonts.
  * a drawing like the motion rung: after every picture rung and the other drawn graphics, just before the card.
Frame-checked by the caller like every other candidate.
"""
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import media

ROOT = Path(__file__).resolve().parents[2] / "hyperframes"          # package.json pins hyperframes + gsap
GSAP = ROOT / "node_modules" / "gsap" / "dist" / "gsap.min.js"
MAX_SECS = 20.0

PROMPT = """Design a {secs:.1f}-second motion graphic ({w}x{h} pixels, {orient}) that plays full-screen while the narrator says:
"{vo}"

It is rendered by HyperFrames: plain HTML + CSS, animated with ONE paused GSAP timeline named tl (already created for you).
Make the line's point visible: big kinetic type (a few key words, never the whole sentence), a number counting up, a simple bar
or line chart, a before/after comparison, arrows, shapes, icons drawn with CSS or inline SVG. One clear idea, readable at a glance.

Rules (the renderer rejects anything else):
- Every element that appears or disappears has class="clip", a unique id, data-start and data-duration in seconds
  (inside 0..{secs:.1f}) and data-track-index (0, 1, 2...; elements that overlap in time need different indexes).
- Animate with tl only, e.g. tl.from("#a", {{opacity: 0, y: 60, duration: 0.6, ease: "power3.out"}}, 0.2);
  animate transforms and opacity, never display/visibility, never left/top/width/height. No CSS transform on an element you tween.
- Counting numbers: tl.to(obj, {{v: 50, duration: 2, onUpdate: () => {{ el.textContent = Math.round(obj.v); }}}}, 0.5).
- No images, no video, no audio, no external URLs or fonts, no Date, no Math.random, no timers, no repeat: -1.
- No logos, brand names, flags or real people's faces. Text: at most 6 words per element.
- Layout: each .clip is already a full-frame, centred flex column with {fs}px bold text - put the scene's content straight inside it,
  big and centred, filling most of the frame (key number or word {big}px or more, labels {fs}px or more, never under {small}px).
  Never position things in a corner. One scene on screen at a time unless they are meant to be compared.
- Numbers: show only numbers that are in the narration above (written as digits is fine: "fifty" -> 50). Invent none.
- Elements start visible in CSS (never opacity: 0 or visibility: hidden in css); entrances use tl.from(...).
- body is placed INSIDE the existing #root: never write another #root. The last element stays on screen until {secs:.1f}s
  (its data-start + data-duration reaches the end) - no fade-out at the end.
- Look: dark background {ground}, white text, one accent colour {accent}, font-family system-ui, sans-serif. Everything settled
  and readable by {settle:.1f}s and held to the end.

Reply with JSON only: {{"idea": "max 12 words", "css": "...", "body": "...the elements inside #root...", "js": "...tl tweens only..."}}"""

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="UTF-8" /><meta name="viewport" content="width={w}, height={h}" />
<script src="assets/gsap.min.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{ margin: 0; width: {w}px; height: {h}px; overflow: hidden; background: {ground}; }}
#root {{ position: relative; width: 100%; height: 100%; font-family: system-ui, sans-serif; color: #fff; }}
/* default: each scene fills the frame, centred (the model's css may refine it) */
.clip {{ position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center;
         text-align: center; gap: {gap}px; font-size: {fs}px; font-weight: 700; line-height: 1.1; }}
{css}
</style></head>
<body>
<div id="root" data-composition-id="main" data-start="0" data-duration="{secs:.3f}" data-width="{w}" data-height="{h}">
{body}
</div>
<script>
const tl = gsap.timeline({{ paused: true }});
{js}
window.__timelines["main"] = tl;
tl.seek(0);
</script>
</body></html>
"""

BANNED = re.compile(r"(?i)https?://|Date\.now|new Date|Math\.random|setTimeout|setInterval|repeat\s*:\s*-1|fetch\(|XMLHttpRequest|<img|<video|<audio|<iframe|@import|url\(")


def _env():
    e = dict(os.environ)
    e.update(HYPERFRAMES_NO_TELEMETRY="1", DO_NOT_TRACK="1", HYPERFRAMES_SKIP_SKILLS="1")
    return e


def _cli():
    """The pinned CLI's own entry (node <bin>), so no npx resolution or download happens at render time."""
    for p in (ROOT / "node_modules" / "hyperframes" / "dist" / "cli.js", ROOT / "node_modules" / "hyperframes" / "bin" / "hyperframes.js"):
        if p.is_file(): return ["node", str(p)]
    pj = ROOT / "node_modules" / "hyperframes" / "package.json"
    if pj.is_file():
        b = json.loads(pj.read_text(encoding="utf-8")).get("bin")
        b = b if isinstance(b, str) else (b or {}).get("hyperframes")
        if b and (pj.parent / b).is_file(): return ["node", str(pj.parent / b)]
    return None


def ensure(log=print):
    """One-time install of the pinned versions (as short._ensure_remotion does for Remotion). -> True when installed."""
    if _cli() and GSAP.is_file(): return True
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm or not (ROOT / "package.json").is_file(): return False
    log("Installing HyperFrames (pinned, local renders only; one time)...")
    try: subprocess.run([npm, "install", "--no-audit", "--no-fund"], cwd=ROOT, env=_env(), check=True, timeout=1800, capture_output=True)
    except (subprocess.SubprocessError, OSError) as e: log(f"  HyperFrames install failed ({str(e)[:120]})"); return False
    return bool(_cli()) and GSAP.is_file()


def available(log=None):
    """-> "" when the rung can run, else why not (installs the pinned versions on first use when log is given)."""
    if log is not None: ensure(log)
    if not _cli(): return f"HyperFrames is not installed (npm install in {ROOT})"
    if not GSAP.is_file(): return "GSAP is not installed next to HyperFrames"
    if not shutil.which("node"): return "node is not on PATH"
    return ""


def page(spec, secs, w, h, ground="#0b0f17"):
    """The composition file from the model's css / body / js (wrapped in the fixed scaffold). Raises ValueError on a rule
    the scaffold can't fix (network, randomness, media)."""
    css, body, js = (str(spec.get(k) or "") for k in ("css", "body", "js"))
    # the scaffold owns the timeline: a model that declares or registers it again would break the page
    js = re.sub(r"(?m)^\s*(?:const|let|var)\s+tl\s*=\s*gsap\.timeline\([^)]*\)\s*;?\s*$", "", js)
    js = re.sub(r"(?m)^\s*window\.__timelines\[[^\]]*\]\s*=\s*tl\s*;?\s*$|^\s*tl\.seek\(0\)\s*;?\s*$", "", js)
    if not body.strip() or not js.strip(): raise ValueError("the composition needs body and js")
    for part in (css, body, js):
        m = BANNED.search(part)
        if m: raise ValueError(f"not allowed in the composition: {m.group(0)}")
    css = re.sub(r"(?i)(?<![\w-])(opacity\s*:\s*0(?![.\d])|visibility\s*:\s*hidden)\s*;?", "", css)   # a hidden start + tl.from = never seen
    body = re.sub(r'(?is)^\s*<div id="root"[^>]*>(.*)</div>\s*$', lambda m: m.group(1), body)   # a second #root inside ours
    if "</script" in body.lower() or "<script" in body.lower(): raise ValueError("no script tags in body: tweens go in js")
    ids = re.findall(r'<[^>]+class="[^"]*\bclip\b[^"]*"[^>]*>', body)
    if not ids: raise ValueError('no element has class="clip" with data-start / data-duration')
    for tag in ids:
        if 'data-start=' not in tag or 'data-duration=' not in tag: raise ValueError(f"clip element without data-start/data-duration: {tag[:80]}")
        try:
            st = float(re.search(r'data-start="([\d.]+)"', tag).group(1)); du = float(re.search(r'data-duration="([\d.]+)"', tag).group(1))
        except (AttributeError, ValueError): raise ValueError(f"data-start / data-duration must be numbers: {tag[:80]}")
        if st < 0 or st + du > secs + 0.05: raise ValueError(f"a clip runs past the video's {secs:.1f}s: {tag[:80]}")
    ends = [float(re.search(r'data-start="([\d.]+)"', t).group(1)) + float(re.search(r'data-duration="([\d.]+)"', t).group(1)) for t in ids]
    if max(ends) < secs - 0.35: raise ValueError(f"the screen goes empty at {max(ends):.1f}s: the last element must stay until {secs:.1f}s")
    return PAGE.format(w=w, h=h, secs=secs, css=css, body=body, js=js, ground=ground, fs=round(min(w, h) * 0.09), gap=round(min(w, h) * 0.03))


WORDNUM = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
           "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
           "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000, "million": 1000000, "billion": 1000000000}


def _numbers(text):
    """Numbers in a text: digits (commas dropped) and number words, as strings of their digits."""
    t = re.sub(r"(?<=\d),(?=\d{3})", "", text or "")
    out = set(re.findall(r"\d+(?:\.\d+)?", t))
    out |= {str(WORDNUM[w]) for w in re.findall(r"[a-z]+", t.lower()) if w in WORDNUM}
    out |= {f"{WORDNUM[a]}.{WORDNUM[b]}" for a, b in re.findall(r"\b([a-z]+) point ([a-z]+)\b", t.lower()) if a in WORDNUM and b in WORDNUM}
    return {x.rstrip("0").rstrip(".") if "." in x else x for x in out}


def invented_numbers(spec, vo):
    """Numbers drawn on screen (body text, counter targets in js) that the narration doesn't have."""
    shown = re.sub(r"<[^>]+>", " ", str(spec.get("body") or ""))
    targets = " ".join(re.findall(r"\b(?:v|val|value|n|num|count)\s*:\s*([\d.,]+)", str(spec.get("js") or "")))
    have = _numbers(vo)
    return sorted(n for n in _numbers(shown + " " + targets) if n not in have and n not in ("0", "1"))


def _rgb(hexs):
    h = str(hexs).lstrip("#")
    try: return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError: return (11, 15, 23)


def fill(clip, ground_rgb=(11, 15, 23), at=0.95):
    """-> "" when the frame near the end holds a reasonably big, centred design, else why not."""
    from PIL import Image, ImageChops
    d = media.dur(clip); tmp = Path(clip).with_name("_fill.png")
    try:
        media.ff("-ss", f"{d * at:.2f}", "-i", clip, "-frames:v", 1, "-vf", "scale=480:-2", tmp, timeout=60, check=False)
        if not tmp.exists(): return "no frame"
        im = Image.open(tmp).convert("RGB")
        diff = ImageChops.difference(im, Image.new("RGB", im.size, ground_rgb)).convert("L").point(lambda v: 255 if v > 40 else 0)
        box = diff.getbbox()
        if not box: return "the last frame is empty"
        W, H = im.size; bw, bh = box[2] - box[0], box[3] - box[1]; cx, cy = (box[0] + box[2]) / 2 / W, (box[1] + box[3]) / 2 / H
        if bw < 0.35 * W and bh < 0.28 * H: return f"the design is small ({bw / W:.0%} of the width, {bh / H:.0%} of the height): make it fill the frame"
        if not (0.35 <= cx <= 0.65 and 0.25 <= cy <= 0.75): return "the design sits off-centre: centre it"
        return ""
    finally:
        tmp.unlink(missing_ok=True)


def blank(clip, at=(0.45, 0.75, 0.95)):
    """-> the first sampled time (seconds) where the frame is one flat colour (nothing drawn), or None."""
    from PIL import Image, ImageStat
    d = media.dur(clip); tmp = Path(clip).with_name("_blank.png")
    try:
        for f in at:
            media.ff("-ss", f"{d * f:.2f}", "-i", clip, "-frames:v", 1, "-vf", "scale=320:-2,format=gray", tmp, timeout=60, check=False)
            if not tmp.exists(): return round(d * f, 1)
            if ImageStat.Stat(Image.open(tmp)).stddev[0] < 4.0: return round(d * f, 1)
            tmp.unlink(missing_ok=True)
        return None
    finally:
        tmp.unlink(missing_ok=True)


def _run(args, cwd, timeout):
    p = subprocess.run(_cli() + args, cwd=cwd, env=_env(), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _lint_errors(proj):
    code, out = _run(["lint", "--json", "."], proj, 300)
    try:
        j = json.loads(out[out.index("{"):out.rindex("}") + 1])
    except ValueError:
        return [] if code == 0 else [out.strip()[-300:]]
    errs = j.get("errors") or [f for f in (j.get("findings") or []) if str(f.get("severity", "")).lower() == "error"]
    return [str(e.get("message") if isinstance(e, dict) else e)[:200] for e in errs]


def make(cfg, vo, secs, w, h, work, log=print, llm=None, key="hf"):
    """-> (clip path | None, why). The script model writes the composition (one retry with the renderer's complaints), the
    pinned CLI lints and renders it locally."""
    why = available(log)
    if why: return None, why
    if not vo.strip(): return None, "no narration for the span"
    secs = max(2.0, min(float(secs), MAX_SECS))
    proj = Path(work) / f"hyperframes_{key}"; shutil.rmtree(proj, ignore_errors=True); (proj / "assets").mkdir(parents=True)
    shutil.copy2(GSAP, proj / "assets" / "gsap.min.js")
    (proj / "hyperframes.json").write_text(json.dumps({"name": key}), encoding="utf-8")
    accent = cfg.get("hyperframes_accent") or "#ffb020"; ground = cfg.get("hyperframes_ground") or "#0b0f17"
    own = llm is None
    if own:
        from .llm import LLM
        llm = LLM(cfg, log, lambda: False).__enter__()
    try:
        prompt = PROMPT.format(secs=secs, w=w, h=h, orient="portrait" if h > w else "landscape", vo=vo.replace('"', "'")[:700],
                               ground=ground, accent=accent, settle=max(1.0, secs * 0.6), fs=round(min(w, h) * 0.09),
                               big=round(min(w, h) * 0.2), small=round(min(w, h) * 0.06))
        idea, errs, why = "", [], "no attempt"
        for attempt in range(3):   # the model's composition; the renderer's / the checks' complaints go back to it
            ask = prompt + ("" if not errs else "\n\nYour last composition was rejected:\n- " + "\n- ".join(errs[:6]) + "\nFix all of these.")
            def chk(j):
                page(j, secs, w, h, ground)   # raises ValueError -> the model is asked again with the reason
                bad = invented_numbers(j, vo)
                if bad: raise ValueError(f"numbers not in the narration: {bad}; show only the narration's numbers")
            try:
                spec = llm.trace(f"hyperframes {key}").chat_json("You are a motion designer who writes HyperFrames compositions. Reply with JSON only.",
                                                                   ask, max_tokens=3500, check=chk)
            except RuntimeError as e:
                if str(e) == "stopped": raise
                return None, f"no usable composition from the script model ({str(e)[:80]})"
            idea = str(spec.get("idea") or "")[:80]
            (proj / "index.html").write_text(page(spec, secs, w, h, ground), encoding="utf-8")
            errs = _lint_errors(proj)
            if errs:
                why = f"composition failed lint ({errs[0][:100]})"; log(f"      hyperframes: lint: {errs[0][:120]}"); continue
            out = proj / "out.mp4"; out.unlink(missing_ok=True); t0 = time.time()
            code, txt = _run(["render", ".", "-o", str(out), "--quality", "looks", "--fps", "30", "--quiet", "--no-wait"], proj, 1200)
            if code != 0 or not media.ok(out, 0.5):
                line = next((l for l in reversed(txt.splitlines()) if re.search(r"(?i)error|fail", l)), txt.strip()[-200:])
                return None, f"render failed ({line.strip()[:120]})"   # the tool itself failing: no point asking the model again
            b = blank(out)
            bad = f"the screen is empty at {b}s: every element must be visible while it is on screen" if b is not None else fill(out, _rgb(ground))
            if bad:
                errs = [bad]; why = f"rendered but {bad}"; log(f"      hyperframes: {bad}"); continue
            log(f"      hyperframes: rendered {media.dur(out):.1f}s in {time.time() - t0:.0f}s ({idea})")
            return out, f"HyperFrames motion graphic ({idea})" if idea else "HyperFrames motion graphic"
        return None, why
    finally:
        if own: llm.__exit__(None, None, None)
