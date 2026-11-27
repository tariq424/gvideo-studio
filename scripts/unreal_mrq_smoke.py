"""Headless MRQ smoke. Prints RUNG_FIRED unreal_mrq when find_unreal() resolves an editor.

A missing editor prints a WARNING and exits 2. This rung does not report blender_fallback.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from studio import unreal_mrq  # noqa: E402

fired = unreal_mrq.smoke()
print("SMOKE_RUNG", fired, flush=True)
sys.exit(0 if fired == "unreal_mrq" else 2)
