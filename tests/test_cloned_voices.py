"""The owner's own voices as narrators (2026-11-19): choice kept, no silent substitution, memory guard, safe text splits."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from studio import tts  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "app"


def test_engines_are_local_and_sized_by_their_own_pace():
    for e in ("my_original", "my_american"):
        cfg = {"tts_engine": e}
        assert tts.engine(cfg) == e and tts.is_local(cfg)
        assert tts.wpm(cfg) == tts.CLONED[e]["wpm"]
    assert tts.wpm({"tts_engine": "my_original", "my_original_wpm": 120}) == 120
    assert tts.engine({"tts_engine": "nonsense"}) == "wanGP"


def test_a_failed_own_voice_never_falls_back_to_another_voice(monkeypatch):
    def boom(*a, **k): raise RuntimeError("VoiceStudio down")
    monkeypatch.setattr(tts, "cloned_batch", boom)
    monkeypatch.setattr(tts, "kokoro_batch", lambda *a, **k: pytest.fail("Kokoro must not read an own-voice job"))
    with pytest.raises(RuntimeError, match="VoiceStudio down"):
        tts.try_kokoro({"tts_engine": "my_original", "tts_fallback": True}, [("hi", "x.wav")], print, "t", lambda: False)


def test_own_voice_routes_to_the_cloned_batch(monkeypatch):
    seen = []
    monkeypatch.setattr(tts, "cloned_batch", lambda cfg, items, *a, **k: seen.append(tts.engine(cfg)))
    assert tts.try_kokoro({"tts_engine": "my_american"}, [("hi", "x.wav")], print, "t", lambda: False)
    assert seen == ["my_american"]


def test_long_text_is_split_between_sentences_and_never_cut():
    text = "A short sentence here. " * 400
    takes = tts._takes(text)
    assert all(len(t) <= 3800 for t in takes) and len(takes) > 1
    assert " ".join(takes).split() == text.split()
    assert tts._takes("One line.") == ["One line."]


def test_low_memory_waits_then_stops_with_what_to_close(monkeypatch):
    monkeypatch.setattr(tts, "memory_headroom", lambda: (1.0, 2.0))
    monkeypatch.setattr(tts, "memory_hogs", lambda n=5: "chrome.exe 9.0 GB")
    monkeypatch.setattr(tts.time, "sleep", lambda s: None)
    logs = []
    with pytest.raises(RuntimeError, match="Low resources.*chrome.exe"):
        tts.wait_for_memory(6, 12, logs.append, "My Voice - Original Accent", wait=0)
    assert any("LOW RESOURCES" in x for x in logs)


def test_enough_memory_passes_at_once(monkeypatch):
    monkeypatch.setattr(tts, "memory_headroom", lambda: (20.0, 50.0))
    tts.wait_for_memory(6, 12, pytest.fail, "x", wait=0)


def test_job_keeps_its_voice_on_resume():
    src = (APP / "studio" / "pipeline.py").read_text(encoding="utf-8")
    for line in ('if tts_engine in ("kokoro", "wanGP", "my_original", "my_american"): j.save(tts_engine=tts_engine)',
                 'if s.get("tts_engine") in ("kokoro", "wanGP", "my_original", "my_american"): cfg["tts_engine"] = s["tts_engine"]'):
        assert line in src


def test_unknown_words_get_a_pronunciation_offline():
    npz = Path(r"F:\audio-transcriber\.openvoice-test\deps\g2p_en\checkpoint20.npz")
    if not npz.is_file(): pytest.skip("narrator cache not on this PC")
    sys.path.insert(0, str(APP))
    import cloned_voice_worker as w
    phones = w._LetterToSound(npz)("humanoid")
    assert phones[:3] == ["HH", "Y", "UW1"] and all(p in w._LetterToSound.PH for p in phones)
