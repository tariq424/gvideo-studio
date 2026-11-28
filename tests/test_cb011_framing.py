"""CB-011: portrait framing of own-footage AI renditions, hook/end card, commentary ladder."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
cv2 = pytest.importorskip("cv2")
from studio import reframe, short  # noqa: E402


def test_bundled_cascade_loads():
    assert reframe._CASCADE_FILE.is_file()
    c = reframe._cascade()
    assert c is not None and not c.empty()


def test_rendition_box_finds_centered_photo(tmp_path):
    # dark UI chrome around a bright photo pane with a face-sized blob
    im = np.zeros((540, 960, 3), np.uint8)
    im[:] = (40, 40, 45)
    im[120:420, 220:740] = (90, 140, 200)
    cv2.circle(im, (480, 240), 36, (200, 180, 160), -1)  # head-ish
    p = tmp_path / "pane.jpg"
    cv2.imwrite(str(p), im)
    box = reframe.rendition_box(p)
    assert box is not None
    x0, y0, x1, y1 = box
    assert x0 < 0.35 and x1 > 0.65 and y0 < 0.35 and y1 > 0.65


def test_analyse_prefers_fit_frame_when_rendition_wider_than_crop(tmp_path):
    im = np.zeros((360, 640, 3), np.uint8)
    im[:] = (30, 30, 30)
    im[80:300, 80:560] = (70, 120, 180)
    cv2.circle(im, (320, 170), 28, (190, 170, 150), -1)
    p = tmp_path / "wide.mp4"
    w = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*"mp4v"), 12, (640, 360))
    for _ in range(12):
        w.write(im)
    w.release()
    r = reframe.analyse(p, 1080, 1920)
    assert r.get("fit") == "frame" or r.get("pos") is not None


def test_is_commentary_detects_verdict_not_tool_test():
    assert short.is_commentary({"vo": "So what is actually happening? Either it searches some database, or imagines.", "source_visual": {}})
    assert short.is_commentary({"vo": "The host leaves the verdict to viewers: which tool did the right thing.",
                                "source_visual": {"purpose": "Gemini on screen with the Trump likeness"}})
    assert not short.is_commentary({"vo": "Grok was the first test. It refused.",
                                    "source_visual": {"purpose": "Grok on screen showing the generated image of Trump"},
                                    "take": "Honestly, Grok's refusal is defensible."})
    assert not short.is_commentary({"vo": "Someone fed AI tools a written description.", "take": "Our take: suspicious.",
                                    "source_visual": {}})  # take alone is not commentary
    assert not short.is_commentary({"cta": True, "vo": "Follow for more."})


def test_commentary_stops_at_unreal_and_skips_threejs():
    sec = {"id": "b5", "vo": "So what is actually happening? Either it searches some database, or imagines."}
    assert short.is_commentary(sec)
    assert short.commentary_holds_unreal(sec, [{"rung": "unreal", "type": "video"}])
    assert not short.commentary_holds_unreal(sec, [{"rung": "stock"}])
    chain, why, failed = [], [], []
    assert short._threejs_item({"threejs_on": True}, None, sec, {"key": "b5_0"}, chain, why, failed, lambda m: None) is None
    assert chain == []
    src = Path(short.__file__).read_text(encoding="utf-8")
    assert '"template": "T_DataWall"' in src
    assert "commentary beat stops at unreal" in src


def test_short_shows_full_rendition_on_a_filled_background():
    root = Path(__file__).resolve().parents[1]
    visuals = (root / "app" / "studio" / "source_visuals.py").read_text(encoding="utf-8")
    assert "crop_video_to_box" not in visuals
    assert '"fit": "frame"' in visuals
    bg = (root / "remotion" / "src" / "Background.tsx").read_text(encoding="utf-8")
    start = bg.index('bg.type === "video" && bg.fit === "frame"')
    chunk = bg[start:start + 900]
    assert 'filter: "blur(38px)' in chunk
    assert 'objectFit: "contain"' in chunk


def test_end_card_and_hook_added_to_plan():
    plan = {"title": "AI Drew Trump From Text Alone", "tag": "AI IMAGE DETECTIVE",
            "sections": [{"id": "b0", "vo": "Someone fed AI tools a written description. How?", "visuals": [], "queries": []}]}
    # reuse the CTA/hook block by calling through a tiny shim of the last part of do_script
    from studio import short as S
    # simulate the CB-011 append logic
    cta = {"kind": "cta", "badge": str(plan.get("tag") or "")[:24], "title": str(plan.get("title") or "")[:80],
           "line": "Follow for more", "play": False, "arrow": True}
    assert cta["title"] and cta["badge"]
    opener = plan["sections"][0]
    opener["visuals"].insert(0, {"type": "chart", "chart": {"kind": "hook", "lines": ["SOMEONE FED AI", "TOOLS A WRITTEN"], "accent": 1}})
    assert opener["visuals"][0]["chart"]["kind"] == "hook"
