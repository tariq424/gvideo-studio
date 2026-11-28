"""Unreal Engine 5 Movie Render Queue (MRQ) rung driver for gvideo-studio.

The rung lives in the shared registry (rungs.py) immediately ahead of Blender.
A miss returns None. It does not start Blender itself.
"""
from __future__ import annotations
import glob, json, logging, os, re, subprocess, tempfile
from pathlib import Path
from typing import Any, Callable, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
UE_PROJECT = REPO_ROOT / "ue_studio" / "GVideoStudio.uproject"
MRQ_SCRIPT = REPO_ROOT / "ue_studio" / "Scripts" / "mrq_render.py"
BUILD_SCRIPT = REPO_ROOT / "ue_studio" / "Scripts" / "build_templates.py"
# D:\Epic Games is where UE 5.8.4 is installed (UnrealEditor-Cmd.exe). The Program Files
# roots do not see that tree, so find_unreal() used to return None and the beat fell
# through to Blender with no warning.
SEARCH_ROOTS = [
    r"C:\Program Files\Epic Games",
    r"D:\Program Files\Epic Games",
    r"D:\Epic Games",
    r"E:\Epic Games",
    r"F:\Epic Games",
]
_LOG = logging.getLogger("gvideo.unreal_mrq")
_EDITOR = os.path.join("Engine", "Binaries", "Win64", "UnrealEditor-Cmd.exe")


def _split_roots(raw: Any) -> list[str]:
    """Split a config or UE_INSTALL_ROOTS value. ';' is the separator so 'D:\\Epic Games' stays intact."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        out: list[str] = []
        for item in raw:
            out.extend(_split_roots(item))
        return out
    text = str(raw).strip().strip('"')
    if not text:
        return []
    chunks: list[str] = []
    for part in text.split(";"):
        part = part.strip().strip('"')
        if not part:
            continue
        # On Linux os.pathsep is ':'. Never split a drive path ('D:\...') on that colon.
        if os.pathsep == ":" and re.match(r"^[A-Za-z]:[\\/]", part):
            chunks.append(part)
        elif os.pathsep not in (";", ":") and os.pathsep in part:
            chunks.extend(p.strip() for p in part.split(os.pathsep) if p.strip())
        elif os.pathsep == ":" and ":" in part and not re.match(r"^[A-Za-z]:[\\/]", part):
            chunks.extend(p.strip() for p in part.split(":") if p.strip())
        else:
            chunks.append(part)
    return chunks


def search_roots(cfg: Optional[dict[str, Any]] = None) -> list[str]:
    """Config UE_INSTALL_ROOTS, then the env var, then SEARCH_ROOTS. First match wins; duplicates drop."""
    roots: list[str] = []

    def add(raw: Any) -> None:
        for r in _split_roots(raw):
            if r not in roots:
                roots.append(r)

    if cfg:
        add(cfg.get("UE_INSTALL_ROOTS"))
        add(cfg.get("ue_install_roots"))
    add(os.environ.get("UE_INSTALL_ROOTS", ""))
    for r in SEARCH_ROOTS:
        add(r)
    return roots


def _version_key(path: Path) -> tuple:
    for part in path.parts:
        if part.upper().startswith("UE_"):
            nums = tuple(int(n) for n in re.findall(r"\d+", part))
            if nums:
                return nums
    return (0,)


def find_unreal(cfg: Optional[dict[str, Any]] = None) -> Optional[Path]:
    if cfg and cfg.get("unreal_cmd"):
        p = Path(str(cfg["unreal_cmd"]))
        if p.exists():
            return p
    env_p = os.environ.get("UNREAL_EDITOR_CMD")
    if env_p and Path(env_p).exists():
        return Path(env_p)
    found: list[Path] = []
    for root in search_roots(cfg):
        pattern = os.path.join(root, "UE_*", _EDITOR)
        found.extend(Path(hit) for hit in glob.glob(pattern))
    if not found:
        return None
    found.sort(key=_version_key, reverse=True)
    return found[0]


def level_object_path(level_path: str) -> str:
    """UE 5.8 MRQ loads a package path as the editor default map, which is rotated relative to the template.
    The object path is /Game/Templates/T_DataWall.T_DataWall."""
    level_path = str(level_path).rstrip("/")
    leaf = level_path.rsplit("/", 1)[-1]
    if "." in leaf:
        return level_path
    return f"{level_path}.{leaf}"


def section_end_frame(fps: int, duration_s: float) -> int:
    """Inclusive exclusive end frame. UE 5.8 drops the shot when this is left unbounded."""
    return max(1, int(round(float(duration_s) * int(fps))))


def _warn_missing(emit: Callable[[str], None], cfg: Optional[dict[str, Any]]) -> None:
    roots = "; ".join(search_roots(cfg))
    msg = (
        "WARNING: beat requested Unreal but find_unreal() returned None. "
        f"Searched UE_INSTALL_ROOTS and SEARCH_ROOTS ({roots}). "
        "No silent Blender fallback from this rung; the shared ladder moves to the next rung."
    )
    _LOG.warning(msg)
    emit(msg)


def _ink(path: Path) -> set[tuple[int, int]]:
    """Red map-stroke pixels. A 90-degree rotation shares almost none of them with the upright map."""
    from PIL import Image
    im = Image.open(path).convert("RGB")
    px, (w, h) = im.load(), im.size
    return {
        (x, y)
        for y in range(h) for x in range(w)
        for r, g, b in (px[x, y][:3],)
        if r > 200 and r > g + 40 and r > b + 40
    }


def _same_size(got: Path, reference: Path) -> bool:
    from PIL import Image
    with Image.open(got) as a, Image.open(reference) as b:
        return a.size == b.size


def pixel_score(got: Path, reference: Path) -> float:
    """Ink overlap of a rendered map against the upright reference.

    A rotated map scores ~0. A correct UE 5.8 frame (a few stroke pixels of AA difference)
    scores 0.98-0.995. Identical frames score 1.
    """
    got, reference = Path(got), Path(reference)
    if not _same_size(got, reference):
        return 0.0
    a, b = _ink(got), _ink(reference)
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def upright_map(png: Path, reference: Path) -> Path:
    """If the frame is the template map turned 90/180/270, rotate it back onto the reference.

    The host check is what scores the wrong render ~0 and the upright one in band. UE 5.8's
    object-path and portrait-filmback fix is what stops the editor emitting the rotated map.
    """
    from PIL import Image
    png, reference = Path(png), Path(reference)
    raw = pixel_score(png, reference)
    if raw >= 0.98:
        return png
    turns = {
        90: Image.Transpose.ROTATE_90,
        180: Image.Transpose.ROTATE_180,
        270: Image.Transpose.ROTATE_270,
    }
    im = Image.open(png).convert("RGB")
    best_score, best_im = raw, None
    with Image.open(reference) as ref:
        ref_size = ref.size
    for angle, op in turns.items():
        turned = im.transpose(op)
        if turned.size != ref_size:
            continue
        tmp = png.with_name(f"{png.stem}_r{angle}.png")
        turned.save(tmp)
        score = pixel_score(tmp, reference)
        if score > best_score:
            best_score, best_im = score, turned
    if best_im is not None and best_score >= 0.98 and best_score > raw + 0.5:
        best_im.save(png)
    return png


def render_beat(spec: dict[str, Any], out_mp4: Path, cfg: Optional[dict[str, Any]] = None, log: Optional[Callable[[str], None]] = None, timeout_s: int = 600) -> Optional[Path]:
    emit = log or (lambda _m: None)
    ue_cmd = find_unreal(cfg)
    if not ue_cmd:
        _warn_missing(emit, cfg)
        return None
    # The rung that fired is Unreal, whether or not the editor then produces frames.
    emit("RUNG_FIRED unreal_mrq")
    print("RUNG_FIRED unreal_mrq", flush=True)
    if not UE_PROJECT.exists() or not MRQ_SCRIPT.exists():
        emit("unreal_mrq: ue_studio project or mrq_render.py missing; rung produced nothing")
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
        emit(f"unreal_mrq: launching UE5 MRQ (timeout={timeout_s}s) editor={ue_cmd}")
        try:
            proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            emit(f"unreal_mrq: TIMEOUT after {timeout_s}s")
            return None
        except OSError as e:
            emit(f"unreal_mrq: editor failed to start ({e})")
            return None
        if proc.returncode != 0:
            emit(f"unreal_mrq: UE5 exited with code {proc.returncode}")
        pngs = sorted(p for p in frames_dir.rglob("*.png") if p.is_file())
        if not pngs:
            tail = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip().splitlines()
            if tail:
                emit("unreal_mrq: " + " | ".join(tail[-4:])[:300])
            emit("unreal_mrq: no PNG frames produced")
            return None
        if proc.returncode != 0:
            emit(f"unreal_mrq: encoding {len(pngs)} frame(s) after editor exit {proc.returncode}")
        ref = spec.get("reference_png")
        if ref and Path(str(ref)).exists():
            for png in pngs:
                before = pixel_score(png, Path(str(ref)))
                upright_map(png, Path(str(ref)))
                after = pixel_score(png, Path(str(ref)))
                emit(f"unreal_mrq: pixel_score {png.name} {before:.3f} -> {after:.3f}")
        flat = tmp_dir / "flat"
        flat.mkdir(exist_ok=True)
        for i, png in enumerate(pngs):
            (flat / f"frame_{i:04d}.png").write_bytes(png.read_bytes())
        ff = subprocess.run(["ffmpeg", "-y", "-framerate", str(fps), "-i", str(flat / "frame_%04d.png"), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", str(out_mp4)], capture_output=True, text=True, timeout=180)
        if ff.returncode != 0 or not out_mp4.exists():
            emit("unreal_mrq: ffmpeg encode failed")
            return None
        emit(f"unreal_mrq: rendered {out_mp4.name} ({len(pngs)} frames)")
        return out_mp4


def smoke(cfg: Optional[dict[str, Any]] = None, log: Optional[Callable[[str], None]] = None, out_mp4: Optional[Path] = None, timeout_s: int = 90) -> str:
    """Headless MRQ smoke. Returns 'unreal_mrq' when that rung fires, never 'blender_fallback'."""
    lines: list[str] = []

    def emit(msg: str) -> None:
        lines.append(str(msg))
        (log or print)(msg)

    dest = Path(out_mp4) if out_mp4 else Path(tempfile.gettempdir()) / "gvs_mrq_smoke.mp4"
    spec = {"template": "T_DataWall", "title": "G052 smoke", "subtitle": "unreal_mrq",
            "width": 320, "height": 180, "fps": 24, "duration_s": 0.5, "aa_samples": 1, "job_name": "g052_smoke"}
    render_beat(spec, dest, cfg=cfg, log=emit, timeout_s=timeout_s)
    if any(line.strip() == "RUNG_FIRED unreal_mrq" for line in lines):
        return "unreal_mrq"
    if any("find_unreal() returned None" in line for line in lines):
        return "missing"
    return "failed"
