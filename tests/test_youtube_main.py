"""owner, 2026-11-12: every successfully finished video goes to the main channel, Private; its Short links to it."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from studio import youtube_main as ym


def job(tmp_path, status="finished", long=True, short=True, title="Mr. Clinton Shows Bermuda"):
    d = tmp_path / "20261112_170000"; (d / "output").mkdir(parents=True)
    s = dict(status=status, title=title)
    for k, on in (("output", long), ("short_output", short)):
        if on:
            f = d / "output" / f"{k}.mp4"; f.write_bytes(b"x"); s[k] = str(f)
    (d / "state.json").write_text(json.dumps(s))
    return d


@pytest.fixture
def pub(monkeypatch):
    calls, records = [], []
    def http(cfg, path, data=None, form=None, timeout=60):
        calls.append((path, data, form))
        if path == "/api/published": return records
        if path.startswith("/api/defaults/"):
            kind = path.split("/")[4]
            return {"data": dict(kind=kind, title="very long file name", visibility="Public", thumbnail="", link_target="")}
        if path == "/api/thumbnail": return {"thumbnail": "t.jpg"}
        if path == "/publish/pair": return {"long_task": "L", "short_task": "S"}
        if path == "/publish": return {"task_id": "S"}
        if path.startswith("/publish/status/"):
            return {"status": "complete", "result": {"url": "u", "related_video_id": "LONGID"}}
        raise AssertionError(path)
    monkeypatch.setattr(ym, "_http", http)
    monkeypatch.setattr(ym.time, "sleep", lambda s: None)
    import studio.media as media
    monkeypatch.setattr(media, "dur", lambda p: 120.0)
    return calls, records


def test_a_finished_job_goes_up_private_as_a_linked_pair(tmp_path, pub):
    calls, _ = pub
    assert ym.process({}, job(tmp_path), log=None)
    pair = [c for c in calls if c[0] == "/publish/pair"]
    assert len(pair) == 1
    body = pair[0][1]
    assert body["long"]["visibility"] == body["short"]["visibility"] == "Private"   # always a private save
    assert body["long"]["channel"] == body["short"]["channel"] == "tariqhasnain"
    assert body["long"]["title"] == "Mr. Clinton Shows Bermuda"                       # the job's title, not the file name
    assert body["short"]["title"].endswith("#shorts")
    assert body["long"]["thumbnail"] == body["short"]["thumbnail"] == "t.jpg"        # a thumbnail for the Short too


def test_only_successful_new_videos_are_saved(tmp_path, pub):
    calls, records = pub
    assert ym.process({}, job(tmp_path, status="failed"), log=None) and not any(c[0].startswith("/publish") for c in calls)
    assert ym.process({}, job(tmp_path / "b", long=False), log=None) and not any(c[0].startswith("/publish") for c in calls)
    d = job(tmp_path / "c")
    records += [dict(channel="tariqhasnain", job=d.name, kind="long", video_id="LONGID", url="https://youtu.be/LONGID"),
                dict(channel="tariqhasnain", job=d.name, kind="short", video_id="S1", url="x", related_video_id="LONGID")]
    assert ym.process({}, d, log=None) and not any(c[0].startswith("/publish") for c in calls)   # already there: no copy


def test_a_missing_short_goes_up_linked_to_the_saved_video(tmp_path, pub):
    calls, records = pub
    d = job(tmp_path)
    records.append(dict(channel="tariqhasnain", job=d.name, kind="long", video_id="LONGID", url="https://www.youtube.com/watch?v=LONGID"))
    assert ym.process({}, d, log=None)
    one = [c for c in calls if c[0] == "/publish"]
    assert len(one) == 1 and one[0][1]["kind"] == "short" and one[0][1]["link_target"] == "LONGID"


def test_a_busy_publisher_is_retried_not_failed(tmp_path, pub, monkeypatch):
    calls, _ = pub
    real = ym._http
    def busy(cfg, path, **kw):
        if path == "/publish/pair": raise RuntimeError("Another publish is in progress.")
        return real(cfg, path, **kw)
    monkeypatch.setattr(ym, "_http", busy)
    assert ym.process({}, job(tmp_path), log=None) is False


def test_an_interview_job_brings_its_own_title_chapters_credits_and_ai_flag(tmp_path, monkeypatch):
    import studio.media as media
    monkeypatch.setattr(media, "dur", lambda p: 100.0)
    d = tmp_path / "j"; (d / "iedit").mkdir(parents=True); (d / "iwork" / "imagegen").mkdir(parents=True)
    parts = [dict(file="a.mp4", m={"type": "bite"}, chapter=c, credits=[[0, 1, cr]]) for c, cr in
             (("", "Pexels: A"), ("One", "Pexels: B"), ("Two", "Pexels: A"), ("Three", ""))]
    (d / "iedit" / "long.json").write_text(json.dumps(dict(title="A Real Title", parts=parts)))
    m = ym.manifest_meta(d, "long")
    assert m["title"] == "A Real Title" and [c["seconds"] for c in m["chapters"]] == [0, 100, 200, 300]
    assert m["chapters"][0]["title"] == "Coming up" and "Pexels: A\nPexels: B" in m["description"]
    assert m["altered_content"] is False
    (d / "iwork" / "imagegen" / "x.png").write_bytes(b"x")                 # the job generated pictures: disclose
    m = ym.manifest_meta(d, "long")
    assert m["altered_content"] is True and "AI-generated" in m["description"]


def test_a_short_whose_link_failed_gets_only_its_link_retried(tmp_path, pub):
    """YouTube offers a new long video as Related video only some time after its upload: the retry reopens the Short and
    sets the link, it never uploads anything again."""
    calls, records = pub
    d = job(tmp_path)
    records += [dict(channel="tariqhasnain", job=d.name, kind="long", video_id="LONGID", url="u"),
                dict(channel="tariqhasnain", job=d.name, kind="short", video_id="S1", url="x", related_video_id=None,
                     metadata=dict(kind="short", title="t #shorts", confirm_replace=True))]
    assert ym.process({}, d, log=None)                                      # the fake publisher reports the link set
    pubs = [c for c in calls if c[0] in ("/publish", "/publish/pair")]
    assert len(pubs) == 1 and pubs[0][1]["mode"] == "resume" and pubs[0][1]["link_target"] == "LONGID"
    assert "confirm_replace" not in pubs[0][1]
    records[1]["related_video_id"] = "LONGID"; calls.clear()
    assert ym.process({}, d, log=None) and not any(c[0].startswith("/publish") for c in calls)   # linked: nothing to do
