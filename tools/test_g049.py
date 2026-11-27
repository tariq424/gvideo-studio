import sys
from pathlib import Path
sys.path.insert(0, r"F:\code\gvideo-studio\app")
from studio import rungs, source_visuals, qc_repair, short, ladder
assert "blender" in rungs.SHARED_RUNG_NAMES and "unreal" not in rungs.SHARED_RUNG_NAMES
j = Path(r"F:\videos\video_studio\jobs\20261124_232033_gsmoke")
assert source_visuals.own(j)
ex = source_visuals.make_many({}, j, j / "short", {"id": "b_smoke", "vo": "ChatGPT Co-Pilot Gemini nail his face while Grok refuses and Google Flow misses"}, print, count=2, used_starts=[0.0])
print("PROVENANCE_OWN_EXTRA_SLOTS:", len(ex), [e.get("timestamp") for e in ex])
assert len(ex) >= 2
