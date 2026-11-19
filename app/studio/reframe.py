"""Reframing wide clips for a tall frame, and the speaker inset (owner, 2026-11-17).

A 16:9 source clip in a 9:16 Short used to be cropped in the middle. When the speakers sit at the sides (a two-shot of hands with
black between them) the Short showed a black frame with a hand cut in half. Now, at render time:
  - faces found and they fit in the crop  -> the crop is centred on them ("pos", CSS object-position across the clip);
  - the action spreads wider than the crop -> the whole frame is shown over a blurred copy (fit "frame");
  - two subjects apart (two people at the sides of a two-shot): the whole frame first, then a cut to each subject in turn, each
    centred and whole in the crop ("seq": [{"fit": "frame"}, {"pos": a}, {"pos": b}], owner 2026-11-17) - never one crop that
    straddles both and cuts each in half. A shot too short for three cuts stays whole;
  - otherwise the crop is centred on where the picture's content is.
The speaker inset (PIP) shows the person the beat is about: their source clip playing, zoomed on the face. A frame with no face
in it (hands, an empty stage) is no inset at all - a box with nothing in it does nothing for the viewer.
Results are cached in <public>/framed/reframe.json. Nothing here changes the timeline; it only shapes the render props."""
import json
from pathlib import Path

import numpy as np

VERSION = 3
SEQ_MIN = 4.5                                      # seconds on screen needed for wide -> subject A -> subject B


def _frames(path, k=6, width=480):
    import cv2
    cap = cv2.VideoCapture(str(path))
    try:
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        idx = [int(n * (i + 0.5) / k) for i in range(k)] if n > 0 else [0]
        out = []
        for i in idx:
            cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            ok, f = cap.read()
            if not ok or f is None: continue
            h, w = f.shape[:2]; sc = width / w
            out.append(cv2.resize(f, (width, int(h * sc))))
        return out
    finally:
        cap.release()


_CASCADE = [None]


def faces(img):
    """[(x0, y0, x1, y1) fractions] of the faces in a BGR picture."""
    import cv2
    if _CASCADE[0] is None:
        _CASCADE[0] = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY); h, w = g.shape
    m = max(20, int(min(h, w) * 0.07))
    return [(x / w, y / h, (x + fw) / w, (y + fh) / h) for x, y, fw, fh in _CASCADE[0].detectMultiScale(g, 1.1, 6, minSize=(m, m))]


def _groups(spans, cw, gap=0.12, slack=1.05, col=None):
    """[(x0, x1)] -> the two subjects (left, right) as crop positions, when the spans split into two groups apart from each other
    that each fit in a crop of width cw (times slack); else None. col (lit share per column): centre each crop on the group's
    weight, not the middle of its extent - a person's bulk, not a stray lit edge."""
    sp = sorted(spans)
    if len(sp) < 2: return None
    gaps = [(sp[i + 1][0] - max(a[1] for a in sp[:i + 1]), i) for i in range(len(sp) - 1)]
    g, i = max(gaps)
    if g < gap: return None
    out = []
    for grp in (sp[:i + 1], sp[i + 1:]):
        x0 = min(a[0] for a in grp); x1 = max(a[1] for a in grp)
        if x1 - x0 > cw * slack: return None
        c = (x0 + x1) / 2
        if col is not None:
            n = len(col); a, b = int(x0 * n), max(int(x0 * n) + 1, int(x1 * n)); w = col[a:b]
            if w.sum() > 0: c = float((w * np.arange(a, b)).sum() / w.sum()) / n
        out.append(_pos(c, cw))
    return out if abs(out[1] - out[0]) > 0.2 else None


def analyse(path, W, H):
    """-> {"pos": 0..1} | {"fit": "frame"[, "two": [a, b]]} | {} for a clip shown in a W x H frame (only a frame narrower than
    the clip is cropped). "two": the crop positions of two separate subjects, for the wide -> each-in-turn sequence."""
    fr = _frames(path)
    if not fr: return {}
    ih, iw = fr[0].shape[:2]
    cw = (W / H) / (iw / ih)                       # the part of the clip's width a cover crop shows
    if cw >= 0.98: return {}
    fs = [f for im in fr for f in faces(im)]
    if fs:
        x0 = min(f[0] - 0.4 * (f[2] - f[0]) for f in fs); x1 = max(f[2] + 0.4 * (f[2] - f[0]) for f in fs)
        if x1 - x0 <= cw:
            return {"pos": _pos((x0 + x1) / 2, cw)}
        two = _groups([(f[0] - 0.4 * (f[2] - f[0]), f[2] + 0.4 * (f[2] - f[0])) for f in fs], cw)
        return {"fit": "frame", "two": two} if two else {"fit": "frame"}
    # no face: where is the picture? (columns that are lit in most frames)
    lum = np.mean([im.mean(axis=2) for im in fr], axis=0)          # h x w
    col = (lum > 40).mean(axis=0)                                  # share of each column that is lit
    lit = np.nonzero(col > 0.12)[0]
    if lit.size == 0: return {}
    c0, c1 = lit[0] / iw, (lit[-1] + 1) / iw
    if c1 - c0 <= cw * 1.1:
        mass = float((col * np.arange(iw)).sum() / max(col.sum(), 1e-6)) / iw
        return {"pos": _pos(mass, cw)}
    mid = col[int((0.5 - cw / 2) * iw):int((0.5 + cw / 2) * iw)]
    if mid.size and mid.mean() < 0.5 * col.mean():                 # lit at the sides, dark in the middle: show it whole
        on = col > 0.2; runs = []; i = 0                           # ... and the clearly lit blocks are the subjects
        while i < iw:
            if on[i]:
                j = i
                while j < iw and on[j]: j += 1
                if j - i > iw * 0.02: runs.append((i / iw, j / iw))
                i = j
            else:
                i += 1
        # a person a little wider than the crop is still one subject: the crop sits on their bulk (head and body), only an
        # outstretched hand or shoulder edge may fall outside - far better than one crop straddling both people
        two = _groups(runs, cw, slack=1.35, col=col)
        return {"fit": "frame", "two": two} if two else {"fit": "frame"}
    return {}


def _skin(img):
    """Share of the picture that is lit skin (YCrCb skin range)."""
    import cv2
    y, cr, cb = cv2.split(cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb))
    return float(((y > 60) & (cr > 135) & (cr < 180) & (cb > 85) & (cb < 135)).mean())


def faceless_people(path):
    """owner, 2026-11-17: a source clip that shows people but none of their faces (a tight two-shot of hands and ties, a
    shoulder at the edge) shows nobody the viewer can recognise, and cropped for a tall frame it is a hand on black. -> the
    reason (str) when the clip has skin (hands, arms) in most frames and no face in any, else "" (a face is in it, or no people:
    a machine, a place, a screen - those clips are kept)."""
    fr = _frames(path, k=6)
    if not fr or any(faces(im) for im in fr): return ""
    sk = [_skin(im) for im in fr]
    if sum(x > 0.006 for x in sk) * 2 >= len(sk):
        return "shows people but no face (hands / torso only)"
    return ""


def _secs(path):
    import cv2
    cap = cv2.VideoCapture(str(path))
    try:
        n, fps = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0, cap.get(cv2.CAP_PROP_FPS) or 30
        return round(max(1.0, n / fps - 0.1), 2) if n else 8.0
    finally:
        cap.release()


def _pos(c, cw):
    """CSS object-position (0..1) that centres the crop on c (a fraction of the clip's width)."""
    left = c - cw / 2
    return round(min(max(left / (1 - cw), 0.0), 1.0), 3)


def inset_look(path):
    """For a speaker inset: (x, y, zoom) that fills the inset with the face(s) in this clip or picture, or None (no face)."""
    import cv2
    p = Path(path)
    if p.suffix.lower() in (".mp4", ".mov", ".webm", ".mkv"):
        fr = _frames(p, k=5)
    else:
        im = cv2.imread(str(p), cv2.IMREAD_COLOR)
        fr = [] if im is None else [cv2.resize(im, (480, int(im.shape[0] * 480 / im.shape[1])))]
    fs = [f for im in fr for f in faces(im)]
    if not fs: return None
    x0 = min(f[0] for f in fs); x1 = max(f[2] for f in fs); y0 = min(f[1] for f in fs); y1 = max(f[3] for f in fs)
    fw = max(x1 - x0, 1e-3)
    zoom = round(min(2.6, max(1.0, 0.45 / fw)), 2)                 # the face(s) about half the inset's width
    return round((x0 + x1) / 2, 3), round(min(0.8, (y0 + y1) / 2 + 0.05), 3), zoom


class Cache:
    def __init__(self, root):
        self.f = Path(root) / "framed" / "reframe.json"
        try: self.d = json.loads(self.f.read_text(encoding="utf-8"))
        except (OSError, ValueError): self.d = {}
        self.dirty = False

    def get(self, key, fn):
        if key not in self.d:
            try: self.d[key] = fn()
            except Exception as e: self.d[key] = {"error": str(e)[:120]}
            self.dirty = True
        return self.d[key]

    def save(self):
        if not self.dirty: return
        try:
            self.f.parent.mkdir(parents=True, exist_ok=True)
            self.f.write_text(json.dumps(self.d, indent=1), encoding="utf-8")
        except OSError:
            pass


def _key(path, *extra):
    p = Path(path)
    try: st = p.stat(); return json.dumps([VERSION, p.name, st.st_size, int(st.st_mtime), *extra])
    except OSError: return None


def apply(out, root, roots=None, log=print):
    """Shape the render-props copy `out` in place: reframe wide clips for a tall frame; give insets a live, face-filled look or
    drop them. root / roots as in camera_moves.framed_copy."""
    W, H = int(out.get("w") or 1080), int(out.get("h") or 1920)
    root = Path(root); roots = roots or {}
    cache = Cache(root); moved = framed = dropped = seqs = 0
    for b in out.get("beats", []):
        bgs = b.get("bgs", [])
        broot = Path(roots.get(b["id"], root))
        source = next((g for g in b.get("bgs", []) if g.get("type") == "video" and str(g.get("tag") or "").startswith("SOURCE")), None)
        for gi, g in enumerate(bgs):
            if H > W and g.get("type") == "video" and not g.get("framed") and not g.get("user") and not g.get("fit") \
                    and not g.get("textbg") and str(g.get("tag") or "") != "GRAPHICS":
                k = _key(broot / g["src"], W, H)
                r = cache.get(k, lambda: analyse(broot / g["src"], W, H)) if k else {}
                if r.get("pos") is not None: g["pos"] = r["pos"]; moved += 1
                elif r.get("fit") == "frame":
                    end = bgs[gi + 1].get("from", 0) if gi + 1 < len(bgs) else float(b.get("dur") or 0)
                    if r.get("two") and end - g.get("from", 0) >= SEQ_MIN:
                        g["seq"] = [{"fit": "frame"}, {"pos": r["two"][0]}, {"pos": r["two"][1]}]; seqs += 1
                    else:
                        g["fit"] = "frame"; framed += 1
            ins = g.get("inset")
            if ins and ins.get("src"):
                look = None
                if source is not None:
                    k = _key(broot / source["src"], "inset")
                    look = cache.get(k, lambda: inset_look(broot / source["src"])) if k else None
                    if look and not (isinstance(look, dict) and look.get("error")):
                        g["inset"] = {"src": source["src"], "video": True, "x": look[0], "y": look[1], "zoom": look[2],
                                      "loop": _secs(broot / source["src"])}
                        continue
                k = _key(broot / ins["src"], "inset")
                look = cache.get(k, lambda: inset_look(broot / ins["src"])) if k else None
                if look and not (isinstance(look, dict) and look.get("error")):
                    g["inset"] = dict(ins, x=look[0], y=look[1], zoom=look[2])
                else:
                    g.pop("inset", None); dropped += 1
    cache.save()
    if moved or framed or dropped or seqs:
        log(f"Reframe: {moved} clip(s) aimed at their subject, {framed} shown whole, {seqs} two-subject clip(s) shown whole then "
            f"each subject in turn, {dropped} empty speaker inset(s) dropped")
    return out


# owner, 2026-11-19 ("example of a bad zoom again", "I would rather see her face, than body"): a source excerpt cut while the
# event camera was mid-move showed hands with black between them, or a body with the face cut off at the top. Before an
# excerpt is used its frames are checked: the share of frames with a whole, readable face in them.
FRAMED_OK = 0.75      # this share of frames with a whole face: well framed
FRAMED_MIN = 0.5      # below this (when nearby footage does show faces) the excerpt is not used


def framing(path, k=8):
    """-> share (0-1) of k frames across the clip showing at least one whole face (not touching the top edge, readable size)."""
    fr = _frames(path, k=k)
    if not fr: return 0.0
    good = 0
    for im in fr:
        if any(y0 > 0.015 and (y1 - y0) >= 0.07 for x0, y0, x1, y1 in faces(im)): good += 1
    return good / len(fr)
