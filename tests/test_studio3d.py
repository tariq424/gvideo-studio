"""owner, 2026-11-13: a real 3D model in a photo studio (like the Volvo render) comes AHEAD of the simple drawings - after
every picture rung, before physics / motion templates and the card - in every ladder."""
import inspect
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from studio import qc_repair, studio3d  # noqa: E402


def test_studio3d_sits_after_the_pictures_and_ahead_of_the_drawings():
    order = [r[0] for r in qc_repair.RUNGS]
    assert order.index("static_image") < order.index("studio3d") < order.index("physics") < order.index("motion") < order.index("hyperframes") < order.index("card")
    d = inspect.signature(qc_repair.ladder_clip).parameters["rungs"].default
    assert d[-4:] == ("studio3d", "physics", "motion", "hyperframes")


def test_every_ladder_tries_studio3d_before_drawing():
    from studio import interview, render, short
    src = inspect.getsource(interview)
    assert src.index('("studio3d",)') < src.index('("physics", "motion")') < src.rindex('("card",)')
    assert src.index("self.sketch(") < src.index('("studio3d",)')   # pictures (and the sketch) first
    s = inspect.getsource(short._v2_rungs)
    assert s.index("studio3d.clip(") < s.index("physics.provide(") < s.index("graphics.make(")
    assert s.index("depth_free(") < s.index("studio3d.clip(")   # parallax of a still is a picture: it comes first
    r = inspect.getsource(render._v2_after_wangp)
    assert r.index("studio3d.clip(") < r.index("physics.action(") < r.index("graphics.make(")


def _lib(tmp_path):
    d = tmp_path / "volvo"; (d / "src").mkdir(parents=True)
    (d / "src" / "scene.gltf").write_text("{}", encoding="utf-8")
    (d / "model.json").write_text(json.dumps({"names": ["volvo xc60", "xc60"], "nouns": ["car", "suv"], "file": "src/scene.gltf", "paint": [0.1, 0.1, 0.1],
                                              "credit": "by someone (CC BY)"}), encoding="utf-8")
    return {"studio3d_models_dir": str(tmp_path)}


def test_the_owners_own_model_wins_on_a_whole_name(tmp_path):
    cfg = _lib(tmp_path)
    p = {"thing": "SUV", "noun": "car", "words": ["suv"], "explode": False}
    m = studio3d.library(cfg, p, "We loaded the Volvo XC60 for the drive north.")
    assert m and m["file"].name == "scene.gltf" and m["credit"] and m["paint"]
    assert studio3d.library(cfg, p, "An XC600 is not the same car.") is None
    # Nov 14: a line about the 737 that also says "my Volvo XC60" must not get the car
    plane = {"thing": "Boeing 737 MAX 8", "noun": "aircraft", "words": ["737"], "explode": False}
    assert studio3d.library(cfg, plane, "From my Volvo XC60 to the Boeing 737 MAX 8 at the gate.") is None


def test_a_line_not_about_one_object_is_not_taken_and_not_asked_twice(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(studio3d, "pick", lambda cfg, vo, log: calls.append(vo) or None)
    for _ in range(2):
        clip, why = studio3d.clip({"studio3d_models_dir": str(tmp_path)}, tmp_path / "w", "She felt hopeful about the future.", "k1",
                                  1920, 1080, 6, print)
        assert clip is None and "not about one physical object" in why
    assert len(calls) == 1


def test_a_lookalike_model_is_refused(monkeypatch, tmp_path):
    """Poly Haven has no airliner: a toy plane that only shares a word must not stand in for a Boeing 737."""
    class R:
        def __init__(self, j): self.j = j
        def json(self): return self.j
    assets = {"ToyPlane_01": {"name": "Wooden Toy Plane", "tags": ["plane", "toy", "wood"], "categories": ["props"]}}
    got = []
    monkeypatch.setattr(studio3d.requests, "get", lambda url, **k: got.append(url) or R(assets))
    monkeypatch.setattr(studio3d, "_choose", lambda cfg, p, vo, cands, log: None)
    p = {"thing": "Boeing 737", "noun": "aircraft", "words": ["boeing 737", "airliner", "plane"], "explode": False}
    assert studio3d.polyhaven({"studio3d_models_dir": str(tmp_path)}, p, "The 737 left Dubai.", print) is None
    assert got == [f"{studio3d.PH}/assets"]   # nothing downloaded


def test_sketchfab_needs_a_token_and_keeps_to_open_licences(monkeypatch, tmp_path):
    monkeypatch.setattr(studio3d, "_sf_token", lambda cfg: "")
    monkeypatch.setattr(studio3d.requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no token: no call")))
    p = {"thing": "Boeing 737", "noun": "aircraft", "words": ["boeing 737"], "explode": False}
    assert studio3d.sketchfab({}, p, "x", print) is None
    assert "by-nc" not in studio3d.OK_LICENSES and "by-nd" not in studio3d.OK_LICENSES


def test_the_rung_hands_a_render_to_the_frame_check(monkeypatch, tmp_path):
    clip = tmp_path / "r.mp4"; clip.write_bytes(b"x")
    monkeypatch.setattr(studio3d, "clip", lambda *a, **k: (clip, "3D studio: Volvo XC60 (library)"))
    monkeypatch.setattr(qc_repair, "clip_ok", lambda cfg, c, w, label="": (True, ""))
    ctx = {"cfg": {}, "vo": "my Volvo XC60", "work": tmp_path, "key": "k", "W": 1920, "H": 1080, "need": 6.0, "log": print}
    c, img, how = qc_repair._rung_studio3d(ctx)
    assert c == clip and "Volvo" in how


def test_written_models_may_only_use_blender_building_blocks():
    ok = "import bpy, bmesh, math\nfrom mathutils import Vector\nbpy.ops.mesh.primitive_cube_add(size=2)\n"
    assert studio3d.CODE_BAD.search(ok) is None
    for bad in ("import os\n", "import subprocess\n", "x = open('a')\n", "__import__('os')\n", "bpy.ops.wm.save_as_mainfile(filepath='x')\n",
                "from pathlib import Path\n", "exec('1')\n"):
        assert studio3d.CODE_BAD.search(ok + bad), bad


def test_generated_models_come_after_the_real_ones_and_parts_lines_prefer_the_written_model(monkeypatch):
    seen = []
    for n in ("library", "polyhaven", "sketchfab", "photo3d", "coded"):
        monkeypatch.setattr(studio3d, n, (lambda name: lambda *a, **k: seen.append(name))(n))
    studio3d.find({}, {"thing": "x", "noun": "x", "words": [], "explode": False}, "", print)
    assert seen == ["library", "polyhaven", "sketchfab", "photo3d", "coded"]
    seen.clear()
    studio3d.find({}, {"thing": "x", "noun": "x", "words": [], "explode": True}, "", print)
    assert seen == ["library", "polyhaven", "sketchfab", "coded", "photo3d"]   # a photo shape is one shell: nothing to explode


def test_a_generated_model_is_tagged_generated():
    assert studio3d.tag("3D studio: Boeing 737 (photo -> Hunyuan3D-2 (AI product shot))") == "GENERATED · 3D"
    assert studio3d.tag("3D studio: phone (model written in code, exploded)") == "GENERATED · 3D"
    assert studio3d.tag("3D studio: Volvo XC60 (library (volvo_xc60))") == "3D RENDER"


def test_written_models_need_claude_unless_switched_on(monkeypatch, tmp_path):
    """Nov 14: DeepSeek-written airliners (fuselage on end) passed the local check: "auto" writes models only with Claude."""
    monkeypatch.setattr(studio3d, "blender_exe", lambda cfg: (_ for _ in ()).throw(AssertionError("should not start")))
    p = {"thing": "x", "noun": "x", "words": [], "explode": False}
    assert studio3d.coded({"studio3d_models_dir": str(tmp_path)}, p, "", print) is None
    assert studio3d.coded({"studio3d_models_dir": str(tmp_path), "coded_model_on": False, "anthropic_api_key": "k"}, p, "", print) is None
