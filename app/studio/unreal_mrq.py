"""Unreal Engine 5 Movie Render Queue (MRQ) rung driver for gvideo-studio."""
from __future__ import annotations
import glob, json, os, subprocess, tempfile
from pathlib import Path
from typing import Any, Callable, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
UE_PROJECT = REPO_ROOT / "ue_studio" / "GVideoStudio.uproject"
MRQ_SCRIPT = REPO_ROOT / "ue_studio" / "Scripts" / "mrq_render.py"
BUILD_SCRIPT = REPO_ROOT / "ue_studio" / "Scripts" / "build_templates.py"
SEARCH_ROOTS = [r"C:\Program Files\Epic Games", r"D:\Program Files\Epic Games", r"E:\Epic Games", r"F:\Epic Games"]

def find_unreal(cfg: Optional[dict[str, Any]] = None) -> Optional[Path]:
    if cfg and cfg.get("unreal_cmd"):
        p = Path(str(cfg["unreal_cmd"]))
        if p.exists():
            return p
    env_p = os.environ.get("UNREAL_EDITOR_CMD")
    if env_p and Path(env_p).exists():
        return Path(env_p)
    found = [Path(h) for r in SEARCH_ROOTS for h in glob.glob(os.path.join(r, "UE_*", "Engine", "Binaries", "Win64", "UnrealEditor-Cmd.exe"))]
    if not found:
        return None
    found.sort(reverse=True)
    return found[0]

def render_beat(spec: dict[str, Any], out_mp4: Path, cfg: Optional[dict[str, Any]] = None, log: Optional[Callable[[str], None]] = None, timeout_s: int = 600) -> Optional[Path]:
    emit = log or (lambda _m: None)
    ue_cmd = find_unreal(cfg)
    if not ue_cmd:
        emit("unreal_mrq: UnrealEditor-Cmd.exe not installed; skipping UE5 rung")
        return None
    if not UE_PROJECT.exists() or not MRQ_SCRIPT.exists():
        emit("unreal_mrq: ue_studio project or mrq_render.py missing; skipping")
        return None
    out_mp4 = Path(out_mp4)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    fps = int(spec.get("fps", 30))
    with tempfile.TemporaryDirectory(prefix="gvs_mrq_") as tmp:
        tmp_dir = Path(tmp)
        frames_dir = tmp_dir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        params = dict(spec)
        params["output_dir"] = str(frames_dir)
        params_file = tmp_dir / "ue_params.json"
        params_file.write_text(json.dumps(params, indent=2), encoding="utf-8")
        env = os.environ.copy()
        env["GVS_MRQ_PARAMS"] = str(params_file)
        cmd = [str(ue_cmd), str(UE_PROJECT), f"-ExecutePythonScript={MRQ_SCRIPT}", "-unattended", "-nop4", "-nosplash"]
        emit(f"unreal_mrq: launching UE5 MRQ (timeout={timeout_s}s)")
        try:
            proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            emit(f"unreal_mrq: TIMEOUT after {timeout_s}s; falling back")
            return None
        if proc.returncode != 0:
            emit(f"unreal_mrq: UE5 exited with code {proc.returncode}; falling back")
            return None
        pngs = sorted(frames_dir.glob("frame_*.png"))
        if not pngs:
            emit("unreal_mrq: no PNG frames produced; falling back")
            return None
        ff = subprocess.run(["ffmpeg", "-y", "-framerate", str(fps), "-i", str(frames_dir / "frame_%04d.png"), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", str(out_mp4)], capture_output=True, text=True, timeout=180)
        if ff.returncode != 0 or not out_mp4.exists():
            emit("unreal_mrq: ffmpeg encode failed; falling back")
            return None
        emit(f"unreal_mrq: rendered {out_mp4.name} ({len(pngs)} frames)")
        return out_mp4
