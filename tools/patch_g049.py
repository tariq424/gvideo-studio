from pathlib import Path
import json, re
Q = chr(39)
S = Path(r"F:\code\gvideo-studio\app\studio")
rungs_lines = [
    "\"\"\"Single shared visual rung registry for Shorts, explainers, interviews, and QC repair.\"\"\"",
    "SHARED_RUNG_NAMES = (",
    "    \"stock\", \"meta_chain\", \"ai_image\", \"chatgpt_chain\", \"blender\", \"video\",",
    "    \"cloud\", \"marble\", \"local_i2v_own_frame\", \"wangp\", \"static_image\",",
    "    \"studio3d\", \"physics\", \"motion\", \"hyperframes\", \"threejs\", \"card\",",
    ")",
    "TERMINAL_FLOOR = (\"illustration\", \"silhouette\", \"card\")",
    "def build_registry(handlers: dict):",
    "    return [(n, handlers[n]) for n in SHARED_RUNG_NAMES if n in handlers]",
    "def short_order(own_footage: bool = False):",
    "    base = [n for n in SHARED_RUNG_NAMES if n != \"card\"]",
    "    return ([\"source\"] + base + [\"card\"]) if own_footage else (base + [\"card\"])",
]
(S / "rungs.py").write_text("\n".join(rungs_lines) + "\n", encoding="utf-8")
print("[OK] wrote app/studio/rungs.py")
qc_p = S / "qc_repair.py"; qc = qc_p.read_text(encoding="utf-8")
if "from .rungs import SHARED_RUNG_NAMES" not in qc:
    qc = "from .rungs import SHARED_RUNG_NAMES\n" + qc
    qc_p.write_text(qc, encoding="utf-8"); print("[OK] qc_repair.py imports SHARED_RUNG_NAMES")
sv_p = S / "source_visuals.py"; sv = sv_p.read_text(encoding="utf-8")
old_bf = "try: start = start if own(job) else _best_framed(source, start, duration, dest, total, req, log)"
new_bf = "try: start = _best_own_frame(source, start, duration, dest, total, req, log) if own(job) else _best_framed(source, start, duration, dest, total, req, log)"
if old_bf in sv: sv = sv.replace(old_bf, new_bf); print("[OK] wired _best_own_frame")
if "def _best_own_frame(" not in sv:
    sv_add = [
        "",
        "def _frame_content_score(img_path):",
        "    from PIL import Image, ImageStat",
        "    with Image.open(img_path) as im:",
        "        rgb = im.convert(\"RGB\").resize((160, 90))",
        "        hsv = rgb.convert(\"HSV\")",
        "        px = list(rgb.getdata())",
        "        wf = sum(1 for r, g, b in px if r > 225 and g > 225 and b > 225) / max(1, len(px))",
        "        sat = ImageStat.Stat(hsv).mean[1] / 255.0",
        "        var = sum(ImageStat.Stat(rgb).stddev) / (3.0 * 128.0)",
        "        return (sat * 1.4) + (var * 0.8) - (wf * 1.3)",
        "def _best_own_frame(local, start, duration, dest, total, req, log):",
        "    import tempfile",
        "    best_t, best_s = start, -9.0",
        "    with tempfile.TemporaryDirectory(prefix=\"gvs_own_\") as td:",
        "        for off in (0.0, 1.5, 3.0, -1.5, 4.5):",
        "            t = max(0.0, min(max(0.0, total - duration), start + off))",
        "            f = Path(td) / (\"f_\" + str(int(t * 10)) + \".jpg\")",
        "            try:",
        "                media.ff(\"-ss\", t + 0.4, \"-i\", local, \"-frames:v\", 1, \"-q:v\", 5, \"-an\", f, timeout=30)",
        "                sc = _frame_content_score(f)",
        "                if sc > best_s: best_s, best_t = sc, t",
        "            except Exception: pass",
        "    if abs(best_t - start) >= 0.4:",
        "        media.ff(\"-ss\", best_t, \"-i\", local, \"-t\", duration, \"-an\", \"-vf\", \"fps=30,scale=960:-2\", \"-c:v\", \"libx264\", \"-crf\", \"20\", \"-pix_fmt\", \"yuv420p\", dest, timeout=120)",
        "        log(f\"  own_footage frame-score: nudged {start:.1f}s -> {best_t:.1f}s (score={best_s:.2f})\")",
        "    return best_t",
        "def make_many(cfg, job, sd, beat, log, count=3, used_starts=None):",
        "    used = list(used_starts or [])",
        "    out = []",
        "    if not own(job) or count <= 0: return out",
        "    cues_f = job / \"src\" / \"source_cues.json\"",
        "    cues = json.loads(cues_f.read_text(encoding=\"utf-8\")) if cues_f.exists() else []",
        "    vo_words = set(re.findall(r\"[a-z0-9]{4,}\", (beat.get(\"vo\") or \"\").lower()))",
        "    scored = []",
        "    for c in cues:",
        "        st = float(c.get(\"start\") or 0)",
        "        cw = set(re.findall(r\"[a-z0-9]{4,}\", (c.get(\"text\") or \"\").lower()))",
        "        scored.append((len(vo_words & cw), st, c.get(\"text\") or \"\"))",
        "    scored.sort(key=lambda x: (-x[0], x[1]))",
        "    bid = beat.get(\"id\", \"b\")",
        "    for _, st, txt in scored:",
        "        if len(out) >= count: break",
        "        if any(abs(st - u) < 3.5 for u in used): continue",
        "        b2 = dict(beat)",
        "        b2[\"id\"] = bid + \"_s\" + str(len(out) + 2)",
        "        b2[\"source_visual\"] = {\"kind\": \"clip\", \"start\": st, \"duration\": 2.5, \"purpose\": txt[:90]}",
        "        try:",
        "            it = make(cfg, job, sd, b2, log)",
        "            if it: out.append(it); used.append(float(it.get(\"timestamp\") or st))",
        "        except Exception as e: log(f\"  own_footage extra slot at {st:.1f}s skipped: {str(e)[:80]}\")",
        "    return out",
    ]
    sv += "\n".join(sv_add) + "\n"
    sv_p.write_text(sv, encoding="utf-8"); print("[OK] added _best_own_frame + make_many")
sh_p = S / "short.py"; sh = sh_p.read_text(encoding="utf-8")
if "from . import rungs" not in sh: sh = "from . import rungs\n" + sh
old_app = "            items.append(source)"
new_app = "\n".join([
    "            items.append(source)",
    "            if source_visuals.own(job) and len(items) < n:",
    "                used_ts = [float(source.get(\"timestamp\") or 0.0)]",
    "                sid = s[\"id\"]",
    "                extras = _safe(log, sid + \" own_footage extra slots\", lambda: source_visuals.make_many(cfg, job, sd, s, log, count=n-len(items), used_starts=used_ts), []) or []",
    "                for ex in extras:",
    "                    ex2 = dict(ex); ex2[\"src\"] = _rel_sd(ex2.pop(\"path\"), sd); items.append(ex2)",
])
if old_app in sh and "make_many(" not in sh: sh = sh.replace(old_app, new_app, 1); print("[OK] short.py multi-slot own_footage")
old_bl = "if v2 and cfg.get(\"blender_on\", True) and s.get(\"subject\") and not items:"
new_bl = "if v2 and cfg.get(\"blender_on\", True) and len(items) < n:"
if old_bl in sh: sh = sh.replace(old_bl, new_bl, 1); print("[OK] short.py unblocked blender rung")
old_bl_call = "clip = _safe(log, \"blender\", lambda: mediaroutes.blender_rung(cfg, sd, s, f\"{s[" + Q + "id" + Q + "]}_blender\", log, aspect=\"9:16\"))"
new_bl_call = "\n".join([
    old_bl_call,
    "            if not clip:",
    "                from . import blender_templates as _bt",
    "                _tspec = {\"template\": \"T_DataWall\", \"title\": (s.get(\"vo\") or \"\")[:40], \"subtitle\": s.get(\"id\", \"\"), \"width\": 540, \"height\": 960, \"fps\": 24, \"duration_s\": 2.5}",
    "                clip = _safe(log, \"blender_templates\", lambda: _bt.render_beat(_tspec, sd / \"blender\" / (s[\"id\"] + \"_tpl.mp4\"), cfg=cfg, log=log))",
    "            if not clip: log(\"  \" + s[\"id\"] + \": blender rung skipped (no scene or template output)\")",
])
if old_bl_call in sh and "blender_templates" not in sh: sh = sh.replace(old_bl_call, new_bl_call, 1); print("[OK] short.py wired blender_templates rung")
old_vid = "if v2 and (cfg.get(\"meta_video_on\", True) or cfg.get(\"flow_on\", True)) and not items:"
new_vid = "if v2 and (cfg.get(\"meta_video_on\", True) or cfg.get(\"flow_on\", True)) and len(items) < n:"
if old_vid in sh: sh = sh.replace(old_vid, new_vid, 1); print("[OK] short.py unblocked video rung")
sh_p.write_text(sh, encoding="utf-8")
ld_p = S / "ladder.py"; ld = ld_p.read_text(encoding="utf-8")
if "from . import rungs" not in ld:
    ld = "from . import rungs\n" + ld; ld_p.write_text(ld, encoding="utf-8"); print("[OK] ladder.py imports shared rungs")
