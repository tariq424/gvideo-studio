"""Each explainer rung logs its own name. A cabin prompt cannot pass an exterior cartoon."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from studio import edit, illustrate  # noqa: E402
from studio.illustrate import wangp_lock  # noqa: E402
from studio.rung_walk import (  # noqa: E402
    climb, is_flight_path, picture_matches, production_attempt, walk_names,
)
from studio.rungs import SHARED_RUNG_NAMES  # noqa: E402

CABIN = ("A passenger cabin interior during turbulence, overhead bins and seat backs visible, "
         "warm cabin lighting, a few blurred passengers bracing against the seat in front, shallow depth of field")
SECTION = "The Flydubai jet dropped in a steep descent toward Tabuk."
DESCENT = "An airliner in a steep descent, aerial view, desert below the wing"
SKY = {"type": "graphic", "g": {"kind": "illustration", "scene": "sky", "aircraft": "airliner", "attitude": "dive"},
       "rung": "illustration", "tag": "ILLUSTRATION"}


def test_cabin_prompt_rejects_the_exterior_cartoon_the_section_would_draw():
    built = illustrate.options({"id": "s0_7", "vo": SECTION, "still_prompt": CABIN}, [])
    assert built and built[0]["g"]["scene"] == "sky"
    ok, why = picture_matches(CABIN, built[0])
    assert ok is False and "interior prompt" in why and "exterior" in why
    ok, why = picture_matches(DESCENT, built[0])
    assert ok is True and why == ""
    # the section narration is what locked WanGP and forced the sky cartoon; the slot prompt does not
    assert wangp_lock({"vo": SECTION, "still_prompt": CABIN}, []) == "aircraft beat"
    assert wangp_lock({"vo": CABIN, "still_prompt": CABIN}, []) == ""


def test_motion_and_threejs_fail_when_the_picture_is_not_the_cabin():
    assert picture_matches(CABIN, {"how": "template:route (airliner)", "template": "route"})[0] is False
    assert picture_matches(CABIN, {"how": "diorama of a house and a car"})[0] is False
    assert picture_matches(CABIN, {"template": "T_DataWall", "how": "T_DataWall"})[0] is False
    assert picture_matches(DESCENT, {"template": "T_DataWall", "how": "T_DataWall"})[0] is True
    assert picture_matches(CABIN, {"how": "a passenger cabin, seats and an aisle"})[0] is True


def test_flight_path_slot_calls_unreal_before_motion(tmp_path):
    assert is_flight_path(DESCENT, SECTION) is True
    assert is_flight_path(CABIN, SECTION) is False
    called, lines = [], []
    clip = tmp_path / "wall.mp4"
    clip.write_bytes(b"not a real mp4")
    visual = {"id": "s0_3", "type": "motion", "prompt": DESCENT}

    def render_unreal(ctx):
        called.append("unreal")
        return clip, "T_DataWall (9 published points)"

    def stub(name):
        def fn(ctx):
            called.append(name)
            return "failed", "stub", None
        return fn

    ctx = {
        "cfg": {"unreal_on": True}, "job": tmp_path, "visual": visual, "prompt": DESCENT,
        "section_vo": SECTION, "slot_id": "s0_3", "work": tmp_path / "work",
        "render_unreal": render_unreal, "ents": [],
        "handlers": {n: stub(n) for n in walk_names() if n not in ("stock", "unreal", "illustration", "card")},
    }
    fired = climb("s0_3", DESCENT, SECTION, lambda name: production_attempt(name, ctx), lines.append)
    assert fired == "unreal"
    assert called[0] == "unreal" or called.index("unreal") < (called.index("motion") if "motion" in called else 99)
    assert "motion" not in called and "threejs" not in called
    text = "\n".join(lines)
    for name in SHARED_RUNG_NAMES:
        assert f"s0_3: {name}:" in text
    assert "s0_3: illustration:" in text
    assert "unreal: fired: T_DataWall" in text
    assert "motion: skipped: unreal already fired" in text
    assert visual["tag"] == "UNREAL" and "g" not in visual


def test_cabin_skips_unreal_and_does_not_paint_the_cartoon(tmp_path):
    lines = []
    visual = {"id": "s0_7", "type": "still", "prompt": CABIN}

    def render_unreal(ctx):
        raise AssertionError("Unreal ran for a cabin prompt")

    def paint(ctx, item):
        raise AssertionError("exterior cartoon was rendered")

    def stub(name):
        def fn(ctx):
            return "failed", "stub", None
        return fn

    ctx = {
        "cfg": {"unreal_on": True}, "job": tmp_path, "visual": visual, "prompt": CABIN,
        "section_vo": SECTION, "slot_id": "s0_7", "work": tmp_path / "work",
        "illustration": SKY, "render_unreal": render_unreal, "render_illustration": paint, "ents": [],
        "handlers": {n: stub(n) for n in walk_names() if n not in ("stock", "unreal", "illustration", "card")},
    }
    fired = climb("s0_7", CABIN, SECTION, lambda name: production_attempt(name, ctx), lines.append)
    assert fired == "card"
    text = "\n".join(lines)
    assert "unreal: skipped: not a route or flight-path beat" in text
    assert "illustration: failed: interior prompt, exterior cartoon (scene sky)" in text
    assert "card: fired: headline card" in text
    assert "stock: failed: nothing suitable" in text
    for name in list(SHARED_RUNG_NAMES)[:14]:
        assert f"s0_7: {name}:" in text
    assert visual["g"]["kind"] == "headline"


def test_a_lying_motion_rung_still_fails_the_cabin():
    lines = []

    def attempt(name):
        if name == "motion":
            return "fired", "template:route (airliner)", {"how": "template:route (airliner)", "template": "route"}
        if name == "threejs":
            return "fired", "diorama of a house", {"how": "diorama of a house"}
        if name == "card":
            return "fired", "headline card", {"how": "headline card"}
        return "skipped", "stub", None

    fired = climb("s0_7", CABIN, SECTION, attempt, lines.append)
    text = "\n".join(lines)
    assert fired == "card"
    assert "motion: failed: interior prompt, exterior cartoon" in text
    assert "threejs: failed: picture was not checked against the subject" in text


def test_the_panel_names_each_rung_and_does_not_invent_a_motion_clip(tmp_path):
    (tmp_path / "log.txt").write_text(
        "[10:31:46]   s0_7: stock: failed: nothing suitable\n"
        "[10:36:48]   s0_7: illustration: failed: interior prompt, exterior cartoon (scene sky)\n"
        "[10:36:48]   s0_7: card: fired: headline card\n",
        encoding="utf-8")
    row = edit.ladder(tmp_path, "s0_7", "explainer", visual={"type": "motion", "prompt": CABIN})[0]
    assert row["failed"][0] == ["stock", "failed: nothing suitable"]
    assert row["failed"][1][0] == "illustration" and "exterior cartoon" in row["failed"][1][1]
    assert row["used"] == "card: headline card"
    # the old line hid the rung name and the panel then invented "Motion clip"
    (tmp_path / "log.txt").write_text(
        "[10:31:46]   s0_7: nothing suitable\n"
        "[10:36:48]   s0_7: illustration (WanGP skipped: aircraft beat)\n",
        encoding="utf-8")
    old = edit.ladder(tmp_path, "s0_7", "explainer", visual={"type": "motion"})[0]
    assert old["failed"][0][0] == "" and "nothing suitable" in old["failed"][0][1]
    assert old["used"] == "Motion clip"
