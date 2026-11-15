"""Finished videos go to Google Photos; the owner's own footage's day is indexed (owner, 2026-11-09)."""
import json
import os
import time
from pathlib import Path

from studio import cloud_backup as cb


def _job(tmp_path, **state):
    jd = tmp_path / "job"; (jd / "output").mkdir(parents=True)
    (jd / "state.json").write_text(json.dumps(dict(status="finished", **state)), encoding="utf-8")
    return jd


def test_finished_videos_are_pending_until_uploaded_at_that_size(tmp_path):
    jd = tmp_path / "job"
    long, short = jd / "output" / "x_documentary.mp4", jd / "output" / "x_short.mp4"
    _job(tmp_path, output=str(long), short_output=str(short))
    long.write_bytes(b"a" * 10); short.write_bytes(b"b" * 5)
    assert {f.name for f in cb.pending(jd)} == {"x_documentary.mp4", "x_short.mp4"}
    (jd / "output" / cb.LEDGER).write_text(json.dumps({"x_short.mp4": {"stamp": cb._stamp(short)}}), encoding="utf-8")
    assert [f.name for f in cb.pending(jd)] == ["x_documentary.mp4"]
    short.write_bytes(b"b" * 7)   # the editor re-assembled it: it goes up again
    assert {f.name for f in cb.pending(jd)} == {"x_documentary.mp4", "x_short.mp4"}


def test_unfinished_job_is_not_backed_up(tmp_path):
    jd = _job(tmp_path, output="nope.mp4"); (jd / "state.json").write_text(json.dumps({"status": "running"}), encoding="utf-8")
    assert cb.pending(jd) == []


def test_own_sources_are_the_interview_footage_and_extras(tmp_path):
    a, b = tmp_path / "a.MOV", tmp_path / "b.MOV"; a.write_bytes(b"1"); b.write_bytes(b"2")
    jd = _job(tmp_path, interview={"src": str(a), "extras": [str(b), str(tmp_path / "gone.MOV")]})
    assert cb.own_sources(jd) == [a, b]


def test_photos_day_and_place_match():
    assert cb.photos_day("2025-11-26") == ("November 26, 2025", "Nov 26, 2025")
    pin, place = (32.2975, -64.7851), {"city": "Hamilton", "country": "Bermuda"}
    assert cb.at_place({"place": "Hamilton, Bermuda"}, pin, place)
    assert cb.at_place({"lat": "32.33", "lon": "-64.75"}, pin, place)
    assert not cb.at_place({"place": "Nashua, New Hampshire"}, pin, place)
    assert not cb.at_place({"lat": "42.76", "lon": "-71.46", "place": ""}, pin, place)


def test_sweep_only_queues_jobs_finished_since_the_feature(tmp_path, monkeypatch):
    got = []
    monkeypatch.setattr(cb, "queue", lambda d, log=None, delay=0: got.append(Path(d).name))
    for name, age in (("old", cb.SINCE - 86400), ("new", time.time())):
        jd = tmp_path / name; (jd / "output").mkdir(parents=True)
        f = jd / "output" / "v.mp4"; f.write_bytes(b"x"); os.utime(f, (age, age))
        (jd / "state.json").write_text(json.dumps({"status": "finished", "output": str(f)}), encoding="utf-8")
    assert cb.sweep(tmp_path) == 1 and got == ["new"]
