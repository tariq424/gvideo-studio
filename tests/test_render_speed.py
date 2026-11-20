"""Render speed (owner, 2026-11-19): long videos render in parallel chunks, finished chunks survive a resume, temp copies are
cleaned up, and slow jobs are reported."""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from studio import explainer, media  # noqa: E402


def test_only_long_videos_are_split():
    assert explainer.parallel_parts({"render_parallel": 3}, 600) == 3
    assert explainer.parallel_parts({}, 600) == 3                      # on by default
    assert explainer.parallel_parts({"render_parallel": 3}, 100) == 1                      # a Short-length video: bundling would cost more
    assert explainer.parallel_parts({"render_parallel": 1}, 3000) == 1


def test_finished_chunks_are_reused_and_joined_exactly(tmp_path):
    job = tmp_path / "job"; pub = job / "build" / "public"; pub.mkdir(parents=True)
    (pub / "teaser.json").write_text('{"beats": []}', encoding="utf-8")
    props = pub / "teaser_render.json"; props.write_text("{}", encoding="utf-8")
    total, k = 9.0, 3
    import hashlib
    sig = hashlib.sha1(props.read_bytes() + (pub / "teaser.json").read_bytes()).hexdigest()[:16]
    cdir = job / "build" / "_chunks"; cdir.mkdir()
    (cdir / "manifest.json").write_text(json.dumps({"sig": sig, "k": k, "frames": 270}), encoding="utf-8")
    for i in range(k):   # three finished 3-second chunks: video + sample-exact audio
        media.ff("-f", "lavfi", "-i", f"color=c=0x{i * 60 + 40:02x}2050:s=320x180:r=30:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", cdir / f"c{i:02}.mp4")
        media.ff("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3", "-ac", 2, "-c:a", "pcm_s16le", cdir / f"c{i:02}.wav")
    logs = []
    out = job / "out.mp4"
    cmd = ["npx", "remotion", "render", "src/Root.tsx", "Teaser", str(out), f"--props={props}", "--concurrency=4"]
    explainer._render_chunked(job, {}, logs.append, lambda: False, tmp_path, out, total, k, cmd)   # would fail if it tried to render
    assert abs(media.dur(out) - 9.0) < 0.1
    assert any("3 of 3 chunk(s) already rendered" in m for m in logs)
    assert not cdir.exists()                                           # the chunks folder is removed once joined


def test_remotion_temp_copies_are_removed(tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    old = tmp_path / "remotion-webpack-bundle-old"; old.mkdir(); (old / "a.bin").write_bytes(b"x" * 10)
    os.utime(old, (time.time() - 7200, time.time() - 7200))
    new = tmp_path / "remotion-webpack-bundle-new"; new.mkdir(); (new / "a.bin").write_bytes(b"x" * 10)
    other = tmp_path / "something-else"; other.mkdir()
    explainer.cleanup_remotion_temp(time.time() - 600, lambda m: None)
    assert old.exists() and not new.exists() and other.exists()         # only this render's copies, nothing else
