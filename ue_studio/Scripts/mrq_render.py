"""Headless UE 5.8 Movie Render Queue (MRQ) beat renderer.

Three 5.8 fixes, ported from the video-studio-test working tree:

1. Level-from-template in one step. load_level() on the template left MRQ bound to the
   editor's default map. LevelEditorSubsystem.new_level_from_template copies the template
   in a single call and that copy is the job map.
2. Explicit section end-times. A camera-cut section with an unbounded end is zero frames
   on 5.8, so the section end is set and bounded and the output setting repeats that range.
3. Rotated-map fix. job.map must be the object path (T_DataWall.T_DataWall). The package
   path loads a map that is turned relative to the template. Portrait filmback matches a
   tall frame so the map is not rotated 90 degrees. The host pixel check
   (unreal_mrq.pixel_score) scores that wrong render ~0 and a correct frame 0.98-0.995.
"""
import json, os, time, unreal

_KEEP = []


def quit_editor():
    """The host waits for this process to exit. An async MRQ render must quit when it finishes."""
    try:
        unreal.SystemLibrary.quit_editor()
    except Exception as e:
        unreal.log_error("gvs_mrq quit_editor failed: %s" % e)


def level_object_path(level_path):
    """Package path -> object path. A package path is the rotated default map on UE 5.8."""
    level_path = str(level_path).rstrip("/")
    leaf = level_path.rsplit("/", 1)[-1]
    if "." in leaf:
        return level_path
    return level_path + "." + leaf


def section_end_frame(fps, duration_s):
    return max(1, int(round(float(duration_s) * int(fps))))


def open_level_from_template(template_path):
    """One call. Do not new_level() and then copy actors onto it."""
    subsys = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    leaf = template_path.rstrip("/").rsplit("/", 1)[-1]
    # A fixed dest fails on the next beat: delete_asset drops the loaded world but the file is still there,
    # and NewLevelFromTemplate then refuses ("an asset already exists").
    dest = "/Game/Temp/GVS_%s_%d" % (leaf, int(time.time() * 1000))
    unreal.EditorAssetLibrary.make_directory("/Game/Temp")
    ok = subsys.new_level_from_template(dest, template_path)
    if not ok:
        raise RuntimeError("new_level_from_template failed: %s -> %s" % (template_path, dest))
    return dest


def explicit_section_end(section, end_frame):
    """UE 5.8 ignores a section whose end is unbounded and renders no frames."""
    section.set_start_frame(0)
    section.set_end_frame(int(end_frame))
    section.set_start_frame_bounded(True)
    section.set_end_frame_bounded(True)


def portrait_filmback(actor, width, height):
    """A landscape sensor on a tall frame turns the map 90 degrees."""
    comp = actor.get_cine_camera_component()
    fb = comp.get_editor_property("filmback")
    width, height = float(width), float(height)
    if height >= width:
        fb.sensor_width = 16.0
        fb.sensor_height = 16.0 * (height / width)
    else:
        fb.sensor_width = 24.0
        fb.sensor_height = 24.0 * (height / max(1.0, width))
    comp.set_editor_property("filmback", fb)


def bind_camera_cut(fps, duration_s, width, height):
    end = section_end_frame(fps, duration_s)
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    seq_path = "/Game/Temp/GVS_MRQ_Seq"
    unreal.EditorAssetLibrary.make_directory("/Game/Temp")
    if unreal.EditorAssetLibrary.does_asset_exist(seq_path):
        unreal.EditorAssetLibrary.delete_asset(seq_path)
    sequence = tools.create_asset("GVS_MRQ_Seq", "/Game/Temp", unreal.LevelSequence, unreal.LevelSequenceFactoryNew())
    sequence.set_display_rate(unreal.FrameRate(int(fps), 1))
    sequence.set_playback_start(0)
    sequence.set_playback_end(end)
    cam = None
    for actor in unreal.EditorLevelLibrary.get_all_level_actors():
        if actor.get_actor_label() == "GVS_Camera":
            cam = actor
            break
    if cam is not None:
        portrait_filmback(cam, width, height)
    track = sequence.add_track(unreal.MovieSceneCameraCutTrack)
    section = track.add_section()
    explicit_section_end(section, end)
    if cam is not None:
        binding = sequence.add_possessable(cam)
        # UE 5.8: LevelSequence has no make_binding_id, and the binding proxy has no get_binding_id.
        bid = unreal.MovieSceneSequenceExtensions.get_portable_binding_id(sequence, sequence, binding)
        section.set_camera_binding_id(bid)
    unreal.EditorAssetLibrary.save_asset(seq_path)
    return sequence, end


def main():
    # -ExecutePythonScript quits the editor as soon as this script returns. The MRQ job is async, so the
    # editor has to stay up until the finished callback calls quit_editor().
    unreal.EditorPythonScriptingLibrary.set_keep_python_script_alive(True)
    params_path = os.environ.get("GVS_MRQ_PARAMS", "")
    if not params_path or not os.path.exists(params_path):
        raise RuntimeError("GVS_MRQ_PARAMS not found: %s" % params_path)
    with open(params_path, "r", encoding="utf-8") as f:
        spec = json.load(f)
    template = spec.get("template", "T_DataWall")
    template_path = "/Game/Templates/%s" % template
    if not unreal.EditorAssetLibrary.does_asset_exist(template_path):
        raise RuntimeError("UE5 template level missing: %s" % template_path)
    level_path = open_level_from_template(template_path)
    for actor in unreal.EditorLevelLibrary.get_all_level_actors():
        lbl = actor.get_actor_label()
        if lbl == "GVS_Title" and spec.get("title"):
            c = actor.get_component_by_class(unreal.TextRenderComponent)
            if c:
                c.set_text(unreal.Text(str(spec["title"])))
        elif lbl == "GVS_Subtitle" and spec.get("subtitle"):
            c = actor.get_component_by_class(unreal.TextRenderComponent)
            if c:
                c.set_text(unreal.Text(str(spec["subtitle"])))
    fps = int(spec.get("fps", 30))
    duration_s = float(spec.get("duration_s", 2.5))
    width, height = int(spec.get("width", 1080)), int(spec.get("height", 1920))
    sequence, end_frame = bind_camera_cut(fps, duration_s, width, height)
    subsys = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    subsys.save_current_level()
    qs = unreal.get_editor_subsystem(unreal.MoviePipelineQueueSubsystem)
    q = qs.get_queue()
    q.delete_all_jobs()
    job = q.allocate_new_job(unreal.MoviePipelineExecutorJob)
    job.job_name = spec.get("job_name", "gvs_beat")
    job.map = unreal.SoftObjectPath(level_object_path(level_path))
    job.sequence = unreal.SoftObjectPath(sequence.get_path_name())
    cfg = job.get_configuration()
    out = cfg.find_or_add_setting_by_class(unreal.MoviePipelineOutputSetting)
    out.output_directory = unreal.DirectoryPath(spec["output_dir"])
    out.output_resolution = unreal.IntPoint(width, height)
    out.file_name_format = "frame_{frame_number}"
    out.use_custom_frame_rate = True
    out.output_frame_rate = unreal.FrameRate(fps, 1)
    out.use_custom_playback_range = True
    out.custom_start_frame = 0
    out.custom_end_frame = end_frame
    aa = cfg.find_or_add_setting_by_class(unreal.MoviePipelineAntiAliasingSetting)
    aa.spatial_sample_count = int(spec.get("aa_samples", 4))
    aa.temporal_sample_count = 1
    aa.override_anti_aliasing = True
    cfg.find_or_add_setting_by_class(unreal.MoviePipelineDeferredPassBase)
    cfg.find_or_add_setting_by_class(unreal.MoviePipelineImageSequenceOutput_PNG)
    executor = unreal.MoviePipelinePIEExecutor()

    def finished(ex, success):
        unreal.log("gvs_mrq finished success=%s" % success)
        quit_editor()

    def errored(ex, pipeline, fatal, text):
        unreal.log_error("gvs_mrq error fatal=%s %s" % (fatal, text))
        quit_editor()

    executor.on_executor_finished_delegate.add_callable(finished)
    executor.on_executor_errored_delegate.add_callable(errored)
    _KEEP.extend([executor, finished, errored])
    qs.render_queue_with_executor_instance(executor)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        unreal.log_error("gvs_mrq failed: %s" % e)
        quit_editor()
        raise
