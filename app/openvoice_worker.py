"""OpenVoice tone-colour conversion worker (owner, 2026-11-09): the owner's own voice over a US-accent Kokoro reading.

Runs in its OWN env (app/ovenv: Python 3.11, CPU PyTorch, OpenVoice's light deps), never in Video Studio's env, like
kokoro_worker.py. OpenVoice's code is a clone of github.com/myshell-ai/OpenVoice in app/openvoice_src (imported from there,
not pip-installed: its setup.py pins 2023 versions that don't build on current Python). The V2 converter
(myshell-ai/OpenVoiceV2 on Hugging Face, converter/) is downloaded to app/ovmodels on first use.

    python openvoice_worker.py job.json

job.json: {"ref": "<recording of the owner, 30-60 s>", "device": "" | "cpu" | "cuda", "tau": 0.3,
           "items": [{"src": "<wav to convert>", "out": "<wav to write>"}]}
or        {"embed": "<recording>"}  - only make (and cache) the voice print, print its path.
The voice print of a recording is cached in app/ovmodels/se/ (keyed by the file and its time), so a clone is made once.
"""
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP / "openvoice_src"))
MODELS = APP / "ovmodels"
CKPT = MODELS / "converter"


def _converter(device):
    import torch
    if not (CKPT / "checkpoint.pth").exists():
        from huggingface_hub import hf_hub_download
        for f in ("converter/config.json", "converter/checkpoint.pth"):
            hf_hub_download("myshell-ai/OpenVoiceV2", f, local_dir=str(MODELS))
    import types   # no audio watermark: OpenVoice asks wavmark for a model at start-up; this stand-in hands it none
    sys.modules.setdefault("wavmark", types.SimpleNamespace(load_model=lambda: types.SimpleNamespace(to=lambda d: None)))
    from openvoice.api import ToneColorConverter
    dev = device or ("cuda:0" if torch.cuda.is_available() else "cpu")
    tcc = ToneColorConverter(str(CKPT / "config.json"), device=dev)
    tcc.load_ckpt(str(CKPT / "checkpoint.pth"))   # torch's safe loader (tensors only, weights_only by default)
    return tcc


def _chunks(wav_path, sr, out_dir, max_s=10.0):
    """The speech in a recording as <= max_s pieces (silences cut out) - what OpenVoice averages a voice print over."""
    import librosa, soundfile
    y, _ = librosa.load(str(wav_path), sr=sr)
    out, buf = [], []
    for a, b in librosa.effects.split(y, top_db=35):
        buf.extend(y[a:b])
        while len(buf) >= max_s * sr:
            f = Path(out_dir) / f"c{len(out):03}.wav"; soundfile.write(f, buf[:int(max_s * sr)], sr); out.append(str(f)); buf = buf[int(max_s * sr):]
    if len(buf) >= 1.0 * sr or not out:
        f = Path(out_dir) / f"c{len(out):03}.wav"; soundfile.write(f, buf or list(y), sr); out.append(str(f))
    return out


def voice_print(tcc, ref):
    import torch
    ref = Path(ref)
    key = hashlib.md5(f"{ref.resolve()}|{ref.stat().st_mtime}|{ref.stat().st_size}".encode()).hexdigest()[:16]
    se_f = MODELS / "se" / f"{key}.pth"
    if se_f.exists(): return torch.load(str(se_f), map_location=tcc.device), se_f
    se_f.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "ref.wav"
        import subprocess
        subprocess.run([_ffmpeg(), "-v", "error", "-y", "-i", str(ref), "-vn", "-ac", "1", "-ar", str(tcc.hps.data.sampling_rate), str(wav)], check=True)
        se = tcc.extract_se(_chunks(wav, tcc.hps.data.sampling_rate, td))
    torch.save(se.cpu(), str(se_f))
    return se, se_f


def _ffmpeg():
    for c in (APP / "env" / "Lib" / "site-packages" / "imageio_ffmpeg" / "binaries",):
        if c.is_dir():
            for f in c.glob("ffmpeg*.exe"): return str(f)
    return "ffmpeg"


def main():
    req = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    tcc = _converter(req.get("device") or "")
    if req.get("embed"):
        _, f = voice_print(tcc, req["embed"]); print(json.dumps({"voice_print": str(f)})); return
    tgt, _ = voice_print(tcc, req["ref"])
    tau = float(req.get("tau", 0.3))
    for it in req["items"]:
        src_se = tcc.extract_se([it["src"]])   # the reading voice's own print, taken from the reading itself
        tcc.convert(audio_src_path=it["src"], src_se=src_se, tgt_se=tgt, output_path=it["out"], tau=tau)
        print("converted", it["out"], flush=True)


if __name__ == "__main__":
    main()
