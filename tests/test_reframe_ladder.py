"""owner, 2026-11-17: Short source clips aimed at their subject (or shown whole), speaker insets that show a face or nothing,
a fresh AI picture before any drawing or card, and the editor's ladder panel (what failed and why, what fired)."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
cv2 = pytest.importorskip("cv2")
from studio import reframe, edit  # noqa: E402


def _clip(path, draw, n=24, size=(640, 360)):
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 12, size)
    for i in range(n):
        f = np.zeros((size[1], size[0], 3), np.uint8); draw(f, i); w.write(f)
    w.release()


def test_action_at_both_sides_of_a_dark_frame_is_shown_whole(tmp_path):
    # hands at the left and right edges, black in the middle (the StarTalk two-shot): never a black crop with half a hand
    p = tmp_path / "two.mp4"
    _clip(p, lambda f, i: (cv2.rectangle(f, (0, 80), (110, 300), (200, 180, 160), -1), cv2.rectangle(f, (530, 60), (640, 360), (90, 90, 120), -1)))
    r = reframe.analyse(p, 1080, 1920)
    assert r["fit"] == "frame" and r["two"] == [0.0, 1.0]  # ... and the two subjects are known, for the wide -> each-in-turn cut


def test_two_subjects_get_the_wide_shot_then_each_one_centred(tmp_path):
    # owner, 2026-11-17: never one crop between two people that cuts both; whole frame, then each person in turn
    pub = tmp_path / "public"; (pub / "media").mkdir(parents=True)
    p = pub / "media" / "two.mp4"
    _clip(p, lambda f, i: (cv2.rectangle(f, (0, 80), (110, 300), (200, 180, 160), -1), cv2.rectangle(f, (530, 60), (640, 360), (90, 90, 120), -1)))
    data = {"w": 1080, "h": 1920, "beats": [
        {"id": "b0", "start": 0, "dur": 6, "bgs": [{"type": "video", "src": "media/two.mp4", "from": 0}]},
        {"id": "b1", "start": 6, "dur": 3, "bgs": [{"type": "video", "src": "media/two.mp4", "from": 0}]}]}
    out = reframe.apply(data, pub, log=lambda *_: None)
    g = out["beats"][0]["bgs"][0]
    assert g["seq"] == [{"fit": "frame"}, {"pos": 0.0}, {"pos": 1.0}] and "fit" not in g
    assert out["beats"][1]["bgs"][0].get("fit") == "frame" and "seq" not in out["beats"][1]["bgs"][0]   # 3 s: too short, whole


def test_a_crop_never_straddles_two_subjects_that_dont_fit_it(tmp_path):
    # one wide blob: no pair of subjects to cut between, so no sequence
    assert reframe._groups([(0.1, 0.6)], 0.316) is None
    assert reframe._groups([(0.0, 0.5), (0.8, 1.0)], 0.316) is None          # the left one is wider than the crop
    assert reframe._groups([(0.3, 0.4), (0.45, 0.55)], 0.316) is None        # too close together: one crop holds both


def test_flow_is_in_the_ladder_but_always_skipped():
    from studio import imagegen, config
    assert "flow" in config.DEFAULTS["imagegen_providers"] and config.DEFAULTS["flow_on"] is False
    with pytest.raises(imagegen.Skip, match="Google flags"):
        imagegen._session_flow({"flow_on": False}, {"vo": "x"}, "9:16", "x.png", 10, lambda *_: None, None)


def test_a_subject_off_centre_moves_the_crop_onto_it(tmp_path):
    p = tmp_path / "right.mp4"
    _clip(p, lambda f, i: cv2.rectangle(f, (470, 40), (600, 340), (180, 170, 160), -1))
    r = reframe.analyse(p, 1080, 1920)
    assert r.get("pos", 0) > 0.8
    assert reframe.analyse(p, 1920, 1080) == {}            # a landscape frame isn't cropped: nothing to aim


def test_an_inset_without_a_face_is_dropped(tmp_path):
    pub = tmp_path / "public"; (pub / "media").mkdir(parents=True)
    cv2.imwrite(str(pub / "media" / "snap.png"), np.zeros((360, 640, 3), np.uint8))
    data = {"w": 1080, "h": 1920, "beats": [{"id": "b0", "start": 0, "dur": 4, "bgs": [
        {"type": "image", "src": "media/x.jpg", "from": 0, "inset": {"src": "media/snap.png"}}]}]}
    out = reframe.apply(data, pub, log=lambda *_: None)
    assert "inset" not in out["beats"][0]["bgs"][0]


def test_the_ladder_panel_lists_what_failed_and_what_fired(tmp_path):
    job = tmp_path / "20261117_000000"; fb = job / "short" / "fallback"; fb.mkdir(parents=True)
    (job / "state.json").write_text("{}", encoding="utf-8")
    (fb / "chains.json").write_text(json.dumps({"b1": {"chain": [
        "source clip, full frame (video at 20.00s of the source, SOURCE)",
        "stock -> AI image (cloudflare, from the narration)  (stock: no coherent stock; WanGP skipped: names a real person)",
        "stock -> graphics -> headline  (stock: no coherent stock; AI image: cloudflare: failed (HTTP 400); hf: skipped (rate limited); "
        "graphics: crash (\x1b[31m at ChildProcess.emit\x1b[39m))"]}}), encoding="utf-8")
    L = edit.ladder(job, "b1", "short")
    assert L[0] == {"slot": "#1", "used": "source clip, full frame", "failed": []}
    assert L[1]["used"] == "AI image (cloudflare, from the narration)" and ["stock", "no coherent stock"] in L[1]["failed"]
    assert L[2]["used"] == "headline"
    reasons = dict((r, w) for r, w in L[2]["failed"])
    assert reasons["AI image"].startswith("cloudflare: failed") and "hf" in reasons and "\x1b" not in reasons["graphics"]


def test_people_are_photoreal_unless_the_sketch_is_asked_for(monkeypatch):
    from studio import people, config
    monkeypatch.setattr(config, "load", lambda: {"people_style": "photo"})
    assert "photorealistic" in people.rule(["Michio Kaku"]) and "sketch" not in people.rule(["Michio Kaku"])
    monkeypatch.setattr(config, "load", lambda: {"people_style": "sketch"})
    assert "courtroom sketch" in people.rule(["Michio Kaku"])


def test_a_clip_of_hands_with_no_face_is_set_aside_for_a_picture(tmp_path):
    # owner, 2026-11-17: hands and torsos with no face (a tight two-shot) -> the beat gets a picture; a clip with no people is kept
    hands = tmp_path / "hands.mp4"
    _clip(hands, lambda f, i: (cv2.rectangle(f, (20, 150), (140, 260), (120, 150, 210), -1), cv2.rectangle(f, (500, 120), (620, 300), (110, 140, 200), -1)))
    assert "no face" in reframe.faceless_people(hands)
    machine = tmp_path / "machine.mp4"
    _clip(machine, lambda f, i: cv2.rectangle(f, (200, 80), (440, 300), (160, 160, 160), -1))
    assert reframe.faceless_people(machine) == ""
    import inspect
    from studio import short, config
    src = inspect.getsource(short)
    assert "reframe.faceless_people" in src and 'f"source clip: {faceless' in src     # the ladder panel says why
    assert config.DEFAULTS["skip_faceless_source"] is True
