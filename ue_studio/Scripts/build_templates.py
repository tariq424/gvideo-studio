"""Generate starter UE5 .umap templates: T_DataWall, T_ComparisonLadder, T_ProofScreen."""
import unreal

TEMPLATES = [
    ("/Game/Templates/T_DataWall", "3D Data Wall"),
    ("/Game/Templates/T_ComparisonLadder", "3D Comparison Ladder"),
    ("/Game/Templates/T_ProofScreen", "3D Proof Screen"),
]

# UE 5.8: one call. new_level() and a second copy left the map on the editor default rotation.
ENGINE_TEMPLATE = "/Engine/Maps/Templates/Template_Default"

def create_starter_level(asset_path: str, label: str) -> None:
    if unreal.EditorAssetLibrary.does_asset_exist(asset_path):
        unreal.log(f"[GVideoStudio] Template already exists: {asset_path}")
        return
    subsys = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    made = False
    if hasattr(subsys, "new_level_from_template"):
        made = bool(subsys.new_level_from_template(asset_path, ENGINE_TEMPLATE))
    if not made:
        subsys.new_level(asset_path)
    cam = unreal.EditorLevelLibrary.spawn_actor_from_class(
        unreal.CineCameraActor, unreal.Vector(-860.0, 0.0, 165.0), unreal.Rotator(pitch=0.0, yaw=0.0, roll=0.0)
    )
    if cam: cam.set_actor_label("GVS_Camera")
    # Text faces local +X and local +Z is up. Yaw 180 aims that face at the camera on -X.
    t = unreal.EditorLevelLibrary.spawn_actor_from_class(
        unreal.TextRenderActor, unreal.Vector(20.0, 0.0, 200.0), unreal.Rotator(pitch=0.0, yaw=180.0, roll=0.0)
    )
    if t:
        t.set_actor_label("GVS_Title")
        c = t.get_component_by_class(unreal.TextRenderComponent)
        if c:
            c.set_text(unreal.Text(label)); c.set_world_size(46.0)
            c.set_horizontal_alignment(unreal.HorizTextAligment.EHTA_CENTER)
    s = unreal.EditorLevelLibrary.spawn_actor_from_class(
        unreal.TextRenderActor, unreal.Vector(20.0, 0.0, 120.0), unreal.Rotator(pitch=0.0, yaw=180.0, roll=0.0)
    )
    if s:
        s.set_actor_label("GVS_Subtitle")
        c = s.get_component_by_class(unreal.TextRenderComponent)
        if c:
            c.set_text(unreal.Text("Parameterized MRQ Beat")); c.set_world_size(28.0)
            c.set_horizontal_alignment(unreal.HorizTextAligment.EHTA_CENTER)
    l = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.PointLight, unreal.Vector(-200.0, 120.0, 280.0))
    if l: l.set_actor_label("GVS_KeyLight")
    subsys.save_current_level()
    unreal.log(f"[GVideoStudio] Created starter template: {asset_path}")

try:
    for p, lbl in TEMPLATES:
        create_starter_level(p, lbl)
finally:
    try:
        unreal.SystemLibrary.quit_editor()
    except Exception as e:
        unreal.log_error("gvs build_templates quit_editor failed: %s" % e)
