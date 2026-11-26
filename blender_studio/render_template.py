import bpy, json, math, os
from pathlib import Path
def make_mat(name, color, emit=0.0, rough=0.35, metal=0.2):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metal
    if emit > 0 and "Emission Strength" in bsdf.inputs:
        bsdf.inputs["Emission Color"].default_value = (*color, 1.0)
        bsdf.inputs["Emission Strength"].default_value = emit
    return m
def add_text(txt, loc, size=0.42, mat=None, extrude=0.04):
    bpy.ops.object.text_add(location=loc, rotation=(math.pi/2, 0, 0))
    obj = bpy.context.active_object
    obj.data.body = str(txt)[:80]
    obj.data.size = size
    obj.data.align_x = "CENTER"
    obj.data.extrude = extrude
    if mat: obj.data.materials.append(mat)
    return obj
def main():
    spec = json.loads(Path(os.environ["GVS_BLENDER_PARAMS"]).read_text(encoding="utf-8"))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE"
    sc.render.resolution_x = int(spec.get("width", 540))
    sc.render.resolution_y = int(spec.get("height", 960))
    sc.render.fps = int(spec.get("fps", 24))
    frames = max(12, int(float(spec.get("duration_s", 1.5)) * sc.render.fps))
    sc.frame_start = 1; sc.frame_end = frames
    w = bpy.data.worlds.new("GVSWorld"); sc.world = w; w.use_nodes = True
    bg = w.node_tree.nodes.get("Background")
    if bg: bg.inputs[0].default_value = (0.015, 0.02, 0.035, 1.0)
    m_floor = make_mat("Floor", (0.04, 0.05, 0.08), rough=0.25, metal=0.6)
    m_cyan = make_mat("Cyan", (0.12, 0.78, 1.0), emit=2.5, rough=0.2)
    m_amber = make_mat("Amber", (1.0, 0.55, 0.15), emit=2.2, rough=0.2)
    m_white = make_mat("White", (0.95, 0.97, 1.0), emit=1.4, rough=0.2)
    m_slate = make_mat("Slate", (0.08, 0.1, 0.16), rough=0.35, metal=0.5)
    bpy.ops.mesh.primitive_plane_add(size=30, location=(0, 0, -1.5))
    bpy.context.active_object.data.materials.append(m_floor)
    bpy.ops.object.light_add(type="AREA", location=(-4, -5, 4))
    l1 = bpy.context.active_object; l1.data.energy = 600; l1.data.color = (0.3, 0.8, 1.0)
    bpy.ops.object.light_add(type="AREA", location=(4, -4, 3))
    l2 = bpy.context.active_object; l2.data.energy = 450; l2.data.color = (1.0, 0.6, 0.25)
    tpl = spec.get("template", "T_DataWall")
    add_text(spec.get("title", "AI PROOF"), (0, 0, 1.85), size=0.42, mat=m_white)
    add_text(spec.get("subtitle", tpl), (0, 0, 1.25), size=0.22, mat=m_cyan, extrude=0.02)
    vals = [0.6, 1.1, 1.7, 2.3] if tpl == "T_ComparisonLadder" else [0.7, 1.3, 1.9]
    if tpl == "T_ProofScreen":
        bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0.2, -0.1))
        f1 = bpy.context.active_object; f1.scale = (1.6, 0.08, 1.0); f1.data.materials.append(m_slate)
    else:
        for idx, h in enumerate(vals):
            bpy.ops.mesh.primitive_cube_add(size=1, location=(-1.1 + idx*0.85, 0.1, -1.5 + h/2))
            b = bpy.context.active_object; b.scale = (0.28, 0.28, h/2)
            b.data.materials.append(m_amber if idx == len(vals)-1 else m_cyan)
    bpy.ops.object.camera_add(location=(0, -7.2, 0.5), rotation=(math.pi/2 - 0.04, 0, 0))
    cam = bpy.context.active_object; sc.camera = cam
    cam.keyframe_insert(data_path="location", frame=1)
    cam.location = (0.35, -5.8, 0.75); cam.keyframe_insert(data_path="location", frame=frames)
    sc.render.image_settings.file_format = "PNG"
    sc.render.filepath = str(Path(spec["output_dir"]) / "frame_")
    bpy.ops.render.render(animation=True)
if __name__ == "__main__":
    main()
