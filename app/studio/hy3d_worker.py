"""Run with the Hunyuan3D-2 venv python (not imported by the app):
    <hy3d venv python> hy3d_worker.py <job.json>
job.json: {"cut": {name: [src, out]}}  (cut-outs only), or {"views": {"front": png, "left": png?, "back": png?, "right": png?}, "out": glb, "steps": 50, "octree": 384,
           "faces": 200000, "seed": 7}
One view -> Hunyuan3D-2 (single image); two or more -> Hunyuan3D-2mv (multi-view). Backgrounds are removed when the image
has no alpha. Prints "SHAPE <faces> <secs>" on success."""
import json, sys, time
import torch
from PIL import Image
from hy3dgen.shapegen import Hunyuan3DDiTFlowMatchingPipeline, FloaterRemover, DegenerateFaceRemover, FaceReducer

J = json.load(open(sys.argv[1], encoding="utf-8"))
if J.get("cut"):   # {"cut": {name: [src, out]}}: the object cut out of each photo and cropped to it (no GPU, no model)
    from hy3dgen.rembg import BackgroundRemover
    rb = BackgroundRemover()
    for name, (src, out) in J["cut"].items():
        im = Image.open(src)
        im = rb(im.convert("RGB")) if (im.mode != "RGBA" or im.getextrema()[3][0] == 255) else im.convert("RGBA")
        bb = im.split()[-1].point(lambda v: 255 if v > 40 else 0).getbbox()
        (im.crop(bb) if bb else im).save(out); print("CUT", name, im.size, bb, flush=True)
    sys.exit(0)
views = {}
rb = None
for k, p in J["views"].items():
    im = Image.open(p)
    if im.mode != "RGBA" or im.getextrema()[3][0] == 255:
        if rb is None:
            from hy3dgen.rembg import BackgroundRemover
            rb = BackgroundRemover()
        im = rb(im.convert("RGB"))
    views[k] = im.convert("RGBA")
multi = len(views) > 1
if multi:
    pipe = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained("tencent/Hunyuan3D-2mv", subfolder="hunyuan3d-dit-v2-mv", variant="fp16")
    image = views
else:
    pipe = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained("tencent/Hunyuan3D-2", subfolder="hunyuan3d-dit-v2-0", variant="fp16")
    image = next(iter(views.values()))
t = time.time()
kw = dict(num_inference_steps=int(J.get("steps", 50)), octree_resolution=int(J.get("octree", 384)), num_chunks=20000,
          generator=torch.manual_seed(int(J.get("seed", 7))), output_type="trimesh")
try:
    mesh = pipe(image=image, **kw)[0]
except Exception as e:
    if not multi or "right" not in views: raise
    print("4 views refused:", str(e)[:120], "- using front / left / back", flush=True)
    image = {k: v for k, v in views.items() if k != "right"}
    mesh = pipe(image=image, **kw)[0]
mesh = FloaterRemover()(mesh); mesh = DegenerateFaceRemover()(mesh); mesh = FaceReducer()(mesh, max_facenum=int(J.get("faces", 200000)))
mesh.export(J["out"])
print("SHAPE", len(mesh.faces), f"{time.time() - t:.0f}", flush=True)
