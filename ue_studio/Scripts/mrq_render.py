"""Headless UE5 Movie Render Queue (MRQ) beat renderer."""
import json, os, unreal

def main() -> None:
    params_path = os.environ.get("GVS_MRQ_PARAMS", "")
    if not params_path or not os.path.exists(params_path):
        raise RuntimeError(f"GVS_MRQ_PARAMS not found: {params_path}")
    with open(params_path, "r", encoding="utf-8") as f:
        spec = json.load(f)
    template = spec.get("template", "T_DataWall")
    level_path = f"/Game/Templates/{template}"
    if not unreal.EditorAssetLibrary.does_asset_exist(level_path):
        raise RuntimeError(f"UE5 template level missing: {level_path}")
    subsys = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    subsys.load_level(level_path)
    for actor in unreal.EditorLevelLibrary.get_all_level_actors():
        lbl = actor.get_actor_label()
        if lbl == "GVS_Title" and spec.get("title"):
            c = actor.get_component_by_class(unreal.TextRenderComponent)
            if c: c.set_text(unreal.Text(str(spec["title"])))
        elif lbl == "GVS_Subtitle" and spec.get("subtitle"):
            c = actor.get_component_by_class(unreal.TextRenderComponent)
            if c: c.set_text(unreal.Text(str(spec["subtitle"])))
    qs = unreal.get_editor_subsystem(unreal.MoviePipelineQueueEngineSubsystem)
    q = qs.get_queue()
    q.delete_all_jobs()
    job = q.allocate_new_job(unreal.MoviePipelineExecutorJob)
    job.job_name = spec.get("job_name", "gvs_beat")
    job.map = unreal.SoftObjectPath(level_path)
    cfg = job.get_configuration()
    out = cfg.find_or_add_setting_by_class(unreal.MoviePipelineOutputSetting)
    out.output_directory = unreal.DirectoryPath(spec["output_dir"])
    out.output_resolution = unreal.IntPoint(int(spec.get("width", 1080)), int(spec.get("height", 1920)))
    out.file_name_format = "frame_{frame_number}"
    out.use_custom_frame_rate = True
    out.output_frame_rate = unreal.FrameRate(int(spec.get("fps", 30)), 1)
    aa = cfg.find_or_add_setting_by_class(unreal.MoviePipelineAntiAliasingSetting)
    aa.spatial_sample_count = int(spec.get("aa_samples", 4))
    aa.temporal_sample_count = 1
    aa.override_anti_aliasing = True
    cfg.find_or_add_setting_by_class(unreal.MoviePipelineDeferredPassBase)
    cfg.find_or_add_setting_by_class(unreal.MoviePipelineImageSequenceOutput_PNG)
    qs.render_queue_with_executor_instance(unreal.MoviePipelinePIEExecutor())

if __name__ == "__main__":
    main()
