"""Run INSIDE Blender (not imported by the app):
    blender -b --factory-startup --python blender_studio3d.py -- <job.json>
job.json: {"model": path (.gltf/.glb/.fbx/.obj/.blend), "frames_dir": dir, "w": int, "h": int, "fps": int, "frames": int,
           "explode": bool, "samples": int, "paint": [r, g, b] | null,
           "paint_material": name | null, "yaw": degrees}
A real 3D model in a photo studio (cyclorama floor curving into the wall, softbox + key + rim lights), the camera orbiting it,
and - when the line is about how the thing is built - an exploded view: the parts fly apart from the centre, hold, then
come back together. Cycles on the GPU (OptiX, else CUDA, else the CPU). PNG frames -> frames_dir/0000.png ...
Prints "FRAME n secs" per frame and "DONE n" at the end (the app reads these)."""
import bpy, bmesh, json, math, os, sys, time, mathutils

V = mathutils.Vector
J = json.load(open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8"))
SIZE = 4.0   # the model is scaled so its longest side is 4 m (the studio is built for that)

bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene

# ------------------------------------------------------------------ import
p = J["model"]; ext = os.path.splitext(p)[1].lower()
if ext in (".gltf", ".glb"): bpy.ops.import_scene.gltf(filepath=p)
elif ext == ".fbx": bpy.ops.import_scene.fbx(filepath=p)
elif ext == ".obj": bpy.ops.wm.obj_import(filepath=p)
elif ext == ".blend":
    with bpy.data.libraries.load(p, link=False) as (src, dst): dst.objects = list(src.objects)
    for o in dst.objects:
        if o is not None and o.type in ("MESH", "EMPTY"): sc.collection.objects.link(o)
else: raise SystemExit(f"unsupported model type {ext}")

# meshes only, flattened (no parents: each part keeps its world placement), lights / cameras from the file dropped
for o in list(sc.objects):
    if o.type in ("LIGHT", "CAMERA"): bpy.data.objects.remove(o, do_unlink=True)
meshes = [o for o in sc.objects if o.type == "MESH" and o.data.polygons]
dg = bpy.context.evaluated_depsgraph_get()
for o in meshes:
    mw = o.matrix_world.copy(); o.parent = None; o.matrix_world = mw
for o in list(sc.objects):
    if o.type != "MESH" or o not in meshes: bpy.data.objects.remove(o, do_unlink=True)
if not meshes: raise SystemExit("model has no meshes")


def bbox(objs):
    lo, hi = V((1e9,) * 3), V((-1e9,) * 3)
    for o in objs:
        for c in o.bound_box:
            w = o.matrix_world @ V(c)
            lo = V(map(min, lo, w)); hi = V(map(max, hi, w))
    return lo, hi


# a single mesh with many loose pieces is split so it can explode (capped: a scanned mesh would shatter into dust)
if J.get("explode") and len(meshes) == 1:
    o = meshes[0]
    bm = bmesh.new(); bm.from_mesh(o.data)
    islands, seen = 0, set()
    for f in bm.faces:
        if f.index in seen: continue
        islands += 1; stack = [f]
        while stack and islands <= 400:
            x = stack.pop()
            if x.index in seen: continue
            seen.add(x.index); stack += [g for e in x.edges for g in e.link_faces if g.index not in seen]
    bm.free()
    if 3 <= islands <= 400:
        bpy.context.view_layer.objects.active = o; o.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT"); bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.separate(type="LOOSE"); bpy.ops.object.mode_set(mode="OBJECT")
        meshes = [x for x in sc.objects if x.type == "MESH"]

# normalise: longest side SIZE, centred on the turntable, standing on the floor
lo, hi = bbox(meshes)
s = SIZE / max(hi - lo)
T = mathutils.Matrix.Translation(V((-(lo.x + hi.x) / 2, -(lo.y + hi.y) / 2, -lo.z)))
S = mathutils.Matrix.Scale(s, 4)
R = mathutils.Matrix.Rotation(math.radians(float(J.get("yaw") or 0)), 4, "Z")   # a model that faces the wrong way
for o in meshes: o.matrix_world = R @ S @ T @ o.matrix_world
lo, hi = bbox(meshes); ctr = (lo + hi) / 2; dims = hi - lo
radius = dims.length / 2

# untextured parts get a neutral studio clay; an optional paint colour recolours the dominant material
def mat(name, col, rough, metal=0.0, coat=0.0):
    m = bpy.data.materials.new(name); m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*col, 1); b.inputs["Roughness"].default_value = rough; b.inputs["Metallic"].default_value = metal
    if coat and "Coat Weight" in b.inputs: b.inputs["Coat Weight"].default_value = coat
    return m
clay = None
for o in meshes:
    if not o.data.materials:
        clay = clay or mat("clay", (0.55, 0.56, 0.58), 0.45); o.data.materials.append(clay)
if J.get("paint"):
    area_of = {}
    for o in meshes:
        for poly in o.data.polygons:
            if poly.material_index < len(o.data.materials) and o.data.materials[poly.material_index]:
                mm = o.data.materials[poly.material_index].name; area_of[mm] = area_of.get(mm, 0) + poly.area
    pm = J.get("paint_material")
    if area_of or pm in bpy.data.materials:
        m = bpy.data.materials[pm] if pm in bpy.data.materials else bpy.data.materials[max(area_of, key=area_of.get)]
        b = next((n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None) if m.use_nodes else None
        if b:
            for l in list(b.inputs["Base Color"].links): m.node_tree.links.remove(l)
            b.inputs["Base Color"].default_value = (*J["paint"], 1)   # car paint: metallic with a clear coat
            b.inputs["Metallic"].default_value = 0.6; b.inputs["Roughness"].default_value = 0.30
            for nm, val in (("Coat Weight", 1.0), ("Coat Roughness", 0.03)):
                if nm in b.inputs: b.inputs[nm].default_value = val

# ------------------------------------------------------------------ studio
bpy.ops.mesh.primitive_plane_add(size=1)
cyc = bpy.context.active_object; cyc.name = "cyclorama"
bm = bmesh.new(); prof = []
for i in range(40):   # profile (y, z): a long floor, then a curve up into the wall behind
    t = i / 39
    if t < 0.7: prof.append((40 - t / 0.7 * 50, 0.0))
    else:
        a = (t - 0.7) / 0.3 * math.pi / 2
        prof.append((-10 - 4 * math.sin(a), 4 - 4 * math.cos(a)))
prof.append((-14, 20))
rows = [[bm.verts.new((x, y, z)) for y, z in prof] for x in (-30, 30)]
for i in range(len(prof) - 1): bm.faces.new((rows[0][i], rows[1][i], rows[1][i + 1], rows[0][i + 1]))
bm.to_mesh(cyc.data); bm.free()
for poly in cyc.data.polygons: poly.use_smooth = True
cyc.data.materials.append(mat("studio_floor", (0.62, 0.63, 0.65), 0.22))


def area(name, loc, rot, size, power, col=(1, 1, 1), sy=None):
    ld = bpy.data.lights.new(name, "AREA"); ld.energy = power; ld.color = col; ld.shape = "RECTANGLE"
    ld.size = size; ld.size_y = sy or size
    lo_ = bpy.data.objects.new(name, ld); lo_.location = loc; lo_.rotation_euler = [math.radians(a) for a in rot]
    sc.collection.objects.link(lo_)
area("top_softbox", (0, 0, 6.5), (0, 0, 0), 6, 2600, sy=3.5)
area("key", (4.5, 4.0, 3.0), (60, 0, 132), 3, 900)
area("rim_left", (-5.0, -3.0, 2.5), (70, 0, -60), 2.5, 700, (0.85, 0.9, 1.0))
area("strip_right", (5.5, -2.0, 1.4), (85, 0, 70), 0.6, 500, sy=5)
w = bpy.data.worlds.new("studio"); w.use_nodes = True
w.node_tree.nodes["Background"].inputs[0].default_value = (0.32, 0.33, 0.35, 1)
w.node_tree.nodes["Background"].inputs[1].default_value = 0.35
sc.world = w

cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); sc.collection.objects.link(cam); sc.camera = cam
cam.data.lens = 50; cam.data.dof.use_dof = True; cam.data.dof.aperture_fstop = 6.3

# ------------------------------------------------------------------ render settings (GPU is a preference: set here)
sc.render.engine = "CYCLES"
dev = "CPU"
try:
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for dt in ("OPTIX", "CUDA"):
        try:
            prefs.compute_device_type = dt; prefs.get_devices()
            if any(d.type == dt for d in prefs.devices): dev = dt; break
        except Exception: pass
    for d in prefs.devices: d.use = d.type == dev
except Exception: pass
sc.cycles.device = "GPU" if dev != "CPU" else "CPU"
sc.cycles.samples = int(J.get("samples", 32)); sc.cycles.use_adaptive_sampling = True; sc.cycles.use_denoising = True
try: sc.cycles.denoiser = "OPTIX" if dev == "OPTIX" else "OPENIMAGEDENOISE"
except Exception: pass
sc.render.use_persistent_data = True
sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = int(J["w"]), int(J["h"]), 100
sc.render.image_settings.file_format = "PNG"
sc.view_settings.view_transform = "AgX"
sc.view_settings.exposure = float(J.get("exposure") or 0)
try: sc.view_settings.look = "AgX - Medium High Contrast"
except Exception: pass
print("DEVICE", dev, "PARTS", len(meshes), flush=True)

# ------------------------------------------------------------------ animation
N, FPS = int(J["frames"]), int(J.get("fps", 24))
clamp = lambda x, a=0.0, b=1.0: max(a, min(b, x))
ease = lambda x: 4 * x ** 3 if x < .5 else 1 - (-2 * x + 2) ** 3 / 2
explode = bool(J.get("explode")) and len(meshes) >= 3
base = {o.name: o.matrix_world.translation.copy() for o in meshes}
offs = {}
if explode:   # each part flies outward from the centre, further the further out it sits; a small lift keeps them off the floor
    for o in meshes:
        lo_o, hi_o = bbox([o]); c = (lo_o + hi_o) / 2
        d = c - ctr; d.z *= 0.6
        if d.length < 1e-3: d = V((0, 0, 1))
        offs[o.name] = d.normalized() * (0.22 * SIZE * (0.4 + d.length / max(radius, 1e-3))) + V((0, 0, 0.25 * SIZE * clamp(d.length / radius)))
# frame width: fit the whole model (and its exploded spread) with a margin, using the narrower field of view
# fit the height and the width separately (a tall thing - a wind turbine, a tower - was cut off by a width-only fit)
tl = 18 / cam.data.lens                                  # tan of half the field across the frame's longer side (sensor 36 mm)
wide_t, tall_t = (tl, tl * J["h"] / J["w"]) if J["w"] >= J["h"] else (tl * J["w"] / J["h"], tl)
spread = 1.0 + (0.3 if explode else 0.0)
need_w = (max(dims.x, dims.y) / 2) * spread / wide_t + max(dims.x, dims.y) / 2
need_h = (dims.z / 2) * spread / tall_t + max(dims.x, dims.y) / 2
half_fov = math.atan(tall_t)
dist_old = radius * spread / math.tan(half_fov) * 0.92   # the earlier fit (cars, aircraft: unchanged)
K = max(1.0, need_h / max(need_w, 1e-3))                  # >1 only for a thing taller than the frame allows
dist = max(dist_old, need_h * 1.05) if K > 1 else dist_old
sweep = min(float(J.get("orbit") or 150.0), 26.0 * N / FPS)   # a slow orbit: ~26 deg a second, at most 150 deg (or "orbit")
az0 = float(J.get("az") or 0) - sweep / 2 if J.get("orbit") else -40.0   # a model with one real side: the orbit stays on it
if J.get("orbit_dir", 1) < 0: az0, sweep = az0 + sweep, -sweep
az1 = az0 + sweep
cyc.rotation_euler = (0, 0, -math.radians((az0 + az1) / 2))   # the wall stays behind the model through the orbit


def explode_u(f):
    if not explode: return 0.0
    u = f / max(N - 1, 1)
    return ease(clamp((u - 0.18) / 0.27)) if u < 0.62 else 1 - ease(clamp((u - 0.70) / 0.25))


# ------------------------------------------------------------------ shot list (owner, 2026-11-13: "close-up, dolly, from above,
# below, pan, zoom, wide angle"): hard cuts between camera moves, all placed from the model's own box (front +Y, left -X)
def shots():
    sx = 1 if int(J.get("side") or -1) > 0 else -1          # a model with one real side keeps the camera on it
    A = lambda a: a if sx < 0 else -a                        # az 0 = in front, -90 = the model's left
    zm = lo.z + dims.z * 0.45
    C = V((ctr.x, ctr.y, zm)); R = radius
    front = V((ctr.x, hi.y - dims.y * 0.06, lo.z + dims.z * 0.35))
    rear = V((ctr.x, lo.y + dims.y * 0.08, hi.z - dims.z * 0.25))
    def P(az, el, d, t):
        d *= K   # a tall thing: every shot stands further back
        a, e = math.radians(az), math.radians(el)
        return t + V((math.cos(e) * math.sin(a), math.cos(e) * math.cos(a), math.sin(e))) * d
    wing = dims.x / 2
    L = [
        ("wide establishing", (P(A(-35), 8, 2.7 * R, C), C, 24), (P(A(-58), 11, 2.5 * R, C), C, 24)),
        ("dolly-in close-up", (P(A(-30), 5, 2.0 * R, front), front, 50), (P(A(-24), 3, 0.42 * R, front), front, 50)),
        ("tracking pan", (V((sx * (wing + 0.35 * R), hi.y - dims.y * 0.15, zm + 0.1 * R)), V((ctr.x, hi.y - dims.y * 0.15, zm)), 45),
                         (V((sx * (wing + 0.35 * R), lo.y + dims.y * 0.2, zm + 0.1 * R)), V((ctr.x, lo.y + dims.y * 0.2, zm)), 45)),
        ("zoom on the tail", (P(A(-150), 7, 2.6 * R, rear), rear, 22), (P(A(-150), 7, 2.6 * R, rear), rear, 95)),
        ("overhead", ("top", 0.0), ("top", 25.0)),
        ("low angle", (V((sx * 0.38 * dims.x, ctr.y + 0.42 * dims.y, lo.z + 0.05)), V((sx * 0.12 * dims.x, ctr.y - 0.1 * dims.y, hi.z * 0.55)), 18),
                      (V((sx * 0.32 * dims.x, ctr.y + 0.30 * dims.y, lo.z + 0.05)), V((sx * 0.10 * dims.x, ctr.y - 0.2 * dims.y, hi.z * 0.55)), 18)),
        ("hero push-in", (P(A(-42), 3, 2.0 * R, C), C, 32), (P(A(-47), 5, 1.55 * R, C), C, 32)),
    ]
    k = max(1, min(len(L), int(N / FPS / 1.9)))           # about two seconds a shot at least
    pick = L[:k - 1] + [L[-1]] if k < len(L) else L       # always end on the hero shot
    return pick, C, R


SHOTS = bool(J.get("shots")) and not explode
if SHOTS:
    plan, C0, R0 = shots()
    cuts = [round(i * N / len(plan)) for i in range(len(plan) + 1)]
    cyc.rotation_euler = (0, 0, -math.radians(-45 if int(J.get("side") or -1) <= 0 else 45))
    cam.data.dof.aperture_fstop = 4.0
    if J.get("cuts_out"): json.dump({"cuts": cuts[:-1], "names": [p[0] for p in plan], "fps": FPS}, open(J["cuts_out"], "w"))
    print("SHOTS", " | ".join(p[0] for p in plan), flush=True)


def shot_cam(f):
    i = max(j for j in range(len(plan)) if cuts[j] <= f)
    name, a, b = plan[i]
    u = ease(clamp((f - cuts[i]) / max(cuts[i + 1] - cuts[i] - 1, 1)))
    if a[0] == "top":   # straight down from above, turning slowly (a camera looks down -Z by default)
        cam.location = V((C0.x, C0.y, hi.z + 2.1 * R0 * K)); cam.data.lens = 35
        cam.rotation_euler = (0, 0, math.radians(lerp_(a[1], b[1], u)))
        cam.data.dof.focus_distance = cam.location.z - C0.z
        return
    p0, t0, l0 = a; p1, t1, l1 = b
    cam.location = p0.lerp(p1, u); tgt = t0.lerp(t1, u); cam.data.lens = lerp_(l0, l1, u)
    cam.rotation_euler = (tgt - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam.data.dof.focus_distance = (tgt - cam.location).length


lerp_ = lambda a, b, u: a + (b - a) * u
for f in range(N):
    u = f / max(N - 1, 1)
    k = explode_u(f)
    if SHOTS:
        shot_cam(f)
        for key in ("location", "rotation_euler"): cam.keyframe_insert(key, frame=f)
        cam.data.keyframe_insert("lens", frame=f); cam.data.keyframe_insert("dof.focus_distance", frame=f)
        continue
    for o in meshes:
        o.location = base[o.name] + (offs.get(o.name, V()) * k)
        o.keyframe_insert("location", frame=f)
    az = math.radians(az0 + (az1 - az0) * ease(u))
    el = math.radians(9 + 6 * math.sin(math.pi * u) + (8 if explode else 0) * k)
    tgt = V((0, 0, dims.z * 0.5 + (0.15 * SIZE * k if explode else 0)))
    cam.location = tgt + V((math.cos(el) * math.sin(az), math.cos(el) * math.cos(az), math.sin(el))) * dist * (1 + 0.12 * k)
    cam.rotation_euler = (tgt - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam.data.dof.focus_distance = (tgt - cam.location).length
    cam.keyframe_insert("location", frame=f); cam.keyframe_insert("rotation_euler", frame=f)
    cam.data.keyframe_insert("dof.focus_distance", frame=f)

os.makedirs(J["frames_dir"], exist_ok=True)
for f in range(N):
    path = os.path.join(J["frames_dir"], f"{f:04d}.png")
    if os.path.exists(path): continue
    sc.frame_set(f); sc.render.filepath = path
    t = time.time(); bpy.ops.render.render(write_still=True)
    print(f"FRAME {f} {time.time() - t:.1f}s", flush=True)
print("DONE", N, flush=True)
