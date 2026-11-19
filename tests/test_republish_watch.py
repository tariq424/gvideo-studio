"""owner, 2026-11-19: "when a video is updated, the newsroom and the scheduler get the modified video" - also when the
change was made outside the editor's Save (a patch, a splice)."""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from studio import republish, youtube_main as ym  # noqa: E402
from test_insert_republish import _job, _rec  # noqa: E402


def _plain(j, age=3600):
    st = json.loads((j / "state.json").read_text(encoding="utf-8")); st.pop("republish_req", None)
    (j / "state.json").write_text(json.dumps(st), encoding="utf-8")
    for f in (j / "output").iterdir(): os.utime(f, (time.time() - age, time.time() - age))
    return j


def _run(tmp_path, monkeypatch, pub, cfg=None, editing=None):
    queued = []
    monkeypatch.setattr(ym, "_http", lambda cfg, path, **k: pub)
    monkeypatch.setattr(republish, "queue", lambda d, log=None, delay=0: queued.append(Path(d).name))
    cfg = {"newsroom_staging": str(tmp_path / "staging"), **(cfg or {})}
    return republish.watch_once(cfg, tmp_path, editing=editing, log=None), queued


def test_a_video_changed_after_its_upload_is_queued_once(tmp_path, monkeypatch):
    j = _plain(_job(tmp_path))
    pub = [_rec(j, "tariqhasnain", "long", "L1", time.time() - 7200)]
    out, q = _run(tmp_path, monkeypatch, pub)
    assert out == [j.name] and q == [j.name]
    s = json.loads((j / "state.json").read_text(encoding="utf-8"))
    assert s["republish_req"] and s["republish_auto"]
    s.pop("republish_req"); (j / "state.json").write_text(json.dumps(s), encoding="utf-8")
    assert _run(tmp_path, monkeypatch, pub)[0] == []   # same files: not again (a blocked or given-up replace doesn't loop)


def test_an_unchanged_video_is_left_alone(tmp_path, monkeypatch):
    j = _plain(_job(tmp_path))
    pub = [_rec(j, "c", "long", "L1", time.time()), _rec(j, "c", "short", "S1", time.time(), "Full breakdown: https://www.youtube.com/watch?v=L1")]
    assert _run(tmp_path, monkeypatch, pub)[0] == []


def test_newsroom_staged_copy_that_differs_is_refreshed_even_before_any_upload(tmp_path, monkeypatch):
    j = _plain(_job(tmp_path))
    stg = tmp_path / "staging" / j.name / "output"; stg.mkdir(parents=True)
    (stg / "x_explainer_1080p.mp4").write_bytes(b"old")
    assert _run(tmp_path, monkeypatch, [])[0] == [j.name]
    (stg / "x_explainer_1080p.mp4").write_bytes(b"v" * 100)


def test_a_file_still_being_written_or_held_by_the_editor_waits(tmp_path, monkeypatch):
    j = _plain(_job(tmp_path), age=10)
    pub = [_rec(j, "c", "long", "L1", time.time() - 7200)]
    assert _run(tmp_path, monkeypatch, pub)[0] == []   # written seconds ago
    _plain(j)
    assert _run(tmp_path, monkeypatch, pub, editing=lambda: j.name)[0] == []   # the editor's own Save handles it
    assert _run(tmp_path, monkeypatch, pub, cfg={"republish_watch": False})[0] == []
    assert _run(tmp_path, monkeypatch, pub)[0] == [j.name]
