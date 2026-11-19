"""Length policy (owner, 2026-11-19): Auto = ~15 min; a set length is a minimum; longer only when essential information would be
dropped (the outline must say what), never padded."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from studio import writer  # noqa: E402

WPM = 155


def secs(*budgets): return [{"title": f"s{i}", "points": ["p"], "words": b} for i, b in enumerate(budgets)]


def test_auto_is_15_minutes_and_may_go_longer():
    assert writer.length_target({}, 0) == (15.0, True, True)
    assert writer.length_target({"auto_target_min": 12}, 0)[0] == 12
    assert writer.length_target({}, 40) == (40.0, True, False)            # no cap on a set length
    assert writer.length_target({"length_flex": False}, 20) == (20.0, False, False)


def test_short_outline_is_raised_to_the_minimum():
    s = secs(300, 300, 300)
    assert writer.size_budgets(s, 2325, True, "", print, WPM) >= 2300


def test_longer_only_with_an_essential_reason():
    long = (2000, 2000, 2000)
    assert writer.size_budgets(secs(*long), 2325, True, "", print, WPM) <= 2400           # no reason: back to ~15 min
    logs = []
    kept = writer.size_budgets(secs(*long), 2325, True, "the safety recall and its cause", logs.append, WPM)
    assert kept == 6000 and "safety recall" in logs[0]                                     # reason: kept, and said why
    assert writer.size_budgets(secs(*long), 2325, False, "a reason", print, WPM) <= 2400  # going longer switched off


def test_fit_length_expands_a_short_script_with_real_content():
    plan = {"sections": [{"title": "a", "vo": "word " * 200, "visuals": [{}] * 3},
                         {"title": "b", "vo": "word " * 900, "visuals": [{}] * 3}]}
    budgets = [{"words": 1000, "points": ["x"]}, {"words": 1000, "points": ["y"]}]
    asked = []

    class L:
        def chat_json(self, system, prompt, max_tokens=0, check=None):
            asked.append(prompt); j = {"vo": "word " * 1000}
            if check: check(j)
            return j
    writer.fit_length(L(), plan, budgets, 2000, {"seconds_per_visual": 6, "wpm": WPM}, print)
    assert sum(len(x["vo"].split()) for x in plan["sections"]) >= 1880
    assert asked and "Expand it" in asked[0]
