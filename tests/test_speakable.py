"""Narration text for the voice (owner, 2026-11-20): dashes never glue words, decimals never split, the voice says "demos" right."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from studio import tts, render  # noqa: E402


def test_dashes_keep_words_apart():
    t = "not robotics—finally, a job. Not just a crunch—it’s structural. capacity—robotics is starved"
    out = tts.ascii_text(t)
    for glued in ("roboticsfinally", "crunchit", "capacityrobotics"):
        assert glued not in out
    assert "robotics, finally" in out and "crunch, it's" in out


def test_a_decimal_is_never_a_sentence_end():
    parts = render.chunks("The $3.5 billion is just the first tranche. Then 2.5 more.", limit=6)
    assert parts[0] == "The $3.5 billion is just the first tranche." and parts[1] == "Then 2.5 more."


def test_the_voice_gets_demoes_and_captions_keep_demos():
    assert "demoes" in tts.speakable("Those demos are built on deep learning")
    assert "demos" in tts.ascii_text("Those demos are built on deep learning")
    assert "Optimist" in tts.speakable("Optimus", {"pronounce": {"Optimus": "Optimist"}})   # settings can add more


def test_own_footage_has_no_copyright_caps(tmp_path):
    """owner, 2026-11-20: his own video's footage is used freely (the 6-clip / 8-second caps are for other people's videos)."""
    import json
    from studio import source_visuals as sv
    (tmp_path / "src").mkdir(); (tmp_path / "src" / "meta.json").write_text(json.dumps({"own_footage": True}), encoding="utf-8")
    assert sv.own(tmp_path)
    assert sv.max_uses({"_own_footage": True}) > 1000 and sv.clip_secs({"_own_footage": True}) == 30.0
    assert sv.max_uses({}) == 6 and sv.clip_secs({}) == 8.0          # someone else's video keeps the caps
