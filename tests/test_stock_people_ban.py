"""The stock people-ban follows the subject (CB-004). A person word in the scene description or the
library tags is incidental when the slot is a thing and the vision check's kind_shown is that thing."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from studio import footage  # noqa: E402

CFG = {
    "vet_provider": "local",
    "clip_check_v2": True,
    "clip_title_check": False,
    "clip_relate_check": False,
    "stock_ban_people": True,
    "stock_fail_closed": True,
    "story_subject_guard": False,
}

_OK = {
    "main_visible": "yes",
    "place_seen": "none",
    "mood_clash": False,
    "unrelated": False,
    "stand_in": False,
    "overlay_text": False,
    "same_scene": True,
    "looks_ok": True,
    "platform": False,
    "brands": [],
}


def _sheet(tmp_path):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (64, 64), "gray")
    d = ImageDraw.Draw(img)
    for x in range(0, 64, 4):
        d.line([(x, 0), (x, 63)], fill="white" if x % 8 else "black")
    sheet = tmp_path / "s.jpg"
    img.save(sheet)
    return sheet


def _check(monkeypatch, tmp_path, vision, desc, mainv, query="q", narration=""):
    monkeypatch.setattr(footage, "_vision", lambda cfg, img, prompt: json.dumps({**_OK, **vision}))
    c = {"id": "c1", "desc": desc, "query": query}
    return footage.verify(CFG, c, _sheet(tmp_path), lambda m: None, narration=narration, mainv=mainv)


def test_kite_with_a_man_in_the_description_passes(monkeypatch, tmp_path):
    ok, why, _ = _check(
        monkeypatch, tmp_path,
        {"seen": "a man flying a kite", "kind_shown": "kite"},
        "man flying a kite, person, outdoor",
        {"main": "a kite", "kind": "kite"},
        query="kite",
    )
    assert ok, why


def test_server_rack_with_a_worker_in_frame_passes(monkeypatch, tmp_path):
    ok, why, _ = _check(
        monkeypatch, tmp_path,
        {"seen": "a technician in frame, a hand on the server rack, a crowd far behind", "kind_shown": "server rack"},
        "worker, human, crowd, server rack, data center",
        {"main": "a server rack", "kind": "rack"},
        query="server rack",
    )
    assert ok, why


def test_named_person_stand_in_still_fails(monkeypatch, tmp_path):
    ok, why, _ = _check(
        monkeypatch, tmp_path,
        {"seen": "one man in a suit at a lectern", "kind_shown": "lectern", "stand_in": True},
        "politician speaking",
        {"main": "a lectern", "kind": "lectern"},
        query="lectern",
        narration="Prime Minister Hale announced the rule.",
    )
    assert not ok and "real person" in why


def test_person_as_the_clip_main_subject_fails(monkeypatch, tmp_path):
    ok, why, _ = _check(
        monkeypatch, tmp_path,
        {"seen": "a kite on the grass", "kind_shown": "man", "stand_in": False},
        "kite",
        {"main": "a kite", "kind": "kite"},
        query="kite",
    )
    assert not ok and "people" in why


def test_slot_about_a_person_is_banned(monkeypatch, tmp_path):
    ok, why, _ = _check(
        monkeypatch, tmp_path,
        {"seen": "the capitol building", "kind_shown": "building", "stand_in": False},
        "capitol",
        {"main": "a politician", "kind": "person"},
        query="politician",
    )
    assert not ok and "people" in why


def test_vision_unavailable_still_fails_closed(monkeypatch, tmp_path):
    def boom(cfg, img, prompt):
        raise RuntimeError("ollama down")
    monkeypatch.setattr(footage, "_vision", boom)
    c = {"id": "c1", "desc": "server rack", "query": "servers"}
    ok, why, _ = footage.verify(CFG, c, _sheet(tmp_path), lambda m: None, mainv={"main": "a server rack", "kind": "rack"})
    assert not ok and "failing closed" in why
