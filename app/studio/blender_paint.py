"""Run INSIDE Blender (not imported by the app):  blender -b --factory-startup --python blender_paint.py -- <job.json>
job.json: {"mesh": glb, "out": blend, "front": "-y", "up": "+z",
           "views": [{"img": png (tight crop of the object), "view": "left|right|front|back|top|bottom", "through": bool}]}
Paints a generated shape (Hunyuan3D) with its reference photos: each photo is projected along its own view direction in
object space, scaled to the mesh's extent in that view (the photos are tight crops), and the views are blended by how
squarely each surface faces that camera (and only inside the photo's silhouette). Full photo resolution, no baking.
"through": the photo of the opposite side reused for this side (a symmetric object: an aircraft's left side for its right);
its text then reads mirrored. Surfaces no photo sees get a neutral clay. Prints "PAINTED <faces>"."""
import bpy, json, sys, mathutils
V = mathutils.Vector
J = json.load(open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8"))

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=J["mesh"])
ob = next(o for o in bpy.context.scene.objects if o.type == "MESH")
mw = ob.matrix_world.copy(); ob.parent = None; ob.matrix_world = mw
for o in list(bpy.context.scene.objects):
    if o is not ob: bpy.data.objects.remove(o, do_unlink=True)
bpy.context.view_layer.objects.active = ob; ob.select_set(True)
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
ob.name = J.get("name", "model")
bpy.ops.object.shade_smooth()


def ax(s):
    v = V((0, 0, 0)); v["xyz".index(s[1])] = 1 if s[0] == "+" else -1; return v
F, UP = ax(J.get("front", "-y")), ax(J.get("up", "+z"))
LW = UP.cross(F)   # the object's own left (up x forward)
DEF = {"left": (LW, -F, UP), "right": (-LW, F, UP), "front": (F, LW, UP), "back": (-F, -LW, UP),
       "top": (UP, -LW, F), "bottom": (-UP, LW, F)}   # view: (camera direction, image +u, image +v)
pts = [V(v.co) for v in ob.data.vertices]
def ext(d):
    xs = [p.dot(d) for p in pts]; return min(xs), max(xs)

m = bpy.data.materials.new("photo_projection"); m.use_nodes = True
nt = m.node_tree; N = nt.nodes; Lk = nt.links
bsdf = N["Principled BSDF"]; bsdf.inputs["Roughness"].default_value = 0.35
if "Coat Weight" in bsdf.inputs: bsdf.inputs["Coat Weight"].default_value = 0.15
tc = N.new("ShaderNodeTexCoord"); geo = N.new("ShaderNodeNewGeometry")
vt = N.new("ShaderNodeVectorTransform"); vt.vector_type = "NORMAL"; vt.convert_from = "WORLD"; vt.convert_to = "OBJECT"
Lk.new(geo.outputs["Normal"], vt.inputs[0])


def dot(vec_out, d):
    n = N.new("ShaderNodeVectorMath"); n.operation = "DOT_PRODUCT"; n.inputs[1].default_value = d
    Lk.new(vec_out, n.inputs[0]); return n.outputs["Value"]
def math(op, x, y=None):
    n = N.new("ShaderNodeMath"); n.operation = op
    for i, v in enumerate((x, y)):
        if v is None: continue
        if isinstance(v, (int, float)): n.inputs[i].default_value = v
        else: Lk.new(v, n.inputs[i])
    return n.outputs[0]
def rng(x, lo, hi):
    n = N.new("ShaderNodeMapRange"); n.inputs[1].default_value = lo; n.inputs[2].default_value = hi
    n.inputs[3].default_value = 0; n.inputs[4].default_value = 1; n.clamp = False; Lk.new(x, n.inputs[0]); return n.outputs[0]


acc_c = acc_w = None
for vw in J["views"]:
    cam, U_, V_ = DEF[vw["view"]]
    if vw.get("through"): U_ = -U_   # the opposite side's photo seen through the object
    u0, u1 = ext(U_); v0, v1 = ext(V_)
    u = rng(dot(tc.outputs["Object"], U_), u0, u1); v = rng(dot(tc.outputs["Object"], V_), v0, v1)
    cxyz = N.new("ShaderNodeCombineXYZ"); Lk.new(u, cxyz.inputs[0]); Lk.new(v, cxyz.inputs[1])
    tex = N.new("ShaderNodeTexImage"); tex.image = bpy.data.images.load(vw["img"]); tex.extension = "CLIP"
    tex.interpolation = "Cubic"; Lk.new(cxyz.outputs[0], tex.inputs[0])
    # the side photos own everything but the true top / bottom (owner, 2026-11-13: a top view's lettering on the side came out
    # mirrored and ghosted): a top / bottom photo only paints surfaces facing within ~35 degrees of straight up / down
    lo_ = 0.82 if vw["view"] in ("top", "bottom") else 0.0
    w = math("POWER", math("MAXIMUM", rng(dot(vt.outputs[0], cam), lo_, 1.0), 0.0), float(J.get("sharp", 8.0)))
    w = math("MULTIPLY", w, tex.outputs["Alpha"])
    col = N.new("ShaderNodeVectorMath"); col.operation = "SCALE"; Lk.new(tex.outputs["Color"], col.inputs[0]); Lk.new(w, col.inputs[3])
    if acc_c is None: acc_c, acc_w = col.outputs[0], w
    else:
        s = N.new("ShaderNodeVectorMath"); s.operation = "ADD"; Lk.new(acc_c, s.inputs[0]); Lk.new(col.outputs[0], s.inputs[1]); acc_c = s.outputs[0]
        acc_w = math("ADD", acc_w, w)
fin = N.new("ShaderNodeVectorMath"); fin.operation = "SCALE"; Lk.new(acc_c, fin.inputs[0])
Lk.new(math("DIVIDE", 1.0, math("ADD", acc_w, 1e-6)), fin.inputs[3])
mix = N.new("ShaderNodeMix"); mix.data_type = "RGBA"
Lk.new(math("MINIMUM", math("MULTIPLY", acc_w, 40.0), 1.0), mix.inputs["Factor"])
mix.inputs["A"].default_value = (0.6, 0.6, 0.62, 1)   # clay where no photo reaches
gam = N.new("ShaderNodeGamma"); gam.inputs[1].default_value = float(J.get("gamma", 1.6))   # the photos already carry their own
hs = N.new("ShaderNodeHueSaturation"); hs.inputs["Saturation"].default_value = float(J.get("saturation", 1.25))
Lk.new(fin.outputs[0], gam.inputs[0]); Lk.new(gam.outputs[0], hs.inputs["Color"]); Lk.new(hs.outputs["Color"], mix.inputs["B"])            # light: deepen them so the studio light doesn't wash them out
Lk.new(mix.outputs["Result"], bsdf.inputs["Base Color"])
ob.data.materials.clear(); ob.data.materials.append(m)
for im in bpy.data.images: im.pack()
bpy.ops.wm.save_as_mainfile(filepath=J["out"])
print("PAINTED", len(ob.data.polygons), flush=True)
