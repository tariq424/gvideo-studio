# Claude handoff — cloned narration integration and VoxCPM2 crash

## Goal and current state

Add two narration selections to Video Studio using already downloaded local models and zero service credits.

- My Voice – Original Accent routes to the running VoiceStudio VoxCPM2 API and saved profile My Voice - YouTube Narration.
- My Voice – American Accent routes to the existing American Narrator renderer using MeloTTS U.S. English and OpenVoice V2.
- Existing Kokoro and WanGP choices remain available. The cloned choices raise an error if they fail, preserving the requested voice.
- Jobs save these choices and keep them when resumed. Long and Short narration paths route through the local worker. Output is 48 kHz stereo.
- Python compilation and git diff checks passed. Full rendering has NOT passed for this integration.

The Git checkout used by the already-running app is F:/code/video-studio-test, origin tariq424/video-studio-test. Changes were also applied to F:/code/video-studio, which has no .git directory. Keep that distinction explicit. The non-Git copy has the same integration files.

## Exact failure observed on 2026-11-19

The smoke test called studio.tts.try_kokoro with tts_engine=my_original and the sentence "On Saturday morning, I walked across the street."

VoiceStudio accepted the request at POST http://127.0.0.1:3900/v1/audio/speech with model=voxcpm2 and voice="My Voice - YouTube Narration", then returned HTTP 500.

Backend exception:

    RuntimeError: voxcpm2 sidecar closed pipe mid-generate

Last model-loading messages:

    Running on device: cuda, dtype: bfloat16
    Loading model from safetensors: .../models--openbmb--VoxCPM2/snapshots/7936080b41163874f38610f46f438e2c0757f9fd/model.safetensors

No successful "Loaded VoxCPM2Model" message followed in this attempt. This is a process death during loading, before a verified synthesis. The client HTTP 500 is a consequence, not the root cause. An out-of-memory condition, Windows commit pressure, or an external termination is plausible but UNCONFIRMED. Do not report any of these as established.

Earlier sessions successfully generated audio with this model on this RTX 5060 Ti. The machine has about 16 GB VRAM and 32 GB RAM. During this attempt the user reported general system instability and requested a reboot. Avoid running extra model probes before reboot.

Local evidence:
- C:/Users/Tarri/AppData/Roaming/OmniVoice/omnivoice.log
- VoiceStudio Electron 0.5.8
- F:/audio-transcriber/tmp/my_original.wav was the requested smoke output.
- The American test followed sequentially and was very slow, but completed successfully before shutdown. studio.tts.try_kokoro validated its output and logged: Test: my_american rendered 1 local narration part(s). Original Accent remains failing.
- F:/audio-transcriber/tmp/my_american.wav was its requested output.

## Repair specification after reboot

No root-cause fix has yet been established. The implementation work below is a repair plan, not a verified fix.

1. Read the current VoiceStudio local health and OpenAPI schema. Confirm the selected TTS engine, saved profile, installed weights, actual compute device and Python runtime. Keep remote providers disabled.
2. Reproduce one short ORIGINAL voice request alone after reboot, with other inference jobs idle. Capture the full HTTP body and backend log with timestamps. Check GPU memory, system available RAM, committed bytes and commit limit. Check Windows Application/Event logs for a process exit or fault at the same timestamp.
3. If clean startup succeeds, serialize cloned narration across relevant local services or otherwise prevent concurrent heavy model loading. The current original and American smoke calls were sequential, but other unrelated services were running. Preserve the factory's concurrency controls.
4. If CUDA loading still exits, use VoiceStudio's supported configuration or UI to perform a diagnostic CPU run, explicitly report the actual device and compare the result. Obtain evidence before choosing permanent CPU fallback. Do not silently change the selected voice or use a paid service.
5. If memory pressure is confirmed, reduce competing residency and validate the change with two consecutive short renders. Do not blindly reinstall dependencies, remove caches, or download replacement models.
6. Add a readable failure message at preflight for unavailable VoiceStudio, missing saved profile, model readiness and missing renderer/runtime/reference. Current code checks some local paths; it still needs stronger live Original Accent preflight.
7. Review cancellation and resource cleanup. Current cloned_batch uses subprocess.run(timeout=3600) and checks cancellation before and after execution. Improve it so cancellation and timeout terminate its renderer descendants and release GPU memory, without terminating unrelated user services.
8. American Accent currently reloads models per narration part. Add a true batch renderer later so one worker loads MeloTTS/BERT/OpenVoice and extracts the reference embedding once for all items. Do not claim a persistent model service is already implemented.
9. Verify through normal Long and Short entry points, including saved-job resume. Use an isolated test job and unique output paths. Confirm choice persistence, WAV decoding, 48 kHz stereo, and no substitution on failure. No full video build is needed for this repair.

## Existing assets and requirements

- American Narrator repository and renderer: F:/audio-transcriber/american-narrator/render.py
- Its models and modified English frontend: F:/audio-transcriber/.openvoice-test
- Python: %APPDATA%/OmniVoice/engines/voxcpm2/voxcpm2/.venv/Scripts/python.exe
- Original recording: %USERPROFILE%/OneDrive/Documents/Sound Recordings/my voice clone.m4a
- VoiceStudio saved profile ID from earlier setup: 53cc8d8a. Rediscover before relying on it.
- Model files, voice recordings and generated WAVs must stay on the PC and outside Git.
- Ask the user before downloading ANY additional model, giving size and license first.
- These narration paths use existing local weights and no credits. Other video pipeline services have their own settings.

## Integration files to review

app/cloned_voice_worker.py, app/studio/tts.py, app/studio/pipeline.py, app/studio/render.py, app/studio/short.py, app/static/index.html, LOCAL_VOICES.md.

American Narrator's existing private GitHub repository is https://github.com/tariq424/american-narrator. This Video Studio integration belongs in video-studio-test. This checkpoint is intentionally incomplete and documents the remaining validation.


## Update from Claude, 2026-11-19, after Codex's handoff (before the reboot)

Done in `app/studio/tts.py` and `app/cloned_voice_worker.py`. The code compiles and the readiness checks pass. NO audio test has been run since; the system was unstable.
- **Item 8 (true batch):** `cloned_voice_worker.py` now loads MeloTTS, BERT and OpenVoice and extracts the reference embedding once for the whole batch. Video Studio converts the reference and output with its own ffmpeg (`media.ff`), not ffmpeg on PATH.
- **Item 7 (cancel and cleanup):** the American worker runs with `Popen`; Stop kills it, and the process exits after the batch, which frees the GPU. Original Accent unloads VoiceStudio's model (`/model/unload/{id}` + `/system/flush-memory`) after every batch, including on failure.
- **Item 6 (preflight):** `tts.cloned_ready()`.
  - Original: VoiceStudio health, and starts `VoiceStudio.exe` if it's closed (waits up to 120 s). Also checks that the profile exists by name.
  - American: worker, VoxCPM2 Python, model cache, voice sample.
  - The job stops with the reason; there's never a silent switch to another voice.
- **4,096-character limit:** text longer than 3,800 characters is split between sentences and the takes are joined. Text is never cut.
- **Settings keys (defaults in code):** voicestudio_url, voicestudio_exe, voicestudio_profile, voxcpm_python, narrator_cache, narrator_ref, narrator_speed, narrator_tau, my_original_wpm, my_american_wpm.
- **Pace:** both voices currently assume 150 wpm for sizing Shorts. Measure each with one paragraph after the reboot and set the defaults in `tts.CLONED`.
- **Still open after the reboot:** items 1–5 (the VoxCPM2 "sidecar closed pipe mid-generate" crash on the first Original request) and item 9 (end-to-end through Long and Short, plus resume).
