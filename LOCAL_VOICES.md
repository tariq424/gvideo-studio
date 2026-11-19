# Local cloned narration

The Narration menu offers **My Voice – Original Accent** (VoxCPM2 through the running local VoiceStudio backend) and **My Voice – American Accent** (cached MeloTTS U.S. English with OpenVoice V2).

The original option requires VoiceStudio running on localhost:3900 and its saved profile named My Voice - YouTube Narration. The American option uses F:/audio-transcriber/american-narrator/render.py and the existing sibling .openvoice-test model cache. Both use the VoiceStudio VoxCPM2 Python environment already installed on this PC. No model installer is invoked.

New jobs save the voice choice and preserve it on resume. Both outputs are converted to 48 kHz stereo for the existing video pipeline. The American option currently reloads its models per narration part. A missing model or unavailable backend stops the selected cloned-voice render.

Restart Video Studio to load the backend changes, then refresh its page. Existing jobs keep their previously saved engine choice.
