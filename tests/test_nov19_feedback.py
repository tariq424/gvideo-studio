"""owner, 2026-11-19 (Kaku review): fresh hook, framed source excerpts, rich voice, dramatic swells, no empty charts,
every picture says where it came from."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from studio import charts, edit, media, music, reframe, source_visuals as sv, writer  # noqa: E402


def _plan(first, later):
    return {"title": "Quantum", "sections": [{"vo": first + " Then the talk begins."}, {"vo": "Some setup. " + later + " More."}]}


def test_a_hook_that_repeats_a_later_line_is_found():
    p = _plan("The first country to build a workable quantum computer could break into every intelligence agency on Earth.",
              "The first country with a workable machine, he says, could break into every intelligence agency in the world.")
    assert writer.hook_repeat(p)
    assert not writer.hook_repeat(_plan("Your bank password may already be stolen, just waiting for a key.",
                                        "The first country with a workable machine could break into every agency."))


def test_fresh_hook_rewrites_only_the_first_sentence():
    p = _plan("The first country to build a workable quantum computer could break into every intelligence agency on Earth.",
              "The first country with a workable machine, he says, could break into every intelligence agency in the world.")
    class L:
        def trace(self, *_): return self
        def chat_json(self, sys_, user, max_tokens=0, check=None):
            j = {"hook": "Somewhere a lab is racing to make every secret you have ever typed readable."}
            check(j); return j
    writer.fresh_hook(L(), p, lambda *_: None)
    assert p["sections"][0]["vo"].startswith("Somewhere a lab") and "Then the talk begins." in p["sections"][0]["vo"]


def test_empty_charts_go_to_the_picture_ladder():
    same = {"kind": "cards", "title": "Race", "items": [{"head": c, "body": "Racing to build one"} for c in ("A", "B", "C", "D")]}
    assert charts.weak(same)
    assert charts.weak({"kind": "line", "x": [1, 2], "y": [3, 4], "note": "Illustrative shape"})
    assert not charts.weak({"kind": "cards", "items": [{"head": "1", "body": "Steam"}, {"head": "2", "body": "Electricity"}]})
    assert not charts.weak({"kind": "bar", "labels": ["a", "b"], "values": [1, 2]})


def test_a_badly_framed_excerpt_moves_to_a_framed_moment(tmp_path, monkeypatch):
    dest = tmp_path / "x.mp4"; dest.write_bytes(b"0")
    scores = {0.0: 0.0, 3.0: 0.25, -3.0: 0.9}
    cur = {"s": 10.0}
    def ff(*a, **k):
        cur["s"] = float(a[a.index("-ss") + 1]); Path(a[-1]).write_bytes(b"1")
    monkeypatch.setattr(sv.media, "ff", ff)
    monkeypatch.setattr(reframe, "framing", lambda p: scores.get(round(cur["s"] - 10.0, 1), 0.0) if "_try" in str(p) else 0.0)
    assert sv._best_framed(tmp_path / "src.mp4", 10.0, 8.0, dest, 60.0, {}, lambda *_: None) == 7.0


def test_an_excerpt_with_faces_nearby_but_none_framed_is_refused(tmp_path, monkeypatch):
    dest = tmp_path / "x.mp4"; dest.write_bytes(b"0")
    monkeypatch.setattr(sv.media, "ff", lambda *a, **k: Path(a[-1]).write_bytes(b"1"))
    monkeypatch.setattr(reframe, "framing", lambda p: 0.25)
    try:
        sv._best_framed(tmp_path / "src.mp4", 10.0, 8.0, dest, 60.0, {}, lambda *_: None); assert False
    except ValueError as e:
        assert "well-framed" in str(e)
    monkeypatch.setattr(reframe, "framing", lambda p: 0.0)   # slides / a demo: no faces anywhere -> kept
    assert sv._best_framed(tmp_path / "src.mp4", 10.0, 8.0, dest, 60.0, {}, lambda *_: None) == 10.0


def test_narration_gets_the_broadcast_voice_chain():
    for k in ("highpass", "deesser", "acompressor", "loudnorm"): assert k in media.VOICE_FX
    src = (Path(__file__).resolve().parents[1] / "app" / "studio" / "explainer.py").read_text(encoding="utf-8")
    assert "media.VOICE_FX" in src


def test_swells_sit_after_whole_sentences_followed_by_a_pause(tmp_path):
    tl = tmp_path / "t.json"
    tl.write_text(json.dumps({"beats": [{"start": 10, "words": [["It", 0, 0.3], ["broke.", 0.3, 0.8], ["Then", 2.0, 2.3], ["more.", 2.3, 2.6], ["Fast", 2.7, 3.0], ["end.", 3.0, 3.3]]}]}))
    c = music.swell_candidates(tl)
    assert c[0][:2] == (10.8, 12.0) and c[0][2] == "It broke." and all(x[2] != "Then more." for x in c)
    ins, parts, labels = music.swell_filters([(10.8, 12.0)], tmp_path / "cue.mp3", 3, {"music_swell_db": 0})
    assert labels == ["[sw0]"] and "[3:a]" in parts[0] and "adelay=8800" in parts[0]


def test_every_explainer_slot_says_where_its_picture_came_from(tmp_path):
    (tmp_path / "log.txt").write_text("", encoding="utf-8")
    L = edit.ladder(tmp_path, "s3_1", "explainer", visual={"type": "chart", "chart": {"kind": "cards", "title": "The Race"}})
    assert L[0]["used"].startswith("Chart planned by the script")
    L = edit.ladder(tmp_path, "s3_1", "explainer", visual={"type": "still", "chart_rejected": "its boxes repeat the same text"})
    assert L[0]["failed"][0] == ["chart", "its boxes repeat the same text"] and L[0]["used"] == "AI picture"
