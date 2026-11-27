"""G052: Unreal is the shared rung ahead of Blender, and UE 5.8 map frames score honestly."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from studio import qc_repair, rungs, unreal_mrq  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def test_search_roots_include_the_real_ue_58_tree_and_env():
    assert r"D:\Epic Games" in unreal_mrq.SEARCH_ROOTS
    roots = unreal_mrq.search_roots({"UE_INSTALL_ROOTS": r"D:\Custom UE;E:\More"})
    assert roots[0] == r"D:\Custom UE" and roots[1] == r"E:\More"
    assert roots.index(r"D:\Epic Games") > 1
    # A drive path must survive ':' which is os.pathsep on Linux.
    assert unreal_mrq._split_roots(r"D:\Epic Games") == [r"D:\Epic Games"]


def test_find_unreal_resolves_ue_58_editor(tmp_path, monkeypatch):
    root = tmp_path / "Epic Games"
    exe = root / "UE_5.8" / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"
    older = root / "UE_5.4" / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"
    exe.parent.mkdir(parents=True)
    older.parent.mkdir(parents=True)
    exe.write_bytes(b"ue58")
    older.write_bytes(b"ue54")
    monkeypatch.setenv("UE_INSTALL_ROOTS", str(root))
    monkeypatch.delenv("UNREAL_EDITOR_CMD", raising=False)
    found = unreal_mrq.find_unreal({})
    assert found == exe
    assert "UE_5.8" in str(found) and found.name == "UnrealEditor-Cmd.exe"


def test_missing_editor_warns_and_does_not_fall_back_to_blender(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("UE_INSTALL_ROOTS", raising=False)
    monkeypatch.delenv("UNREAL_EDITOR_CMD", raising=False)
    from studio import blender_templates
    monkeypatch.setattr(blender_templates, "render_beat", lambda *a, **k: (_ for _ in ()).throw(AssertionError("blender fallback")))
    logs = []
    with caplog.at_level("WARNING"):
        got = unreal_mrq.render_beat({"template": "T_DataWall"}, tmp_path / "x.mp4", cfg={}, log=logs.append)
    assert got is None
    text = "\n".join(logs)
    assert "WARNING" in text and "find_unreal() returned None" in text
    assert "RUNG_FIRED" not in text and "blender_fallback" not in text
    assert any("find_unreal() returned None" in r.message and r.levelname == "WARNING" for r in caplog.records)


def test_headless_smoke_logs_unreal_mrq(tmp_path, monkeypatch, capsys):
    root = tmp_path / "Epic Games"
    exe = root / "UE_5.8" / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, struct, zlib\n"
        "from pathlib import Path\n"
        "spec = json.loads(Path(os.environ['GVS_MRQ_PARAMS']).read_text())\n"
        "out = Path(spec['output_dir']); out.mkdir(parents=True, exist_ok=True)\n"
        "w = h = 8\n"
        "def chunk(t, d):\n"
        "    return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)\n"
        "raw = b''.join(b'\\x00' + b'\\xff\\x00\\x00' * w for _ in range(h))\n"
        "png = b'\\x89PNG\\r\\n\\x1a\\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')\n"
        "(out / 'frame_0000.png').write_bytes(png)\n",
        encoding="utf-8",
    )
    exe.chmod(0o755)
    monkeypatch.setenv("UE_INSTALL_ROOTS", str(root))
    monkeypatch.delenv("UNREAL_EDITOR_CMD", raising=False)
    from studio import blender_templates
    monkeypatch.setattr(blender_templates, "render_beat", lambda *a, **k: (_ for _ in ()).throw(AssertionError("blender fallback")))
    logs = []
    fired = unreal_mrq.smoke(cfg={}, log=logs.append, out_mp4=tmp_path / "smoke.mp4", timeout_s=30)
    text = "\n".join(logs)
    assert fired == "unreal_mrq"
    assert "RUNG_FIRED unreal_mrq" in text
    assert "blender_fallback" not in text
    assert "RUNG_FIRED unreal_mrq" in capsys.readouterr().out


def test_shared_registry_places_unreal_ahead_of_blender():
    names = [n for n, _ in qc_repair.RUNGS]
    assert names == list(rungs.SHARED_RUNG_NAMES)
    assert names[names.index("unreal") + 1] == "blender"
    import inspect
    from studio import interview, short
    assert "unreal_mrq.render_beat" in inspect.getsource(short)
    src = inspect.getsource(interview)
    chunk = src[src.index("rungs = tuple(("):src.index("if rungs is None else rungs)")]
    assert '"unreal"' in chunk and chunk.index('"unreal"') < chunk.index('"blender"')
    default = inspect.signature(qc_repair.ladder_clip).parameters["rungs"].default
    assert default[default.index("unreal") + 1] == "blender"
    assert default[-4:] == ("studio3d", "physics", "motion", "hyperframes")


def test_ue58_script_has_the_three_fixes():
    src = (REPO / "ue_studio" / "Scripts" / "mrq_render.py").read_text(encoding="utf-8")
    build = (REPO / "ue_studio" / "Scripts" / "build_templates.py").read_text(encoding="utf-8")
    assert "new_level_from_template" in src and "new_level_from_template" in build
    assert "explicit_section_end" in src and "set_end_frame_bounded(True)" in src and "custom_end_frame" in src
    assert "level_object_path" in src and "portrait_filmback" in src
    assert unreal_mrq.level_object_path("/Game/Templates/T_DataWall") == "/Game/Templates/T_DataWall.T_DataWall"
    assert unreal_mrq.section_end_frame(30, 2.5) == 75


def _map(path, drop=0, turn=None):
    from PIL import Image
    im = Image.new("RGB", (160, 160), (12, 18, 32))
    px = im.load()
    ink = [(24, y) for y in range(20, 120)] + [(x, 119) for x in range(25, 110)]
    for i, (x, y) in enumerate(ink):
        if i < drop:
            continue
        px[x, y] = (240, 30, 30)
    if turn is not None:
        im = im.transpose(turn)
    im.save(path)
    return len(ink)


def test_rotated_map_scores_near_zero_and_correct_frame_is_in_band(tmp_path):
    from PIL import Image
    ref = tmp_path / "ref.png"
    wrong = tmp_path / "wrong.png"
    right = tmp_path / "right.png"
    n = _map(ref, drop=0)
    _map(wrong, drop=0, turn=Image.Transpose.ROTATE_90)
    # Two missing stroke pixels on an otherwise upright map: 0.98-0.995, not a perfect copy.
    assert n >= 100
    _map(right, drop=2)
    assert unreal_mrq.pixel_score(wrong, ref) < 0.05
    score = unreal_mrq.pixel_score(right, ref)
    assert 0.98 <= score <= 0.995
    fixed = unreal_mrq.upright_map(wrong, ref)
    assert unreal_mrq.pixel_score(fixed, ref) >= 0.98
