"""Single shared visual rung registry for Shorts, explainers, interviews, and QC repair."""
SHARED_RUNG_NAMES = (
    "stock", "meta_chain", "ai_image", "chatgpt_chain", "unreal", "blender", "video",
    "cloud", "marble", "local_i2v_own_frame", "wangp", "static_image",
    "studio3d", "physics", "motion", "hyperframes", "threejs", "card",
)
TERMINAL_FLOOR = ("illustration", "silhouette", "card")
def build_registry(handlers: dict):
    return [(n, handlers[n]) for n in SHARED_RUNG_NAMES if n in handlers]
def short_order(own_footage: bool = False):
    base = [n for n in SHARED_RUNG_NAMES if n != "card"]
    return (["source"] + base + ["card"]) if own_footage else (base + ["card"])
