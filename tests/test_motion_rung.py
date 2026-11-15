"""owner, 2026-11-12: a drawn animation (map, route, stat, timeline, stick figures...) comes BEFORE any AI picture."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from studio import graphics, qc_repair  # noqa: E402


def test_motion_is_the_last_resort_before_the_card():
    """owner, 2026-11-12: "This should be the last resort before card" - after every picture rung, never ahead of one."""
    order = [r[0] for r in qc_repair.RUNGS]
    assert order.index("motion") == order.index("card") - 1
    assert all(order.index(r) < order.index("motion") for r in ("stock", "ai_image", "chatgpt_chain", "static_image"))
    import inspect
    from studio import interview
    src = inspect.getsource(interview.Grabber.media) if hasattr(interview, "Grabber") else inspect.getsource(interview)
    i_motion, i_card = src.index('("physics", "motion")'), src.rindex('("card",)')
    assert all(src.index(x) < i_motion for x in ('"cloud", "wangp", "static_image"', "self.sketch("))
    assert i_motion < i_card


def test_the_look_comes_from_settings(monkeypatch, tmp_path):
    seen = {}
    from studio import review
    monkeypatch.setattr(review, "_remotion", lambda sd, bg, *a, **k: seen.setdefault("bg", bg))
    monkeypatch.setattr(graphics, "_normalise", lambda raw, out: None)
    graphics.render_template({"motion_look": "whiteboard"}, tmp_path, "figure", {"pose": "shrug"}, 8, tmp_path / "o.mp4", print)
    assert seen["bg"]["g"]["d"]["look"] == "whiteboard"


def test_the_motion_rung_takes_only_what_a_drawing_explains(monkeypatch, tmp_path):
    seen = {}
    def classify(cfg, sec, log, ctx=""):
        return {"template": seen["t"], "data": {"text": "x"}, "why": "", "how": "model"}
    monkeypatch.setattr(graphics, "classify", classify)
    monkeypatch.setattr(graphics, "render_template", lambda *a, **k: (_ for _ in ()).throw(AssertionError("rendered")))
    for t in ("abstract", "quote"):   # text cards never replace a picture of what is said
        seen["t"] = t
        g = graphics.make({}, tmp_path, {"vo": "a line"}, f"k_{t}", print, allow=qc_repair.MOTION_TEMPLATES, claude=False)
        assert not g["ok"] and not g["hard"]
    seen["t"] = None                 # nothing fits: no Claude-written composition from this rung
    monkeypatch.setattr(graphics, "claude_generate", lambda *a, **k: (_ for _ in ()).throw(AssertionError("claude")))
    g = graphics.make({"graphics_claude": True}, tmp_path, {"vo": "a line"}, "k_none", print, allow=qc_repair.MOTION_TEMPLATES, claude=False)
    assert not g["ok"]


def test_new_templates_validate():
    assert graphics._valid("place", {"name": "Hamilton", "lat": 32.29, "lon": -64.78})
    assert not graphics._valid("place", {"name": "Nowhere", "lat": 120, "lon": 0})
    assert graphics._valid("figure", {"pose": "shrug", "caption": "He doesn't know yet"})
    assert not graphics._valid("figure", {"pose": "dance"})
    assert "place" in graphics.TEMPLATES and "figure" in graphics.TEMPLATES


def test_in_every_video_kind_drawings_come_after_the_pictures():
    """owner, 2026-11-12: "not just for interview videos ... for all videos, this before the card if all else failed"."""
    import inspect
    from studio import short, render
    s = inspect.getsource(short._v2_rungs)
    assert s.index("depth_free(") < s.index("physics.provide(") < s.index("graphics.make(") < s.index('chain.append("headline")')
    r = inspect.getsource(render._v2_after_wangp)   # explainers: reached only after the cloud and WanGP pictures failed
    assert r.index("graphics.make(") < r.index('"headline"', r.index("graphics.make("))


def test_interviews_get_the_same_picture_rungs_as_every_other_video():
    """owner, 2026-11-12: "for the interviews too, I want to keep the same rungs" - no reduced interview ladder."""
    import inspect
    from studio import interview
    src = inspect.getsource(interview)
    for r in ("meta_chain", "ai_image", "chatgpt_chain", "blender", "video", "cloud", "wangp", "static_image"):
        assert f'"{r}"' in src[src.index('rungs = tuple(('):src.index('if rungs is None else rungs)')]
    names = [r[0] for r in qc_repair.RUNGS]
    pictures = ("meta_chain", "ai_image", "chatgpt_chain", "blender", "video", "cloud", "wangp", "static_image")
    assert all(n in names for n in pictures)
    assert max(names.index(p) for p in pictures) < names.index("physics") < names.index("motion") < names.index("card")
    d = inspect.signature(qc_repair.ladder_clip).parameters["rungs"].default   # the fact / repair ladder: the same set
    assert all(p in d for p in pictures) and d[-2:] == ("physics", "motion")


def test_gemini_is_back_in_the_ai_image_rungs():
    import inspect
    from studio import interview
    assert "_cfg_no_google" not in inspect.getsource(qc_repair._rung_aiimage)
    assert "_cfg_no_google" not in inspect.getsource(interview.Ladder.sketch)


def test_the_short_route_map_waits_for_the_pictures():
    import inspect
    from studio import short
    s = inspect.getsource(short._v2_rungs)
    assert s.index("physics.provide(") < s.index('"rung": "map"') < s.index("graphics.make(")
