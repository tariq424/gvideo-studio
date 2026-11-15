"""Interview mode (owner, 2026-11-08): your own interview footage where the real conversation leads - a preview of later
moments, background lines, then your questions and the answers in order, commentary where the guest gets specific."""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from studio import config, interview, pipeline  # noqa: E402


def _segs(n, sec=10.0):
    return [{"i": i, "start": i * sec, "end": i * sec + sec - 1.0, "text": f"Sentence {i}.", "words": []} for i in range(n)]


def test_preview_then_title_then_background_then_the_conversation():
    segs = _segs(12)
    tri = {"preview": [[8, 8], [10, 10]], "background": ["Nea Dixon is a Bermudian artist.", "We met at Dockyard."],
           "drop": [[3, 3, "small talk"]], "facts": [{"after": 6, "note": 0, "text": "A fact."}], "chapters": [{"at": 0, "title": "Start"}]}
    its = interview.augment_items(tri, segs)
    assert [x.get("bite") for x in its[:2]] == [[8, 8], [10, 10]] and all(x.get("preview") for x in its[:2])
    assert its[1].get("title_after") and not its[0].get("title_after")   # the title card comes after the last teaser
    assert [x.get("narr") for x in its[2:4]] == tri["background"] and all(x.get("background") for x in its[2:4])
    convo = [i for x in its[4:] if "bite" in x for i in range(x["bite"][0], x["bite"][1] + 1)]
    assert convo == [0, 1, 2, 4, 5, 6, 7, 8, 9, 10, 11]   # the whole conversation in order; teasers are played again in place
    assert any("insert" in x for x in its[4:])


def test_no_preview_keeps_the_old_shape():
    its = interview.augment_items({"chapters": [{"at": 0, "title": "Start"}]}, _segs(4))
    assert "bite" in its[0] and not its[0].get("preview") and not any(x.get("background") for x in its)


class _LLM:
    plan = None
    def __init__(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def chat_json(self, system, user, max_tokens=0, check=None):
        _LLM.prompt = user
        if check: check(_LLM.plan)
        return _LLM.plan


def test_triage_asks_for_questions_kept_and_checks_the_preview(tmp_path, monkeypatch):
    import studio.llm
    monkeypatch.setattr(studio.llm, "LLM", _LLM)
    segs = _segs(10)
    _LLM.plan = {"chapters": [{"at": 0, "title": "A"}], "preview": [[2, 4]]}   # 27 s: too long for a teaser - shortened, not refused
    (tmp_path / "a").mkdir()
    assert interview.triage({}, segs, "ctx", [], tmp_path / "a")["preview"] == [[2, 2]]   # (Nov 9: a long teaser failed a job)
    assert "questions in front of the" in _LLM.prompt and "Never drop a question whose answer stays" in _LLM.prompt
    _LLM.plan = {"chapters": [{"at": 0, "title": "A"}], "preview": [[5, 5]], "background": ["Who and where."], "guest": "Nea Dixon"}
    (tmp_path / "b").mkdir()
    assert interview.triage({}, segs, "ctx", [], tmp_path / "b")["guest"] == "Nea Dixon"


def test_no_old_guest_baked_in():
    src = Path(interview.__file__).read_text(encoding="utf-8")
    assert "Thank you, Mr. Clinton" not in src and "Islamic Center of Bermuda\"" not in src and "\"A conversation in Bermuda\"" not in src
    assert "keep his narrative" not in interview.TRIAGE and "man holding the camera" not in interview.TRIAGE


def test_create_interview_job_checks_the_file_and_queues(tmp_path):
    clip = tmp_path / "talk.MOV"; clip.write_bytes(b"x")
    with tempfile.TemporaryDirectory() as temp, patch.object(config, "load", return_value={**config.DEFAULTS, "jobs_dir": temp}), \
            patch("threading.Thread.start"):
        st = pipeline.Studio()
        with pytest.raises(ValueError, match="not found"): st.create_interview(tmp_path / "missing.mov")
        with pytest.raises(ValueError, match="video file"): st.create_interview(__file__)
        jid = st.create_interview(clip, "Nea at Dockyard", ["", str(clip)], "Thank you, Nea")
        s = st.job(jid).state
        assert s["kind"] == "interview" and s["status"] == "queued" and s["interview"]["context"] == "Nea at Dockyard"
        assert s["interview"]["extras"] == [str(clip)] and s["interview"]["thanks"] == "Thank you, Nea"


def test_adopt_links_outputs_and_writes_manifests(tmp_path):
    from studio import interview_edit, media
    out = tmp_path / "work" / "x_short.mp4"; out.parent.mkdir(); out.write_bytes(b"v")
    part = tmp_path / "work" / "p0.mp4"; part.write_bytes(b"p")
    mp = tmp_path / "work" / "x_short_parts.json"
    mp.write_text(json.dumps({"out": str(out), "title": "T", "sources": [], "parts": [{"file": str(part), "m": {"type": "card"}}]}), encoding="utf-8")
    with patch.object(media, "dur", return_value=3.0):
        outs = interview_edit.adopt(tmp_path / "job", {"short": mp, "long": tmp_path / "work" / "missing.json"}, log=lambda m: None)
    assert list(outs) == ["short"] and Path(outs["short"]).read_bytes() == b"v"
    m = json.loads((tmp_path / "job" / "iedit" / "short.json").read_text(encoding="utf-8"))
    assert m["parts"][0]["dur"] == 3.0 and m["parts"][0]["check"] is False


def test_topic_notes_research_what_the_guest_mentions(tmp_path, monkeypatch):
    import studio.llm
    from studio import websearch

    class L(_LLM):
        def chat_json(self, system, user, max_tokens=0, check=None):
            if "SPECIFIC things" in user:
                j = {"topics": [{"seg": 3, "topic": "three passports", "query": "Bermudian status citizenship"},
                                {"seg": 7, "topic": "sailing", "query": "Caribbean sailing"}]}
                if check: check(j)
                return j
            assert "Bermudian status:" in user
            return {"facts": ["Bermudian status is a legal status that gives the right to live and work in Bermuda.", ""]}
    monkeypatch.setattr(studio.llm, "LLM", L)
    monkeypatch.setattr(websearch, "_wikipedia", lambda cfg, q: [f"Bermudian status: about {q}"] if "Bermudian" in q else [])
    monkeypatch.setattr(websearch, "search", lambda cfg, q, log=None: ([], ""))
    res = interview.topic_notes({}, _segs(10), "ctx", tmp_path, log=lambda m: None)
    assert res == [{"fact": "Bermudian status is a legal status that gives the right to live and work in Bermuda.",
                    "source": "https://en.wikipedia.org/wiki/Bermudian_status", "topic": "three passports", "seg": 3, "kind": "topic"}]
    assert "(about what is said at segment 3: three passports)" in interview.note_lines(res + [{"fact": "Dockyard.", "kind": "place"}])
    assert "[1] (place) Dockyard." in interview.note_lines(res + [{"fact": "Dockyard.", "kind": "place"}])


def test_a_fact_on_what_was_said_goes_where_it_was_said(tmp_path, monkeypatch):
    import studio.llm
    monkeypatch.setattr(studio.llm, "LLM", _LLM)
    _LLM.plan = {"chapters": [{"at": 0, "title": "A"}], "facts": [{"after": 1, "note": 0, "text": "Status."}, {"after": 8, "note": 1, "text": "Dockyard."}]}
    notes = [{"fact": "Status.", "seg": 6, "kind": "topic", "topic": "passports"}, {"fact": "Dockyard.", "kind": "place"}]
    j = interview.triage({}, _segs(10), "ctx", notes, tmp_path)
    assert [f["after"] for f in j["facts"]] == [7, 8]   # moved to the answer about it; the place note stays where it was put
    assert "Spell people's and places' names the way the creator's note" in _LLM.prompt


def test_topic_note_source_is_the_snippet_the_fact_came_from(tmp_path, monkeypatch):
    import studio.llm
    from studio import websearch

    class L(_LLM):
        def chat_json(self, system, user, max_tokens=0, check=None):
            if "SPECIFIC things" in user: return {"topics": [{"seg": 2, "topic": "citizenship", "query": "British citizenship Bermuda"}]}
            return {"facts": ["A British Overseas Territories citizen holds a category of British nationality."]}
    monkeypatch.setattr(studio.llm, "LLM", L)
    monkeypatch.setattr(websearch, "_wikipedia", lambda cfg, q: ["Bermuda: an island territory in the Atlantic.",
                                                                 "British Overseas Territories citizen: a category of British nationality."])
    res = interview.topic_notes({}, _segs(5), "ctx", tmp_path, log=lambda m: None)
    assert res[0]["source"] == "https://en.wikipedia.org/wiki/British_Overseas_Territories_citizen"


def test_away_talk_gets_pip_background_gets_a_picture_and_dives_follow_the_answer():
    segs = _segs(10)
    tri = {"background": [{"text": "We met at Dockyard.", "visual": "Royal Naval Dockyard Bermuda"}],
           "pictures": [{"from": 4, "to": 6, "visual": "Horseshoe Bay Bermuda", "away": True}, {"from": 0, "to": 3, "visual": "arts centre", "away": False}],
           "chapters": [{"at": 0, "title": "A"}, {"at": 4, "title": "Beach"}], "facts": [{"after": 6, "note": 0, "text": "A fact."}],
           "dives": [{"seg": 6, "title": "Horseshoe Bay", "source": "https://en.wikipedia.org/wiki/Horseshoe_Bay,_Bermuda",
                      "lines": [{"text": "Pink sand line one.", "visual": "pink sand"}, {"text": "Line two.", "visual": "", "card": "1 km of beach"}]}]}
    its = interview.augment_items(tri, segs)
    assert its[0] == {"narr": "We met at Dockyard.", "broll": "", "background": True, "visual": "Royal Naval Dockyard Bermuda"}
    bites = [x for x in its if "bite" in x]
    assert [x.get("pip", False) for x in bites][:2] == [False, True]
    i = next(k for k, x in enumerate(its) if x.get("insert") == "A fact.")
    assert [x.get("insert") for x in its[i + 1:i + 3]] == ["Pink sand line one.", "Line two."] and its[i + 1]["card"] == "Horseshoe Bay"
    assert its[i + 2]["card"] == "1 km of beach" and its[i + 2]["visual"] == "Horseshoe Bay" and its[i + 1]["source"].endswith("Bermuda")


def test_interview_job_keeps_back_camera_and_length(tmp_path):
    clip = tmp_path / "talk.mov"; clip.write_bytes(b"x")
    with tempfile.TemporaryDirectory() as temp, patch.object(config, "load", return_value={**config.DEFAULTS, "jobs_dir": temp}), \
            patch("threading.Thread.start"):
        st = pipeline.Studio()
        iv = st.job(st.create_interview(clip, back_camera=False, minutes=99)).state["interview"]
        assert iv["back_camera"] is False and iv["minutes"] == 30.0
        assert st.job(st.create_interview(clip)).state["interview"]["minutes"] == 15


def test_deep_dives_written_from_the_article_and_sized(tmp_path, monkeypatch):
    import studio.llm

    class L(_LLM):
        def chat_json(self, system, user, max_tokens=0, check=None):
            if "SPECIFIC things" in user: return {"topics": [{"seg": 4, "topic": "glass bottom boats", "query": "glass bottom boat Bermuda"},
                                                             {"seg": 8, "topic": "dad", "query": "nothing here"}]}
            assert "ONLY from the reference text" in user and "Glass-bottom boat" in user
            j = {"dives": [{"topic": 1, "thin": True, "title": "Glass-bottom boats", "lines": [{"text": "A glass-bottom boat has a clear hull panel.", "visual": "glass bottom boat reef"},
                                                                               {"text": "Too short", "visual": ""}]}]}
            if check: check(j)
            return j
    monkeypatch.setattr(studio.llm, "LLM", L)
    monkeypatch.setattr(interview, "wiki_article", lambda q, topic="": ("Glass-bottom boat", "https://en.wikipedia.org/wiki/Glass-bottom_boat", "text " * 50) if "glass" in q else None)
    from studio import websearch
    monkeypatch.setattr(websearch, "search", lambda cfg, q, log=None: ([], ""))
    assert interview.deep_dives({}, _segs(10), "ctx", tmp_path, 20, log=lambda m: None) == []   # nothing needed: no research
    res = interview.deep_dives({}, _segs(10), "we rode a glass bottom boat", tmp_path, 600, log=lambda m: None)
    assert res == [{"seg": 4, "title": "Glass-bottom boats", "lines": [{"text": "A glass-bottom boat has a clear hull panel.", "visual": "glass bottom boat reef", "card": ""}],
                    "source": "https://en.wikipedia.org/wiki/Glass-bottom_boat", "topic": "glass bottom boats"}]


def test_wiki_article_rejects_namesakes_and_off_island_pages(monkeypatch):
    import requests

    class R:
        def __init__(self, j): self.status_code, self._j = 200, j
        def json(self): return self._j
    pages = {"Belonger status": "Belonger status is a legal status in the Cayman Islands and British Virgin Islands. " * 10,
             "Bermudian status": "Bermudian status is the right to live and work in Bermuda without restriction. " * 10}
    def get(url, params=None, headers=None, timeout=0):
        if params.get("list") == "search": return R({"query": {"search": [{"title": "Belonger status"}, {"title": "Cricket"}, {"title": "Bermudian status"}]}})
        t = params["titles"]; return R({"query": {"pages": {"1": {"title": t, "extract": pages.get(t, "")}}}})
    monkeypatch.setattr(requests, "get", get)
    t, url, text = interview.wiki_article("Bermudian status belonger", topic="island status")
    assert t == "Bermudian status" and url.endswith("Bermudian_status")


def test_deep_dives_leave_out_a_namesake_and_ask_for_full_length(tmp_path, monkeypatch):
    import studio.llm
    seen = {}

    class L(_LLM):
        def chat_json(self, system, user, max_tokens=0, check=None):
            if "SPECIFIC things" in user:
                assert "never a brand or a" in user
                return {"topics": [{"seg": 3, "topic": "sailing the Caribbean", "query": "sailing Caribbean islands"},
                                   {"seg": 5, "topic": "Dockyard", "query": "Royal Naval Dockyard Bermuda"}]}
            assert 'What the guest said: "Sentence 2. Sentence 3. Sentence 4."' in user
            short = {"dives": [{"topic": 1, "fits": False}, {"topic": 2, "title": "Dockyard", "lines": [{"text": "A short line of six words.", "visual": "v"}]}]}
            with pytest.raises(ValueError, match="write about"): check(short)
            seen["ok"] = True
            return {"dives": [{"topic": 1, "fits": False}, {"topic": 2, "title": "Dockyard", "thin": True, "lines": [{"text": "A short line of six words.", "visual": "v"}]}]}
    monkeypatch.setattr(studio.llm, "LLM", L)
    monkeypatch.setattr(interview, "wiki_article", lambda q, topic="": (q.title(), "u/" + q, "text " * 80))
    res = interview.deep_dives({}, _segs(10), "sailing, then Dockyard", tmp_path, 600, log=lambda m: None)
    assert seen["ok"] and [r["title"] for r in res] == ["Dockyard"]


def test_a_page_she_never_named_is_left_out():
    heard = "I used to ski a lot in Vermont, but also New Hampshire. We met at Dockyard."
    assert not interview.named("Stowe Mountain Resort", heard) and not interview.named("Bretton Woods, New Hampshire", heard)
    assert interview.named("Royal Naval Dockyard, Bermuda", heard) and interview.named("National Museum of Bermuda", heard)


def test_trip_footage_only_under_lines_about_where_you_filmed(tmp_path):
    lad = interview.Ladder({"interview_rungs": [], "interview_card_fallback": False}, "src.mov", False, tmp_path, "documentary", log=lambda m: None, trip=object())
    lad.here = {"bermu", "docky"}
    asked = []
    lad.trip_clip = lambda v, t, d, out, modes: asked.append(modes) or None
    lad._photo = lambda v, t: (None, "")
    lad._stock = lambda v, t, local=True: None
    assert lad.media("Merrimack River", "Merrimack is a town in New Hampshire.", 5, tmp_path / "a.mp4") is None and asked == []
    lad.media("Dockyard clock tower", "The Dockyard clock towers.", 5, tmp_path / "b.mp4")
    assert asked == [("place",), ("subject",), ("scenery",)]


def test_when_every_picture_fails_the_line_gets_a_card_never_her_face(tmp_path):
    (tmp_path / "kd").mkdir()
    lad = interview.Ladder({"interview_rungs": ["ai_image"]}, "src.mov", False, tmp_path, "documentary", log=lambda m: None)
    lad.here = {"bermu"}
    lad._photo = lambda v, t: (None, "")
    lad._stock = lambda v, t, local=True: None
    tried = []
    lad._rungs = lambda text, visual, d, out, rungs: tried.append(rungs) or ((out, "rung card", "") if rungs == ("card",) else None)
    lad.sketch = lambda *a, **k: None   # (no live image service in a test)
    assert lad.media("Merrimack River", "Merrimack, New Hampshire.", 5, tmp_path / "c.mp4")[1] == "rung card"
    assert tried == [("ai_image",), ("studio3d",), ("physics",), ("motion",), ("card",)]   # owner, 2026-11-12/13: 3D studio, then drawings, last before the card


def test_no_ai_image_cap_by_default():
    assert config.DEFAULTS["imagegen_max_per_video"] == 0


def test_names_spelled_as_the_creator_spells_them():
    note = "Nea Dixon, a Bermudian, at the Bermuda Arts Centre in Dockyard."
    assert interview.spell_as_noted("Three Passports: Nia Dixon at the Bermuda Arts Centre", note) == "Three Passports: Nea Dixon at the Bermuda Arts Centre"
    assert interview.spell_as_noted("Nia.", note) == "Nea." and interview.spell_as_noted("Nine Bermudans", note) == "Nine Bermudans"


def test_everyday_words_are_not_respelled():
    note = "Nea Dixon, a Bermudian, at the Bermuda Arts Centre in Dockyard."
    assert interview.spell_as_noted("Where in New Hampshire do you live? N-E-A, Nia.", note) == "Where in New Hampshire do you live? N-E-A, Nea."
    assert interview.spell_as_noted("Now Dixon spoke", "Nod Dixon") == "Nod Dixon spoke"


def test_adopt_replaces_an_older_cut(tmp_path):
    from studio import interview_edit, media
    out = tmp_path / "w" / "x_short.mp4"; out.parent.mkdir(); out.write_bytes(b"new")
    mp = tmp_path / "w" / "p.json"; mp.write_text(json.dumps({"out": str(out), "title": "T", "sources": [], "parts": []}), encoding="utf-8")
    old = tmp_path / "job" / "output" / "x_short.mp4"; old.parent.mkdir(parents=True); old.write_bytes(b"old")
    with patch.object(media, "dur", return_value=1.0):
        interview_edit.adopt(tmp_path / "job", {"short": mp}, log=lambda m: None)
    assert old.read_bytes() == b"new"


def test_each_clip_says_which_camera_filmed_it(tmp_path):
    """Owner, 2026-11-09: one conversation mixes both cameras - the front camera's clips are mirrored, the back camera's not."""
    from studio import shots
    f, b, n = tmp_path / "f.MOV", tmp_path / "b.MOV", tmp_path / "n.mp4"
    f.write_bytes(b"\0" * 1000 + b"iPhone 16 Pro front camera 2.69mm f/1.9" + b"\0" * 1000)
    b.write_bytes(b"\0" * 1000 + b"iPhone 16 Pro back camera 6.765mm f/1.78" + b"\0" * 1000)
    n.write_bytes(b"\0" * 2000)
    assert (shots.camera(f), shots.camera(b), shots.camera(n)) == ("front", "back", "")


def test_back_camera_clip_keeps_a_close_face_as_the_guest():
    from studio import shots
    big = [0.5, 0.5, 0.5, 0.5]   # a close face: the holder on the front camera, the guest on the back camera
    assert shots.split([big], back=True) == ([big], [])
    assert shots.split([big], back=False)[1] == [big]


def test_floor_or_ceiling_stretch_is_not_kept_as_the_picture():
    """Owner, 2026-11-09: 'Mr. Clinton is talking as we look at the floor... no rungs fired' - a blank / ceiling second is bad."""
    from studio import shots
    assert shots.kind_keep({"faces": [], "plain": 0.9}) == "blur"
    assert shots.kind_keep({"faces": [], "top": 0.99}) == "blur"
    assert shots.kind_keep({"faces": [], "plain": 0.1}) == "none"


def test_bad_stretch_gets_media_of_what_he_says_before_any_blur(tmp_path):
    lad = interview.Ladder({}, tmp_path / "src.mp4", False, tmp_path, "documentary", log=lambda *_: None)
    with patch.object(interview.Ladder, "media", return_value=(tmp_path / "o.mp4", "stock", "Pexels")) as m:
        assert lad.picture(10, 16, tmp_path / "o.mp4", "Muhammad Mosque Hamilton", "we pray here", why="empty")[1] == "stock"
    assert m.called


def test_clips_go_in_the_order_they_were_filmed():
    stamps = {"main.MOV": "2025-11-26T16:37:49Z", "start.MOV": "2025-11-26T16:35:40Z"}
    with patch.object(interview, "filmed_at", side_effect=lambda p: stamps.get(Path(p).name, "")):
        assert interview.filmed_at("start.MOV") < interview.filmed_at("main.MOV")


def test_cut_off_audio_is_extracted_again(tmp_path):
    """Nov 9: a restart mid-extraction left 2 minutes of a 26-minute drive, and the edit used only those."""
    wav = tmp_path / "audio16k.wav"; wav.write_bytes(b"x" * 5000)
    (tmp_path / "transcript.json").unlink(missing_ok=True)
    calls = []
    def ff(*a, **k):
        calls.append(a); Path(a[-1]).write_bytes(b"y" * 5000)
    with patch.object(interview.media, "ok", side_effect=lambda p, *_: Path(p).exists()), \
         patch.object(interview.media, "dur", side_effect=lambda p: 120.0 if Path(p).name == "audio16k.wav" and not calls else 1578.0), \
         patch.object(interview.media, "ff", side_effect=ff), \
         patch.dict(sys.modules, {"faster_whisper": None}):
        with pytest.raises(Exception):
            interview.transcribe(tmp_path / "src.MOV", tmp_path, log=lambda *_: None)
    assert calls and wav.read_bytes() == b"y" * 5000 and not (tmp_path / "audio16k.part.wav").exists()



def test_front_camera_creator_framed_stays_giant_face_and_chin_covered():
    """Nov 9, owner: the camera holder is the creator; his framed front-camera face is fine to show, a "nobody" second beside
    it too. Later the same day: "not such a giant face of closeup" - a face filling the frame and the chin between two such
    close-ups are covered (keep mode: the clean moment nearby / the ladder)."""
    from studio import shots
    secs = [{"t": t, "faces": [[0.5, 0.3, 0.22, 0.4]] if t in (0, 1, 5) else [], "tilt": 0} for t in range(8)]
    kinds = {p[2] for p in shots.plan_keep({"secs": secs}, 0, 8, min_len=1.0)}
    assert "blur" not in kinds and "vlog" in kinds
    giant = [dict(x, faces=[[0.5, 0.45, 0.4, 0.8]] if x["faces"] else []) for x in secs]
    assert {p[2] for p in shots.plan_keep({"secs": giant}, 0, 8, min_len=1.0)} == {"blur"}
    secs = [{"t": t, "faces": [[0.5, 0.3, 0.4, 0.8]] if t in (0, 1, 5) else [], "tilt": 0} for t in range(8)]
    back = [dict(x, back=True) for x in secs]
    assert any(p[2] == "guest" for p in shots.plan_keep({"secs": back}, 0, 8, min_len=1.0))

def test_trip_clip_of_the_place_must_show_what_the_line_is_about(tmp_path):
    """Nov 9: a Crystal Caves ticket filmed in Hamilton went under 'Muhammad Mosque, Hamilton'."""
    tf = interview.TripFootage(None, root=None, log=lambda *_: None)
    tf.recs = [{"original": str(tmp_path / "t.jpg"), "_vid": False, "_words": {"ticket", "cave", "tour"}, "_where": "hamilton", "_near": True},
               {"original": str(tmp_path / "m.jpg"), "_vid": False, "_words": {"mosque", "building"}, "_where": "hamilton", "_near": True}]
    with patch.object(interview.TripFootage, "_faceless", return_value=True):
        pk = tf.pick("Muhammad Mosque Hamilton", "", 5, "place")
    assert pk and Path(pk[0]).name == "m.jpg"


def test_creator_vlog_shot_stays_but_not_for_long():
    """Nov 9, owner: "leave me in as a vlogger... don't overdo it". The creator alone on screen stays as filmed for up to
    VLOG_RUN seconds at a time; the rest of a long stretch gets other pictures."""
    from studio import shots
    whole = {"t": 0, "faces": [[0.5, 0.5, 0.3, 0.5]], "tilt": 0}
    sliver = {"t": 0, "faces": [[0.97, 0.4, 0.08, 0.6]], "tilt": 0}
    assert shots.kind_keep(whole) == "vlog" and shots.kind_of(whole) == "vlog" and shots.kind_keep(sliver) == "vlog"
    plan = [(0, 30, "vlog", None, 0.0), (30, 40, "guest", [0.5, 0.5, 0.1, 0.1], 0.0), (40, 50, "vlog", None, 0.0)]
    out = shots.cap_vlog(plan, 0, 50, "blur")
    assert out[0] == (0, shots.VLOG_RUN, "vlog", None, 0.0) and out[1][2] == "blur" and out[1][1] == 30
    assert out[-1][2] == "vlog" and sum(p[1] - p[0] for p in out) == 50   # 10 s: a little over, kept whole


def test_editor_commentary_insert_fade_and_undo(tmp_path, monkeypatch):
    """Nov 9, owner: insert my own commentary (camera or a file) where I want, and fade pieces in / out; undo reverses both."""
    from studio import interview_edit as ie
    job = tmp_path / "job"; (job / "iedit").mkdir(parents=True)
    parts = []
    for n in range(3):
        f = job / f"p{n}.mp4"; f.write_bytes(b"x"); parts.append({"file": str(f), "dur": 5.0, "m": {"type": "bite"}})
    (job / "iedit" / "short.json").write_text(json.dumps({"parts": parts, "history": [{"bid": "p02", "old": "a", "new": "b"}]}), encoding="utf-8")
    monkeypatch.setattr(ie, "_k", lambda j: "short")
    monkeypatch.setattr(ie, "_assemble", lambda *a, **k: None)
    monkeypatch.setattr(ie, "_background", lambda job, busy, bid, work, cfg, log: (work(), "")[1])
    monkeypatch.setattr(ie, "_commentary_clip", lambda job, src, out, k, log=print, syn=False, cfg=None: (Path(out).write_bytes(b"v"),
                        ([("hello", 0.1, 0.4)], {"label": "on_speaking", "synthetic": False, "camera": str(out), "words": [["hello", 0.1, 0.4]]}))[1])
    monkeypatch.setattr(ie, "_faded", lambda src, out, d, fin, fout: Path(out).write_bytes(b"f"))
    monkeypatch.setattr(ie.media, "dur", lambda f: 4.0)
    rec = tmp_path / "me.webm"; rec.write_bytes(b"r")
    assert ie.insert_commentary(job, "p00", str(rec), {}, print) == ""
    m = ie._man(job, "short")
    assert [p["m"]["type"] for p in m["parts"]] == ["bite", "comment", "bite", "bite"] and m["parts"][1]["words"][0][0] == "hello"
    assert m["history"][0]["bid"] == "p03"   # the older edit's piece moved up one
    assert ie.beats(job)["beats"][1]["label"] == "Your commentary"
    ie.set_fade(job, "p02", True, False, {}, print)
    m = ie._man(job, "short"); assert m["parts"][2]["fade"] == {"in": True, "out": False} and m["parts"][2]["unfaded"].endswith("p1.mp4")
    ie.undo(job, print, {})   # the fade
    m = ie._man(job, "short"); assert m["parts"][2]["file"].endswith("p1.mp4") and m["parts"][2]["fade"] == {}
    ie.undo(job, print, {})   # the commentary
    m = ie._man(job, "short"); assert len(m["parts"]) == 3 and m["history"][0]["bid"] == "p02"
    with pytest.raises(ValueError): ie.insert_commentary(job, "p00", str(tmp_path / "nope.mp4"), {}, print)


def test_dressing_puts_hook_banner_and_strap_on_the_right_pieces(tmp_path, monkeypatch):
    """Nov 9, owner: "the videos have to have a strong hook and banner". The stronger hook replaces the draft; the topic
    banner runs over the story (not the opener, not cards) as one event; the name strap comes on his first soundbite."""
    from studio import interview
    dr = {"hooks": {"He found a church": "A CHURCH TURNED MOSQUE"}, "banner": "INSIDE A BERMUDA MOSQUE", "who": "Mr. Clinton", "role": "Muhammad Mosque"}
    parts = [{"file": "a", "hook": "He found a church", "m": {"type": "bite"}}, {"file": "b", "m": {"type": "card"}},
             {"file": "c", "m": {"type": "bite"}}, {"file": "d", "m": {"type": "fact"}}, {"file": "e", "m": {"type": "bite"}, "chapter": "Food"},
             {"file": "f", "m": {"type": "card"}}]
    interview.dress(parts, dr, "documentary")
    assert parts[0]["hook"] == "A CHURCH TURNED MOSQUE" and "banner" not in parts[0] and "banner" not in parts[1]
    assert parts[2]["banner"] == parts[3]["banner"] == "INSIDE A BERMUDA MOSQUE" and "banner" not in parts[5]
    assert parts[2]["strap"] == ["Mr. Clinton", "Muhammad Mosque"] and parts[4].get("strap") and not parts[3].get("strap")
    ev = [interview.piece_events(p, 5.0, 1920, 1080, 46, 9, parts[i - 1].get("banner") if i else None,
                                 parts[i + 1].get("banner") if i + 1 < len(parts) else None) for i, p in enumerate(parts)]
    ban = [e for x in ev for e in x if ",Banner," in e]
    assert len(ban) == 3 and "fad(250,0)" in ban[0] and "fad(0,0)" in ban[1] and "fad(0,250)" in ban[2]   # c+d+e read as one banner
    assert sum(",Strap," in e for x in ev for e in x) == 2 and any("A CHURCH TURNED MOSQUE" in e for e in ev[0])


def test_an_edit_re_dresses_only_the_changed_piece(tmp_path, monkeypatch):
    """Nov 9, owner: "do not redo video like clinton. do only the beat that needs to change". Each piece is dressed and kept;
    changing one piece's words re-encodes that piece only (plus nothing else), the rest is reused and joined by copy."""
    from studio import interview
    made = []
    def ff(*a, **k):
        out = Path(str(a[-1])); out.write_bytes(b"x" * 10)
        if "subtitles" in " ".join(map(str, a)): made.append(out.name)
    monkeypatch.setattr(interview.media, "ff", ff)
    monkeypatch.setattr(interview.media, "dur", lambda f: 5.0)
    monkeypatch.setattr(interview.media, "ok", lambda f, *a: Path(f).exists())
    monkeypatch.setattr(interview, "_writable", lambda f: False)
    for n in "abcd": (tmp_path / n).write_bytes(b"v")
    parts = [{"file": str(tmp_path / n), "words": [(n, 0.0, 1.0)]} for n in "abcd"]
    interview.assemble(parts, tmp_path / "o.mp4", "documentary", tmp_path / "w", {}, print, "t")
    assert len(made) == 4
    made.clear(); parts[2]["words"] = [("changed", 0.0, 1.0)]
    interview.assemble(parts, tmp_path / "o.mp4", "documentary", tmp_path / "w", {}, print, "t")
    assert len(made) == 1 and made[0].startswith("002_")
    assert len(list((tmp_path / "w" / "dressed").glob("*.mp4"))) == 4   # the replaced piece's old dressing is cleared



def test_audio_routing_my_voice_off_camera_and_voiceover_override(tmp_path, monkeypatch):
    """Nov 9, owner's audio routing: off camera -> my US voice (rule 1); on camera, silent -> nothing to voice (rule 2);
    on camera speaking -> a voiceover in my US voice, camera audio dropped, no lip-sync, captions carry it (rule 3).
    Until OpenVoice is set up the Kokoro US voice reads it; the toggle and undo work per piece."""
    from studio import interview, interview_edit as ie, tts
    assert ie.voice_label(False, [("a", 0, 1)]) == "off" and ie.voice_label(True, []) == "on_silent" and ie.voice_label(True, [("a", 0, 1)]) == "on_speaking"
    calls = []
    monkeypatch.setattr(tts, "kokoro_batch", lambda cfg, items, log, tag="", stop=None: calls.append((cfg.get("kokoro_voice"), [str(o) for _, o in items])))
    monkeypatch.setattr(tts.media, "ok", lambda f, *a: False)
    assert tts.openvoice_ready({}) and tts.my_voice_id({}) == "kokoro:am_michael"
    tts.my_voice({"my_voice_kokoro": "am_fenrir"}, [("hi", tmp_path / "a.wav")], print)
    assert calls[-1][0] == "am_fenrir"   # no OpenVoice yet: the Kokoro US voice reads it
    interview.speak({"interview_my_voice": True, "my_voice_kokoro": "am_puck"}, [("x", tmp_path / "b.wav")], print, "t")
    assert calls[-1][0] == "am_puck"
    interview.speak({"kokoro_voice": "af_heart"}, [("x", tmp_path / "c.wav")], print, "t")
    assert calls[-1][0] == "af_heart"
    kd = tmp_path / "kd"; (kd / "narr").mkdir(parents=True); (kd / "narr" / "n1.wav").write_bytes(b"w"); (kd / "03_narr.mp4").write_bytes(b"v")
    (kd / "04_bite.mp4").write_bytes(b"v")
    interview.voice_stamp({}, kd); assert (kd / "narr" / "n1.wav").exists()   # first stamp: nothing dropped
    interview.voice_stamp({"interview_my_voice": True}, kd); assert (kd / "narr" / "n1.wav").exists()   # same Kokoro voice: kept
    interview.voice_stamp({"interview_my_voice": True, "my_voice_kokoro": "am_fenrir"}, kd)
    assert not (kd / "narr" / "n1.wav").exists() and not (kd / "03_narr.mp4").exists() and (kd / "04_bite.mp4").exists()
    # the per-piece toggle on a commentary piece, and undo
    job = tmp_path / "job"; (job / "iedit").mkdir(parents=True)
    cam = job / "cam.mp4"; cam.write_bytes(b"c")
    part = {"file": str(cam), "dur": 4.0, "words": [["hello", 0.1, 0.4]], "m": {"type": "comment"},
            "voice": {"label": "on_speaking", "synthetic": False, "camera": str(cam), "words": [["hello", 0.1, 0.4]]}}
    (job / "iedit" / "short.json").write_text(json.dumps({"parts": [part], "history": []}), encoding="utf-8")
    monkeypatch.setattr(ie, "_k", lambda j: "short")
    monkeypatch.setattr(ie, "_assemble", lambda *a, **k: None)
    monkeypatch.setattr(ie, "_background", lambda job, busy, bid, work, cfg, log: (work(), "")[1])
    said = []
    monkeypatch.setattr(ie, "_revoice", lambda cam, text, out, k, cfg, log=print: (said.append(text), Path(out).write_bytes(b"s"), [("hello", 0.0, 0.5)])[2])
    monkeypatch.setattr(ie.media, "dur", lambda f: 4.5)
    assert ie.set_voice(job, "p00", True, {}, print) == "" and said == ["hello"]
    p = ie._man(job, "short")["parts"][0]
    assert p["voice"]["synthetic"] and p["file"] != str(cam) and p["words"][0] == ["hello", 0.0, 0.5]
    assert ie.beats(job)["beats"][0]["voice"]["label"] == "on_speaking"
    ie.undo(job, print, {})
    p = ie._man(job, "short")["parts"][0]; assert p["file"] == str(cam) and not p["voice"]["synthetic"]
    m = ie._man(job, "short"); m["parts"][0]["m"] = {"type": "bite"}; ie._save_man(job, "short", m)
    with pytest.raises(ValueError): ie.set_voice(job, "p00", True, {}, print)   # only commentary pieces


def test_voiceover_override_drops_camera_audio_and_holds_the_picture(tmp_path, monkeypatch):
    """Rule 3: the voice track replaces the camera audio (no lip-sync) and the picture holds if the voice runs longer."""
    from studio import interview_edit as ie, tts, captions
    runs = []
    monkeypatch.setattr(tts, "my_voice", lambda cfg, items, log, tag="": None)
    monkeypatch.setattr(ie.media, "dur", lambda f: 6.0 if str(f).endswith(".me.wav") else 4.0)
    monkeypatch.setattr(ie.media, "ff", lambda *a, **k: runs.append([str(x) for x in a]))
    monkeypatch.setattr(captions, "word_times", lambda wav, text: [("hi", 0.1, 0.3)])
    w = ie._revoice(tmp_path / "cam.mp4", "hi there", tmp_path / "out.mp4", "short", {}, print)
    a = runs[-1]
    assert a[a.index("-map", a.index("-map") + 1) + 1] == "1:a:0"   # the camera audio is not used
    assert "tpad=stop_mode=clone:stop_duration=2.40" in a and w == [("hi", 0.1, 0.3)]


def test_a_finished_piece_is_kept_unless_its_shot_plan_changed(tmp_path, monkeypatch):
    """Nov 9, owner: "why do a full render? the editor splices a beat". A soundbite piece is cut again only when its shot
    plan comes out different (a rule change); otherwise the finished file is kept. Credits of a re-cut piece start over."""
    import os
    from studio import interview
    made = []
    def ff(*a, **k):
        outs = [x for x in a if str(x).endswith(".mp4")]
        Path(outs[-1]).write_bytes(b"v"); made.append(Path(outs[-1]).name)
    monkeypatch.setattr(interview.media, "ff", ff)
    monkeypatch.setattr(interview.media, "ok", lambda f, *a: Path(f).exists())
    monkeypatch.setattr(interview.media, "dur", lambda f: 10.0)
    guest = {"secs": [{"t": t, "faces": [[0.3, 0.4, 0.1, 0.12]], "tilt": 0} for t in range(12)]}
    out = tmp_path / "03_bite.mp4"
    cr = []
    interview.cut_bite_framed("src.mov", 0, 10, out, "short", False, guest, None, print, keep=True, credits=cr)
    assert (tmp_path / "03_bite.key").exists() and "03_bite.mp4" in made
    made.clear()
    interview.cut_bite_framed("src.mov", 0, 10, out, "short", False, guest, None, print, keep=True, credits=cr)
    assert made == []   # same plan: kept
    cr.append((0, 1, "x", "credit"))
    tilted = {"secs": [dict(s, tilt=6.0) for s in guest["secs"]]}   # the rules now see this stretch differently
    interview.cut_bite_framed("src.mov", 0, 10, out, "short", False, tilted, None, print, keep=True, credits=cr)
    assert "03_bite.mp4" in made and cr == []
    old = tmp_path / "04_bite.mp4"; old.write_bytes(b"v")   # made before plan keys...
    os.utime(old, (interview.RULES_SINCE - 99, interview.RULES_SINCE - 99)); made.clear()
    interview.cut_bite_framed("src.mov", 0, 10, old, "short", False, guest, None, print, keep=True)
    assert "04_bite.mp4" in made   # ...under older rules: cut again
    new = tmp_path / "05_bite.mp4"; new.write_bytes(b"v"); made.clear()   # ...since today's rules: adopted
    interview.cut_bite_framed("src.mov", 0, 10, new, "short", False, guest, None, print, keep=True)
    assert made == [] and (tmp_path / "05_bite.key").exists()


def test_own_footage_library_found_wherever_the_source_is(tmp_path):
    """A Bermuda story uses your F:\bermuda catalogue even when its source video is kept elsewhere (owner, 2026-11-09)."""
    from studio import interview as iv
    cat = tmp_path / "lib" / "labeled" / "catalog"; cat.mkdir(parents=True)
    libs = {"trip_libraries": [{"place": "bermuda", "catalog": str(cat), "lat": 32.32, "lon": -64.76, "km": 60}]}
    src = tmp_path / "elsewhere" / "talk.mp4"; src.parent.mkdir()
    assert iv.trip_catalog(libs, src, None, "Inside a Bermuda mosque", log=lambda *a: None) == cat          # the story names it
    assert iv.trip_catalog(libs, src, (32.29, -64.78), "A chat", log=lambda *a: None) == cat                # filmed there
    assert iv.trip_catalog(libs, src, (43.0, -71.5), "A chat in New Hampshire", log=lambda *a: None) is None  # elsewhere: none
    here = src.parent / "labeled" / "catalog"; here.mkdir(parents=True)
    assert iv.trip_catalog(libs, src, None, "Bermuda", log=lambda *a: None) == here                        # the one beside the source first


def test_covered_stretch_split_at_pauses_each_with_its_own_words():
    """Nov 9, owner: "whenever I or Mr. Clinton describe or mention something, I don't want to stare at the floor" - a long
    covered stretch is several pictures, each picked from the words said in it."""
    from studio import interview
    words = [(f"w{i}", i * 0.5, i * 0.5 + 0.3) for i in range(60)]   # a word every half second, pauses between
    plan = interview.split_covered([(0.0, 30.0, "blur", None, 0.0), (30.0, 35.0, "vlog", None, 0.0)], ("blur",), words)
    blur = [p for p in plan if p[2] == "blur"]
    assert len(blur) >= 3 and all(p[1] - p[0] <= 12 for p in blur) and blur[0][0] == 0.0 and blur[-1][1] == 30.0
    assert plan[-1][2] == "vlog"
    assert interview.said_in(words, 0.0, 1.2) == "w0 w1 w2"


def test_own_clips_skip_a_clip_the_vision_check_rejects(tmp_path, monkeypatch):
    """Nov 9: the phone resting on the carpet and a shirt filmed sideways passed as "own footage of the place"."""
    from studio import interview, shots
    monkeypatch.setattr(shots, "vet_clip", lambda cfg, f, rot="", log=print, **k: (Path(f).stem != "floor", "x"))
    calls = []
    def fake(cfg, f, rot="", log=print, **k):
        calls.append(Path(f).stem); return (Path(f).stem != "floor", "x")
    monkeypatch.setattr(shots, "vet_clip", fake)
    tf = interview.TripFootage(None, root=None, log=lambda *_: None, cfg={})
    tf.recs = [dict(original=str(tmp_path / n), _vid=True, seconds=30, _words={"mosque", "room"}, _where="hamilton", _near=True, people="none")
               for n in ("floor.MOV", "hall.MOV")]
    for r in tf.recs: Path(r["original"]).write_bytes(b"x")
    tf._faceless = lambda f, r, d: True
    got = tf.pick("mosque room", "the mosque room", 5, "subject")
    assert got and Path(got[0]).stem == "hall" and "floor" in calls


def test_sideways_clip_is_turned_not_rejected(tmp_path, monkeypatch):
    """Nov 9, owner: "sideway and upside down media should not be reject. instead it should be rotated or flipped"."""
    import json
    from studio import shots, footage, media, media_db
    f = tmp_path / "hall.MOV"; f.write_bytes(b"x" * 10)
    monkeypatch.setattr(media_db, "path", lambda cfg: tmp_path / "media.db")
    monkeypatch.setattr(media, "dur", lambda p: 20.0)
    filters = []
    monkeypatch.setattr(media, "ff", lambda *a, **k: filters.append(a[a.index("-vf") + 1]))
    def seen(cfg, img, prompt):   # upright only once turned a quarter clockwise
        up = "transpose=1" in filters[-1]
        return json.dumps({"shows": "a school hallway", "floor_percent": 5, "upright": up, "worth_showing": True})
    monkeypatch.setattr(footage, "_vision", seen)
    ok, why, rot = shots.vet_clip({}, f, "")
    assert ok and rot == "transpose=1"
    monkeypatch.setattr(footage, "_vision", lambda cfg, img, prompt: json.dumps({"shows": "a shirt", "floor_percent": 0, "upright": False, "worth_showing": False}))
    g = tmp_path / "shirt.MOV"; g.write_bytes(b"y" * 10)
    assert not shots.vet_clip({}, g, "")[0]   # junk however it is turned


def test_boring_or_off_topic_picture_drops_to_the_next_rung(tmp_path, monkeypatch):
    """Nov 9, owner: "weed out boring static images which don't jive with the narration... even a card is better... even an
    artist courtroom type rendition is better than boring stuff"."""
    from studio import interview
    (tmp_path / "kd").mkdir()
    lad = interview.Ladder({"interview_rungs": ["ai_image"]}, tmp_path / "src.MOV", False, tmp_path / "kd", "documentary", log=lambda *_: None)
    lad._rungs = lambda text, visual, d, out, rungs: None   # the AI image rungs fail too
    tried = []
    monkeypatch.setattr(lad, "trip_clip", lambda v, t, d, out, modes: (tried.append(modes[0]), (out, "trip " + modes[0], ""))[1])
    monkeypatch.setattr(lad, "_photo", lambda v, t: (None, ""))
    monkeypatch.setattr(lad, "_stock", lambda v, t, local=True: None)
    sketch = tmp_path / "sk.mp4"
    monkeypatch.setattr(lad, "fits", lambda clip, v, t: clip == sketch)   # every found picture is the boring floor; the sketch is fine
    monkeypatch.setattr(lad, "sketch", lambda v, t, d, out: (sketch, "sketch (AI)", "AI sketch"))
    got = lad.media("kids at the school", "the kids in this school", 6.0, tmp_path / "o.mp4")
    assert got[1] == "sketch (AI)" and tried == ["place", "subject", "scenery"]


def _audit_ladder(tmp_path, monkeypatch, turns, shows, bad=(), content=True, seen=None):
    """A Ladder whose vision model sees `turns` (degrees per second) and `shows` (text per second; "floor" is boring).
    content: the per-second content judge on (it is off by default since Nov 10 - the Vision Checker does it)."""
    from studio import interview, footage, media
    import studio.llm as llm
    (tmp_path / "kd").mkdir(parents=True, exist_ok=True)
    lad = interview.Ladder({"interview_content_audit": content}, tmp_path / "src.MOV", False, tmp_path / "kd", "documentary", log=lambda *_: None)
    clip = tmp_path / "pic.mp4"; clip.write_bytes(b"x" * 100)
    monkeypatch.setattr(media, "ok", lambda f, s=0.0: Path(f).exists())
    monkeypatch.setattr(media, "dur", lambda f: float(len(turns)))
    calls = {"i": 0, "k": 0}
    seen = [] if seen is None else seen
    def ff(*a, **k):   # a frame grab: remember which second it is
        if "-ss" in a: calls["t"] = int(float(a[a.index("-ss") + 1]))
        Path(a[-1]).write_bytes(b"jpg")
    monkeypatch.setattr(media, "ff", ff)
    import numpy as np
    from studio import shots
    cv2, _ = shots._cv()
    monkeypatch.setattr(cv2, "imread", lambda f, *a: np.zeros((36, 64, 3), np.uint8))
    monkeypatch.setattr(cv2, "imwrite", lambda f, im: True)
    def vision(cfg, img, prompt):
        t = calls["t"]; seen.append(prompt[:12])
        if "Four copies" in prompt:   # the letter of the copy that undoes this second's turn
            i = calls["k"]; calls["k"] += 1; order = [0, 90, 180, 270][i % 4:] + [0, 90, 180, 270][:i % 4]
            return json.dumps({"upright": "ABCD"[order.index((360 - turns[t]) % 360)]})
        return json.dumps({"shows": shows[t], "boring": "floor" in shows[t], "flawed": False})
    monkeypatch.setattr(footage, "_vision", vision)
    class L:
        def __init__(self, *a): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def chat_json(self, sysm, prompt, max_tokens=0): seen.append("JUDGE"); return {"bad": list(bad), "why": "a football team"}
    monkeypatch.setattr(llm, "LLM", L)
    return lad, clip


def test_audit_finds_a_picture_turned_one_way_throughout(tmp_path, monkeypatch):
    """Nov 10, owner: "a flipped man won't match a standing man description... take a 1 sec snapshot of the beat"."""
    lad, clip = _audit_ladder(tmp_path, monkeypatch, [180] * 6, ["a man standing upside down"] * 6)
    ok, turn, why = lad.audit(clip, "the imam", "this is the imam")
    assert ok and turn == 180


def test_editor_audit_only_checks_turns_by_default(tmp_path, monkeypatch):
    """Nov 10, owner: "the main editor should not do heavy per-second content checks" - a few looks for a turned picture,
    no description, no judge; an off-topic picture passes here (the Vision Checker flags it after the render)."""
    seen = []
    lad, clip = _audit_ladder(tmp_path, monkeypatch, [0] * 6, ["football players"] * 6, bad=[1, 2, 3, 4, 5, 6], content=False, seen=seen)
    assert lad.audit(clip, "the mosque", "inside the mosque")[:2] == (True, 0)
    assert len(seen) == lad.TURN_SAMPLES and all(x.startswith("Four copies") for x in seen)   # no SEE, no JUDGE
    lad, clip = _audit_ladder(tmp_path / "b", monkeypatch, [90] * 12, ["a door"] * 12, content=False)
    assert lad.audit(clip, "the door", "the door")[:2] == (True, 270)   # filmed 90 deg off throughout: still turned upright
    lad, clip = _audit_ladder(tmp_path / "c", monkeypatch, [90, 270, 180, 90, 0, 270], ["a shelf"] * 6, content=False)
    assert not lad.audit(clip, "the shop", "the kebab shop")[0]   # turned every which way: not trusted


def test_audit_rejects_mixed_turns_boring_and_off_topic_seconds(tmp_path, monkeypatch):
    lad, clip = _audit_ladder(tmp_path, monkeypatch, [90, 270, 180, 90, 0, 270], ["a shelf"] * 6)
    assert lad.audit(clip, "the shop", "the kebab shop")[:2] == (False, 0)   # turned every which way: not trusted
    lad, clip = _audit_ladder(tmp_path / "b", monkeypatch, [0] * 6, ["floor", "floor", "a room", "a room", "a room", "a room"], bad=[1, 2])
    assert not lad.audit(clip, "the mosque", "the mosque")[0]   # two empty seconds of six, not what is talked about
    lad, clip = _audit_ladder(tmp_path / "c", monkeypatch, [0] * 6, ["football players"] * 6, bad=[1, 2, 3, 4, 5, 6])
    assert not lad.audit(clip, "the mosque", "inside the mosque")[0]   # off the words
    lad, clip = _audit_ladder(tmp_path / "d", monkeypatch, [0] * 6, ["the mosque hall"] * 6, bad=[3])
    assert lad.audit(clip, "the mosque", "inside the mosque")[0]   # one stray second of six is let go


def test_beat_audit_sends_a_failed_stretch_down_the_rungs_and_skips_cards(tmp_path, monkeypatch):
    from studio import interview
    (tmp_path / "kd").mkdir()
    lad = interview.Ladder({}, tmp_path / "src.MOV", False, tmp_path / "kd", "documentary", log=lambda *_: None)
    segs = []
    for n in range(3):
        f = tmp_path / f"s{n:02}.mp4"; f.write_bytes(b"old"); segs.append(f)
    looked = []
    def audit(v, visual, said, ref="", mode="full"):
        looked.append((Path(v).name, mode))
        return (Path(v).name != "s01.mp4", 0, "a football team")
    lad.audit = audit
    def picture(a, b, out, visual="", text="", why=""):
        Path(out).write_bytes(b"new"); return out, "rung ai_image", "AI image"
    lad.picture = picture
    monkeypatch.setattr(interview.media, "dur", lambda f: 4.0)
    plan = [(10, 14, "guest", [0.1, 0.1, 0.5, 0.5], 0), (14, 18, "blur", None, 0), (18, 22, "blur", None, 0)]
    credits = [(8.0, 12.0, "card", "")]
    changed = interview.beat_audit(plan, segs, 10, lad, "the mosque", "words", [], credits)
    assert changed and segs[1].read_bytes() == b"new" and segs[0].read_bytes() == b"old"
    assert looked == [("s00.mp4", "orient"), ("s01.mp4", "full")]   # the card at 18 s is never audited
    assert (4.0, 8.0, "rung ai_image", "AI image") in credits
