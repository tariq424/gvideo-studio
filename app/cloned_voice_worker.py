"""My Voice - American Accent: MeloTTS U.S. English reads each part, OpenVoice V2 turns it into the owner's voice.

Same models and settings as the American Narrator app (F:/audio-transcriber/american-narrator/render.py), but loaded ONCE for
the whole batch (render.py reloads them per call, ~110 times for a long video). Runs in the VoiceStudio VoxCPM2 Python
environment (CUDA PyTorch) with the narrator's local model cache; nothing is downloaded. Exits when done, so the GPU is freed.

Usage: python cloned_voice_worker.py request.json
request = {"cache": ".openvoice-test folder", "ref": "reference .wav (mono)", "speed": 0.94, "tau": 0.12,
           "items": [{"text": "...", "out": "raw .wav to write"}]}
"""
import json
import os
import sys
import textwrap
from pathlib import Path


def main():
    req = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    cache = Path(req["cache"])
    models = cache / "models"
    for name in ("converter/config.json", "converter/checkpoint.pth", "converter/en-us.pth",
                 "melo/config.json", "melo/checkpoint.pth", "bert/model.safetensors"):
        if not (models / name).is_file():
            sys.exit(f"Missing cached model file: {models / name}")
    sys.path.extend(str(cache / name) for name in ("deps", "MeloTTS", "OpenVoice"))
    import soundfile
    import torch
    import melo.utils
    from melo.api import TTS
    from melo.text.english_bert import get_bert_feature
    from openvoice.api import ToneColorConverter

    def english_features(text, word2ph, language, device):
        return get_bert_feature(text, word2ph, device)

    melo.utils.get_bert = english_features
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    torch.manual_seed(42)
    conv_dir = models / "converter"
    converter = ToneColorConverter(str(conv_dir / "config.json"), device=device, enable_watermark=False)
    converter.load_ckpt(str(conv_dir / "checkpoint.pth"))
    source = torch.load(conv_dir / "en-us.pth", map_location=device, weights_only=True)
    target = converter.extract_se(str(req["ref"]))
    melo_dir = models / "melo"
    tts = TTS(language="EN", device=device, config_path=str(melo_dir / "config.json"), ckpt_path=str(melo_dir / "checkpoint.pth"))
    speaker = tts.hps.data.spk2id["EN-US"]
    rate = converter.hps.data.sampling_rate
    speed, tau = float(req.get("speed", 0.94)), float(req.get("tau", 0.12))
    items = req["items"]
    print(f"American Accent on {device}: {len(items)} part(s)", flush=True)
    for i, item in enumerate(items, 1):
        out = Path(item["out"]); out.parent.mkdir(parents=True, exist_ok=True)
        base = out.with_suffix(".melo.wav"); tmp = out.with_suffix(".part.wav")
        chunks = [c for line in item["text"].splitlines() if line.strip()
                  for c in textwrap.wrap(line, width=480, break_long_words=False, break_on_hyphens=False)]
        with soundfile.SoundFile(tmp, "w", rate, 1, subtype="PCM_16") as wav:
            for chunk in chunks:
                tts.tts_to_file(chunk, speaker, str(base), speed=speed, quiet=True)
                wav.write(converter.convert(str(base), source, target, tau=tau))
        base.unlink(missing_ok=True)
        os.replace(tmp, out)
        print(f"part {i}/{len(items)}", flush=True)


if __name__ == "__main__":
    main()
