"""owner, 2026-11-16: framed camera moves for stills (spec VIDEO_STUDIO_CAMERA_SPEC.md): big sharp rigid moves pre-rendered in
Python, dissolves between framed shots, legacy kept behind camera_moves: "legacy"."""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from studio import camera_moves as cm  # noqa: E402

cv2 = pytest.importorskip("cv2")


def test_push_goes_from_the_whole_frame_to_about_half_and_stays_inside():
    W, H = 1920, 1080
    ws = cm.windows("push", 60, 2 * W, 2 * H, W, H)
    assert abs(ws[0][2] - 2 * W) < 1 and abs(ws[-1][2] - 0.52 * 2 * W) < 1
    for x0, y0, ww, ang in ws:
        assert x0 >= -1e-6 and y0 >= -1e-6 and x0 + ww <= 2 * W + 1e-6 and y0 + ww * H / W <= 2 * H + 1e-6 and ang == 0


def test_the_moves_ease_and_the_spin_turns_nine_degrees():
    W, H = 1920, 1080
    ws = cm.windows("close", 30, 2 * W, 2 * H, W, H)
    steps = [abs(ws[i + 1][2] - ws[i][2]) for i in range(len(ws) - 1)]
    assert steps[0] < steps[len(steps) // 2] > steps[-1]   # slow, fast, slow
    sp = cm.windows("spin", 30, 2 * W, 2 * H, W, H)
    assert sp[0][3] == 0 and abs(sp[-1][3] + 9) < 1e-6


def test_a_pan_sweeps_edge_to_edge_and_a_wide_picture_in_a_short_is_swept_whole():
    W, H = 1920, 1080
    ws = cm.windows("pan-lr", 30, 2 * W, 2 * H, W, H)
    assert ws[0][0] == 0 and abs(ws[-1][0] + ws[-1][2] - 2 * W) < 1
    # a 16:9 still in a 9:16 Short: the master is much wider than the frame -> sweep across all of it at nearly full height
    W, H = 1080, 1920
    iw, ih = int(2 * H * 16 / 9), 2 * H
    ws = cm.windows("pan-rl", 30, iw, ih, W, H)
    assert abs(ws[0][0] + ws[0][2] - iw) < 1 and ws[-1][0] == 0 and ws[0][2] > 0.85 * 2 * W


def test_only_generated_and_sourced_stills_get_a_move():
    assert cm.eligible({"type": "layers", "layers": ["a_L0.jpg"]})
    assert cm.eligible({"type": "image", "src": "a.jpg"})
    assert cm.eligible({"type": "image", "src": "a.jpg", "fit": "frame", "credit": ""})
    assert not cm.eligible({"type": "image", "src": "a.jpg", "fit": "frame", "credit": "Photo: AP"})   # credited photo shown whole
    assert not cm.eligible({"type": "image", "src": "c.png", "fit": "still"})                         # chart / slide
    assert not cm.eligible({"type": "image", "src": "mine.jpg", "user": True})                        # your own file
    assert not cm.eligible({"type": "video", "src": "v.mp4"})
    assert not cm.eligible({"type": "graphic", "g": {"kind": "map"}})


def test_never_the_same_move_twice_and_at_most_one_spin_a_minute():
    prev, last_spin, spins, seq = None, -1e9, [], []
    for k in range(60):
        t = k * 6.0
        m = cm.choose(k, prev, t, last_spin, [None, 0.3, 0.0][k % 3] or None)
        assert m != prev and m in cm.MOVES
        if m == "spin": spins.append(t); last_spin = t
        prev = m; seq.append(m)
    assert all(b - a >= 60 for a, b in zip(spins, spins[1:])) and spins
    assert {"push", "close", "pull", "dolly"} <= set(seq)


def _job(tmp_path):
    job = tmp_path / "20261116_000000"; pub = job / "short" / "public"; (pub / "media").mkdir(parents=True)
    (job / "state.json").write_text("{}", encoding="utf-8")
    rng = np.random.default_rng(1)
    img = (rng.random((540, 960, 3)) * 255).astype(np.uint8)
    cv2.imwrite(str(pub / "media" / "a.jpg"), img)
    cv2.imwrite(str(pub / "media" / "b.png"), img[:, ::-1])
    cv2.imwrite(str(pub / "media" / "own.jpg"), img)
    data = {"audio": "voice.wav", "total": 6.0, "tag": "", "beats": [
        {"id": "b0", "start": 0.0, "dur": 3.0, "words": [], "graphics": [], "bgs": [
            {"type": "image", "src": "media/a.jpg", "from": 0.0, "tag": "GENERATED"},
            {"type": "image", "src": "media/b.png", "from": 1.5}]},
        {"id": "b1", "start": 3.0, "dur": 3.0, "words": [], "graphics": [], "bgs": [
            {"type": "image", "src": "media/own.jpg", "from": 0.0, "user": True},
            {"type": "image", "src": "media/a.jpg", "from": 1.0, "fit": "still"}]}]}
    (pub / "teaser.json").write_text(json.dumps(data), encoding="utf-8")
    return job, pub, data


def test_legacy_is_an_untouched_copy(tmp_path):
    _, pub, data = _job(tmp_path)
    out = cm.framed_copy(data, pub, {"camera_moves": "legacy"})
    assert out == data and not (pub / "framed").exists()


def test_framed_render_props_replace_stills_dissolve_and_leave_the_timeline_alone(tmp_path):
    _, pub, data = _job(tmp_path)
    pf = cm.render_props(pub, {"camera_moves": "framed"}, log=lambda *_: None)
    out = json.loads(pf.read_text(encoding="utf-8"))
    b0, b1 = out["beats"]
    assert [g.get("framed") for g in b0["bgs"]] == [True, True]
    assert b0["bgs"][0]["tag"] == "GENERATED" and b0["bgs"][0]["rate"] == 1.0
    assert not b0["bgs"][0].get("xf") and b0["bgs"][1]["xf"]                     # first shot cuts in, second dissolves over it
    assert b0["bgs"][0]["move"] != b0["bgs"][1]["move"]
    assert b1["bgs"] == data["beats"][1]["bgs"]                                   # your file and the slide: exactly as before
    tl = json.loads((pub / "teaser.json").read_text(encoding="utf-8"))
    assert [g["type"] for g in tl["beats"][0]["bgs"]] == ["image", "image"]       # the timeline stays a timeline of stills...
    assert [g["cam"] for g in tl["beats"][0]["bgs"]] == [g["move"] for g in b0["bgs"]]   # ...that remembers each move
    mp4 = pub / b0["bgs"][0]["src"]
    from studio import media
    n = int(subprocess.run([media._bin("ffprobe"), "-v", "error", "-count_frames", "-select_streams", "v:0",
                            "-show_entries", "stream=nb_read_frames,width,height", "-of", "csv=p=0", str(mp4)],
                           capture_output=True, text=True).stdout.strip().split(",")[-1])
    assert n == round((1.5 + cm.TAIL) * 30)                                       # its slot + the dissolve tail
    again = json.loads(cm.render_props(pub, {"camera_moves": "framed"}, log=lambda *_: None).read_text(encoding="utf-8"))
    assert again == out                                                           # cached and stable


def test_a_cut_out_whose_original_is_gone_keeps_its_old_look(tmp_path):
    job, pub, _ = _job(tmp_path)
    rng = np.random.default_rng(2)
    full = (rng.random((192, 108, 3)) * 255).astype(np.uint8)
    a = np.zeros((192, 108), np.uint8); a[60:140, 30:80] = 255
    cv2.imwrite(str(pub / "media" / "x_px_L0.jpg"), full)
    cv2.imwrite(str(pub / "media" / "x_px_L1.png"), np.dstack([full * (a[..., None] > 0), a]))   # rembg: black under the alpha
    assert cm.picture({"type": "layers", "layers": ["media/x_px_L0.jpg", "media/x_px_L1.png"]}, pub, cm._Index(job)) is None
    # ...but with the original still next to the working copy of the layer, that is used, and the subject is the focus
    wd = job / "short" / "imagegen"; wd.mkdir()
    cv2.imwrite(str(wd / "x_px_L0.jpg"), full); cv2.imwrite(str(wd / "x_cloud.png"), full)
    got = cm.picture({"type": "layers", "layers": ["media/x_px_L0.jpg", "media/x_px_L1.png"]}, pub, cm._Index(job))
    assert got is not None and got[0].shape == full.shape and abs(got[1][0] - 55 / 108) < 0.05 and abs(got[1][1] - 100 / 192) < 0.05


def test_the_narration_picks_the_shot():
    assert cm.cue("An aerial view of the airport")[1][0] == "spin"
    assert cm.cue("Look closely at the badge")[1][0] == "close"
    assert cm.cue("The tower rises over the bay")[1][0] == "tilt-down"
    assert cm.cue("A row of jets lined up on the apron")[1][0] == "pan-lr"
    assert cm.cue("The whole city lights up")[1][0] == "pull"
    assert cm.cue("as it approaches the gate")[1][0] == "dolly"
    assert cm.cue("It costs forty million dollars") is None
    beat = {"words": [["Seen", 0.1, 0.3], ["from", 0.3, 0.5], ["above", 0.5, 0.8], ["the", 3.1, 3.2], ["face", 3.2, 3.5]]}
    assert cm.shot_words(beat, {"from": 0}, 3.0) == "Seen from above"
    assert cm.choose(0, None, 100, -1e9, None, said=["spin", "pull"]) == "spin"
    assert cm.choose(0, "spin", 100, 90, None, said=["spin", "pull"]) == "pull"          # never twice running, spin rationed
    assert cm.choose(3, "close", 100, -1e9, 0.3, said=["close", "hold"]) == "hold"       # the cue's next move, not the rotation
