"""Research narration, then the beat, then the joke. FZ1073's descent is the published track."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from studio import flight_path, render_repair, unreal_mrq, writer  # noqa: E402


def test_a_take_left_off_the_voice_is_spoken_before_the_beat():
    plan = {"sections": [{"id": "b0", "vo": "The plane dropped toward Tabuk. A joke lands here.",
                          "take": "Public track shows the plunge.", "punchline": "A joke lands here."}]}
    assert writer.place_commentary_before_the_joke(plan) == 1
    vo = plan["sections"][0]["vo"]
    assert vo.index("Public track") < vo.index("The plane dropped") < vo.index("A joke lands here")
    assert writer.place_commentary_before_the_joke(plan) == 0


def test_headless_shell_uses_software_gl(tmp_path):
    js = tmp_path / "remotion" / "node_modules" / "@remotion" / "renderer" / "dist" / "open-browser.js"
    js.parent.mkdir(parents=True)
    js.write_text(
        "if (renderer === 'swangle') {\n        return ['--use-gl=angle', '--use-angle=swiftshader'];\n    }\n        timeout: 25000,\n",
        encoding="utf-8")
    from studio import short
    assert short.patch_headless_shell_gl(tmp_path) is True
    text = js.read_text(encoding="utf-8")
    assert "--disable-gpu" in text and "swiftshader" not in text and "timeout: 120000," in text
    assert short.patch_headless_shell_gl(tmp_path) is False


def test_commentary_comes_before_the_beat_and_the_joke_after():
    vo = writer.order_commentary_beat_joke(
        "The plane dropped. A joke lands here. More of the beat.",
        "Public track shows the plunge.",
        "A joke lands here.")
    assert vo.index("Public track") < vo.index("The plane dropped") < vo.index("A joke lands here")
    assert vo.endswith("A joke lands here.")


def test_fz1073_descent_uses_the_published_points():
    points = flight_path.fz1073_descent()
    assert points[0]["alt_ft"] == 34000
    assert points[-1]["alt_ft"] == flight_path.TABUK["alt_ft"]
    assert any(p["lat"] == 31.1 and p["lon"] == 37.2 and p["alt_ft"] == 15025 for p in points)
    start = points[0]
    assert 230 <= flight_path._km(start, flight_path.TABUK) <= 240
    assert min(p["alt_ft"] for p in points) < 20000 < max(p["alt_ft"] for p in points)
    voiced = flight_path.for_narration("Flydubai descended toward Tabuk on the way to Tel Aviv.")
    assert voiced and voiced[0]["alt_ft"] > voiced[-1]["alt_ft"]
    assert flight_path.for_narration("A quiet day at the office.") is None


def test_route_plan_uses_the_data_wall():
    assert unreal_mrq.template_for_text("The flight plan diverted and descended.") == "T_DataWall"
    assert unreal_mrq.template_for_text("A side by side comparison of the two pilots.") == "T_ComparisonLadder"
    assert unreal_mrq.template_for_text("The receipt is the proof.") == "T_ProofScreen"


def test_render_failure_fixes_once_then_gives_up():
    state = {"n": 0, "longer": False}
    logs = []

    def render(note):
        state["n"] += 1
        if state["n"] == 1:
            note("unreal_mrq: TIMEOUT after 600s")
            return None
        return "ok" if state["longer"] else None

    def apply(_blob):
        state["longer"] = True

    out = render_repair.run_rung(
        render, logs.append,
        [{"name": "relaunch", "match": lambda blob: "TIMEOUT" in blob and not state["longer"], "apply": apply}],
        lambda result: result == "ok")
    assert out == "ok"
    assert any("fix verified: relaunch" in line for line in logs)

    logs.clear()
    out = render_repair.run_rung(
        lambda note: note("template level missing") or None,
        logs.append,
        [{"name": "nope", "match": lambda blob: False, "apply": lambda _b: None}],
        lambda result: False)
    assert out is None
    assert any("not fixable" in line for line in logs)
