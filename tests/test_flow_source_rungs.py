import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.studio import flow, short, source_visuals


def vb():
    return {"g": False, "hard": False, "p": False, "pok": False, "capcut": "", "f": False}


class SourceClipRungTests(unittest.TestCase):
    def setUp(self):
        self.job = Path(tempfile.mkdtemp())
        (self.job / "src").mkdir()
        cues = [{"start": 2, "text": "welcome back"}, {"start": 120.5, "text": "Google says its quantum chip"},
                {"start": 123.0, "text": "cut error rates in a single step"}, {"start": 126.2, "text": "a problem open for twenty years"}]
        (self.job / "src" / "source_cues.json").write_text(json.dumps(cues))

    def test_matches_a_real_cue(self):
        req = source_visuals.auto_request(self.job, {"vo": "Google's quantum chip cut error rates in one step, a twenty year problem."})
        self.assertEqual(req["start"], 120.5)
        self.assertEqual(req["kind"], "clip")
        self.assertIsNotNone(source_visuals._request({"source_visual": req}))

    def test_weak_match_is_skipped(self):
        self.assertIsNone(source_visuals.auto_request(self.job, {"vo": "Meta launched an image model for creators."}))

    def test_make_uses_auto_request_and_falls_through_on_failure(self):
        logs = []
        with patch.object(source_visuals, "_extract", side_effect=RuntimeError("download blocked")):
            item = source_visuals.make({}, self.job, self.job / "short", {"id": "b1", "vo": "Google quantum chip cut error rates single step twenty years"}, logs.append)
        self.assertIsNone(item)
        self.assertTrue(any("source clip rung" in l for l in logs))
        self.assertTrue(any("continuing visual ladder" in l for l in logs))


class FlowRungTests(unittest.TestCase):
    def setUp(self):
        flow.reset()
        self.sd = Path(tempfile.mkdtemp())

    def test_missing_profile_skips_for_the_rest_of_the_job(self):
        logs = []
        cfg = {"flow_profile": str(self.sd / "nope")}
        self.assertIsNone(flow.generate(cfg, self.sd, {"vo": "x"}, "b1_0", logs.append))
        self.assertIsNone(flow.generate(cfg, self.sd, {"vo": "x"}, "b2_0", logs.append))
        self.assertTrue(any("no signed-in Flow profile" in l for l in logs))
        self.assertTrue(any("flow skipped" in l for l in logs))

    def test_flow_is_no_longer_in_the_late_rungs(self):
        # Flow is rung 3 (prepare, right after the source clip); rungs 7+ must not call it
        with patch.object(flow, "generate") as gen, \
             patch("app.studio.physics.provide", return_value=None), \
             patch("app.studio.graphics.make", return_value={"ok": False, "why": "x", "hard": False}), \
             patch("app.studio.illustrate.aircraft_for", return_value=None), patch("app.studio.illustrate.pick", return_value=None):
            it = short._v2_rungs({"flow_on": True, "slot_pictures": False}, self.sd, {"id": "b1", "vo": "a beat"}, {"key": "b1_0"}, {}, [], {}, {}, vb(),
                                 [], [], [], 0, lambda m: None)
        gen.assert_not_called()
        self.assertEqual(it["rung"], "headline")


    def test_a_fresh_ai_picture_comes_before_any_drawing_or_card(self):
        # owner, 2026-11-17: "worst case we could have made a cloudflare image from description" - the slot's own picture rung
        img = self.sd / "x.jpg"; img.write_bytes(b"x")
        with patch("app.studio.imagegen.generate_many", return_value=[(img, "p")]) as gm,              patch("app.studio.imagegen.last_site", return_value="cloudflare"),              patch("app.studio.depth.cutout_layers", return_value=[self.sd / "x_L0.jpg", self.sd / "x_L1.png"]),              patch("app.studio.graphics.make") as gfx:
            chain = []
            it = short._v2_rungs({}, self.sd, {"id": "b1", "vo": "a beat"}, {"key": "b1_2"}, {}, [], {}, {}, vb(), chain, [], [], 0, lambda m: None)
        gfx.assert_not_called()
        self.assertEqual(it["rung"], "imagegen"); self.assertEqual(it["type"], "layers")
        self.assertIn("from the narration", chain[-1])
        self.assertIsNone(gm.call_args.kwargs.get("image"))       # from the narration, not the screenshot again

    def test_when_no_picture_comes_the_reasons_are_kept_for_the_ladder_panel(self):
        with patch("app.studio.imagegen.generate_many", return_value=[]),              patch("app.studio.imagegen.tries_text", return_value="cloudflare: failed (HTTP 429)"),              patch("app.studio.studio3d.clip", return_value=(None, "no object")),              patch("app.studio.physics.provide", return_value=None),              patch("app.studio.graphics.make", return_value={"ok": False, "why": "x", "hard": False}):
            why, failed = [], []
            it = short._v2_rungs({}, self.sd, {"id": "b1", "vo": "a beat"}, {"key": "b1_2"}, {}, [], {}, {}, vb(), [], why, failed, 0, lambda m: None)
        self.assertEqual(it["rung"], "headline")
        self.assertIn("AI image: cloudflare: failed (HTTP 429)", why); self.assertIn("imagegen", failed)


if __name__ == "__main__":
    unittest.main()


class VoiceCheckTests(unittest.TestCase):
    def test_quoted_tolerates_punctuation_and_small_edits(self):
        self.assertTrue(short._quoted("Our take: real engineering, but not proven yet",
                                      "Willow cut errors. Our take - real engineering, but it's not proven yet."))
        self.assertFalse(short._quoted("hype cycle cashing a check", "nothing like it here"))
        self.assertFalse(short._quoted("too short", "too short"))   # a two-word 'joke' is not a joke

    def test_vnorm_is_not_shadowed(self):
        self.assertEqual(short._vnorm("It's a dry, joke!"), "its a dry joke")
