"""owner, 2026-11-18: the explainer opens in the editor, explainer length set by content (Auto), a designed thumbnail for the
explainer, researched commentary before each joke, a real hook."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from studio import writer, config, websearch  # noqa: E402


class FakeLLM:
    name = "fake"
    def __init__(self, answers): self.answers = list(answers); self.prompts = []
    def trace(self, *_): return self
    def chat_json(self, system, prompt, max_tokens=0, check=None):
        self.prompts.append(prompt)
        for a in list(self.answers):
            self.answers.remove(a)
            try:
                if check: check(a)
                return a
            except ValueError:
                continue
        raise RuntimeError("no valid answer")


def test_the_editor_opens_remotion_explainers_by_default():
    assert config.DEFAULTS["post_edit_v2"] is True


def test_auto_length_follows_the_source_not_a_slot():
    cfg = {"wpm": 150}
    assert writer.auto_words(cfg, "word " * 1000) == int(writer.AUTO_MIN * 150)          # a short source: the floor
    assert writer.auto_words(cfg, "word " * 20000) == 8000                               # 40% of a long source
    assert writer.auto_words(cfg, "word " * 100000) == int(writer.AUTO_MAX * 150)        # the sanity ceiling


def test_commentary_uses_only_facts_from_the_research(monkeypatch):
    monkeypatch.setattr(websearch, "light", lambda cfg, q, log=None, llm=None: (["Haber-Bosch: the process uses about 1 percent of world energy."], "wikipedia"))
    llm = FakeLLM([{"query": "Haber-Bosch process"},
                   {"commentary": "The fertilizer process eats 7 percent of the world's energy. Honestly, that is the real prize."},   # 7 is invented
                   {"commentary": "Making fertilizer already uses about 1 percent of the world's energy. Honestly, that is the real prize here."}])
    t = writer.research_commentary(llm, {}, "Quantum", "A quantum machine could model a fertilizer catalyst.", lambda *_: None, "long", "s4")
    assert "1 percent" in t and "7 percent" not in t


def test_no_research_means_no_commentary(monkeypatch):
    monkeypatch.setattr(websearch, "light", lambda cfg, q, log=None, llm=None: ([], ""))
    llm = FakeLLM([{"query": "x y z"}])
    assert writer.research_commentary(llm, {}, "T", "Some beat.", lambda *_: None, "short", "b1") == ""


def test_a_short_beat_gets_its_commentary_just_before_its_joke(monkeypatch):
    monkeypatch.setattr(writer, "research_commentary", lambda *a, **k: "Fun fact goes here, and it matters.")
    import studio.llm as L
    class Ctx:
        def __init__(self, *a, **k): pass
        def __enter__(self): return FakeLLM([])
        def __exit__(self, *a): return False
    monkeypatch.setattr(L, "LLM", Ctx)
    plan = {"title": "T", "sections": [{"id": "b0", "vo": "Kaku says silicon is dying. Guess the abacus wins again.",
                                         "joke": "Guess the abacus wins again."},
                                        {"id": "b1", "vo": "No joke in this one yet."}]}
    writer.short_commentary({}, plan, lambda *_: None, budget=500)
    assert plan["sections"][0]["vo"] == "Kaku says silicon is dying. Fun fact goes here, and it matters. Guess the abacus wins again."
    assert plan["sections"][1]["vo"].endswith("Fun fact goes here, and it matters.")
    plan["sections"][1]["commentary"] = "x"
    tight = {"title": "T", "sections": [{"id": "b0", "vo": "word " * 100}]}
    writer.short_commentary({}, tight, lambda *_: None, budget=110)                       # would pass 3:00: skipped
    assert "Fun fact" not in tight["sections"][0]["vo"]


def test_the_explainer_hook_is_never_a_recap():
    import inspect
    src = inspect.getsource(writer.write_plan)
    assert "the first sentence is the single most surprising claim" in src and "never a recap" in src


def test_the_explainer_gets_a_designed_wide_thumbnail(tmp_path):
    PIL = pytest.importorskip("PIL")
    from PIL import Image
    from studio import thumb916
    job = tmp_path / "20261118_000000"; pub = job / "build" / "public" / "img"; pub.mkdir(parents=True)
    Image.new("RGB", (1920, 1080), (40, 60, 90)).save(pub / "a.jpg")
    (job / "build" / "public" / "teaser.json").write_text(json.dumps({"beats": [{"id": "s0", "bgs": [{"type": "image", "src": "img/a.jpg"}]}]}), encoding="utf-8")
    (job / "state.json").write_text("{}", encoding="utf-8")
    plan = {"title": "Quantum Supremacy and the Multiverse That Runs Your iPhone", "sections": [{"visuals": []}]}
    out = thumb916.make_wide(job, plan, {}, lambda *_: None)
    im = Image.open(out)
    assert im.size == (1280, 720) and out.name == "thumbnail_16_9.png"
    lines, _ = thumb916._wide_lines(job, plan, {})
    assert " ".join(lines) == plan["title"].upper()                                     # the whole title, never cut off


def test_a_short_paired_with_an_explainer_is_fitted_to_three_minutes():
    # 3:04 made YouTube file the Short as a regular video, and Studio has no Related-video field there: no link to the explainer
    from studio import short
    assert short.fit3({}, "both") == (True, short.MAX_SPEED)          # paired: sped up first, a beat dropped only past that
    assert short.fit3({}, "short") == (False, 1.0)                    # alone: runs long, as the owner chose on 2026-10-23
    assert short.fit3({"short_fit_3min": True}, "short") == (True, 1.0)
    assert short.fit3({"short_fit_3min_paired": False}, "both") == (False, 1.0)


def test_no_video_ends_by_saying_where_it_came_from():
    import inspect
    from studio import short, render
    assert "SUMMARIZED FROM" not in inspect.getsource(short)
    assert '"credit": ""' in inspect.getsource(render.do_charts)
    tsx = (Path(__file__).resolve().parents[1] / "remotion" / "src" / "Graphics.tsx").read_text(encoding="utf-8")
    assert "{title || thumb || play ? <div" in tsx                   # no empty box on the end card


def test_research_waits_and_retries_when_rate_limited(monkeypatch):
    """Wikipedia 429 / DuckDuckGo 202 after a burst: wait and try again instead of giving up on the rest of the sections."""
    import requests
    from studio import websearch
    calls, waits = [], []
    class R:
        def __init__(self, code, text=""): self.status_code, self.text, self.headers = code, text, {}
    seq = [R(429), R(429), R(200, "ok")]
    monkeypatch.setattr(requests, "get", lambda *a, **k: calls.append(1) or seq[len(calls) - 1])
    r = websearch._polite_get("https://en.wikipedia.org/w/api.php", {}, {}, sleep=waits.append)
    assert r.status_code == 200 and len(calls) == 3 and any(w >= 4 for w in waits)
    calls.clear(); seq[:] = [R(202), R(200, "no results here"), R(200, "<a class=\"result__snippet\">x</a>")]
    r = websearch._polite_get("https://html.duckduckgo.com/html/", {}, {}, ok=lambda r: "result__snippet" in r.text, sleep=waits.append)
    assert "result__snippet" in r.text and len(calls) == 3
    calls.clear(); seq[:] = [R(404)]
    try: websearch._polite_get("https://x.org", {}, {}, sleep=waits.append); assert False
    except RuntimeError as e: assert "404" in str(e) and len(calls) == 1


def test_every_search_is_deepseek_only(monkeypatch):
    """owner, 2026-11-18: "for searches, we don't use duck duck... at all. we use deepseek with API key"."""
    from studio import websearch
    for name in ("_wikipedia", "_ddg", "_bing", "_copilot", "_google"):
        monkeypatch.setattr(websearch, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError("no web route may be asked")))
    monkeypatch.setattr(websearch, "ROUTES", {k: getattr(websearch, "_" + k if k != "duckduckgo" else "_ddg") for k in websearch.ROUTES})
    class L:
        def trace(self, n): return self
        def chat_json(self, sys_, prompt, max_tokens=0, check=None, web=False): return {"facts": [{"fact": "Gott and Li published the idea in 1998.", "url": "https://journals.aps.org/x"}, "short"]} if web else {}
        def __enter__(self): return self
        def __exit__(self, *a): return False
    got, route = websearch.light({}, "Gott Li universe", llm=L())
    assert route == "deepseek" and got == ["Gott and Li published the idea in 1998."]
    import studio.llm as llm_mod
    monkeypatch.setattr(llm_mod, "LLM", lambda cfg, log, stop=None: L())
    assert websearch.search({}, "Gott Li universe") == (["Gott and Li published the idea in 1998."], "deepseek")   # QC repair / interview path
