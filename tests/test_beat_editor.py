"""Beat editor (owner, 2026-11-19): rungs, camera shots for a still, and never interfering with a video being made."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from studio import beat_editor, media  # noqa: E402


class _Job:
    def __init__(self, state): self.state = state


class _Studio:
    def __init__(self, cur=None): self.current = cur
    def job(self, jid): return _Job({"title": "Robots", "stage": "assemble"})


def test_nothing_running_means_no_wait():
    assert beat_editor.pipeline_busy(_Studio()) == ""


def test_a_running_job_makes_heavy_work_wait_with_a_reason():
    m = beat_editor.pipeline_busy(_Studio("20261119_061158"), "20261117_075804")
    assert "Robots" in m and "assemble" in m and "wait" in m.lower() and "the video you are editing" not in m
    assert "the video you are editing" in beat_editor.pipeline_busy(_Studio("x"), "x")


def test_rung_menu_offers_every_rung_and_your_file():
    ids = [r for r, _, _ in beat_editor.RUNGS]
    for r in ("stock", "photo", "imagegen", "storyboard", "animate", "wangp", "headline", "all", "own"):
        assert r in ids
    assert {"wangp", "animate", "all", "shots"} <= beat_editor.HEAVY and "stock" not in beat_editor.HEAVY
    shot_ids = [s for s, _ in beat_editor.SHOTS]
    for s in ("auto", "spin", "dolly", "pan-lr", "push", "pull", "none"):     # overhead, dolly, pan, zoom ...
        assert s in shot_ids
    assert set(beat_editor.ACTIONS) == {"photo", "imagegen", "wangp", "shots"}


@pytest.mark.parametrize("move", ["auto", "dolly", "none"])
def test_a_still_becomes_camera_shots_that_fill_the_beat(tmp_path, move):
    from PIL import Image
    pic = tmp_path / "still.jpg"
    Image.new("RGB", (1600, 900), (40, 90, 160)).save(pic)
    out = beat_editor._render_still_shots(pic, 9.6, move, tmp_path / f"shot_{move}.mp4", (640, 360))
    assert abs(media.dur(out) - 9.6) < 0.15
    assert not list(tmp_path.glob("shot_auto_m*.mp4"))        # mixed-shot pieces are cleaned up


def test_server_refuses_heavy_beat_work_while_a_video_is_being_made(monkeypatch):
    import server
    monkeypatch.setattr(server.studio, "current", "20261119_061158")
    monkeypatch.setattr(server.studio, "job", lambda jid: _Job({"title": "Robots", "stage": "assemble"}) if jid == "20261119_061158"
                        else type("J", (), {"dir": Path("."), "state": {}, "log": staticmethod(print)})())
    c = server.app.test_client()
    for action, body in (("preview", {"cands": ["c1"]}), ("splice", {"cands": ["c1"]}), ("shots", {"cid": "c1"}),
                         ("rung", {"rung": "wangp"})):
        r = c.post(f"/api/jobs/20261117_075804/beat/s0_5/{action}?space=long", json=body)
        assert r.status_code == 409 and r.get_json()["busy"] is True, action
    assert c.get("/beat/20261117_075804/s0_5").status_code == 200
