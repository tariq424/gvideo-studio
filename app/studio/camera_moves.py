"""Framed camera moves for stills (owner, 2026-11-16: "I like the way we pan, zoom, do overhead and dolly shots ... implement the same
technique on the Video Studio project"; spec: F:/videos/google-flow-videos/visitor_production/VIDEO_STUDIO_CAMERA_SPEC.md).

Instead of moving a still with CSS inside Remotion (which upscales the 1080p frame, so every % of zoom softens it and the moves
had to stay tiny), each still shot is pre-rendered here to an MP4:
  - the still is upscaled ONCE with Lanczos so the full window is 2x the output size,
  - every frame is ONE rigid affine resample of that master (zoom / pan / rotate the whole picture; no depth warp, so faces,
    limbs and straight lines never bend or tear), no sharpening (it makes fine texture shimmer),
  - the move follows a cosine ease-in-out aimed at a focus point (the subject's centre when the parallax cut-out found one).
Remotion then plays the MP4 untouched, and consecutive framed shots join with a 0.47 s dissolve (Background.tsx, "framed").

Only generated / sourced stills get this: the user's own files, clips, charts and slides (fit "still"), credited photos shown whole,
maps, graphics and captions are left exactly as they were. `camera_moves: "legacy"` in the config turns it all off.
Self-contained: nothing here depends on the Flow factory (F:/code/video-flow)."""
import hashlib
import json
import math
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

FPS = 30
XF = 14                      # dissolve frames (0.47 s at 30 fps)
TAIL = 0.5                   # seconds a framed shot keeps playing under the next one (>= the dissolve)
DEFAULT_FOCUS = (0.5, 0.45)
VERSION = 2                  # bump to invalidate cached renders when the maths changes

# (start width, end width) of the window as a fraction of the full frame; path type. Owner-approved values (spec 2.2).
MOVES = {
    "wide":      (1.00, 0.80, "zoom"),     # establishing: whole picture, clear ease in
    "push":      (1.00, 0.52, "zoom"),     # strong push in to the subject
    "close":     (0.58, 0.40, "zoom"),     # tight on the subject, still moving
    "pull":      (0.45, 1.00, "zoom"),     # start tight on the subject, reveal the scene
    "pan-lr":    (0.60, 0.60, "pan"),      # sweep across the picture
    "pan-rl":    (0.60, 0.60, "pan"),
    "tilt-down": (0.62, 0.55, "tilt"),     # from the top of the frame down onto the subject
    "dolly":     (1.00, 0.58, "dolly"),    # push in with a sideways drift
    "hold":      (0.42, 0.36, "zoom"),     # tight, slow creep (viewfinder POV)
    "spin":      (0.76, 0.50, "spin"),     # overhead drone: rotate while descending
    # the subject is wider than the frame can hold (a 16:9 picture of a person in a 9:16 Short): the WHOLE picture over a blurred
    # copy of itself, a gentle push that never cuts into it (owner, 2026-11-17: a pan showed only his hands and tie)
    "whole":     (1.00, 0.94, "whole"),
}
ROTATION = ["wide", "push", "close", "pan-lr", "pull", "dolly", "tilt-down", "spin"]
# owner, 2026-11-17: "don't zoom so much that the subject is cropped ... zoom closer only for a close-up, and centred". The tight end
# of every move is at least the subject's box plus this margin, centred on it; without a known subject a move zooms at most to
# NO_SUBJECT_MIN of the frame (about 1.4x), centred.
MARGIN = 1.18
NO_SUBJECT_MIN = 0.72
SUBJECT_MOVES = ("close", "hold")          # a clear subject / face: stay on it
SCENE_MOVES = ("wide", "pull", "spin")     # no single subject (landscape, crowd)


def mode(cfg):
    return "framed" if str((cfg or {}).get("camera_moves", "framed")).lower() == "framed" else "legacy"


def ease(p):
    return 0.5 - 0.5 * math.cos(math.pi * p)


# ------------------------------------------------------------------ geometry
def need(box, img_w, img_h, W, H):
    """The window width (fraction of the full frame) that shows the whole subject box with a margin, in master pixels."""
    if not box: return None
    bw, bh = (box[2] - box[0]) * img_w, (box[3] - box[1]) * img_h
    return max(bw * MARGIN, bh * MARGIN * W / H) / (2 * W)


def windows(move, n, img_w, img_h, W, H, focus=None, box=None):
    """Per-frame (x0, y0, ww, angle) in master-image pixels for a move over n frames. The master is the still scaled so the
    largest window of the output's aspect that fits it is exactly 2W x 2H; an image wider/taller than the frame keeps its extra
    width/height (a pan then sweeps across the whole picture). box = the subject (x0, y0, x1, y1, fractions of the picture): the
    tight end of the move never cuts into it and is centred on it."""
    w0, w1, kind = MOVES.get(move, MOVES["push"])
    fx, fy = focus or DEFAULT_FOCUS
    if box: fx, fy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    if kind == "whole": fx, fy, box = 0.5, 0.5, None   # (the picture was already fitted whole on a blurred canvas)
    MW, MH = 2 * W, 2 * H                         # the full-frame window in master pixels
    if kind == "pan" and img_w >= MW * 1.25:      # a wide picture in a tall frame: sweep it all at full height
        w0 = w1 = 0.9
    nd = need(box, img_w, img_h, W, H)
    floor = min(1.0, nd) if nd is not None else NO_SUBJECT_MIN
    if kind != "pan":
        lo, hi = min(w0, w1), max(w0, w1)
        lo2 = max(lo, floor)
        if hi < lo2 * 1.06:                       # the floor ate the move: keep some movement
            hi = min(1.0, lo2 * (1.25 if lo2 < 0.8 else 1.08)) if lo2 < 1.0 else 1.0
        hi = max(hi, lo2)
        w0, w1 = (lo2, hi) if w0 < w1 else (hi, lo2)
    elif nd is not None:
        w0 = w1 = max(w0, min(floor, 0.9))
    out = []
    for i in range(n):
        p = ease(i / max(1, n - 1))
        w = w0 + (w1 - w0) * p
        ww, wh = MW * w, MH * w
        if kind == "pan":
            a, b = (0.0, 1.0) if move == "pan-lr" else (1.0, 0.0)
            lo_x, hi_x = 0.0, img_w - ww
            if box and nd is not None and nd * MW <= ww:   # the subject fits: sweep only as far as keeps all of it in frame
                lo_x, hi_x = max(lo_x, box[2] * img_w - ww), min(hi_x, box[0] * img_w)
                if lo_x > hi_x: lo_x = hi_x = min(max(fx * img_w - ww / 2, 0.0), img_w - ww)
            x0 = lo_x + (a + (b - a) * p) * (hi_x - lo_x)
            cy = fy * img_h
            y0 = cy - wh / 2
        else:
            if kind == "tilt":
                cx = fx * img_w
                fyp = fy * img_h
                top = wh / 2 if not box else max(wh / 2, box[3] * img_h - wh / 2)   # start as high as keeps the subject whole
                cy = top + p * (fyp - top) if fyp > top else top
            elif kind == "dolly":
                cx, cy = fx * img_w + (0.5 - p) * 0.08 * MW * (0.5 if box else 1.0), fy * img_h
            else:
                cx, cy = fx * img_w, fy * img_h
            if kind == "spin":                    # keep a margin so the rotated corners stay inside the picture
                bw, bh = min(ww * 1.3, img_w), min(wh * 1.3, img_h)
                bx = min(max(cx - bw / 2, 0.0), img_w - bw)
                by = min(max(cy - bh / 2, 0.0), img_h - bh)
                x0, y0 = bx + (bw - ww) / 2, by + (bh - wh) / 2
            else:
                x0, y0 = cx - ww / 2, cy - wh / 2
        x0 = min(max(x0, 0.0), max(0.0, img_w - ww))
        y0 = min(max(y0, 0.0), max(0.0, img_h - wh))
        out.append((x0, y0, ww, -9.0 * p if kind == "spin" else 0.0))
    return out


def master_size(iw, ih, W, H):
    """The master's size for a picture iw x ih (see _master)."""
    sc = (2 * W / iw) if iw / ih <= W / H else (2 * H / ih)
    return max(2 * W, int(round(iw * sc))), max(2 * H, int(round(ih * sc)))


def _master(img, W, H):
    """Scale (Lanczos, once) so the largest output-aspect window that fits the picture becomes 2W x 2H."""
    import cv2
    ih, iw = img.shape[:2]
    # the largest window of aspect W/H inside iw x ih: limited by width when the picture is narrower than the frame's aspect
    sc = (2 * W / iw) if iw / ih <= W / H else (2 * H / ih)
    nw, nh = master_size(iw, ih, W, H)
    interp = cv2.INTER_LANCZOS4 if sc > 1 else cv2.INTER_AREA
    return cv2.resize(img, (nw, nh), interpolation=interp)


def _whole_canvas(img, W, H):
    """A 2W x 2H master holding the WHOLE picture (fitted to 94% of the width or height, so the gentle push of "whole" never cuts
    into it) over a blurred, darkened, enlarged copy of itself."""
    import cv2
    CW, CH = 2 * W, 2 * H
    ih, iw = img.shape[:2]
    r = max(CW / iw, CH / ih)
    bg = cv2.resize(img, (int(iw * r + 1), int(ih * r + 1)), interpolation=cv2.INTER_AREA if r < 1 else cv2.INTER_LINEAR)
    y0, x0 = (bg.shape[0] - CH) // 2, (bg.shape[1] - CW) // 2
    bg = bg[y0:y0 + CH, x0:x0 + CW]
    small = cv2.resize(bg, (CW // 16, CH // 16), interpolation=cv2.INTER_AREA)
    bg = cv2.resize(cv2.GaussianBlur(small, (0, 0), 3), (CW, CH), interpolation=cv2.INTER_LINEAR)
    bg = (bg.astype(np.float32) * 0.55).astype(np.uint8)
    f = min(CW * 0.94 / iw, CH * 0.94 / ih)
    fw, fh = int(iw * f), int(ih * f)
    fg = cv2.resize(img, (fw, fh), interpolation=cv2.INTER_LANCZOS4 if f > 1 else cv2.INTER_AREA)
    ox, oy = (CW - fw) // 2, (CH - fh) // 2
    bg[oy:oy + fh, ox:ox + fw] = fg
    return bg


def _ffmpeg():
    try:
        from . import media
        return media._bin("ffmpeg")
    except Exception:
        return "ffmpeg"


def render_framed(still, seconds, move, out_mp4, focus=None, size=(1920, 1080), fps=FPS, box=None):
    """Render a still (a path or a BGR array) as a rigid framed camera move -> out_mp4 (H.264, no audio)."""
    import cv2
    W, H = size
    img = still if isinstance(still, np.ndarray) else cv2.imread(str(still), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"cannot read {still}")
    m = _whole_canvas(img, W, H) if MOVES.get(move, MOVES["push"])[2] == "whole" else _master(img, W, H)
    n = max(2, int(round(seconds * fps)))
    out_mp4 = Path(out_mp4); out_mp4.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_mp4.with_name(out_mp4.stem + ".part.mp4")
    ff = subprocess.Popen([_ffmpeg(), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(fps),
                           "-i", "-", "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "15", "-pix_fmt", "yuv420p",
                           "-g", str(fps), "-movflags", "+faststart", str(tmp)],
                          stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    err = b""
    try:
        for x0, y0, ww, ang in windows(move, n, m.shape[1], m.shape[0], W, H, focus, box):
            s = W / ww                              # master px -> output px
            M = np.float32([[s, 0, -x0 * s], [0, s, -y0 * s]])
            if ang:
                R = cv2.getRotationMatrix2D((W / 2, H / 2), ang, 1.0)
                M = (np.vstack([R, [0, 0, 1]]) @ np.vstack([M, [0, 0, 1]]))[:2].astype(np.float32)
            frame = cv2.warpAffine(m, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
            ff.stdin.write(frame.tobytes())
    finally:
        try: ff.stdin.close()
        except Exception: pass
        err = ff.stderr.read() if ff.stderr else b""
        ff.wait()
    if ff.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"framed render failed for {getattr(still, 'name', 'still')}: {err.decode(errors='replace')[-200:]}")
    os.replace(tmp, out_mp4)
    return out_mp4


# ------------------------------------------------------------------ stills from timeline backgrounds
def eligible(bg):
    """A generated / sourced still that gets a framed move. Never: your own files, slides/charts (fit still), credited photos
    shown whole, clips, graphics."""
    if bg.get("user"): return False
    t = bg.get("type")
    if t == "layers": return bool(bg.get("layers"))
    if t == "image":
        fit = bg.get("fit")
        if fit == "still": return False
        if fit == "frame" and bg.get("credit"): return False
        return bool(bg.get("src"))
    return False


def _read_rgba(p):
    import cv2
    im = cv2.imread(str(p), cv2.IMREAD_UNCHANGED)
    if im is None: raise RuntimeError(f"cannot read {p}")
    if im.ndim == 2: im = cv2.cvtColor(im, cv2.COLOR_GRAY2BGR)
    return im


_IMG = (".jpg", ".jpeg", ".png", ".webp")


def _job_of(root):
    r = Path(root).resolve()
    for d in [r, *r.parents]:
        if (d / "state.json").exists(): return d
    return None


class _Index:
    """basename -> paths of the job's working files (not the render folders), to find the original still behind a parallax layer."""
    def __init__(self, job):
        self.by = {}
        if not job: return
        for dp, dn, fn in os.walk(job):
            dn[:] = [d for d in dn if d not in ("public", "framed", "output", "edit", "qc", "logs") and not d.endswith("_frames")]
            for f in fn:
                if f.lower().endswith(_IMG): self.by.setdefault(f.lower(), []).append(Path(dp) / f)


def _cover(img, W, H):
    import cv2
    ih, iw = img.shape[:2]; r = max(W / iw, H / ih)
    im = cv2.resize(img, (int(iw * r + 0.5), int(ih * r + 0.5)), interpolation=cv2.INTER_AREA if r < 1 else cv2.INTER_LANCZOS4)
    x0, y0 = (im.shape[1] - W) // 2, (im.shape[0] - H) // 2
    return im[y0:y0 + H, x0:x0 + W], (x0, y0, r)


def _original(l0, ref, mask, index):
    """The still a parallax layer was cut from: a picture next to the layer's working copy whose name starts with the same shot id
    (stills/s0_0.jpg for s0_0_px_L0.jpg, imagegen/b0_cloudflare.jpg for b0_px_L0.jpg), accepted only if it matches the layer's
    pixels where the subject is. -> (BGR image, (x0, y0, r) of its crop to the frame) or None."""
    import re
    import cv2
    m = re.match(r"^(.+?)(_px\d*|_r|_hold|_tail|_p\d+)?_L0\.\w+$", l0.name)
    if not m or index is None: return None
    base = m.group(1).lower()
    for src_l0 in index.by.get(l0.name.lower(), []):
        cands = sorted((p for p in src_l0.parent.iterdir() if p.suffix.lower() in _IMG and not re.search(r"_L\d+\.", p.name)
                        and "_src" not in p.name and (p.stem.lower() == base or p.stem.lower().startswith(base + "_"))),
                       key=lambda p: (p.stem.lower() != base, p.name))
        for c in cands:
            im = cv2.imread(str(c), cv2.IMREAD_COLOR)
            if im is None: continue
            crop, geo = _cover(im, ref.shape[1], ref.shape[0])
            sel = mask if mask is not None and mask.mean() > 0.01 else np.ones(ref.shape[:2], bool)
            diff = float(np.abs(crop.astype(np.int16) - ref.astype(np.int16))[sel].mean())
            if diff < 14: return im, geo
    return None


def _box_of(mask):
    """The subject's box (x0, y0, x1, y1 as fractions) from a cut-out mask, ignoring stray specks (1st-99th percentile)."""
    ys, xs = np.nonzero(mask)
    if xs.size < 50: return None
    h, w = mask.shape
    return (float(np.percentile(xs, 1)) / w, float(np.percentile(ys, 1)) / h,
            float(np.percentile(xs, 99)) / w, float(np.percentile(ys, 99)) / h)


_FACES = [None]


def faces_box(img):
    """Head-and-shoulders box around the faces in a picture (fractions), or None - for stills without a cut-out."""
    import cv2
    try:
        if _FACES[0] is None:
            _FACES[0] = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        h, w = img.shape[:2]; sc = 640 / max(h, w)
        g = cv2.cvtColor(cv2.resize(img, (int(w * sc), int(h * sc))), cv2.COLOR_BGR2GRAY)
        m = max(24, int(min(g.shape) * 0.06))
        fs = _FACES[0].detectMultiScale(g, 1.1, 6, minSize=(m, m))
    except Exception:
        return None
    if len(fs) == 0: return None
    gh, gw = g.shape
    x0 = min(x - 0.8 * fw for x, y, fw, fh in fs); x1 = max(x + 1.8 * fw for x, y, fw, fh in fs)
    y0 = min(y - 0.5 * fh for x, y, fw, fh in fs); y1 = max(y + 2.6 * fh for x, y, fw, fh in fs)
    return (max(0.0, x0 / gw), max(0.0, y0 / gh), min(1.0, x1 / gw), min(1.0, y1 / gh))


def picture(bg, root, index=None):
    """-> (BGR image, focus (x, y) or None, subject share or None, subject box or None) for an eligible background, or None when
    the true picture can't be had (a rembg cut-out whose original is gone: putting its layers back together leaves a halo, so that
    shot keeps its old look)."""
    import cv2
    root = Path(root)
    if bg.get("type") == "image":
        im = _read_rgba(root / bg["src"])
        im = im[..., :3] if im.shape[2] == 4 else im
        box = faces_box(im)
        return im, (((box[0] + box[2]) / 2, (box[1] + box[3]) / 2) if box else None), None, box
    lays = [_read_rgba(root / rel) for rel in bg["layers"]]
    l0 = lays[0][..., :3] if lays[0].shape[2] == 4 else lays[0]
    top = lays[-1] if len(lays) > 1 and lays[-1].ndim == 3 and lays[-1].shape[2] == 4 else None
    focus = share = mask = strict = box = None; full = None
    if top is not None:
        if top.shape[:2] != l0.shape[:2]: top = cv2.resize(top, (l0.shape[1], l0.shape[0]))
        a = top[..., 3].astype(np.float32) / 255.0
        mask = a > 0.5; strict = a > 0.95; share = float(mask.mean())
        if 0.01 < share < 0.8:
            ys, xs = np.nonzero(mask)
            focus = (float(np.median(xs)) / mask.shape[1], float(np.median(ys)) / mask.shape[0])
            box = _box_of(mask)
        if len(lays) == 2:
            under = top[..., :3][a == 0]
            if under.size and float(under.mean()) > 2: full = top[..., :3]     # the cut-out keeps the whole picture under its alpha
        else:
            full = l0; share = None                                            # depth slices over the full picture
    else:
        full = l0
    ref = full if full is not None else top[..., :3]
    orig = _original(root / bg["layers"][0], ref, strict if full is None else None, index)
    if orig is not None:
        im, (x0, y0, r) = orig
        X = lambda v: (v * l0.shape[1] + x0) / (im.shape[1] * r)
        Y = lambda v: (v * l0.shape[0] + y0) / (im.shape[0] * r)
        if focus: focus = (X(focus[0]), Y(focus[1]))
        if box: box = (max(0.0, X(box[0])), max(0.0, Y(box[1])), min(1.0, X(box[2])), min(1.0, Y(box[3])))
        out = im
    elif full is not None:
        out = full
    else:
        return None
    if box is None:
        box = faces_box(out)
        if box: focus = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    if focus: focus = (min(max(focus[0], 0.15), 0.85), min(max(focus[1], 0.2), 0.75))
    return out, focus, share, box


# What the narration says over a shot picks its framing (owner, 2026-11-16: "add narration-driven shot choice"). First match wins;
# each cue lists its moves in preference order, so a cue repeated on the next shot still varies (close -> hold, pull -> wide).
CUES = [
    ("overhead", ["spin", "pull"], r"\b(aerial|overhead|from above|above the|bird'?s[- ]eye|drone|from the air|from the sky|top[- ]down|"
                                   r"satellite|from up high|high above)\b"),
    ("close", ["close", "hold"], r"\b(face|faces|eyes?|close[- ]?up|up close|closer look|look closely|detail|details|logo|badge|emblem|"
                                 r"grille|button|buttons|screen|hands?|tiny|small|inside the|stitch\w*|texture)\b"),
    ("tilt", ["tilt-down", "push"], r"\b(tall|taller|tallest|tower|towers|skyscraper\w*|height|rises?|rising|soar\w*|from the top|"
                                    r"top to bottom|looms?|towering)\b"),
    ("pan", ["pan-lr", "pan-rl"], r"\b(across|along|row of|rows of|line of|lined up|side by side|stretch(es|ed)?|one after another|"
                                  r"fleet|convoy|lineup|line-up)\b"),
    ("wide", ["pull", "wide"], r"\b(whole|entire|landscape|city|cities|horizon|panorama\w*|crowds?|everywhere|vast|huge|massive|"
                               r"skyline|the scene|all around|surround\w*)\b"),
    ("dolly", ["dolly", "push"], r"\b(approach\w*|towards?|heads? (in|into|for)|closer|moving in|step inside|enter\w*|drives? up)\b"),
]


def cue(text):
    """-> (cue name, moves in preference order) for the words spoken over a shot, or None."""
    import re
    t = (text or "").lower()
    for name, moves, rx in CUES:
        if re.search(rx, t): return name, moves
    return None


def shot_words(beat, bg, dur):
    """The narration spoken while this background is on screen (word times are relative to the beat)."""
    a = float(bg.get("from", 0)); e = a + dur
    return " ".join(str(w[0]) for w in beat.get("words", []) if len(w) > 2 and a - 0.2 <= float(w[1]) < e)


def choose(k, prev, t, last_spin, share, avoid=(), said=None, need=None):
    """The k-th framed shot's move: what the narration asks for (said = cue moves) when it can, else the rotation adapted to the
    picture; never the same move twice in a row; one spin a minute (30 s when the narration asks for an aerial view)."""
    if need is not None and need > 1.0:
        return "whole"   # no window of the frame's shape holds the whole subject: show the whole picture (even twice running)
    order = ROTATION[k % len(ROTATION):] + ROTATION[:k % len(ROTATION)]
    def ok(m, gap=60):
        if m == prev or m in avoid: return False
        if need is not None and need > 0.8 and m in SUBJECT_MOVES: return False   # a big subject: a close-up would crop it
        if m == "spin" and t - last_spin < gap: return False
        return True
    for m in said or ():
        if ok(m, 30): return m
    first = order[0]
    if share is not None and share >= 0.12 and first in ("wide", "spin"):
        on = ["push", "close", "dolly", "hold"]                            # a clear subject: on it (varied)
        order = on[k % 4:] + on[:k % 4] + order
    elif share is None and first in SUBJECT_MOVES:
        order = list(SCENE_MOVES) + order                                  # a scene: show it
    return next((m for m in order if ok(m)), "push" if prev != "push" else "dolly")


def _shots(data):
    """Backgrounds in the Teaser's drawing order with their start and length (Teaser.tsx)."""
    out = []
    for b in data["beats"]:
        bgs = b.get("bgs", [])
        for i, bg in enumerate(bgs):
            end = bgs[i + 1]["from"] if i + 1 < len(bgs) else b["dur"]
            out.append((b, bg, b["start"] + bg.get("from", 0), max(0.1, end - bg.get("from", 0))))
    return out


def framed_copy(data, root, cfg, log=print, stop=lambda: False, roots=None):
    """The Teaser timeline as Remotion should render it: a copy in which every eligible still is replaced by its framed-move MP4
    (written to <root>/framed/, cached by picture + move + length + focus + size) and consecutive framed shots dissolve.
    The timeline itself is never rewritten: only each still's chosen move is remembered on it ("cam"), so re-renders and beat
    edits keep the same moves. roots = {beat id: dir} for beats whose media paths are relative to another folder (editor preview).
    Legacy mode: a plain copy."""
    out = json.loads(json.dumps(data))
    if mode(cfg) == "legacy":
        return out
    W, H = int(data.get("w") or 1080), int(data.get("h") or 1920)
    root = Path(root); roots = roots or {}
    shots, oshots = _shots(data), _shots(out)
    index = _Index(_job_of(root))
    jobs, k, prev, last_spin = [], 0, None, -1e9
    for idx, ((b, bg, start, dur), (ob, obg, _, _)) in enumerate(zip(shots, oshots)):
        if stop(): raise RuntimeError("cancelled")
        if not eligible(bg):
            continue
        broot = Path(roots.get(b["id"], root))
        try:
            got = picture(bg, broot, index)
        except Exception as e:
            got = None; log(f"  camera move: {b['id']} still unreadable ({str(e)[:80]}); kept as it was")
        if got is None:
            continue
        img, focus, share, box = got
        mw, mh = master_size(img.shape[1], img.shape[0], W, H)
        nd = need(box, mw, mh, W, H)
        move = bg.get("cam")
        if move not in MOVES or move == prev:
            nxt = shots[idx + 1][1] if idx + 1 < len(shots) else None
            c = cue(shot_words(b, bg, dur))
            move = choose(k, prev, start, last_spin, share, ((nxt or {}).get("cam"),), c[1] if c else None, need=nd)
            bg["cam"] = move
        if move == "spin": last_spin = start
        secs = round(dur + TAIL, 3)
        h = hashlib.sha1(img.tobytes()[::97] + json.dumps([VERSION, move, secs, focus, box, W, H]).encode()).hexdigest()[:16]
        rel = f"framed/{h}.mp4"
        jobs.append((img, secs, move, broot / rel, focus, box))
        nb = {"type": "video", "src": rel, "framed": True, "move": move, "rate": 1.0, "from": bg.get("from", 0)}
        for x in ("slot", "inset", "tag"):
            if x in bg: nb[x] = bg[x]
        ob["bgs"][ob["bgs"].index(obg)] = nb
        prev = move; k += 1
    todo = [j for j in jobs if not (j[3].exists() and j[3].stat().st_size > 1000)]
    if todo:
        n = max(1, min(int(cfg.get("camera_moves_workers", 0) or 0) or max(2, (os.cpu_count() or 4) // 4), 8, len(todo)))
        log(f"Camera moves: rendering {len(todo)} framed still shot(s) at {W}x{H}, {n} at a time ({len(jobs) - len(todo)} cached)")
        errs, lock = [], threading.Lock()
        def one(j):
            if stop(): return
            try: render_framed(j[0], j[1], j[2], j[3], focus=j[4], size=(W, H), box=j[5])
            except Exception as e:
                with lock: errs.append(str(e)[:160])
        with ThreadPoolExecutor(n) as ex: list(ex.map(one, todo))
        if stop(): raise RuntimeError("cancelled")
        if errs: log(f"  camera moves: {len(errs)} shot(s) failed ({errs[0]}); those keep the old motion")
    elif jobs:
        log(f"Camera moves: {len(jobs)} framed still shot(s), all cached")
    # a shot whose render failed goes back to its old look; then the dissolves: a framed shot fades in over a framed shot
    for (b, bg, _, _), (ob, obg, _, _) in zip(shots, _shots(out)):
        if obg.get("framed") and not (Path(roots.get(b["id"], root)) / obg["src"]).exists():
            ob["bgs"][ob["bgs"].index(obg)] = json.loads(json.dumps(bg))
    before = None
    for _, obg, _, _ in _shots(out):
        if obg.get("framed") and before is not None and before.get("framed"): obg["xf"] = True
        before = obg
    if cfg.get("reframe", True):   # wide clips aimed at their subject in a tall frame; speaker insets that show a face (reframe.py)
        from . import reframe
        try: reframe.apply(out, root, roots, log)
        except Exception as e: log(f"  reframe skipped ({str(e)[:120]})")
    return out


def render_props(pub, cfg, log=print, stop=lambda: False):
    """Before a full Remotion render: write <pub>/teaser_render.json (the framed copy) and remember the moves in teaser.json.
    -> the props file to pass to Remotion."""
    pub = Path(pub); tl = pub / "teaser.json"
    data = json.loads(tl.read_text(encoding="utf-8"))
    before = json.dumps(data, sort_keys=True)
    out = framed_copy(data, pub, cfg, log, stop)
    if json.dumps(data, sort_keys=True) != before:
        tl.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    pf = pub / "teaser_render.json"
    pf.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return pf


def first_framed_at(props, fps=FPS):
    """Frames at which a framed shot starts in a render-props timeline (an edit next to one must re-render it: its dissolve shows
    the shot before it)."""
    return {max(1, math.floor((b["start"] + bg.get("from", 0)) * fps + 0.5)) if b["start"] + bg.get("from", 0) > 0 else 0
            for b, bg, _, _ in _shots(props) if bg.get("framed")}
