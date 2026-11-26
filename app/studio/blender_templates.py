"""Blender 5.2.3 headless 3D template driver sharing the unreal_mrq.py JSON contract."""
from __future__ import annotations
import glob, json, os, shutil, subprocess, tempfile
from pathlib import Path
from typing import Any, Callable, Optional
REPO_ROOT = Path(__file__).resolve().parents[2]
RENDER_SCRIPT = REPO_ROOT / "blender_studio" / "render_template.py"
def find_blender(cfg: Optional[dict[str, Any]] = None) -> Optional[Path]:
    if cfg and cfg.get("blender_exe") and Path(str(cfg["blender_exe"])).exists(): return Path(str(cfg["blender_exe"]))
    w = shutil.which("blender")
    if w: return Path(w)
    pats = [os.path.expandvars(r"%LOCALAPPDATA%\Programs\Blender Foundation\blender-*\blender.exe"), r"C:\Program Files\Blender Foundation\Blender*\blender.exe"]
    hits = [Path(h) for p in pats for h in glob.glob(p)]
    hits.sort(reverse=True)
    return hits[0] if hits else None
def render_beat(spec: dict[str, Any], out_mp4: Path, cfg: Optional[dict[str, Any]] = None, log: Optional[Callable[[str], None]] = None, timeout_s: int = 300) -> Optional[Path]:
    emit = log or (lambda _m: None)
    bl = find_blender(cfg)
    if not bl or not RENDER_SCRIPT.exists(): return None
    out_mp4 = Path(out_mp4); out_mp4.parent.mkdir(parents=True, exist_ok=True)
    fps = int(spec.get("fps", 24))
    with tempfile.TemporaryDirectory(prefix="gvs_bl_") as tmp:
        fdir = Path(tmp) / "frames"; fdir.mkdir(parents=True, exist_ok=True)
        p = dict(spec); p["output_dir"] = str(fdir)
        pf = Path(tmp) / "params.json"; pf.write_text(json.dumps(p), encoding="utf-8")
        env = os.environ.copy(); env["GVS_BLENDER_PARAMS"] = str(pf)
        proc = subprocess.run([str(bl), "--background", "--python", str(RENDER_SCRIPT)], env=env, capture_output=True, text=True, timeout=timeout_s)
        pngs = sorted(fdir.glob("frame_*.png"))
        if proc.returncode != 0 or not pngs: return None
        ff = subprocess.run(["ffmpeg", "-y", "-framerate", str(fps), "-i", str(fdir / "frame_%04d.png"), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", str(out_mp4)], capture_output=True, text=True, timeout=120)
        if ff.returncode != 0 or not out_mp4.exists(): return None
        emit(f"blender_templates: rendered {out_mp4.name} ({len(pngs)} frames)")
        return out_mp4
