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


class _LetterToSound:
    """g2p_en's neural letter-to-sound model (Apache-2.0) run straight from its local checkpoint20.npz with numpy.
    The narrator cache's English frontend raises on any word missing from its pronunciation dictionary ("humanoid",
    names, brands), which would stop almost every video. Importing g2p_en itself would make NLTK download data, so only
    its predictor is used here: offline, no download."""
    PH = ["<pad>", "<unk>", "<s>", "</s>"] + [
        "AA0", "AA1", "AA2", "AE0", "AE1", "AE2", "AH0", "AH1", "AH2", "AO0", "AO1", "AO2", "AW0", "AW1", "AW2", "AY0", "AY1",
        "AY2", "B", "CH", "D", "DH", "EH0", "EH1", "EH2", "ER0", "ER1", "ER2", "EY0", "EY1", "EY2", "F", "G", "HH", "IH0", "IH1",
        "IH2", "IY0", "IY1", "IY2", "JH", "K", "L", "M", "N", "NG", "OW0", "OW1", "OW2", "OY0", "OY1", "OY2", "P", "R", "S",
        "SH", "T", "TH", "UH0", "UH1", "UH2", "UW", "UW0", "UW1", "UW2", "V", "W", "Y", "Z", "ZH"]

    def __init__(self, npz):
        import numpy as np
        self.np = np; v = np.load(npz)
        self.g2i = {g: i for i, g in enumerate(["<pad>", "<unk>", "</s>"] + list("abcdefghijklmnopqrstuvwxyz"))}
        self.v = {k: v[k] for k in v.files}

    def _cell(self, x, h, p):
        np, v = self.np, self.v
        a = x @ v[p + "_w_ih"].T + v[p + "_b_ih"]; b = h @ v[p + "_w_hh"].T + v[p + "_b_hh"]
        k = a.shape[-1] * 2 // 3
        r, z = np.split(1 / (1 + np.exp(-(a[:, :k] + b[:, :k]))), 2, -1)
        n = np.tanh(a[:, k:] + r * b[:, k:])
        return (1 - z) * n + z * h

    def __call__(self, word):
        np, v = self.np, self.v
        ids = [self.g2i.get(c, 1) for c in word] + [self.g2i["</s>"]]
        x = np.take(v["enc_emb"], np.array([ids]), axis=0)
        h = np.zeros((1, v["enc_w_hh"].shape[-1]), np.float32)
        for t in range(len(ids)): h = self._cell(x[:, t, :], h, "enc")
        dec, out = np.take(v["dec_emb"], [2], axis=0), []
        for _ in range(20):
            h = self._cell(dec, h, "dec")
            p = int((h @ v["fc_w"].T + v["fc_b"]).argmax())
            if p == 3: break
            out.append(self.PH[p]); dec = np.take(v["dec_emb"], [p], axis=0)
        return out


def _oov_fallback(cache):
    """Words missing from the local dictionary get the letter-to-sound model's pronunciation instead of stopping the job."""
    import re
    import melo.text.english as en
    npz = cache / "deps" / "g2p_en" / "checkpoint20.npz"
    if not npz.is_file(): return
    lts, orig, seen = _LetterToSound(npz), en._g2p, set()

    def g2p(word):
        try: return orig(word)
        except ValueError:
            w = re.sub(r"[^a-z]", "", word.lower())
            if not w: return list(word)
            if w not in seen: seen.add(w); print(f"pronounced by letter-to-sound: {w}", flush=True)
            return lts(w) or list(w)
    en._g2p = g2p


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
    _oov_fallback(cache)
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
