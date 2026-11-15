"""Defect report from the post-render Vision Checker (owner, 2026-11-10): only the flagged stretches are re-made, guided by the
defect type, then the video is re-assembled with every other approved piece as it is. AI is never dropped for being AI."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from studio import interview_edit as ie  # noqa: E402

REPORT = {"mismatches": [
    {"start": "0:12", "end": "0:16", "defect_type": "wrong_concept", "note": "Narration is about prayer direction, visual is a deli shopkeeper."},
    {"start": "0:25", "end": "0:26", "defect_type": "bad_framing", "note": "Too close, just a shirt collar."},
    {"start": 34, "end": 36, "defect_type": "artifact", "note": "Melted hands."},
    {"start": "0:50", "end": "0:52", "defect_type": "nonsense", "note": "?"},
    {"start": "9:00", "end": "9:05", "defect_type": "wrong_concept", "note": "past the end"},
]}


def _job(tmp_path):
    """A job with a 4-piece long cut: bite 0-20 s, narration 20-30 s, AI-pictured bite 30-45 s, end card 45-55 s."""
    job = tmp_path / "job"; (job / "iedit").mkdir(parents=True)
    parts = []
    for n, (typ, d) in enumerate([("bite", 20.0), ("narr", 10.0), ("bite", 15.0), ("card", 10.0)]):
        f = job / f"{n:02}.mp4"; f.write_bytes(b"v%d" % n)
        words = [["word%d" % s, float(s), s + 0.9] for s in range(int(d))]
        parts.append({"file": str(f), "dur": d, "words": words, "m": {"type": typ, "src": str(job / "src.MOV"), "text": f"piece {n}"},
                      "credits": [[2.0, 8.0, "AI image"]] if n == 2 else [], **({"credit": "Pexels: someone"} if n == 1 else {})})
    (job / "iedit" / "long.json").write_text(json.dumps({"parts": parts, "gps": [32.29, -64.78]}), encoding="utf-8")
    return job


def test_parse_defects_reads_times_types_and_fenced_json(tmp_path):
    ds = ie.parse_defects("```json\n" + json.dumps(REPORT) + "\n```")
    assert [(d["start"], d["end"], d["type"]) for d in ds[:4]] == [(12, 16, "wrong_concept"), (25, 26, "bad_framing"),
                                                                     (34, 36, "artifact"), (50, 52, "wrong_concept")]
    f = tmp_path / "r.json"; f.write_text(json.dumps(REPORT), encoding="utf-8")
    assert len(ie.parse_defects(str(f))) == 5   # a path on this PC
    assert ie._secs("1:02:03.5") == 3723.5
    with pytest.raises(ValueError): ie.parse_defects('{"nothing": []}')
    with pytest.raises(ValueError): ie.parse_defects("{not json")


def test_defect_plan_maps_video_times_onto_pieces(tmp_path):
    job = _job(tmp_path)
    pl = ie.defect_plan(job, REPORT, "long")
    by = {(x["n"], x["bid"]): x for x in pl}
    assert (by[(1, "p00")]["a"], by[(1, "p00")]["b"]) == (12, 16)
    assert (by[(2, "p01")]["a"], by[(2, "p01")]["b"]) == (4.5, 6.5)   # 1 s flagged: widened to 2 s inside the piece
    assert (by[(3, "p02")]["a"], by[(3, "p02")]["b"]) == (4, 6)
    assert by[(4, "p03")]["skip"]            # a title / end card is not re-made
    assert by[(5, "")]["skip"].startswith("past the end")
    span = ie.defect_plan(job, {"mismatches": [{"start": "0:18", "end": "0:24", "defect_type": "wrong_concept"}]}, "long")
    assert [(x["bid"], x["a"], x["b"]) for x in span] == [("p00", 18, 20), ("p01", 0, 4)]   # across two pieces: each fixed
    sliver = ie.defect_plan(job, {"mismatches": [{"start": "0:16.5", "end": "0:20.5", "defect_type": "bad_framing"}]}, "long")
    assert [x["bid"] for x in sliver] == ["p00"]   # 0.5 s spilling into the next piece is not a second defect


def test_rework_follows_the_defect_type_and_keeps_ai_as_a_rung(tmp_path, monkeypatch):
    job = _job(tmp_path)
    calls = []

    class Lad:
        context = "Hamilton, Bermuda"
        def media(self, visual, text, d, out):
            calls.append(("media", visual, round(d, 1)))
            Path(out).parent.mkdir(parents=True, exist_ok=True); Path(out).write_bytes(b"pic")
            return out, "rung ai_image", "AI image"   # an AI picture is a fine answer
        def _rungs(self, text, visual, d, out, rungs):
            calls.append(("rungs", visual, rungs[0]))
            Path(out).parent.mkdir(parents=True, exist_ok=True); Path(out).write_bytes(b"pic")
            return out, "rung " + rungs[0], "AI image"
        def fits(self, clip, visual, text): return True

    monkeypatch.setattr(ie, "_rework_ladder", lambda *a: Lad())
    ideas = {"wrong_concept": ("Kaaba direction arrow on a prayer rug", ""), "bad_framing": ("the mosque prayer hall", ""),
             "artifact": ("a man pouring tea", "natural hands with five fingers")}
    monkeypatch.setattr(ie, "_rework_idea", lambda cfg, x, text, was, where, log: (
        ("wide shot of " if x["type"] == "bad_framing" else "") + ideas[x["type"]][0], ideas[x["type"]][1]))
    laid = []
    monkeypatch.setattr(ie, "_overlay", lambda src, pic, at, d, out, k="long": (laid.append((Path(src).name, at, d)), Path(out).write_bytes(b"new")))
    assembled = []
    monkeypatch.setattr(ie, "_assemble", lambda job, k, cfg, log: assembled.append(k))
    rep = {"mismatches": REPORT["mismatches"][:3]}
    res = ie.rework_defects(job, rep, {"interview_rungs": ["ai_image", "chatgpt_chain"]}, lambda *_: None, k="long")
    assert calls[0] == ("media", "Kaaba direction arrow on a prayer rug", 4.0)       # wrong concept: a new idea, the whole ladder
    assert calls[1] == ("media", "wide shot of the mosque prayer hall", 2.0)         # bad framing: a wide shot, the whole ladder
    assert calls[2] == ("rungs", "a man pouring tea, natural hands with five fingers", "ai_image")   # artifact: same idea, prompt fixed
    assert laid == [("00.mp4", 12, 4), ("01.mp4", 4.5, 2.0), ("02.mp4", 4, 2)]   # only the flagged spans, the audio kept
    assert assembled == ["long"]   # one re-assembly at the end
    m = json.loads((job / "iedit" / "long.json").read_text(encoding="utf-8"))
    assert [Path(p["file"]).name for p in m["parts"]] == ["long_p00_1.mp4", "long_p01_1.mp4", "long_p02_1.mp4", "03.mp4"]
    assert "credit" not in m["parts"][1] and m["parts"][1]["credits"] == [[0.0, 4.5, "Pexels: someone"], [4.5, 6.5, "AI image"],
                                                                        [6.5, 10.0, "Pexels: someone"]]   # the photo credit only where the photo still is
    assert m["parts"][2]["credits"] == [[4, 6, "AI image"]]   # the old AI credit over 4-6 s replaced; AI stays a rung
    assert len(m["history"]) == 3 and m["history"][0]["old"].endswith("00.mp4")   # Undo takes them back one by one
    assert all(r["result"].startswith("rung") for r in res["items"])


def test_rework_leaves_a_stretch_as_it_was_when_the_ladder_finds_nothing(tmp_path, monkeypatch):
    job = _job(tmp_path)
    class Lad:
        context = ""
        def media(self, *a): return None
    monkeypatch.setattr(ie, "_rework_ladder", lambda *a: Lad())
    monkeypatch.setattr(ie, "_rework_idea", lambda *a: ("x", ""))
    monkeypatch.setattr(ie, "_assemble", lambda *a: (_ for _ in ()).throw(AssertionError("nothing changed: no re-assembly")))
    res = ie.rework_defects(job, {"mismatches": REPORT["mismatches"][:1]}, {}, lambda *_: None, k="long")
    assert res["items"][0]["result"] == "unchanged"
    assert json.loads((job / "iedit" / "long.json").read_text(encoding="utf-8"))["parts"][0]["file"].endswith("00.mp4")


def test_stretches_show_which_rung_made_each_picture(tmp_path):
    """Nov 10, owner: "I don't see the source of the clip, e.g. stock video or a particular rung that was fired"."""
    kd = tmp_path / "iwork"; kd.mkdir()
    f = kd / "05_bite.mp4"; f.write_bytes(b"v")
    (kd / "05_bite.json").write_text(json.dumps([[2.0, 6.0, "rung ai_image", ""], [8.0, 12.0, "stock", "Pexels: someone"]]), encoding="utf-8")
    p = {"file": str(f), "credits": [[8.0, 12.0, "Pexels: someone"], [14.0, 18.0, "AI mock-up on Esri satellite imagery"]]}
    m = {"parts": [p], "history": []}
    ss = ie._stretches(m, 0, p, 20.0)
    assert [(s["a"], s["b"], s["how"]) for s in ss] == [(2.0, 6.0, "rung ai_image"), (8.0, 12.0, "stock"),
                                                        (14.0, 18.0, "AI mock-up on Esri satellite imagery")]   # a splice laid by hand
    fixed = str(tmp_path / "long_p00_1.mp4")
    m["history"].append({"bid": "p00", "old": str(f), "new": fixed, "why": "defect report #1: artifact - hands",
                         "stretch": {"a": 3.0, "b": 5.0, "how": "rung chatgpt_chain", "credit": "", "fix": "artifact"}})
    p2 = dict(p, file=fixed)
    assert [(s["a"], s["how"]) for s in ie._stretches(m, 0, p2, 20.0)][:1] == [(3.0, "rung chatgpt_chain")]   # the fix replaces 2-6 s
    picked = str(tmp_path / "long_p00_2.mp4")
    m["history"].append({"bid": "p00", "old": fixed, "new": picked, "t": 1})
    assert [s["how"] for s in ie._stretches(m, 0, dict(p, file=picked, credits=[]), 20.0)] == ["picked in the editor"]


def test_parallax_is_one_picture_drifting_never_cut_out_layers(tmp_path, monkeypatch):
    """Owner, Nov 11: "ghosting... the parallax is broken" - the cut-out layers showed their painted-in holes as grey ghosts."""
    from PIL import Image
    import numpy as np
    from studio import qc_repair, depth, media
    img = tmp_path / "s.jpg"
    a = np.zeros((360, 640, 3), np.uint8); a[:, :, 1] = np.linspace(0, 255, 640, dtype=np.uint8)[None]; a[120:240, 260:380] = 255
    Image.fromarray(a).save(img)
    monkeypatch.setattr(depth, "cutout_layers", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no cut-outs")))
    monkeypatch.setattr(depth, "depth_map", lambda im, log: np.tile(np.linspace(0, 1, im.size[1], dtype=np.float32)[:, None], (1, im.size[0])))
    out = qc_repair.image_to_video(img, tmp_path / "o.mp4", 2, 320, 180)
    assert abs(media.dur(out) - 2.0) < 0.1


def test_bare_walls_are_never_held(tmp_path):
    """Owner, Nov 11: "why this filthy wall? ... Another filthy wall." - a remade frame that is a bare wall is not a picture."""
    import cv2
    import numpy as np
    from studio import interview
    wall = np.full((360, 640, 3), 225, np.uint8); wall[100:140, 200:230] = 200   # a bare wall with a stain
    room = wall.copy()
    for x in range(0, 640, 40): cv2.rectangle(room, (x, 150), (x + 25, 300), (60, 80, 120), 2)   # chairs, windows...
    cv2.imwrite(str(tmp_path / "w.jpg"), wall); cv2.imwrite(str(tmp_path / "r.jpg"), room)
    assert interview.blank_frame(tmp_path / "w.jpg") and not interview.blank_frame(tmp_path / "r.jpg")


def test_a_clip_you_added_is_never_dropped_whole():
    """Owner, Nov 11: "none of the pictures of the kebab... the Menus. Gone. why leave out menu? it more authentic" - the triage
    had dropped the kebab-window clip whole as "halal ordering chatter" (and five more of the eight clips added)."""
    from studio import interview
    segs, i = [], 0
    for src, n, step in ((0, 6, 10.0), (1, 7, 9.0), (2, 40, 5.0)):   # the main talk, a 63 s kebab window, a 200 s tour
        for k in range(n):
            segs.append({"i": i, "src": src, "start": k * step, "end": k * step + step - 1, "text": "word " * (1 + (k == 20) * 9)}); i += 1
    tri = {"drop": [[2, 3, "small talk"], [6, 12, "halal ordering chatter"], [13, 52, "the tour"]]}
    out = interview.keep_every_clip(tri, segs, log=lambda *_: None)
    gone = {i for d in out["drop"] for i in range(d[0], d[1] + 1)}
    assert not any(i in gone for i in range(6, 13))         # a short clip: all of it back
    kept = [i for i in range(13, 53) if i not in gone]
    assert kept and 33 in kept and segs[kept[-1]]["end"] - segs[kept[0]]["start"] <= 45   # a long one: its wordiest stretch, <= 45 s
    assert {2, 3} <= gone                                    # the main clip's drops stand
    assert interview.keep_every_clip({"drop": [[2, 3, "x"]]}, segs)["drop"] == [[2, 3, "x"]]


def test_a_short_never_runs_past_three_minutes():
    """owner, 2026-11-12: Clinton's Short came out 3:07, so YouTube filed it as a regular video. The cards and narration only
    get their real lengths at assembly; there it is cut to fit, from the end, keeping the hook and the closing card, and
    never ending on a narration line that led into a dropped soundbite."""
    from studio import interview as iv
    k = lambda t, d: {"file": f"{t}{d}", "m": {"type": t}, "d": d}
    parts = [k("bite", 16.4), k("card", 3.2), k("narr", 8.6), k("bite", 20.2), k("narr", 6.1), k("bite", 55.5), k("fact", 8.3),
             k("bite", 16.7), k("narr", 7.0), k("bite", 16.8), k("narr", 5.1), k("bite", 19.3), k("card", 4.0)]
    out = iv.cap_short(parts, dur=lambda p: p["d"], log=lambda *_: None)
    assert sum(p["d"] for p in out) <= iv.SHORT_MAX
    assert out[0] is parts[0] and out[-1] is parts[-1]                      # the hook opens, the end card closes
    assert out[-2]["m"]["type"] == "bite"                                   # ends on a soundbite, not a dangling narration
    assert out == parts[:10] + [parts[-1]]                                  # only the end was trimmed
    short = parts[:4] + [parts[-1]]
    assert iv.cap_short(short, dur=lambda p: p["d"], log=lambda *_: None) == short   # a Short that fits is untouched
