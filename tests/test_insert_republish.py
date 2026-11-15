"""owner, 2026-11-14: "If I save the edited video, it replaces its predecessor in both publisher and newsroom" and "I can
add a beat with my own video or still seamlessly anywhere in the timeline or start/end"."""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from studio import insert, republish  # noqa: E402


def test_chapters_after_an_insert_move_and_zero_stays():
    d = "Intro\n\n0:00 Start\n1:05 Middle\n10:30 End\n"
    out = insert.shift_chapters(d, 60, 5)
    assert "0:00 Start" in out and "1:10 Middle" in out and "10:35 End" in out
    ch = republish.shift_chapters_list([{"seconds": 0, "title": "a"}, {"seconds": 65, "title": "b"}],
                                       [{"t": 0.0, "secs": 3.0, "time": 1}, {"t": 60.0, "secs": 5.0, "time": 2}])
    assert [c["seconds"] for c in ch] == [0, 73]


def test_a_picture_of_another_shape_sits_on_a_blur_not_cropped():
    assert "gblur" in insert.fit_chain(1920, 1080, 1080, 1920)
    assert "gblur" not in insert.fit_chain(1920, 1080, 1920, 1080)
    assert insert.kind_of("a.JPG") == "still" and insert.kind_of("a.mov") == "video" and insert.kind_of("a.txt") is None


def _job(tmp_path, kinds=("long", "short")):
    j = tmp_path / "20261114_000000"; (j / "output").mkdir(parents=True)
    st = {"status": "finished", "republish_req": time.time()}
    for k in kinds:
        f = j / "output" / (f"x_short.mp4" if k == "short" else "x_explainer_1080p.mp4"); f.write_bytes(b"v" * (100 if k == "long" else 50))
        st["output" if k == "long" else "short_output"] = str(f)
    (j / "state.json").write_text(json.dumps(st), encoding="utf-8")
    return j


def _rec(j, ch, kind, vid, ts, desc=""):
    return {"job": j.name, "channel": ch, "kind": kind, "video_id": vid, "url": f"https://www.youtube.com/watch?v={vid}",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts)),
            "metadata": {"title": "t", "description": desc, "visibility": "Private", "channel": ch, "job": j.name, "kind": kind}}


def test_only_videos_changed_after_their_upload_are_replaced_and_the_short_follows(tmp_path):
    j = _job(tmp_path)
    old = time.time() - 3600
    pub = [_rec(j, "tariqhasnain", "long", "L1", old), _rec(j, "tariqhasnain", "short", "S1", time.time() + 60, "Full breakdown: https://www.youtube.com/watch?v=L1"),
           _rec(j, "youtour", "short", "S9", time.time() + 60)]
    todo, _ = republish.plan(j, pub)
    assert todo == {"tariqhasnain": ["long", "short"]}   # the Short follows its explainer; youtour's Short is unchanged


def test_the_short_gets_the_new_explainer_link(tmp_path):
    j = _job(tmp_path)
    old = _rec(j, "c", "short", "S1", 0, "Hook\nFull breakdown: https://www.youtube.com/watch?v=L1")
    b = republish._body(j, old, "short", {"url": "https://www.youtube.com/watch?v=L2", "video_id": "L2", "old_url": "https://www.youtube.com/watch?v=L1"})
    assert b["mode"] == "replace" and b["confirm_replace"] and b["link_target"] == "L2"
    assert "watch?v=L2" in b["description"] and "watch?v=L1" not in b["description"]


def test_nothing_is_uploaded_while_the_publisher_still_sees_the_old_file(tmp_path, monkeypatch):
    j = _job(tmp_path)
    pub = [_rec(j, "c", "long", "L1", time.time() - 3600)]
    calls = []
    def http(cfg, path, data=None, **k):
        calls.append(path)
        if path == "/api/published": return pub
        if path.startswith("/api/defaults/"): return {"media": {"filename": "x_explainer_1080p.mp4", "size": 7}}   # stale
        raise AssertionError("must not publish")
    monkeypatch.setattr(republish.ym, "_http", http)
    monkeypatch.setattr(republish, "_newsroom", lambda cfg, body, timeout=120: {"items": []})
    assert republish.process({}, j) is False
    assert "/publish" not in calls


def test_an_older_replace_waiting_for_its_delete_stops_without_looping(tmp_path, monkeypatch):
    j = _job(tmp_path, ("long",))
    pub = [_rec(j, "c", "long", "L1", time.time() - 3600)]
    def http(cfg, path, data=None, **k):
        if path == "/api/published": return pub
        if path.startswith("/api/defaults/"): return {"media": {"size": 100}}
        if path == "/publish": raise RuntimeError("An older upload from the last replace is still on YouTube: delete it (or keep it) first.")
    monkeypatch.setattr(republish.ym, "_http", http)
    monkeypatch.setattr(republish, "_newsroom", lambda cfg, body, timeout=120: {"items": []})
    assert republish.process({}, j) is True
    s = json.loads((j / "state.json").read_text(encoding="utf-8"))
    assert not s.get("republish_req") and "delete it" in s["republish_blocked"]


def test_old_uploads_are_never_deleted_by_the_worker():
    import inspect
    src = inspect.getsource(republish)
    assert "/delete" not in src.replace("delete it", "")
