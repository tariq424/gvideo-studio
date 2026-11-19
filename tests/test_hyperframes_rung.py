"""owner, 2026-11-18: HyperFrames (HTML -> video, local render) as a ladder rung after the motion templates, before the card."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from studio import hyperframes as H, qc_repair  # noqa: E402


def spec(body=None, js='tl.from("#a", {y: 40, duration: 0.5}, 0);', css=""):
    return {"css": css, "js": js, "body": body or '<div id="a" class="clip" data-start="0" data-duration="6" data-track-index="0">Fifty atoms</div>'}


def test_rung_sits_after_motion_and_before_the_card():
    names = [n for n, _ in qc_repair.RUNGS]
    assert names.index("motion") < names.index("hyperframes") == names.index("card") - 1


def test_page_wraps_the_model_output_in_the_fixed_scaffold():
    html = H.page(spec(css=".clip{opacity:0;color:red}", js='const tl = gsap.timeline({ paused: true });\ntl.from("#a",{y:9},0);'), 6, 1920, 1080)
    assert html.count("gsap.timeline(") == 1 and 'src="assets/gsap.min.js"' in html   # local GSAP, one timeline
    assert "opacity:0" not in html and "color:red" in html                              # hidden start removed, the rest kept
    assert html.count('id="root"') == 1


@pytest.mark.parametrize("bad, why", [
    (spec(js='fetch("https://x.org"); tl.to("#a",{x:1},0);'), "not allowed"),
    (spec(js='tl.to("#a",{x: Math.random()},0);'), "not allowed"),
    (spec(body='<img src="a.png" class="clip" data-start="0" data-duration="6">'), "not allowed"),
    (spec(body='<div id="a">no clip class</div>'), "class=\"clip\""),
    (spec(body='<div id="a" class="clip" data-start="0" data-duration="9" data-track-index="0">x</div>'), "runs past"),
    (spec(body='<div id="a" class="clip" data-start="0" data-duration="3" data-track-index="0">x</div>'), "goes empty"),
])
def test_page_rejects_what_the_renderer_or_the_owner_would_not_take(bad, why):
    with pytest.raises(ValueError, match=why): H.page(bad, 6, 1920, 1080)


def test_numbers_on_screen_must_come_from_the_narration():
    vo = "fifty atoms across; the signal crossed one point four billion light years and 1,400 stations"
    assert H.invented_numbers({"body": "<b>50</b><i>1.4</i><u>1400</u>", "js": "tl.to(o,{v: 50})"}, vo) == []
    assert H.invented_numbers({"body": "<b>1/1,000,000,000</b>", "js": ""}, "smaller than a proton") == ["1000000000"]


def test_rung_is_skipped_when_switched_off(tmp_path):
    ctx = {"vo": "fifty atoms", "cfg": {"hyperframes_on": False}, "need": 6, "W": 1920, "H": 1080, "work": tmp_path, "log": print, "key": "k"}
    clip, img, why = qc_repair._rung_hyperframes(ctx)
    assert clip is None and "hyperframes_on" in why


def test_make_reports_why_when_not_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(H, "_cli", lambda: None)
    monkeypatch.setattr(H, "ensure", lambda log=None: False)   # no real npm install from a test
    clip, why = H.make({}, "fifty atoms", 6, 1920, 1080, tmp_path)
    assert clip is None and "not installed" in why


def test_cli_runs_with_telemetry_off():
    e = H._env()
    assert e["HYPERFRAMES_NO_TELEMETRY"] == "1" and e["DO_NOT_TRACK"] == "1"
