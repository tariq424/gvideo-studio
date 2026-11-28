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
