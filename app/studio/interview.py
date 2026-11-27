"""Interview edit: your own footage where the ORIGINAL AUDIO leads and narration only bridges (owner, 2026-10-24).

Made for footage you shot yourself (no third-party copyright), e.g. a walk-and-talk interview filmed on the phone's front camera.
Nothing here strips the clip's sound (the rest of Video Studio does, by design, for third-party sources).

Steps (each cached in the work folder, so a re-run continues):
  1. prep        the interview's audio (16 kHz mono) for transcription
  2. transcribe  faster-whisper with word times (GPU large-v3-turbo, else CPU small.en) -> transcript.json
  3. plan        the script AI reads the numbered, timed transcript and picks the strongest moments (soundbites) and writes
                 short narration bridges (hook, place names, a line between topics, outro). One plan for a Short (<= ~90 s)
                 and one for a mini-documentary (8-12 min, chapters). Narration is at most ~15% of the runtime.
  4. cut         each soundbite cut WITH its audio: front-camera mirror removed (hflip), Short = 9:16 crop centred on the
                 faces found in it (OpenCV; centre if none), alternating gentle punch-ins; documentary = 16:9 as filmed.
                 Each bridge = narration (Kokoro) over your other clips from the same folder (B-roll, muted), or over the
                 next soundbite's picture when no B-roll fits.
  5. assemble    parts joined, captions burned in (soundbites: whisper's words; bridges: the narration text), music bed
                 ducked under every voice (music.mix).
Outputs: <work>/<name>_short.mp4, <work>/<name>_documentary.mp4, plus plan.json / transcript.json for review.
Visual work is ffmpeg on the CPU (no GPU effects); only transcription and Kokoro may use the GPU."""
import json
import math
import random
import re
import shutil
import time
from pathlib import Path

from . import media

VIDEO_EXT = (".mov", ".mp4", ".m4v", ".mkv")
CUT_VERSION = "19"   # bump when the way parts are cut changes, so cached parts are made again
RULES_SINCE = 1794236400   # 2026-11-09 10:00: pieces made since then without a key used today's shot rules - adopted, not re-cut
PIECE_VERSION = "1"  # bump when the way a soundbite's picture is MADE changes (the shot rules show up in its plan by themselves)
PLAN = """You edit a documentary from the creator's OWN interview footage. The interview audio is the star: viewers should mostly
hear the people on camera. Your narration only bridges: a hook, where we are, a line between topics, a closing line.

About the footage (from the creator): {context}

Transcript, numbered segments with times (seconds from the start of the footage):
{segments}

Other clips in the same folder you may use as B-roll under narration (name: seconds): {broll}

Research notes (checked facts with sources; the ONLY facts you may add beyond the transcript):
{notes}

Write JSON only:
{{
 "title": "a YouTube title, under 70 characters, honest, no clickbait",
 "short": {{"items": [ ... ]}},
 "documentary": {{"chapters": [{{"title": "chapter title", "items": [ ... ]}}, ...]}}
}}
Each item is one of:
  {{"bite": [first_segment, last_segment]}}  consecutive segments played with their own audio (the main thing);
  {{"narr": "one or two short sentences", "broll": "a clip name from the list, or empty"}}  a bridge;
  {{"insert": "one or two sentences of context", "note": <research note number>, "visual": "what a photo for it should show,
    3-8 words (e.g. 'Sea Venture shipwreck painting', 'Hamilton Bermuda harbour')", "card": "optional on-screen fact, under 40
    characters (e.g. '~1,000 Muslims, 0.8% of Bermuda')"}}  a short context tidbit right after the soundbite it explains.
Rules:
- short: {short_secs} seconds or less in total; open with the single most striking soundbite or a one-line hook, 3-6 soundbites.
- documentary: {doc_min}-{doc_max} minutes in total, 4-7 chapters in a sensible order (you may rearrange the footage's order).
- Soundbites: complete thoughts (start and end at sentence boundaries), each 6-60 seconds; skip small talk, dead air,
  directions, repeated points and anything the speaker would not want aired (private details, addresses, phone numbers).
- Context inserts: sparingly, only where a soundbite raises a question a viewer would have (history, numbers, what happened
  next). short: at most 2; documentary: one or two per chapter (use most of the notes somewhere). Every insert uses exactly one research note and says only what
  that note says. Narration and inserts together stay under 30% of the runtime; the interview stays the main driver.
- Narration: at most 15% of the runtime, first person plural is fine ("we"), never invent facts that are not in the transcript
  or the creator's note, respectful and warm toward the people and their faith; light humour only at our own expense.
- Never put words in anyone's mouth; a soundbite is exactly what was said."""


def _log(log, m):
    (log or print)(m)


def note_lines(notes):
    """Research notes for a prompt: [n] the fact, tagged with what kind it is (a fact on something the guest said, with the
    segment where it comes up, or a note on the place)."""
    out = []
    for i, n in enumerate(notes or []):
        tag = f"(about what is said at segment {n['seg']}: {n.get('topic', '')}) " if n.get("seg") is not None else \
              "(place) " if n.get("kind") == "place" else ""
        out.append(f"[{i}] {tag}{n['fact']}")
    return "\n".join(out) or "(none)"


TOPICS = """Here is a conversation the creator filmed. List the SPECIFIC things the guest mentions that a viewer would want
explained or put in context: a place, a law or status (e.g. dual citizenship), an institution, a group, an activity, an event,
a date, a number. Not the guest's private life (family, health, money), not small talk. Things at or near where the
conversation was filmed (its places, attractions, history) first. The query names the thing she means, never a brand or a
namesake she didn't name (sailing in the Caribbean -> 'sailing Caribbean islands', not a cruise line; skiing in Vermont ->
'skiing in Vermont', not one resort she never named).
About the footage: {context}
Transcript:
{segments}
Reply with JSON only: {{"topics": [{{"seg": <segment number where it comes up>, "topic": "2-6 words", "query": "an encyclopedia
search for it, 2-6 words, with the place if it helps (e.g. 'Bermudian status citizenship')"}}]}} - at most {n}, most interesting first."""

FACTS_FROM = """For each topic below, write ONE or TWO plain sentences that a documentary narrator could say to explain it, using
ONLY the search snippets given for that topic. If the snippets don't clearly cover the topic, write an empty string - never
guess, never use outside knowledge.
{items}
Reply with JSON only: {{"facts": ["sentence(s) for topic 1 or empty", "... topic 2", ...]}}"""


DIVES = """A documentary is built around the creator's conversation with a guest. Right after the guest mentions each topic
below, the film pauses for a short researched segment that tells the viewer more about it - concrete, colourful, visual, the
kind of detail a good travel documentary narrator gives. Write each segment ONLY from the reference text given for that topic:
never outside knowledge, never guesses, never the guest's private life. About {words} words per segment (fewer if the text is
thin). Leave a topic OUT when its reference is about a different place or thing than the one the guest means (another
island's law, a namesake, a person or event she never spoke of) or is only loosely related - check it against her words. Split each segment into lines of one or two sentences, and give each line
what to show under it (a photographable thing, 3-8 words, with the place, e.g. 'glass bottom boat Bermuda reef') and a short
on-screen card when a number, date or name stands out.
About the footage: {context}

{items}

For every topic say whether the reference fits what she means ("fits": false for a namesake, a brand, another place, or
anything she didn't actually talk about - write no lines then), and "thin": true only when the reference truly has too little
for about {words} words.
Reply with JSON only: {{"dives": [{{"topic": <topic number>, "fits": true, "thin": false, "title": "chapter title, 2-5 words", "lines": [{{"text": "...", "visual": "...", "card": "under 40 characters, or empty"}}]}}]}}"""


def wiki_article(q, chars=3500, topic=""):
    """(title, url, text) of the Wikipedia article that best matches q: the article's opening sections as plain text, or None.
    The title must share a word with the topic / query, and the page for a Bermuda query must mention Bermuda (Nov 8: the
    Cayman Islands' "Belonger status" and a Bahamas island came back for Bermuda topics)."""
    import requests
    h = {"User-Agent": "VideoStudio/1.0 (personal documentary research tool)"}
    for wait in (0, 4, 10, 20):   # Wikipedia answers 429 when asked too fast (Nov 8: half the topics lost): wait and ask again
        if wait: time.sleep(wait)
        r = requests.get("https://en.wikipedia.org/w/api.php", params={"action": "query", "list": "search", "srsearch": q, "srlimit": 5, "format": "json"},
                         headers=h, timeout=20)
        if r.status_code != 429: break
    titles = [x["title"] for x in r.json().get("query", {}).get("search", [])] if r.status_code == 200 else []
    stem = lambda w: w[:5]
    qw = {stem(w) for w in re.findall(r"[a-z]{4,}", (q + " " + topic).lower())} - {"bermu"}
    score = lambda t: len(qw & {stem(w) for w in re.findall(r"[a-z]{4,}", t.lower())})
    for best in sorted([t for t in titles if score(t)], key=lambda t: (-score(t), titles.index(t)))[:3]:
        r = requests.get("https://en.wikipedia.org/w/api.php", params={"action": "query", "prop": "extracts", "explaintext": 1, "exchars": chars,
                         "titles": best, "format": "json", "redirects": 1}, headers=h, timeout=20)
        pg = next(iter((r.json().get("query", {}).get("pages", {}) or {}).values()), {})
        text = re.sub(r"\n?=+ *(See also|References|External links|Further reading|Notes)[\s\S]*$", "", str(pg.get("extract") or ""))
        text = re.sub(r"\s+", " ", re.sub(r"=+ *([^=]+?) *=+", r"\1:", text)).strip()
        if len(text.split()) < 40: continue
        if "bermud" in q.lower() and "bermud" not in text.lower(): continue   # a Bermuda topic: never another island's page
        t = pg.get("title") or best
        return t, "https://en.wikipedia.org/wiki/" + t.replace(" ", "_"), text
    return None


def spell_as_noted(text, note):
    """A name the transcript misheard, spelled the way the creator's note spells it (Nov 8: the Short's title said "Nia
    Dixon"; the note says Nea): a capitalised word one letter away from a capitalised word of the note, same first letter."""
    names = {w for w in re.findall(r"\b[A-Z][a-z]{2,}", str(note or ""))}
    text = str(text or "")
    def fix(m):
        w = m.group(0)
        if w in names: return w
        nxt = re.match(r"\s+([A-Z][a-z]+)", text[m.end():])
        # an everyday word ("New Hampshire") is never "fixed" unless the note's next name follows it ("Nia Dixon")
        if w.lower() in COMMON_WORDS and not (nxt and nxt.group(1) in names): return w
        for n in names:
            if len(n) == len(w) and n[0] == w[0] and sum(x != y for x, y in zip(n, w)) == 1: return n
        return w
    return re.sub(r"\b[A-Z][a-z]{2,}", fix, text)


COMMON_WORDS = set("""new now not nor net nap and are but can did for get got had has her him his how its let may man men one our out own
put say see she the too two use was way who why yes yet you all any big boy day end far few god hot job law lot low mom old per
red run sat set sir six sun ten top try war win won also back bank bars bath bays been best boat body book born both busy call
came care case city come cost dark days deal dear does done door down each east easy else even ever face fact fair fall farm
fast feel feet fine fire five food four free from full gave girl give goes gold gone good hand hard have head hear heat help
here high hill home hope hour huge idea into just keep kept kind king knew know land last late left less life like line live
long look lord lost lots love made main make many mark mind miss more most move much must name near need next nice nine none
note once only open over park part pass past pick plan play pool port quite real rest ride road rock room rose said sale same
save says seen sell send ship shop show side sign some song soon sort star stay step stop such sure take talk tell than that
them then they this time told took town tree trip true turn type unit upon very view wait walk wall want warm wash week well
went were west what when wide wife wild will wind wise wish with word work yard year your""".split())


NAME_GENERIC = set("royal naval national museum island islands mountain mountains resort beach park house church street town city county "
              "state river lake north south east west great saint new old fort harbour harbor bermuda".split())


def named(title, heard):
    """Is the article's own name something said in the conversation or the creator's note? (Nov 8: she said she skied in
    Vermont and the research came back with Stowe and Bretton Woods, places she never named.) The main name - before a comma or
    bracket - needs one distinctive word that was heard."""
    main = re.split(r"[,(]", title)[0].lower()
    words = [w for w in re.findall(r"[a-z]{4,}", main) if w not in NAME_GENERIC]
    said = {w[:5] for w in re.findall(r"[a-z]{4,}", heard.lower())}
    return not words or any(w[:5] in said for w in words)


def deep_dives(cfg, segs, context, work, need_secs, log=print, n=12):
    """Researched segments where the guest mentions something (owner, 2026-11-08: "a 4-minute interview is too short to
    interest a viewer... the biggest improvement needed is colour, context media"): each thing she mentions is looked up
    (Wikipedia's article, else the search routes), and the AI writes a short segment from that text only, split into lines
    with a picture for each. Sized to fill need_secs. -> [{"seg", "title", "lines": [{"text", "visual", "card"}], "source",
    "topic"}], cached in deep_dives.json; [] on any failure (the film is made without them)."""
    out = Path(work) / "deep_dives.json"
    if out.exists(): return json.loads(out.read_text(encoding="utf-8"))
    if need_secs < 45: return []
    from .llm import LLM
    from . import websearch
    nseg = len(segs)
    lines = "\n".join(f"[{s['i']}] {s['text']}" for s in segs)
    def check(j):
        for t in j.get("topics") or []:
            if not 0 <= int(t.get("seg", -1)) < nseg: raise ValueError(f"segment {t.get('seg')} is outside 0-{nseg - 1}")
            if not str(t.get("query") or "").strip(): raise ValueError("every topic needs a query")
    try:
        with LLM(dict(cfg), log, lambda: False) as llm:
            topics = (llm.chat_json("You are a documentary researcher. Reply with JSON only.",
                                    TOPICS.format(context=context, segments=lines[:60000], n=n), max_tokens=1500, check=check).get("topics") or [])[:n]
            found, seen = [], set()
            heard = " ".join(s["text"] for s in segs) + " " + str(context or "")
            for t in topics:
                art = None
                try: art = wiki_article(t["query"], topic=str(t.get("topic") or ""))
                except Exception: pass
                if art and art[1].startswith("https://en.wikipedia.org/") and not named(art[0], heard):
                    _log(log, f"  segment {t['seg']} ({t['topic']}): \"{art[0]}\" is never named in the conversation - left out"); continue
                if not art:
                    try:
                        sn, route = websearch.search(cfg, t["query"], log)
                        if sn: art = (t["topic"], f"{route} search: {t['query']}", " ".join(sn[:4]))
                    except Exception: pass
                if art and art[1] not in seen: seen.add(art[1]); found.append((t, art))
            _log(log, f"Interview: researched segments - {len(found)} of {len(topics)} thing(s) the guest mentions found")
            if not found: out.write_text("[]", encoding="utf-8"); return []
            words = int(max(50, min(220, need_secs * 2.6 / len(found))))
            said = lambda i: " ".join(s["text"] for s in segs[max(0, i - 1):i + 2])
            items = "\n\n".join(f"Topic {k + 1}: {t['topic']} (reference: {a[0]})\nWhat the guest said: \"{said(int(t['seg']))}\"\nReference text:\n{a[2][:3500]}"
                                for k, (t, a) in enumerate(found))
            def check2(j):
                for d in j.get("dives") or []:
                    if not 1 <= int(d.get("topic", 0)) <= len(found): raise ValueError(f"topic {d.get('topic')} is not 1-{len(found)}")
                    if d.get("fits") is False: continue
                    if not d.get("lines"): raise ValueError("every segment that fits needs lines")
                    w = sum(len(str(x.get("text") or "").split()) for x in d["lines"])
                    if w < 0.6 * words and not d.get("thin"): raise ValueError(f"topic {d['topic']} has {w} words; write about {words} (or say thin)")
            dv = llm.chat_json("You are a careful documentary writer. Reply with JSON only.", DIVES.format(words=words, context=context, items=items),
                               max_tokens=8000, check=check2).get("dives") or []
    except Exception as e:
        if str(e) == "cancelled": raise
        _log(log, f"  researched segments skipped ({str(e)[:120]})"); return []
    res, budget = [], need_secs * 2.6 * 1.15
    for d in dv:
        t, a = found[int(d["topic"]) - 1]
        if d.get("fits") is False:
            _log(log, f"  segment {t['seg']} ({t['topic']}): {a[0]} doesn't fit what she means - left out"); continue
        ls = [{"text": re.sub(r"\s+", " ", str(x.get("text") or "")).strip(), "visual": str(x.get("visual") or "")[:80], "card": str(x.get("card") or "")[:40]}
              for x in d.get("lines") or [] if len(str(x.get("text") or "").split()) >= 4]
        keep = []
        for x in ls:
            if budget - len(x["text"].split()) < 0: break
            budget -= len(x["text"].split()); keep.append(x)
        if keep: res.append({"seg": int(t["seg"]), "title": str(d.get("title") or t["topic"])[:40], "lines": keep, "source": a[1], "topic": str(t["topic"])[:60]})
    for r_ in res: _log(log, f"  segment {r_['seg']}: \"{r_['title']}\" - {len(r_['lines'])} line(s), {sum(len(x['text'].split()) for x in r_['lines'])} words ({r_['source']})")
    out.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    return res


def topic_notes(cfg, segs, context, work, log=print, n=6):
    """Commentary where the guest gets specific (owner, 2026-11-08: "when she talks about specifics, that is where our
    commentary comes in"): the AI lists what the guest mentions, each is looked up (Wikipedia first, then the other search
    routes; a blocked route just moves on), and the AI turns only the snippets into a fact. -> [{"fact", "source", "topic",
    "seg"}], cached in topic_notes.json; [] on any failure (the video is made without them)."""
    out = Path(work) / "topic_notes.json"
    if out.exists(): return json.loads(out.read_text(encoding="utf-8"))
    if not cfg.get("interview_topic_notes", True): return []
    from .llm import LLM
    from . import websearch
    nseg = len(segs)
    lines = "\n".join(f"[{s['i']}] {s['text']}" for s in segs)
    def check(j):
        for t in j.get("topics") or []:
            if not 0 <= int(t.get("seg", -1)) < nseg: raise ValueError(f"segment {t.get('seg')} is outside 0-{nseg - 1}")
            if not str(t.get("query") or "").strip(): raise ValueError("every topic needs a query")
    try:
        with LLM(dict(cfg), log, lambda: False) as llm:
            topics = (llm.chat_json("You are a documentary researcher. Reply with JSON only.",
                                    TOPICS.format(context=context, segments=lines[:60000], n=n), max_tokens=1500, check=check).get("topics") or [])[:n]
            found = []
            for t in topics:
                snips, route = [], ""
                try: snips, route = websearch._wikipedia(cfg, t["query"]), "wikipedia"
                except Exception: pass
                if not snips: snips, route = websearch.search(cfg, t["query"], log)
                if snips: found.append((t, snips[:3], route))
            _log(log, f"Interview: {len(topics)} thing(s) the guest mentions, {len(found)} found in research")
            if not found:
                out.write_text("[]", encoding="utf-8"); return []
            items = "\n\n".join(f"Topic {k + 1}: {t['topic']}\nSnippets:\n" + "\n".join(f"- {s[:600]}" for s in sn) for k, (t, sn, _r) in enumerate(found))
            facts = llm.chat_json("You are a careful fact writer. Reply with JSON only.", FACTS_FROM.format(items=items), max_tokens=2000).get("facts") or []
    except Exception as e:
        if str(e) == "cancelled": raise
        _log(log, f"  topic research skipped ({str(e)[:120]}); the video uses the place notes only"); return []
    res = []
    for (t, sn, route), f in zip(found, facts):
        f = re.sub(r"\s+", " ", str(f or "")).strip()
        if len(f.split()) < 6: continue
        fw = set(re.findall(r"[a-z]{4,}", f.lower()))   # the source is the snippet the fact came from, not just the first one
        best = max(sn, key=lambda x: len(fw & set(re.findall(r"[a-z]{4,}", x.lower()))))
        title = best.split(":", 1)[0].strip() if route == "wikipedia" else ""
        src = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}" if title else f"{route} search: {t['query']}"
        res.append({"fact": f, "source": src, "topic": str(t["topic"])[:60], "seg": int(t["seg"]), "kind": "topic"})
        _log(log, f"  segment {t['seg']} ({t['topic']}): {f[:90]}")
    out.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    return res


# ------------------------------------------------------------------ 1-2 transcript
_GPU_BAD = False
def cfg_gpu_ok():
    """GPU whisper only when the CUDA BLAS library is present (else ctranslate2 fails, and a retry hangs)."""
    import ctypes.util, os
    if os.name != "nt": return True
    return any(os.path.exists(os.path.join(d, "cublas64_12.dll")) for d in os.environ.get("PATH", "").split(os.pathsep) if d)


def transcribe(src, work, log=print):
    out = Path(work) / "transcript.json"
    if out.exists(): return json.loads(out.read_text(encoding="utf-8"))
    wav = Path(work) / "audio16k.wav"
    if media.ok(wav, 1) and media.dur(wav) < media.dur(src) - 2:   # Nov 9: a restart mid-extraction left 2 min of a 26-min drive
        wav.unlink()
    if not media.ok(wav, 1):
        _log(log, "Interview: extracting the audio for transcription")
        part = wav.with_name("audio16k.part.wav")   # written whole, then named: a cut-off file is never taken for the audio
        media.ff("-i", src, "-map", "0:a:0", "-ac", 1, "-ar", 16000, part, timeout=1800)
        part.replace(wav)
    from faster_whisper import WhisperModel
    def run(m, label):
        _log(log, f"Interview: transcribing {media.dur(wav) / 60:.0f} min ({label})")
        segs, _ = m.transcribe(str(wav), language="en", vad_filter=True, word_timestamps=True, beam_size=5)
        res, t0 = [], time.time()
        for s in segs:
            res.append({"i": len(res), "start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip(),
                        "words": [[w.word.strip(), round(w.start, 2), round(w.end, 2)] for w in (s.words or []) if w.word.strip()]})
            if time.time() - t0 > 60: _log(log, f"  transcribed to {int(s.end // 60)} min"); t0 = time.time()
        return res
    global _GPU_BAD
    # a second CUDA attempt after a failed one (missing cublas) hangs forever, so try the GPU once per process
    if not _GPU_BAD and cfg_gpu_ok():
        try: segs = run(WhisperModel("large-v3-turbo", device="cuda", compute_type="float16"), "GPU")
        except Exception as e:
            _GPU_BAD = True
            _log(log, f"  GPU transcription unavailable ({str(e)[:80]}); CPU")
            segs = run(WhisperModel("small.en", device="cpu", compute_type="int8"), "CPU")
    else:
        try: segs = run(WhisperModel("small.en", device="cpu", compute_type="int8"), "CPU")
        except RuntimeError as e:   # the PC short of memory for a moment (the vision model / a news render): wait, try once more
            if "alloc" not in str(e): raise
            _log(log, "  transcription ran out of memory; waiting 2 min and trying again"); time.sleep(120)
            segs = run(WhisperModel("small.en", device="cpu", compute_type="int8"), "CPU")
    out.write_text(json.dumps(segs, indent=1, ensure_ascii=False), encoding="utf-8")
    return segs


# ------------------------------------------------------------------ 3 plan
def broll_list(src):
    """Your other clips in the interview's folder (name -> seconds), for narration bridges."""
    out = {}
    for f in sorted(Path(src).parent.iterdir()):
        if f.suffix.lower() in VIDEO_EXT and f.resolve() != Path(src).resolve():
            try: d = media.dur(f)
            except Exception: continue
            if d >= 3: out[f.stem] = round(d, 1)
    return out


def plan(cfg, segs, context, broll, work, log=print, short_secs=90, doc_min=8, doc_max=12, notes=None):
    out = Path(work) / "plan.json"
    if out.exists(): return json.loads(out.read_text(encoding="utf-8"))
    lines = "\n".join(f"[{s['i']}] {s['start']:.0f}-{s['end']:.0f}: {s['text']}" for s in segs)
    from .llm import LLM
    n = len(segs)
    def check(j, strict=True):
        for it in _items(j.get("short") or {}) + [x for c in (j.get("documentary") or {}).get("chapters", []) for x in c.get("items", [])]:
            if "bite" in it:
                a, b = it["bite"]
                if not (0 <= int(a) <= int(b) < n): raise ValueError(f"bite {it['bite']} is outside 0-{n - 1}")
                d = segs[int(b)]["end"] - segs[int(a)]["start"]
                if strict and d > 75: raise ValueError(f"bite {it['bite']} is {d:.0f} s long; soundbites must be 6-60 s (use fewer segments)")
        if not _items(j.get("short") or {}) or not (j.get("documentary") or {}).get("chapters"): raise ValueError("both short and documentary are needed")
        if strict:
            st = total_secs(_items(j["short"]), segs)
            if st > short_secs + 20: raise ValueError(f"the short is {st:.0f} s; it must be {short_secs} s or less")
            dt = total_secs([x for c in j["documentary"]["chapters"] for x in c.get("items", [])], segs) / 60
            if not doc_min - 2 <= dt <= doc_max + 3: raise ValueError(f"the documentary is {dt:.1f} min; it must be {doc_min}-{doc_max} min")
    _log(log, f"Interview: picking soundbites from {n} segments (script AI)")
    with LLM(dict(cfg), log, lambda: False) as llm:
        try: j = llm.chat_json("You are an award-winning documentary editor. Reply with JSON only.",
                          PLAN.format(context=context, segments=lines[:60000], broll=json.dumps(broll), short_secs=short_secs,
                                      notes=note_lines(notes),
                                      doc_min=doc_min, doc_max=doc_max), max_tokens=6000, check=check)
        except RuntimeError:   # lengths still off after 3 tries: take a valid plan and trim it to size
            j = llm.chat_json("You are an award-winning documentary editor. Reply with JSON only.",
                              PLAN.format(context=context, segments=lines[:60000], broll=json.dumps(broll), short_secs=short_secs,
                                          notes=note_lines(notes),
                                          doc_min=doc_min, doc_max=doc_max), max_tokens=6000, check=lambda x: check(x, False))
    j = fit(j, segs, short_secs)
    out.write_text(json.dumps(j, indent=1, ensure_ascii=False), encoding="utf-8")
    return j


SHORT = """Cut a YouTube Short of about {short_secs} seconds (at least {short_secs} minus 30) from a mini-documentary's material.
The interview audio leads.
About the footage: {context}
The documentary's soundbites (segment numbers with their words) and context notes:
{material}
Rules: tell ONE story with a beginning, middle and end that a tourist would want to watch. It MUST touch each of these topics,
using the guest's own words about them where they come up: {topics}. Open with the most gripping soundbite (under 20 seconds);
then 6-10 more soundbites (each 5-20 seconds; a sub-range of a soundbite's segments is fine), joined by up to five one-sentence
narration lines that carry the story forward (right after the opener, one of them says who the guest is and where);
keep the creator's question in front of an answer that doesn't make sense without it; up to THREE context inserts from the notes (only what the note says);
END ON A SOUNDBITE that lands the story. No small talk, no logistics, nothing off-thread.
Narration only connects: it never states anything that is not in the transcript or the notes, never links two facts that the
sources don't link (e.g. do not claim the mosque stands on land that was taken), and attributes the speaker's claims to them
("she says ...", "as he tells it ...") instead of stating them as fact.
Also write "hook": an on-screen line for the opening soundbite, under 45 characters, true to the story.
Reply with JSON only: {{"hook": "...", "items": [{{"bite": [first, last]}} | {{"narr": "...", "broll": ""}} | {{"insert": "...", "note": n, "visual": "...", "card": "..."}}]}}"""


def short_plan(cfg, segs, pl, context, notes, work, log=print, short_secs=90, tri=None):
    """The Short, cut from the documentary's chosen material (a separate pass: one plan for both came out weak). Cached."""
    import hashlib
    topics = ", ".join(cfg.get("interview_short_topics") or []) or "(the strongest thread)"
    out = Path(work) / f"short_plan_{hashlib.md5((SHORT + topics + str(short_secs) + str(bool(tri))).encode()).hexdigest()[:6]}.json"   # a changed brief makes a new plan
    if cfg.get("interview_short_file") and Path(cfg["interview_short_file"]).exists():   # a Short plan written by hand
        return json.loads(Path(cfg["interview_short_file"]).read_text(encoding="utf-8"))
    if out.exists():
        j = json.loads(out.read_text(encoding="utf-8")); j["items"] = end_on_bite(_items(j)); return j
    from .llm import LLM
    if tri:   # everything the documentary keeps, from every clip (the drive, the third floor, the kebab window)
        bites = [tuple(it["bite"]) for it in augment_items(tri, segs) if "bite" in it]
    else:
        bites = sorted({tuple(it["bite"]) for c in pl["documentary"]["chapters"] for it in c.get("items", []) if "bite" in it})
    mat = "\n".join(f"[{a}-{b}] " + " ".join(segs[i]["text"] for i in range(a, b + 1))[:600] for a, b in bites)
    mat += "\n\nNotes:\n" + note_lines(notes)
    n = len(segs)
    def check(j, strict=True):
        its = j.get("items") or []
        if not its or not any("bite" in it for it in its): raise ValueError("the Short needs soundbites")
        if strict and "bite" not in its[-1]: raise ValueError("the Short must END on a soundbite, not narration")
        for it in its:
            if "bite" in it:
                a, b = int(it["bite"][0]), int(it["bite"][1])
                if not 0 <= a <= b < n: raise ValueError(f"bite {it['bite']} out of range")
                if strict and segs[b]["end"] - segs[a]["start"] > 30: raise ValueError(f"bite {it['bite']} is longer than 20 s; use fewer segments")
        if strict and total_secs(its, segs) > short_secs + 20: raise ValueError(f"the Short is {total_secs(its, segs):.0f} s; keep it under {short_secs} s")
    try:
        with LLM(dict(cfg), log, lambda: False) as llm:
            try: j = llm.chat_json("You are a YouTube Shorts editor. Reply with JSON only.",
                                   SHORT.format(short_secs=short_secs, context=context, material=mat[:60000], topics=topics), max_tokens=1500, check=check)
            except RuntimeError:   # still too long after 3 tries: take its story and trim it to size
                j = llm.chat_json("You are a YouTube Shorts editor. Reply with JSON only.",
                                  SHORT.format(short_secs=short_secs, context=context, material=mat[:60000], topics=topics), max_tokens=1500,
                                  check=lambda x: check(x, False))
        j = fit({"short": j, "documentary": {"chapters": []}}, segs, short_secs, bite_max_short=22)["short"]
        j["items"] = end_on_bite(_items(j))
    except Exception as e:
        _log(log, f"  short plan: kept the combined plan's Short ({str(e)[:100]})"); return None
    out.write_text(json.dumps(j, indent=1, ensure_ascii=False), encoding="utf-8")
    return j


TRIAGE = """You edit a documentary from the creator's OWN footage of a conversation: the creator asks the questions, the guest
answers. The real conversation is the backbone: keep it in its original order with the creator's questions in front of the
answers they lead to (the viewer hears the question, then the answer), and AUGMENT it: a preview first, then background, then
the conversation with commentary where the guest gets specific.

About the footage (from the creator): {context}

Transcript, numbered segments with times (seconds):
{segments}

Research notes (checked facts with sources; the ONLY facts you may add):
{notes}

Mark up the WHOLE conversation. Reply with JSON only:
{{
 "title": "a YouTube title, under 70 characters, honest, no clickbait",
 "guest": "the guest's name as said in the conversation or the creator's note, else empty",
 "preview": [[first_seg, last_seg], ...],
 "background": [{{"text": "one or two sentences", "visual": "what to show under it, 3-8 words, e.g. 'Royal Naval Dockyard Bermuda clock tower'"}}, ...],
 "drop": [[first_seg, last_seg, "why"], ...],
 "facts": [{{"after": seg, "note": n, "text": "1-2 sentences, only what note n says", "visual": "what a photo for it should show, 3-8 words", "card": "on-screen fact under 40 characters"}}],
 "jokes": [{{"after": seg, "text": "one light, warm line"}}],
 "chapters": [{{"at": seg, "title": "2-5 words"}}],
 "pictures": [{{"from": seg, "to": seg, "visual": "the photographable thing being talked about, 3-8 words, e.g. 'Tucker's Town Bermuda', 'St. George's Bermuda harbour', or empty", "away": true}}]
}}
Rules:
- preview: 2-4 of the most intriguing moments from LATER in the conversation (each 3-10 seconds, a complete phrase, the guest
  speaking), played first as a "coming up" teaser, before anything else.
- background: 2-4 short narration lines that come after the preview and before the conversation: who the guest is, where and
  when the conversation happened, and why it is worth hearing. Only what the creator's note, the notes or the transcript say.
- drop ONLY what is boring or repetitive: small talk, directions and logistics, dead air, the same point said again, chatter about
  the drive, anything private (addresses, phone numbers, money of private people). Everything else stays, however long the video
  gets. Each "next clip" is a place the creator chose to film (the kebab window, a hallway, a room): drop chatter
  inside it, but never a whole clip - keep its best stretch, where we see and hear what that place is. Never drop a question whose answer stays: a question goes only when it is repeated or its answer is dropped.
- facts: commentary where the guest gets specific. A note marked "(about what is said at segment N)" goes right after
  segment N, at the end of that answer (use every one of those). A "(place)" note goes in the background lines or at a change of
  topic (a new chapter), never in the middle of a personal answer. "after" = the segment after which the talk pauses for the fact (it must end a sentence); the
  conversation picks up again at the next segment. Use most notes once; never two facts in a row; at most one fact per ~90
  seconds of talk. A fact must be about what is being talked about in the 3 segments before "after" - the same place, person
  or event named there. Never put a fact about one place where the talk is about another (e.g. Horseshoe Bay is not Tucker's
  Town); if no note fits what is said at a pause, put no fact there.
- jokes: at most 3, only in relaxed moments (the drive, food, sports, the weather), gentle and at our own expense; NEVER near
  slavery, race, land losses, poverty or religion.
- chapters: 4-8, at the segment where a new topic starts; the first at segment 0.
- pictures: cover every kept stretch with what is being talked about (one entry per topic stretch); empty when it's personal talk.
  "away": true when the guest describes a place or thing somewhere else (a beach, a boat tour, a town, an event) - the viewer
  then sees that place with the guest talking from a corner; false when the talk is about the guest or the room you are in.
- Spell people's and places' names the way the creator's note spells them (the transcript may mishear a name).
- Never put words in anyone's mouth; never invent facts."""


def triage(cfg, segs, context, notes, work, log=print):
    """The augment plan for the documentary (owner, 2026-10-24): keep the conversation in order (questions in front of their
    answers), drop only boring or repeated stretches, pause for facts where the guest gets specific, a few light jokes in relaxed
    moments; open with a preview of later moments and a few background lines (owner, 2026-11-08). Cached in triage.json."""
    out = Path(work) / "triage.json"
    if cfg.get("interview_triage_file") and Path(cfg["interview_triage_file"]).exists():   # an edit plan written by hand
        return json.loads(Path(cfg["interview_triage_file"]).read_text(encoding="utf-8"))
    if out.exists(): return json.loads(out.read_text(encoding="utf-8"))
    n = len(segs)
    lines = "\n".join((f"--- next clip of the same afternoon (clip {x['src']}) ---\n" if k and x.get("src", 0) != segs[k - 1].get("src", 0) else "")
                      + f"[{x['i']}] {x['start']:.0f}-{x['end']:.0f}: {x['text']}" for k, x in enumerate(segs))
    span = lambda a, b: sum(x["end"] - x["start"] for x in segs[a:b + 1])   # clips restart at 0 s: sum segments, never end - start
    total = span(0, n - 1)
    def check(j):
        for d in j.get("drop") or []:
            if int(d[1]) >= n and int(d[0]) < n: d[1] = n - 1                # a range running past the end: clamp, don't refuse
            if not (0 <= int(d[0]) <= int(d[1]) < n): raise ValueError(f"drop {d[:2]} is outside 0-{n - 1}")
        for f in (j.get("facts") or []) + (j.get("jokes") or []):
            if not 0 <= int(f.get("after", -1)) < n: raise ValueError(f"'after' {f.get('after')} is outside 0-{n - 1}")
            if not str(f.get("text") or "").strip(): raise ValueError("every fact / joke needs text")
        for f in j.get("facts") or []:
            if notes and not (str(f.get("note", "")).isdigit() and int(f["note"]) < len(notes)): raise ValueError(f"fact note {f.get('note')} isn't a note number")
        if not j.get("chapters"): raise ValueError("chapters are needed")
        if len(j.get("preview") or []) > 4: j["preview"] = j["preview"][:4]   # too many teasers: the first four, don't refuse
        for p in j.get("preview") or []:
            if not (isinstance(p, list) and len(p) >= 2 and 0 <= int(p[0]) <= int(p[1]) < n): raise ValueError(f"preview {p} is outside 0-{n - 1}")
        # a teaser too long (Nov 9: three tries of 24-64 s ended a 38-minute interview): shortened from its end to its first
        # segment(s); one that is still too long is left out - a long teaser never fails the job
        pv = []
        for p in j.get("preview") or []:
            a, b = int(p[0]), int(p[1])
            while b > a and span(a, b) > 15: b -= 1
            if span(a, b) <= 15: pv.append([a, b] + list(p[2:]))
        j["preview"] = pv
        bg = j.get("background") or []
        if not isinstance(bg, list) or len(bg) > 4 or not all(isinstance(x, str) or (isinstance(x, dict) and str(x.get("text") or "").strip()) for x in bg):
            raise ValueError("background is a list of up to 4 lines, each {text, visual}")
        dropped = sum(span(int(d[0]), int(d[1])) for d in j.get("drop") or [])
        if dropped > 0.6 * total: raise ValueError(f"{dropped / 60:.0f} min dropped; drop only boring or repeated stretches")
    _log(log, f"Interview: marking up all {n} segments (keep / drop / facts / jokes; script AI)")
    from .llm import LLM
    with LLM(dict(cfg), log, lambda: False) as llm:
        j = llm.chat_json("You are an award-winning documentary editor. Reply with JSON only.",
                          TRIAGE.format(context=context, segments=lines[:140000],
                                        notes=note_lines(notes)),
                          max_tokens=6000, check=check)
    for f in j.get("facts") or []:   # a fact on what the guest said: right where it is said (Nov 8: facts landed after unrelated answers)
        nt = (notes or [])[int(f["note"])] if str(f.get("note", "")).isdigit() and int(f["note"]) < len(notes or []) else {}
        if nt.get("seg") is not None and abs(int(f["after"]) - int(nt["seg"])) > 3: f["after"] = min(n - 1, int(nt["seg"]) + 1)
    out.write_text(json.dumps(j, indent=1, ensure_ascii=False), encoding="utf-8")
    dropped = sum(span(int(d[0]), int(d[1])) for d in j.get("drop") or [])
    _log(log, f"  triage: {len(j.get('preview') or [])} preview moment(s), {len(j.get('background') or [])} background line(s), "
              f"{len(j.get('drop') or [])} stretch(es) dropped ({dropped / 60:.1f} min), {len(j.get('facts') or [])} fact(s), "
              f"{len(j.get('jokes') or [])} joke(s), {len(j.get('chapters') or [])} chapter(s)")
    return j


def _cut_points(segs, a, b):
    """Audio in/out for segments a..b: halfway into the pauses around them, so pieces of one stretch join without repeats."""
    same = lambda i, j: segs[i].get("src", 0) == segs[j].get("src", 0)
    t0 = segs[a]["start"] - 0.15 if a == 0 or not same(a, a - 1) else max(segs[a]["start"] - 0.4, (segs[a - 1]["end"] + segs[a]["start"]) / 2)
    t1 = segs[b]["end"] + 0.3 if b == len(segs) - 1 or not same(b, b + 1) else min(segs[b]["end"] + 0.4, (segs[b]["end"] + segs[b + 1]["start"]) / 2)
    return max(0.0, t0), t1


def augment_items(tri, segs, max_bite=45.0):
    """Triage -> items: the preview (teaser bites from later on, then the title card), the background lines, then the
    conversation in its order (bites, split at facts / jokes / chapters and every ~max_bite s so each gets its own picture),
    the facts and jokes at their pauses, dropped stretches gone."""
    n = len(segs)
    ends = lambda i: 0 <= i < n and segs[i]["text"].rstrip()[-1:] in ".?!"
    drop = set()
    for d in tri.get("drop") or []:   # never cut him off mid-sentence: a drop starts after a finished sentence and ends at one
        a, b = int(d[0]), int(d[1])
        if tri.get("exact"): drop.update(range(a, b + 1)); continue   # a hand-written plan: its drops as written
        for _ in range(3):
            if a <= b and a > 0 and not ends(a - 1): a += 1
        for _ in range(3):
            if b >= a and not ends(b): b -= 1
        if a <= b: drop.update(range(a, b + 1))
    def at_end(i):   # a fact / joke waits for the end of his sentence
        for j in range(i, min(n, i + 4)):
            if ends(j): return j
        return i
    after = {}
    for f in tri.get("facts") or []: after.setdefault(at_end(int(f["after"])), []).append(dict(insert=f["text"], note=f.get("note"), visual=f.get("visual", ""), card=f.get("card", "")))
    for f in tri.get("jokes") or []: after.setdefault(at_end(int(f["after"])), []).append(dict(narr=f["text"], broll="", joke=True))
    for dv in tri.get("dives") or []:   # a researched segment on what the guest just mentioned (owner, 2026-11-08: colour, length)
        lines = [x for x in dv.get("lines") or [] if str(x.get("text") or "").strip()]
        if not lines or not 0 <= int(dv.get("seg", -1)) < n: continue
        for m, x in enumerate(lines):
            after.setdefault(at_end(int(dv["seg"])), []).append(dict(insert=str(x["text"]).strip(), visual=str(x.get("visual") or dv.get("title") or ""),
                                                                     card=str(x.get("card") or (dv.get("title") if m == 0 else "") or ""),
                                                                     source=str(dv.get("source") or ""), dive=True))
    chap = {int(c["at"]): str(c.get("title") or "")[:40] for c in tri.get("chapters") or [] if 0 <= int(c.get("at", -1)) < n}
    pics = [(int(p.get("from", 0)), int(p.get("to", 0)), str(p.get("visual") or ""), bool(p.get("away"))) for p in tri.get("pictures") or []]
    def visual(a, b): return next((v for f, t, v, _w in pics if v and f <= b and t >= a), "")
    def away(a, b): return next((w for f, t, v, w in pics if v and f <= b and t >= a), False)
    items, cur, pend_chap = [], None, ""
    def flush():
        nonlocal cur
        if cur:
            it = {"bite": [cur[0], cur[1]], "visual": visual(cur[0], cur[1])}
            if cur[2]: it["chapter"] = cur[2]
            if it["visual"] and away(cur[0], cur[1]): it["pip"] = True   # she describes somewhere else: that place, her in a corner
            items.append(it); cur = None
    for i in range(n):
        if i in chap:
            flush(); pend_chap = chap[i]
        if cur and segs[i].get("src", 0) != segs[cur[0]].get("src", 0): flush()   # a new clip starts a new piece
        if i in drop:
            flush()
        else:
            if cur and segs[i]["end"] - segs[cur[0]]["start"] > max_bite and segs[i - 1]["text"].rstrip()[-1:] in ".?!": flush()
            if cur is None: cur = [i, i, pend_chap]; pend_chap = ""
            else: cur[1] = i
        if i in after:
            flush()
            for x in after[i]:
                if not tri.get("exact") and not x.get("dive") and items and ("insert" in x or "narr" in x) and ("insert" in items[-1] or "narr" in items[-1]): continue   # never two in a row
                items.append(x)
    flush()
    head = []
    for p in tri.get("preview") or []:   # "coming up": the most intriguing moments from later on, first
        a, b = int(p[0]), int(p[1])
        if 0 <= a <= b < n: head.append({"bite": [a, b], "visual": visual(a, b), "preview": True})
    if head: head[-1]["title_after"] = True   # the title card comes after the teasers
    for line in tri.get("background") or []:   # who, where, why: before the conversation starts
        t_, v_ = (line.get("text"), line.get("visual")) if isinstance(line, dict) else (line, "")
        if str(t_ or "").strip(): head.append({"narr": str(t_).strip(), "broll": "", "background": True, "visual": str(v_ or "")})
    return head + items


def keep_every_clip(tri, segs, most=45.0, whole=75.0, log=print):
    """Every clip the creator added stays in the video (owner, Nov 11: "why leave out menu? it more authentic"): the triage
    dropped six of eight clips whole as small talk, the kebab window's menus with them. A clip whose every segment is dropped
    gets its best stretch back - all of it when it is short (up to `whole` s), else the wordiest run of up to `most` s."""
    if not tri: return tri
    n = len(segs)
    gone = set(i for d in tri.get("drop") or [] for i in range(int(d[0]), min(n - 1, int(d[1])) + 1))
    by = {}
    for x in segs: by.setdefault(x.get("src", 0), []).append(x["i"])
    keep = set()
    for k, ii in by.items():
        if not k or not all(i in gone for i in ii): continue   # the main clip, or a clip with something kept: as triaged
        span = lambda a, b: segs[b]["end"] - segs[a]["start"]
        if span(ii[0], ii[-1]) <= whole: best = (ii[0], ii[-1])
        else:
            best, top = (ii[0], ii[0]), -1
            for a in range(len(ii)):
                for b in range(a, len(ii)):
                    if span(ii[a], ii[b]) > most: break
                    w = sum(len(segs[i]["text"].split()) for i in ii[a:b + 1])
                    if w > top: best, top = (ii[a], ii[b]), w
        keep.update(range(best[0], best[1] + 1))
        _log(log, f"  clip {k} was dropped whole: its segments {best[0]}-{best[1]} ({span(*best):.0f}s) kept")
    if not keep: return tri
    drop = []
    for d in tri.get("drop") or []:   # split each drop around the kept segments
        a, b, rest = int(d[0]), int(d[1]), list(d[2:])
        run = None
        for i in range(a, b + 1):
            if i in keep:
                if run: drop.append([run[0], run[1]] + rest); run = None
            else: run = [run[0] if run else i, i]
        if run: drop.append([run[0], run[1]] + rest)
    return dict(tri, drop=drop)


def total_secs(items, segs):
    """Rough runtime: soundbites from the transcript, narration at ~2.6 words a second."""
    t = 0.0
    for it in items:
        if "bite" in it: t += segs[int(it["bite"][1])]["end"] - segs[int(it["bite"][0])]["start"] + 0.45
        else: t += len(str(it.get("narr") or it.get("insert") or "").split()) / 2.6 + 0.35
    return t


def fit(j, segs, short_secs=90, bite_max_short=40, bite_max_doc=60):
    """Hard limits the script AI sometimes ignores: long soundbites end at a segment boundary within the limit; the Short loses
    items from the end until it fits (the opening is kept)."""
    def trim(items, mx):
        for it in items:
            if "bite" in it:
                a, b = int(it["bite"][0]), int(it["bite"][1])
                while b > a and segs[b]["end"] - segs[a]["start"] > mx: b -= 1
                it["bite"] = [a, b]
    trim(_items(j["short"]), bite_max_short)
    for c in j["documentary"]["chapters"]: trim(c.get("items", []), bite_max_doc)
    items = _items(j["short"])
    while len(items) > 2 and total_secs(items, segs) > short_secs + 10: items.pop(-2 if "narr" in items[-1] else -1)
    j["short"]["items"] = items
    return j


def _items(p):
    return list(p.get("items") or [])


def end_on_bite(items):
    """The Short ends on the guest's own words: trailing narration / context lines are dropped (the AI sometimes adds one)."""
    items = list(items)
    while len(items) > 1 and "bite" not in items[-1]: items.pop()
    return items


# ------------------------------------------------------------------ 4 cut
def _faces_x(src, t0, t1, flip=True):
    """Average horizontal centre (0..1) of faces in 3 frames of the range, or None (OpenCV; None when it isn't installed)."""
    try:
        import cv2
        import numpy as np  # noqa: F401
    except ImportError:
        return None
    casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    xs = []
    tmp = Path(media.__file__).parent / "_facegrab.jpg"
    for k in range(3):
        t = t0 + (t1 - t0) * (k + 1) / 4
        try:
            media.ff("-ss", f"{t:.2f}", "-i", src, "-frames:v", 1, "-vf", ("hflip," if flip else "") + "scale=640:-2", tmp, timeout=120)
            im = cv2.imread(str(tmp)); g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
            for (x, y, w, h) in casc.detectMultiScale(g, 1.15, 5, minSize=(40, 40)):
                xs.append(((x + w / 2) / im.shape[1], w * h))
        except Exception:
            continue
    try: tmp.unlink()
    except OSError: pass
    if not xs: return None
    tot = sum(a for _, a in xs)
    return sum(x * a for x, a in xs) / tot


def _vf(kind, xc=None, zoom=1.0, flip=True):
    """Video filter: mirror removed, then Short = 9:16 crop around xc (0..1) / documentary = 16:9; gentle zoom."""
    f = ["hflip"] if flip else []
    if kind == "short":
        xc = 0.5 if xc is None else min(max(xc, 0.0), 1.0)
        cw = f"ih*9/16/{zoom}"; ch = f"ih/{zoom}"
        f.append(f"crop={cw}:{ch}:max(0\\,min(iw-{cw}\\,iw*{xc:.3f}-{cw}/2)):(ih-{ch})/2")
        f.append("scale=1080:1920:flags=lanczos")
    else:
        if zoom > 1.0: f.append(f"crop=iw/{zoom}:ih/{zoom}")
        f.append("scale=1920:1080:force_original_aspect_ratio=decrease:flags=lanczos,pad=1920:1080:(ow-iw)/2:(oh-ih)/2")
    f.append("fps=30,format=yuv420p,setsar=1")
    return ",".join(f)


GENERIC = {"town", "city", "old", "aerial", "view", "island", "history", "historic", "photo", "image", "street", "people", "building",
           "buildings", "area", "land", "house", "houses", "black", "white", "early", "modern", "today", "parish", "local"}
FITS = """A documentary narrator says: "{line}"
Wikimedia Commons offers a photo titled: "{title}"
Would that photo be a fitting, honest illustration of what the narrator says (same place AND the same kind of subject or era)?
A group portrait of soldiers, a sports team, a ship or a map that is not what the line is about = no.
JSON: {{"fits": true|false, "why": "a few words"}}"""


def _fits(cfg, line, title, log=print):
    if not cfg or not line: return True
    try:
        from .llm import LLM
        with LLM(dict(cfg), log, lambda: False) as llm:
            j = llm.chat_json("You check picture choices for a documentary. Reply with JSON only.",
                              FITS.format(line=line[:400], title=title[:200]), max_tokens=120)
        ok = bool(j.get("fits"))
        if not ok: _log(log, f"    Commons photo '{title[:60]}' skipped: {str(j.get('why', ''))[:60]}")
        return ok
    except Exception:
        return True   # the checker being down doesn't block the edit


def commons_photo(query, out, log=print, place="Bermuda", cfg=None, line=""):
    """A freely licensed photo from Wikimedia Commons: each suggestion in the query (split at ';'), then shorter forms of it.
    -> (path, credit) or (None, "")."""
    tries = []
    for q in [x.strip() for x in re.split(r"[;|]", str(query)) if x.strip()]:
        words = [w for w in re.findall(r"[A-Za-z0-9']+", q) if w.lower() not in ("of", "the", "a", "an", "and", "on", "in", "with", "showing")]
        for t in (q, " ".join(words[:4]), " ".join(words[:2])):
            if t and place and place.lower() not in t.lower(): t = f"{t} {place}"   # stay in the story's place (no Dubai for Tucker's Town)
            if t and t not in tries: tries.append(t)
    for n, q in enumerate(tries[:8]):
        if n: time.sleep(1.5)   # Commons rate-limits bursts (429)
        got = _commons_one(q, out, log, need=[w for w in re.findall(r"[A-Za-z]{4,}", q) if w.lower() != place.lower()], place=place,
                           judge=(lambda t: _fits(cfg, line, t, log)) if cfg and line else None)
        if got[0]: return got
    return None, ""


def _commons_one(query, out, log=print, need=(), place="", judge=None):
    import requests
    UA = {"User-Agent": "VideoStudio/1.0 (interview edit; contact via YouTube channel)"}
    try:
        r = requests.get("https://commons.wikimedia.org/w/api.php", headers=UA, timeout=30, params={
            "action": "query", "format": "json", "generator": "search", "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": 6,
            "gsrlimit": 10, "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1600,
            "iiextmetadatafilter": "Artist|LicenseShortName"})
        pages = sorted((r.json().get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 99))
        for pg in pages:
            ii = (pg.get("imageinfo") or [{}])[0]
            if ii.get("mime") not in ("image/jpeg", "image/png") or (ii.get("width") or 0) < 800: continue
            title = pg.get("title", "").lower()
            if place and place.lower() not in title: continue   # the file itself must be about the place (title names it)
            key = [w for w in need if w.lower() not in GENERIC]
            if key and not all(w.lower() in title for w in key): continue   # and about the thing asked for (every distinctive word)
            if need and not key and not any(w.lower() in title for w in need): continue
            if judge and not judge(pg.get("title", "")): continue   # and it actually shows what the line is about
            meta = ii.get("extmetadata") or {}
            lic = re.sub(r"<.*?>", "", (meta.get("LicenseShortName") or {}).get("value", "")).strip()
            if not lic or not re.search(r"CC|Public domain|PD", lic, re.I): continue
            artist = re.sub(r"<.*?>", "", (meta.get("Artist") or {}).get("value", "")).strip()[:60] or "Wikimedia Commons"
            img = requests.get(ii.get("thumburl") or ii["url"], headers=UA, timeout=60); img.raise_for_status()
            Path(out).write_bytes(img.content)
            _log(log, f"    Commons photo for '{query}': {pg.get('title', '')[:70]} ({lic})")
            return out, f"Photo: {artist} / Wikimedia Commons, {lic}"
    except Exception as e:
        _log(log, f"    Commons search failed ({str(e)[:80]})")
    return None, ""


def still_part(img, wav, out, kind):
    """A still with a slow push-in (CPU) under the narration."""
    w, h = (1080, 1920) if kind == "short" else (1920, 1080)
    d = media.dur(wav) + 0.35; n = int(d * 30) + 1
    vf = (f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},"
          f"zoompan=z='min(zoom+0.0006,1.10)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n}:s={w}x{h}:fps=30,format=yuv420p,setsar=1,"
          f"fade=t=in:st=0:d=0.35,fade=t=out:st={d - 0.35:.2f}:d=0.35")
    media.ff("-loop", 1, "-i", img, "-i", wav, "-t", f"{d:.2f}", "-map", "0:v:0", "-map", "1:a:0", "-vf", vf,
             "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,apad", *ENC, "-shortest", out, timeout=900)
    return out


ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-profile:v", "high", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


def cut_bite(src, t0, t1, out, kind, k, flip=True):
    if media.ok(out, 0.5): return out
    xc = _faces_x(src, t0, t1, flip) if kind == "short" else None
    zoom = 1.0 if k % 2 == 0 else 1.12   # every other soundbite a little closer: a cut, not a jump
    media.ff("-ss", f"{t0:.2f}", "-i", src, "-t", f"{t1 - t0:.2f}", "-map", "0:v:0", "-map", "0:a:0",
             "-vf", _vf(kind, xc, zoom, flip), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", *ENC, out, timeout=1800)
    return out


class Cutaways:
    """Pictures to cover a soundbite where only the camera holder's face is on screen: the guest elsewhere in the video first,
    then your B-roll clips (turned upright when filmed sideways). Each stretch is used once while others remain."""
    def __init__(self, src, an, used, broll_files, work, flip, log=print):
        from . import shots
        self.src, self.flip, self.work, self.log = src, flip, work, log
        self.runs = [[a, b] for a, b in shots.cutaway_pool(an, used)]
        self.an = an
        self.broll = [(f, media.dur(f)) for f in broll_files]
        self.bi = 0

    def take(self, secs):
        """-> (file, start, flip, guest_box, rotation)"""
        from . import shots
        for r in self.runs:
            if r[1] - r[0] >= secs:
                a = r[0]; r[0] += secs + 1
                gs = [f for s in self.an["secs"] if a <= s["t"] < a + secs for f in shots.split(s["faces"])[0]]
                g = [sum(x[n] for x in gs) / len(gs) for n in range(4)] if gs else None
                return self.src, float(a), self.flip, g, ""
        if self.broll:
            f, d = self.broll[self.bi % len(self.broll)]; self.bi += 1
            st = (self.bi * 7.3) % max(0.1, d - secs) if d > secs else 0.0
            return f, st, False, None, shots.orient(f, self.work, self.log)
        return self.src, 0.0, self.flip, None, ""


SAME_WORD = {"kebob": "kebab", "kabob": "kebab", "kabab": "kebab", "kebap": "kebab", "shawarma": "shawarma", "gyro": "gyro",
             "masjid": "mosque", "musallah": "mosque", "musalla": "mosque", "harbor": "harbour", "center": "centre", "colour": "color"}


def norm_words(text):
    """Lower-case words of 3+ letters, plurals off and spellings joined (owner, 2026-11-09: the talk says "kebab", the sign
    "KEBOBS ON THE CURVE" - the photo was never found)."""
    out = set()
    for w in re.findall(r"[a-z]{3,}", str(text or "").lower()):
        w = w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w
        out.add(SAME_WORD.get(w, w))
    return out


class TripFootage:
    """Your other trip clips and photos, from the catalogue (scripts/label_media.py catalog: place from GPS, what is seen, what
    is said). Used under his voice where the picture is bad (owner, 2026-10-24: "use my other Bermuda videos to replace blur"):
      place    - only clips GPS puts at a place he names (St. George's, Hamilton, Dockyard...)
      subject  - clips showing the kind of thing he talks about (houses, harbour, church, school, field...)
      scenery  - neutral Bermuda scenery (water, beach, street, sky) for stretches that name no place
    Never: face close-ups, shaky / dark clips, airport / plane / hotel-room clips; each stretch of a clip is used once."""
    SCENIC = {"beach", "ocean", "sea", "water", "harbour", "harbor", "bay", "coast", "coastline", "sunset", "sky", "boat", "boats",
              "landscape", "street", "road", "houses", "house", "town", "view", "island", "palm", "trees", "garden", "cliff", "lighthouse"}
    SKIP = {"airplane", "airport", "plane", "aircraft", "cabin", "seat", "hotel room", "bed", "bathroom", "screenshot", "phone", "menu",
            "selfie", "receipt", "document", "screen", "hotel", "guest room", "ironing", "couch", "sofa", "bedroom", "dimly lit",
            "traveller", "traveler", "tourist", "television", "tv ", "monitor", "laptop"}   # (the catalogue calls the person filming "the traveller": that is you)
    STOP = set("the a an and or of in on at to for with from by is are was were this that these those it its he she they them his her their "
               "what who where when how very just like yeah okay oh so but not no yes there here have has had be been do does did will "
               "would can could about over into up down out all some many more most also bermuda".split())

    def __init__(self, catalog_dir, exclude=(), root=None, log=print, near=None, radius_km=2.0, cfg=None):
        self.log, self.root, self.recs, self.pos, self.cfg = log, root, [], {}, cfg
        self.near, self.radius = near, radius_km
        ex = {str(Path(x)).lower() for x in exclude}
        lens = {round(float(x[1]), 1) for x in exclude if isinstance(x, tuple)}
        for f in sorted(Path(catalog_dir).glob("*.json")) if catalog_dir and Path(catalog_dir).is_dir() else []:
            try: r = json.loads(f.read_text(encoding="utf-8"))
            except Exception: continue
            p = Path(r.get("original", ""))
            if str(p).lower() in ex or not p.exists() or "vision failed" in str(r.get("description", "")): continue
            words = (str(r.get("subject", "")) + " " + str(r.get("description", ""))).lower()   # (tags echo the prompt's examples: not used)
            if r.get("face_closeup") or str(r.get("quality", "good")) in ("shaky", "dark", "blurry") or str(r.get("people", "")) == "one": continue
            if any(k in words for k in self.SKIP): continue
            vid = p.suffix.lower() in VIDEO_EXT
            if vid and float(r.get("seconds") or 0) < 3: continue
            hm = re.search(r"T(\d\d):", str(r.get("taken", "")))
            if hm and not 6 <= int(hm.group(1)) < 20: continue   # night clips (a hotel TV passed for "aerial view") don't fit a daytime story
            r["_vid"], r["_words"] = vid, norm_words(words + " " + str(r.get("readable_text", ""))) - self.STOP
            r["_sign"] = norm_words(r.get("readable_text", "")) - self.STOP   # the words on a sign in the picture
            r["_where"] = " ".join(str(r.get(k, "")) for k in ("place", "area", "address")).lower()
            r["_near"] = True
            if near and r.get("lat") is not None:   # filmed in the area of the conversation (Hamilton), not Dockyard / a hotel lawn
                km = 6371 * math.hypot(math.radians(float(r["lat"]) - near[0]), math.radians(float(r["lon"]) - near[1]) * math.cos(math.radians(near[0])))
                r["_near"] = km <= radius_km
            elif near: r["_near"] = False   # no GPS: can't tell where it was filmed
            self.recs.append(r)
        _log(log, f"  trip footage: {len(self.recs)} usable clip(s) / photo(s) from the catalogue")

    def _places(self, text):
        out = set()
        for m in re.finditer(r"\b(St\.? George'?s?|Hamilton|Dockyard|Somerset|Sandys|Paget|Warwick|Southampton|Horseshoe Bay|Tucker'?s Town|"
                             r"Flatts|Smith'?s|Devonshire|Pembroke|Crystal Caves?|Elbow Beach|Coco Reef|North Village|Bernard'?s Park|Front Street)\b", text, re.I):
            out.add(re.sub(r"[^a-z ]", "", m.group(1).lower()).replace("st georges", "st george").replace("st george s", "st george").strip())
        return out

    def pick(self, visual, text, d, mode):
        want = norm_words(visual + " " + text) - self.STOP
        places = self._places(visual + " " + text)
        # what the picture must show besides the place (Nov 9: a Crystal Caves ticket filmed in Hamilton went under "Muhammad
        # Mosque, Hamilton"): a clip of the right place showing something else is not it
        about = set(re.findall(r"[a-z]{3,}", (visual or text[:80]).lower())) - self.STOP - {w for pl in places for w in pl.split()}
        best, bs = None, 0.0
        for r in self.recs:
            if r["_vid"]:
                used = self.pos.get(r["original"], 0.5)
                if float(r.get("seconds") or 0) - used < min(d, 4.0): continue
            elif self.pos.get(r["original"]): continue
            if mode == "place":
                if not places or not any(pl in r["_where"].replace("'", "").replace(".", "") for pl in places): continue
                if about and not (about & r["_words"]): continue
                sc = 3 + len(want & r["_words"])
            elif mode == "subject":
                if not r["_near"]: continue   # owner: "Dockyard is nowhere near Hamilton" - only footage from where he is talking
                subj = want - {w for pl in places for w in pl.split()}   # owner, 2026-11-11: a ferry window with a "Welcome to
                sc = len(subj & r["_words"]) + 2 * len(subj & r.get("_sign", set()))   # Hamilton" sign under talk of a church -
                if sc < 2 or str(r.get("people", "")) not in ("none", ""): continue   # the place name alone is not the subject
            else:
                if not r["_near"] or str(r.get("people", "")) not in ("none", ""): continue   # scenery: from the area, nobody in it
                sc = len(r["_words"] & self.SCENIC)
                if sc < 2 or places: continue   # a named place he talks about never gets anonymous scenery as if it were it
            sc += 0.5 if r["_vid"] else 0
            if sc > bs: best, bs = r, sc
        if not best: return None
        f = Path(best["original"])
        if best["_vid"] and self.root and self.cfg is None:   # no vision check to confirm a turn: a clip that looks sideways is not used
            from . import shots
            if shots.orient(f, self.root, self.log):
                self.recs = [r for r in self.recs if r is not best]
                return self.pick(visual, text, d, mode)
        if not self._faceless(f, best, d):   # someone's face in the stretch we'd use (you, family): never; try the next best
            self.recs = [r for r in self.recs if r is not best]
            return self.pick(visual, text, d, mode)
        if best["_vid"] and self.cfg is not None:   # floor / wall / shirt / sideways (owner, 2026-11-09): never; try the next best
            from . import shots
            v = shots.vet_clip(self.cfg, f, "", self.log)
            if not v[0]:
                self.recs = [r for r in self.recs if r is not best]
                return self.pick(visual, text, d, mode)
            best["_rot"] = v[2] if len(v) > 2 else ""   # a sideways clip, turned upright
        self.last = best["original"]
        if best["_vid"]:
            st = self.pos.get(best["original"], 0.5); self.pos[best["original"]] = st + d + 0.5
        else:
            st = 0.0; self.pos[best["original"]] = 1
        return f, st, best

    def _faceless(self, f, r, d):
        """No face bigger than a passer-by (> 1% of the frame) in the part of the clip we'd use (5 frames) / the photo.
        The catalogue's people / close-up flags missed family selfies; this checks the pixels."""
        from . import shots
        cv2, np = shots._cv()
        if not cv2: return True
        tmp = Path(self.root or ".") / "_trip_face.jpg"
        try:
            if r["_vid"]:
                st = self.pos.get(r["original"], 0.5); fd = float(r.get("seconds") or 0)
                times = [min(max(0.0, fd - 0.2), st + d * k / 4) for k in range(5)]
            else:
                times = [None]
            for t in times:
                if t is None:
                    if f.suffix.lower() == ".heic":
                        import pillow_heif; from PIL import Image
                        pillow_heif.register_heif_opener(); Image.open(f).convert("RGB").save(tmp, quality=90)
                    else: tmp = f
                else:
                    media.ff("-ss", f"{t:.2f}", "-i", f, "-frames:v", 1, "-vf", "scale=640:-2", tmp, timeout=120)
                im = cv2.imread(str(tmp))
                if im is None: continue
                if im.shape[1] > 960: im = cv2.resize(im, (960, int(im.shape[0] * 960 / im.shape[1])))
                for rot in (None, cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE):   # sideways clips too
                    im2 = im if rot is None else cv2.rotate(im, rot)
                    if any(x[2] * x[3] > 0.01 for x in shots._faces(cv2, cv2.cvtColor(im2, cv2.COLOR_BGR2GRAY), im2)):
                        _log(self.log, f"    trip clip {f.name} skipped: a face in it"); return False
            return True
        except Exception:
            return False

    def render(self, pick, d, out, kind):
        from . import shots
        f, st, r = pick
        W, H = (1080, 1920) if kind == "short" else (1920, 1080)
        if r["_vid"]:
            rot = r.get("_rot", "")   # (vet_clip's turn for a clip held sideways / upside down)
            fd = float(r.get("seconds") or 0)
            vf = (rot + "," if rot else "") + f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},fps=30,format=yuv420p,setsar=1"
            media.ff(*(["-stream_loop", "-1"] if fd - st < d else []), "-ss", f"{min(st, max(0.0, fd - 1)):.2f}", "-i", f, "-t", f"{d:.2f}", "-an", "-vf", vf, *ENC[:6], out, timeout=900)
        else:
            img = f
            if f.suffix.lower() == ".heic":
                try:
                    import pillow_heif; from PIL import Image
                    pillow_heif.register_heif_opener(); img = Path(out).with_suffix(".jpg"); Image.open(f).convert("RGB").save(img, quality=92)
                except Exception: return None
            still_clip(img, d, out, kind)
        return out


def aerial(lat, lon, out, log=print, z=19):
    """Satellite picture centred on (lat, lon), 16:9, about 190 m across (Esri World Imagery tiles, stitched; credit Esri)
    -> path or None."""
    out = Path(out)
    if media.ok(out, 0.0): return out
    try:
        import requests
        from PIL import Image
        import io
        n = 2 ** z
        fx = (lon + 180) / 360 * n
        fy = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
        tx, ty = int(fx), int(fy)
        sheet = Image.new("RGB", (256 * 5, 256 * 3))
        for i in range(-2, 3):
            for j in range(-1, 2):
                r = requests.get(f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{ty + j}/{tx + i}",
                                 timeout=30, headers={"User-Agent": "VideoStudio/1.0"})
                if r.status_code != 200: raise RuntimeError(f"tile HTTP {r.status_code}")
                sheet.paste(Image.open(io.BytesIO(r.content)).convert("RGB"), ((i + 2) * 256, (j + 1) * 256))
        cx, cy = (fx - tx + 2) * 256, (fy - ty + 1) * 256   # the point on the sheet
        w, h = 1024, 576
        x0, y0 = int(min(max(0, cx - w / 2), sheet.width - w)), int(min(max(0, cy - h / 2), sheet.height - h))
        out.parent.mkdir(parents=True, exist_ok=True)
        sheet.crop((x0, y0, x0 + w, y0 + h)).resize((1600, 900), Image.LANCZOS).save(out, quality=92)
        return out
    except Exception as e:
        _log(log, f"    satellite picture failed ({str(e)[:80]})"); return None


class Ladder:
    """The picture for a stretch where his story is good but the picture isn't (car ceiling, blank wall, the camera holder):
    his voice goes on and the picture comes from the ladder, wherever it lands neatly (owner, 2026-10-24):
      1. a Wikimedia Commons photo of what he is talking about (title must name the place and the thing; story-fit check)
      2. stock video (Pexels / Pixabay / Internet Archive) through the clip checker (Bermuda, the right kind of thing)
      3. his own footage of that moment, blurred
    Short stretches (< 4 s) go straight to the blur, so pictures don't flash by. (AI images / Marble scenes: not in this edit.)"""
    def __init__(self, cfg, src, flip, kd, kind, log=print, own=None, scenic=None, trip=None):
        self.cfg, self.src, self.flip, self.kd, self.kind, self.log = cfg, src, flip, Path(kd), kind, log
        self.trip = trip
        self.scenic = scenic   # (file, start, flip, rotation): the bay, blurred under a stretch where only the holder is in shot
        self.photos, self.stock, self.uses, self.stock_tries, self.rung_tries = {}, {}, {}, 0, 0
        self.own, self.own_i, self.own_pos = list(own or []), 0, {}
        (self.kd / "ladder").mkdir(exist_ok=True)

    OWN_WORDS = re.compile(r"mosque|masjid|musallah|islamic|prayer|center|centre|imam|muslim", re.I)
    gps, context = None, ""   # set by build: where the conversation was filmed, your note
    PERSON = re.compile(r"portrait|face|person|people|\bman\b|\bmen\b|woman|women|imam|sheikh|selfie|photo of", re.I)   # (Nov 9: an
    # invented woman on a phone for "is this Imam Niteza?" - a person's picture is never drawn)

    STRETCH = """Two people talk while one films on a phone. Where: {context}
In this {d:.0f}-second stretch the picture is unusable (the camera is on the person filming, his chin, the floor), so we show
something else while their voices go on. What is said in the stretch:
"{text}"
Just before it they said: "{prev}" (a short reaction - "they're used to having their way", "that's nice" - is about what was
said just before; picture that). The longer passage it sits in is about: {visual}
Reply with JSON only: {{"show": "here" | "plan" | "thing" | "meet", "visual": "...", "edit": "..."}}
show: "here" when they talk about the room or building they are standing in right now, or this mosque / its rooms / its school
      (show that very place from our footage - never a picture of another building);
      "plan" when they talk about a planned change to the building that shows from outside or above (a new floor, a rooftop
      terrace, an extension, a new building); a planned room inside (toilets, a kitchen, classrooms) is "here";
      "meet" when someone is introduced, greeted or met in this stretch ("there's Tariq", "this is the imam", "nice to meet
      you") - it is drawn as a loose courtroom-style sketch of the meeting, never a photo;
      "thing" for anything else they mention (a place, a school and its children, food, a job, a person's background, a country).
Never a portrait of a specific person they meet, name or introduce (an imam, a friend, a family member): an invented face of a
real person is worse than none - show the place they are in or the thing talked about instead.
visual: for "meet": who meets whom by role and where, never names (e.g. "an imam in a white kufi shaking hands with a
        visitor in a mosque hallway"); otherwise a 3-8 word picture search for what is talked about in THIS stretch (e.g. "children in an Islamic school classroom",
        "Kaaba in Mecca", "halal kebab shop counter"); never the speakers' faces. If they only greet or chat, the place they are in.
edit: for "here": what the empty place looks like, from what is said ("unfinished upper floor with bare walls and exposed beams");
      for "plan": the change to draw onto the building ("a large rooftop terrace with railings and seating on the roof"); else ""."""

    def _at_place(self):
        """Was the clip being cut filmed at the place of the conversation (within 1 km)?"""
        here, g = self.gps, gps_of(self.src)
        if not here or not g: return bool(here)
        return 6371 * math.hypot(math.radians(g[0] - here[0]), math.radians(g[1] - here[1]) * math.cos(math.radians(here[0]))) <= 1.0

    def stretch(self, text, visual, d):
        """What to show while this stretch is said -> {"show", "visual", "edit"} (cached per stretch), or None."""
        import hashlib
        f = self.kd / "ladder" / "stretch.json"
        try: memo = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        except (OSError, ValueError): memo = {}
        prev = getattr(self, "prev_text", "")
        self.prev_text = text
        key = hashlib.md5(f"v4|{text}|{visual}|{prev}".encode()).hexdigest()[:16]   # (v2: no portraits, this mosque is "here")
        if key in memo: return memo[key]
        try:
            from .llm import LLM
            with LLM(dict(self.cfg), self.log, lambda: False) as llm:
                j = llm.chat_json("You pick pictures for a documentary. Reply with JSON only.",
                                  self.STRETCH.format(context=self.context or "a conversation", d=d, text=text[:900], visual=visual or "-", prev=prev[-400:] or "-"), max_tokens=300)
        except Exception as e:
            if str(e) == "cancelled" or type(e).__name__ == "Paused": raise
            _log(self.log, f"    stretch query failed ({str(e)[:80]}); the passage's picture"); return None
        q = {"show": str(j.get("show") or "thing"), "visual": str(j.get("visual") or "")[:140], "edit": str(j.get("edit") or "")[:240]}
        memo[key] = q; f.write_text(json.dumps(memo, indent=1, ensure_ascii=False), encoding="utf-8")
        return q

    def _edit_image(self, img, instruction, key):
        """An AI edit of a real picture (ChatGPT with the picture attached) -> image path or None."""
        from . import imagegen
        cfg = dict(self.cfg, imagegen_providers=["chatgpt"])
        res = imagegen.generate_many(cfg, self.kd.parent, {"vo": "EDIT: " + instruction}, key, self.log,
                                     aspect="9:16" if self.kind == "short" else "16:9", image=img)
        return Path(res[0][0]) if res else None

    def _i2v(self, img, d, out):
        from . import qc_repair
        W, H = (1080, 1920) if self.kind == "short" else (1920, 1080)
        clip = qc_repair.image_to_video(img, Path(out).with_name(Path(out).stem + "_i2v.mp4"), d, W, H, self.log)
        if not clip or not media.ok(clip, 0.5): still_clip(img, d, out, self.kind); return out
        media.ff("-i", clip, "-t", f"{d:.2f}", "-an", "-vf", "fps=30,format=yuv420p,setsar=1", *ENC[:6], out, timeout=900)
        return out

    def remake(self, a, b, q, d, out, holder_only=False):
        """Owner, 2026-11-09 ("remove my giant face... reconstruct the structure"; then "for big face, can't you ask for it to be
        removed... use that frame and parallax... Ken Burns style is still better than trying to shoehorn subject"): one frame of
        this same stretch with the camera holder taken out by an AI edit - everyone else and the scene stay - played with
        parallax -> (out, how, credit) or None."""
        from . import shots
        cv2, np = shots._cv()
        best, tmp = None, self.kd / "ladder" / f"{'face' if holder_only else 'here'}2_{Path(self.src).stem[:30]}_{a:.0f}.jpg"   # (2: stood upright)
        if media.ok(tmp.with_suffix(".done.jpg"), 0.0): best = tmp.with_suffix(".done.jpg")
        if best is not None and blank_frame(best): return None   # (made before the bare-wall check)
        if media.ok(tmp.with_suffix(".dropped.jpg"), 0.0): return None
        if best is None:
            least = 9.0
            for k in range(5):   # the frame where the least of a face is in the way
                t = a + (b - a) * (k + 0.5) / 5
                fk = tmp.with_name(tmp.stem + f"_{k}.jpg")
                media.ff("-ss", f"{t:.2f}", "-i", self.src, "-frames:v", 1, "-vf", ("hflip," if self.flip else "") + "scale=1280:-2", fk, timeout=120)
                im = cv2.imread(str(fk)) if cv2 is not None and fk.exists() else None
                if im is None: continue
                area = sum(x[2] * x[3] for x in shots._faces(cv2, cv2.cvtColor(im, cv2.COLOR_BGR2GRAY), im))
                if area < least: least, best = area, fk
            if best is None: return None
            best = self._stand_up(best)
            what = ("First make sure the picture is upright and level: if it was taken sideways or upside down, turn it so the floor or ground is at the bottom and walls are vertical. Remove only the person right in front of the camera - the large face, chin, neck, shoulder or arm of whoever is "
                    "filming, close to the lens - and fill in what is behind him. Every other person stays exactly as in the photo: same "
                    "face, same pose, looking the same way, not removed, not redrawn. Keep the vehicle or room, the light and the "
                    "camera angle exactly as they are.") if holder_only else (
                    "First make sure the picture is upright and level: if it was taken sideways or upside down, turn it so the floor or ground is at the bottom and walls are vertical. Remove the person right in front of the camera (the large face, chin, neck or body of whoever is filming) and show "
                    "the place behind as it really is, same room, same light, same camera angle. Keep anyone farther away as they are"
                    f": {q.get('edit') or 'the room'}.")
            img = self._edit_image(best, what, f"{'face' if holder_only else 'here'}2_{Path(self.src).stem[:20]}_{a:.0f}")
            if not img: return None
            if blank_frame(img):   # owner, 2026-11-11: "why this filthy wall?" - an edit that leaves a bare wall is no picture
                _log(self.log, "    remade frame is a bare wall / floor; next rung")
                shutil.copyfile(img, tmp.with_suffix(".dropped.jpg")); return None
            shutil.copyfile(img, tmp.with_suffix(".done.jpg")); best = tmp.with_suffix(".done.jpg")
        self._i2v(best, d, out)
        ref = (self.describe((a + b) / 2) or "the place they are in") if self.content_audit else ""   # (Nov 9: ornate mosque rooms)
        if not self.fits(out, q.get("visual") or "the place they are in", q.get("text") or q.get("edit") or "the place they are in", ref=ref):
            try: Path(best).replace(Path(best).with_name(Path(best).name.replace(".done.jpg", ".dropped.jpg")))   # never reused
            except OSError: pass
            return None   # (Nov 9: blank white walls) - the next rung
        return out, "our footage, the camera holder taken out (AI)", "AI-recreated from our footage"

    def clean_frame(self, a, b, d, out, text, visual):
        """Owner, 2026-11-09: "fix clips with finger fuzz or smudges... pick a frame, ask ChatGPT to fix/enhance, then parallax or
        Ken Burns". The sharpest of five frames of this stretch, cleaned by an AI edit; kept only if it fits the words."""
        tmp = self.kd / "ladder" / f"fix_{Path(self.src).stem[:30]}_{a:.0f}.mp4"
        try:
            media.ff("-ss", f"{a:.2f}", "-i", self.src, "-t", f"{max(1.0, b - a):.2f}", "-an", "-vf", ("hflip," if self.flip else "") + "scale=1920:-2",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", tmp, timeout=600)
        except Exception: return None
        self.flawed = True
        got = self.polish((tmp, "our footage", ""), d)
        if not got or "cleaned" not in got[1] or not self.fits(got[0], visual, text): return None
        shutil.copyfile(got[0], out)
        return out, "our footage, cleaned (AI)", "AI-cleaned"

    def nearby_still(self, a, d, out):
        """The picture already made from this clip nearest to `a` (a remade / cleaned frame), held for a short covered bit.
        Owner, 2026-11-11 (the same bare wall held over five stretches in a row; a sideways door): each frame is held once,
        never a bare wall, and only the frames stood upright before the edit (here2 / face2); turned upright if still sideways."""
        stem = Path(self.src).stem[:30]
        used = self.__dict__.setdefault("held", set())
        cands = []
        for f in (self.kd / "ladder").glob("*.done.jpg"):
            m = re.match(r"(?:here|face)2_(.+)_(\d+)\.done$", f.stem)
            if m and m.group(1) == stem and str(f) not in used and abs(int(m.group(2)) - a) <= 60 and not blank_frame(f):
                cands.append((abs(int(m.group(2)) - a), f))
        for _, f in sorted(cands):
            used.add(str(f))
            still_clip(f, d, out, self.kind)
            if self.fits(out, "the place they are in", "the place they are in", boring_only=True):   # (turned upright if sideways)
                return out, "the nearest remade frame of this clip, held"
        return None

    def _stand_up(self, img):
        """A still about to go to an AI edit, turned upright when it was filmed sideways / upside down (Nov 9: a door hinge lying
        flat in a remade frame)."""
        from . import shots
        if not self.cfg.get("interview_upright_check", True): return img
        try:
            mp = Path(img).with_suffix(".up.mp4")
            media.ff("-loop", 1, "-i", img, "-t", "1", "-vf", "fps=2", mp, timeout=120)
            rot = shots.upright_rot(self.cfg, mp, 0.5, False, self.log)
            if not rot: return img
            out = Path(img).with_name(Path(img).stem + f"_r{rot}.jpg")
            media.ff("-i", img, "-vf", {90: "transpose=1", 270: "transpose=2", 180: "hflip,vflip"}[rot], out, timeout=120)
            return out
        except Exception:
            return img

    def mockup(self, q, d, out):
        """A planned change to the building ("a huge terrace up on the roof"): the satellite picture of where we filmed, drawn
        by an AI edit as an aerial view with the change -> (out, how, credit) or None."""
        if not self.gps: return None
        import hashlib
        key = "plan_" + hashlib.md5((q.get("edit") or q.get("visual") or "").encode()).hexdigest()[:8]
        shown = getattr(self, "plan_secs", 0.0)
        if shown >= float(self.cfg.get("interview_mockup_max_secs", 12)): return None   # (Nov 9: one mock-up ran ~25 s)
        self.plan_secs = shown + d
        done = self.kd / "ladder" / f"{key}.jpg"
        if not media.ok(done, 0.0):
            base = aerial(self.gps[0], self.gps[1], self.kd / "ladder" / f"aerial_{self.gps[0]:.4f}_{self.gps[1]:.4f}.jpg", self.log)
            if not base: return None
            img = self._edit_image(base, "This satellite photo looks straight down on a city block; the building nearest the centre is the one "
                                         "they talk about. Draw it as a photorealistic aerial view from a 45-degree angle, the neighbouring "
                                         f"buildings as they are, with this planned change to that building: {q.get('edit') or q.get('visual')}.", key)
            if not img: return None
            shutil.copyfile(img, done)
        self._i2v(done, d, out)
        return out, "mock-up of the plan (AI)", "AI mock-up on Esri satellite imagery"

    def _own(self, visual, d):
        """Your own footage of the place he's talking about (clips filmed within ~150 m of the interview, no one's face in
        them): the mosque's prayer hall, its hallways. Only for stretches about that place."""
        if not self.own or not self.OWN_WORDS.search(visual or ""): return None
        for _ in range(len(self.own)):
            f, dur, rot = self.own[self.own_i % len(self.own)]; self.own_i += 1
            st = self.own_pos.get(str(f), 0.5)
            if dur - st >= d: self.own_pos[str(f)] = st + d + 0.5; return f, st, rot
        return None

    def _photo(self, visual, text):
        if visual not in self.photos:
            img, credit = commons_photo(visual, self.kd / "ladder" / f"p{len(self.photos):02}.jpg", self.log, cfg=self.cfg, line=text[:300])
            self.photos[visual] = (img, credit)
        img, credit = self.photos[visual]
        if img and self.uses.get(str(img), 0) < 2: self.uses[str(img)] = self.uses.get(str(img), 0) + 1; return img, credit
        return None, ""

    def _stock(self, visual, text, local=True):
        if visual in self.stock: return self.stock[visual]
        self.stock[visual] = None
        self.stock_tries += 1
        try:
            from . import footage
            sid = f"s{len(self.stock):02}"
            qs = [visual, re.sub(r"\bBermuda\b", "", visual).strip() + " Bermuda"] if local else [visual]
            got = footage.fill(self.cfg, [{"id": sid, "text": text[:300], "narration": text[:300], "queries": qs}],
                               "portrait" if self.kind == "short" else "landscape", {sid: 1}, self.kd / "ladder" / "stock", self.log, lambda: False,
                               1920, 12, self.kd / "ladder" / f"stock_{sid}.json")
            c = (got.get(sid) or [None])[0]
            if c and local and "bermuda" not in (str(c.get("desc", "")) + " " + str(c.get("seen", ""))).lower():   # (a line on New Hampshire needs no Bermuda)
                _log(self.log, f"    stock clip {c.get('id')} not used: nothing says it is Bermuda ({str(c.get('desc', ''))[:50]}); a mosque elsewhere would mislead")
                c = None
            f = self.kd / "ladder" / "stock" / "clips" / f"{c['id']}.mp4" if c else None
            self.stock[visual] = (f, f"{c.get('src', 'stock')}: {c.get('author', '')}".strip(": ")) if f and f.exists() else None
        except Exception as e:
            _log(self.log, f"    stock search failed ({str(e)[:80]})")
        return self.stock[visual]

    def trip_clip(self, visual, text, d, out, modes):
        """Your own trip footage for d seconds -> (out, how, credit) or None."""
        if not self.trip: return None
        for m in modes:
            pk = self.trip.pick(visual or "", text or "", d, m)
            if pk and self.trip.render(pk, d, out, self.kind):
                r = pk[2]
                return out, f"trip {m}", ""
        return None

    FIT = """A documentary filmed at: {where}. It shows this picture while these words are heard: "{text}"
(the picture was looked up as: {visual}). Reply with JSON only: {{"fits": true|false, "boring": true|false, "shows": "..."}}
"fits": the picture shows what is talked about, or plainly the place it is said in / about. A recognisably different specific
building or place (another mosque, a famous landmark elsewhere) presented as the one they are at or talk about does NOT fit.
"boring": a static, empty, uninformative picture - mostly floor, carpet, a blank wall or ceiling, an empty corner, a blur.
"flawed": a finger or hand over part of the lens, a smudge, haze or fuzz - the subject is right but the picture needs cleaning.
Reply: {{"fits": true|false, "boring": true|false, "flawed": true|false, "shows": "..."}}"""

    def fits(self, clip, visual, text, boring_only=False, ref=""):
        """Owner, 2026-11-09: "weed out boring static images which don't jive with the narration... even a card is better than a
        boring image"; 2026-11-10: "a flipped man won't match a standing man description... take a 1 sec snapshot of the beat and
        compare - reject and give it the rung regimen". Every second of the candidate is read (audit); a picture turned on its
        side / upside down the same way throughout is turned upright, anything else that fails -> False (the next rung).
        True when the check can't run. Owner, 2026-11-10 (the build took ~16 h): content judging moved to the separate
        post-render Vision Checker; by default (interview_content_audit off) only the cheap turned-picture check runs here."""
        if not self.cfg.get("interview_fit_check", True): return True
        mode = "remake" if ref else "boring" if boring_only else "full"
        ok, turn, why = self.audit(clip, visual, text, ref=ref, mode=mode)
        if ok and turn:
            self.turn(clip, turn)
            ok, turn, why = self.audit(clip, visual, text, ref=ref, mode=mode)
            ok = ok and not turn
        if not ok: _log(self.log, f"    picture dropped ({why}); next rung")
        return ok

    SEE = """One frame from a documentary. Reply with JSON only: {"shows": "...", "boring": true|false, "flawed": true|false}.
"shows": 6-14 words: what is in it - people, the place, objects, any writing - and say "upside down" or "sideways" if it is.
"boring": mostly floor, carpet, a blank wall or ceiling, a shirt / chest / body close-up, a finger over the lens, a blur, near black.
"flawed": a finger at the edge, a smudge, haze or fuzz over a picture that is otherwise right."""
    ORIENT = """Four copies of one photo, each turned a different way, labelled A, B, C and D. Exactly one is the right way up: the
ground or floor at the bottom, the sky or ceiling at the top, people standing on their feet, walls and door edges vertical, writing
readable. Which one? Reply with JSON only: {"upright": "A|B|C|D"}"""
    MATCH = """A documentary shows a picture for {n} seconds while these words are heard: "{text}"
It was filmed at: {where}. The picture was chosen to show: {visual}.{ref}
A look at each second of the picture saw:
{seen}
Which seconds do NOT belong on screen here? A second does not belong when it shows:
- something unrelated to the words and to the place they are said in (a sports team, a swimming pool, a scooter under talk about
  a mosque); "plausibly related" is not enough - it must show what is said, or the place they are in;
- somewhere recognisably else: a big-city skyline, skyscrapers, a landscape or street of another country than {where};
- a recognisably different specific building or place presented as the one talked about, or a grander / different-looking version
  of the place our camera really shows (see below);
- a generic stand-in for one specific person, shop or building named in the words (an invented shopkeeper, an invented mosque);
- a person or thing upside down or sideways;
- a made-up stranger standing in for someone named or talked about;
- nothing worth seeing: mostly floor or carpet, a blank wall or ceiling, a shirt or body close-up, a finger over the lens, a blur,
  near black - unless the words are about that very space (an empty floor shown while they talk about that floor belongs).
The place they are in (the car, the street, the building, the people there) always belongs. When in doubt, a second does
not belong: a sketch or a card will be shown instead, which is better than a wrong picture.
Reply with JSON only: {{"bad": [second numbers], "why": "a few words"}}"""
    HERE = ("\nOur own camera at this moment (before any picture was put over it) showed: {ref}. If the picture stands for this "
            "place, a grander, different-looking or invented version of it (ornate decoration, chandeliers, a room the camera did "
            "not show) does not belong.")
    REMADE = ("\nThis picture is meant to be the real place, rebuilt by an AI edit from a frame of our own footage that showed: {ref}. "
              "It need not show what the words are about, but a second showing a grander, different or invented place (ornate "
              "decoration, chandeliers, a room or people the frame did not show) does not belong.")
    AUDIT_VERSION = 1
    TURN_SAMPLES = 5   # frames looked at for the cheap turned-picture check

    @property
    def content_audit(self):
        """Owner, 2026-11-10: the editor does no per-second content judging (the Vision Checker app does, after the render)."""
        return bool(self.cfg.get("interview_content_audit", False))

    def audit(self, clip, visual, text, ref="", mode="full"):
        """One look a second at a finished picture -> (ok, turn, why). Owner, 2026-11-10: "if qc cannot stop flipped / inverted /
        sideways video from slipping through, the comparison with the script should fail too". Each second: which of four turned
        copies is the right way up (comparing copies is far easier for a small vision model than judging one frame), and what it
        shows; then the words decide which seconds don't belong. mode "full": against the words; "remake": an AI-rebuilt frame of
        our footage, checked for invention against `ref` (what the frame really showed); "boring": only empty / turned pictures;
        "orient": only turned. `turn` (degrees clockwise) when the whole picture reads turned one way: the caller turns it."""
        import hashlib, math
        from collections import Counter
        clip = Path(clip)
        if not media.ok(clip, 0.2): return True, 0, "nothing to check"
        cheap = not self.content_audit
        if cheap: mode = "orient" if mode == "orient" else "turn"   # (Nov 10: only turned / sideways pictures; content -> Vision Checker)
        f = self.kd / "ladder" / "audit.json"
        try: memo = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        except (OSError, ValueError): memo = {}
        st = clip.stat()
        ck = hashlib.md5(f"{clip.name}|{st.st_size}|{st.st_mtime_ns}|v{self.AUDIT_VERSION}".encode()).hexdigest()[:16]
        try:
            from . import footage, shots
            from .llm import parse_json
            cv2, np = shots._cv()
            if cv2 is None: return True, 0, "no image library"
            sk = "see|" + ck if "see|" + ck in memo or not cheap else "turn|" + ck   # (a full look already made is reused)
            if sk not in memo:
                d = media.dur(clip); n = max(1, min(30, int(d))) if not cheap else max(1, min(self.TURN_SAMPLES, int(d)))
                fd = self.kd / "ladder" / "_audit"; fd.mkdir(exist_ok=True)
                rows = []
                for i in range(n):
                    t = min(d - 0.1, i + 0.5) if not cheap else max(0.0, min(d - 0.1, d * (i + 0.5) / n))
                    fi = fd / "f.jpg"; gi = fd / "g.jpg"
                    media.ff("-ss", f"{t:.2f}", "-i", clip, "-frames:v", 1, "-vf", "scale=640:-2", fi, timeout=120)
                    im = cv2.imread(str(fi))
                    if im is None: continue
                    order = [0, 90, 180, 270][i % 4:] + [0, 90, 180, 270][:i % 4]   # the as-filmed copy moves round (no letter bias)
                    cv2.imwrite(str(gi), _four_turns(cv2, np, im, order))
                    try:
                        pick = str(parse_json(footage._vision(dict(self.cfg, vet_provider="local"), gi, self.ORIENT)).get("upright", "")).strip()[:1].upper()
                        rot = order["ABCD".index(pick)] if pick and pick in "ABCD" else 0
                    except Exception:
                        rot = 0
                    j = {} if mode in ("orient", "turn") else parse_json(footage._vision(dict(self.cfg, vet_provider="local"), fi, self.SEE))
                    rows.append({"s": i + 1, "turn": rot, "shows": str(j.get("shows", ""))[:120], "boring": bool(j.get("boring", False)),
                                 "flawed": bool(j.get("flawed", False))})
                memo[sk] = rows
                f.write_text(json.dumps(memo, indent=1, ensure_ascii=False), encoding="utf-8")
            rows = memo[sk]
        except Exception as e:
            if str(e) == "cancelled" or type(e).__name__ == "Paused": raise
            _log(self.log, f"    picture check unavailable ({str(e)[:60]})"); return True, 0, "unavailable"
        n = len(rows)
        if not n: return True, 0, "no frames"
        self.flawed = sum(r["flawed"] for r in rows) * 2 >= n
        turned = [r["turn"] for r in rows if r["turn"]]
        mark = clip.with_suffix(".turned")   # (Nov 10: a bare room with tiled floor and ceiling read upside down on one look and
        if mark.exists() and mark.read_text(encoding="utf-8").strip() == str(st.st_size): turned = []   # upright on the next: turned once only)
        if n >= 2 and len(turned) >= max(2, math.ceil(n / 2)):
            m, c = Counter(turned).most_common(1)[0]
            if c >= max(2, 0.75 * n): return True, m, f"turned {m} deg"
            if mode != "orient" and n >= 3: return False, 0, "sideways / upside down"   # (the guest on camera: turned every which
        if mode in ("orient", "turn"): return True, 0, "upright"                        # way reads as the model's noise - kept)
        tol = max(1 if n >= 5 else 0, int(n * 0.15))   # (Nov 10: 2 finger seconds of a good 28 s shot are not a reason to drop it)
        bad = {r["s"] for r in rows if r["boring"]} if mode == "boring" else set()   # (Nov 10: "an empty room with a tiled floor"
        why = "nothing worth seeing" if bad else ""                                   # was the third floor they talk about - the words decide)
        if mode in ("full", "remake"):
            mk = "match|" + ck + "|" + hashlib.md5(f"v3|{mode}|{visual}|{text}|{ref}".encode()).hexdigest()[:12]
            if mk not in memo:
                try:
                    from .llm import LLM
                    seen = "\n".join(f"{r['s']}: {r['shows']}" + (" [looks empty / uninformative]" if r["boring"] else "") for r in rows)
                    with LLM(dict(self.cfg), self.log, lambda: False) as llm:
                        j = llm.chat_json("You check pictures for a documentary. Reply with JSON only.",
                                          self.MATCH.format(n=n, text=(text or "-")[:600], where=(self.context or "-")[:300], visual=visual or "-",
                                                            ref=(self.REMADE if mode == "remake" else self.HERE).format(ref=ref) if ref else "",
                                                            seen=seen), max_tokens=200)
                    memo[mk] = {"bad": [int(x) for x in (j.get("bad") or []) if str(x).strip().isdigit()], "why": str(j.get("why", ""))[:100]}
                    f.write_text(json.dumps(memo, indent=1, ensure_ascii=False), encoding="utf-8")
                except Exception as e:
                    if str(e) == "cancelled" or type(e).__name__ == "Paused": raise
                    memo[mk] = {"bad": [], "why": ""}
            bad |= set(memo[mk]["bad"]); why = memo[mk]["why"] or why
        ok = len(bad) <= tol
        return ok, 0, ("" if ok else f"{len(bad)} of {n} s: {why or 'off the words'}; saw {rows[min(bad) - 1]['shows'][:60] if bad and min(bad) <= n else ''}")

    def turn(self, clip, deg):
        """Turn a finished picture upright in place (filling the frame)."""
        W, H = (1080, 1920) if self.kind == "short" else (1920, 1080)
        tmp = Path(clip).with_name(Path(clip).stem + "_turned.mp4")
        media.ff("-i", clip, "-an", "-vf", {90: "transpose=1", 270: "transpose=2", 180: "hflip,vflip"}[deg] +
                 f",scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},fps=30,format=yuv420p,setsar=1",
                 *ENC[:6], tmp, timeout=900)
        Path(tmp).replace(clip)
        Path(clip).with_suffix(".turned").write_text(str(Path(clip).stat().st_size), encoding="utf-8")   # never turned twice
        _log(self.log, f"    {Path(clip).name}: turned upright ({deg} deg)")

    def describe(self, t):
        """What the clip being cut shows at t seconds (a few words, cached) - the truth an AI-rebuilt frame is held to."""
        f = self.kd / "ladder" / "audit.json"
        try: memo = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        except (OSError, ValueError): memo = {}
        k = f"desc|{Path(self.src).name}|{t:.0f}|{bool(self.flip)}"
        if k not in memo:
            try:
                from . import footage
                from .llm import parse_json
                fi = self.kd / "ladder" / "_desc.jpg"
                media.ff("-ss", f"{t:.2f}", "-i", self.src, "-frames:v", 1, "-vf", ("hflip," if self.flip else "") + "scale=640:-2", fi, timeout=120)
                memo[k] = str(parse_json(footage._vision(dict(self.cfg, vet_provider="local"), fi, self.SEE)).get("shows", ""))[:120]
                f.write_text(json.dumps(memo, indent=1, ensure_ascii=False), encoding="utf-8")
            except Exception as e:
                if str(e) == "cancelled" or type(e).__name__ == "Paused": raise
                return ""
        return memo[k]

    flawed = False

    def polish(self, got, d):
        """Owner, 2026-11-09 ("others need to be cleaned up because they have smudges or fat fingers or fuzz"): found media that
        fits but is flawed -> its clearest frame cleaned by an AI edit (finger, smudge out, sharpened), as a slow push-in."""
        if not got or not self.flawed: return got
        out = Path(got[0]); self.flawed = False
        best, sharp = None, -1.0
        try:
            from . import shots
            cv2, np = shots._cv()
            for k in range(5):   # the sharpest of five frames
                fk = out.with_name(out.stem + f"_c{k}.jpg")
                media.ff("-ss", f"{media.dur(out) * (k + 0.5) / 5:.2f}", "-i", out, "-frames:v", 1, fk, timeout=120)
                im = cv2.imread(str(fk), cv2.IMREAD_GRAYSCALE) if cv2 is not None else None
                v = float(cv2.Laplacian(im, cv2.CV_64F).var()) if im is not None else 0.0
                if v > sharp: best, sharp = fk, v
            best = self._stand_up(best)
            img = self._edit_image(best, "First make sure the picture is upright and level: if it was taken sideways or upside down, turn it so the floor or ground is at the bottom and walls are vertical. Clean up this photo: remove any finger or hand over the lens, smudges and haze, and fill in what "
                                         "was behind them; sharpen and restore detail; keep the scene, the sign and its text exactly as they are.",
                                   "clean_" + out.stem[:30])
            if not img: return got
            self._i2v(img, d, out)
            return out, got[1] + ", cleaned (AI)", ((got[2] + "; ") if got[2] else "") + "AI-cleaned"
        except Exception as e:
            if str(e) == "cancelled" or type(e).__name__ == "Paused": raise
            _log(self.log, f"    clean-up failed ({str(e)[:80]}); used as it is"); return got

    def sketch(self, visual, text, d, out, meet=False):
        """A courtroom-sketch style drawing of what is said (owner: "even an artist courtroom type rendition is better than
        boring stuff") -> (out, how, credit) or None. The rung before the card."""
        import hashlib
        try:
            from . import imagegen, qc_repair
            cfg = dict(self.cfg, imagegen_rotate=False, imagegen_per_beat=1)   # all image services, Gemini too (2026-11-12)
            key = "sketch_" + hashlib.md5(f"{visual}|{text}".encode()).hexdigest()[:10]
            vo = ("A courtroom-sketch style drawing in pastel and pencil, loose strokes, of: {v}. Faces are loosely suggested in a few "
                  "strokes, not portraits; no names or writing." if meet else
                  "A courtroom-sketch style drawing in pastel and pencil, loose strokes, of: {v}. Scene: {t}").format(v=visual, t=text[:300])
            res = imagegen.generate_many(cfg, self.kd.parent, {"vo": vo}, key, self.log,
                                         aspect="9:16" if self.kind == "short" else "16:9")
            if not res: return None
            still_clip(res[0][0], d, out, self.kind); return out, "sketch (AI)", "AI sketch"
        except Exception as e:
            if str(e) == "cancelled" or type(e).__name__ == "Paused": raise
            _log(self.log, f"    sketch failed ({str(e)[:80]})"); return None

    def media(self, visual, text, d, out):
        """Media of what is being talked about for d seconds (owner, 2026-11-08: "instead of her fuzzy face, show the actual
        yard... stills, video snippets, stock, renditions - anything from the rungs"): your trip footage of that place, a Commons
        photo, your footage of that kind of thing, stock, then the repair ladder's picture rungs (AI image, ChatGPT chain, still),
        then scenery from the area, a courtroom-style sketch, a card. Every found (not drawn) picture must fit the words and not
        be a boring static shot (fits), else the next rung. -> (out, how, credit) or None."""
        visual = (visual or text[:80]).strip()
        # your trip footage only under a line about where you filmed (Nov 8: a line on Merrimack, New Hampshire got Bermuda scenery)
        here = getattr(self, "here", set())
        mine = not here or bool(here & {w[:5] for w in re.findall(r"[a-z]{4,}", (visual + " " + text).lower())})
        ok = lambda g: self.polish(g, d) if g and self.fits(g[0], visual, text) else None
        got = ok(self.trip_clip(visual, text, d, out, ("place",)) if mine else None)
        if got: return got
        img, credit = self._photo(visual, text)
        if img:
            still_clip(img, d, out, self.kind)
            if self.fits(out, visual, text): return self.polish((out, "photo", credit), d)
        got = ok(self.trip_clip(visual, text, d, out, ("subject",)) if mine else None)
        if got: return got
        st = self._stock(visual, text, local=mine)
        if st:
            from . import shots
            f, credit = st
            vf = shots.vf_frame(self.kind, None, 0.0, False, 1080 if self.kind == "short" else 1920, 1920 if self.kind == "short" else 1080)
            media.ff(*(["-stream_loop", "-1"] if media.dur(f) < d else []), "-i", f, "-t", f"{d:.2f}", "-an", "-vf", vf, *ENC[:6], out, timeout=900)
            if self.fits(out, visual, text): return self.polish((out, "stock", credit), d)
        # owner, 2026-11-08: no cap on AI pictures - make as many as needed; a failure moves to the next rung; cards last
        rungs = self.cfg.get("interview_rungs")
        # owner, 2026-11-12: the same picture rungs as explainers and Shorts ("for the interviews too, I want to keep the same
        # rungs"): Meta chain, AI image (Gemini too), ChatGPT chain, Blender, Meta video -> Flow, cloud still, WanGP, held still
        rungs = tuple(("meta_chain", "ai_image", "chatgpt_chain", "unreal", "blender", "video", "cloud", "wangp", "static_image")
                      if rungs is None else rungs)
        for r in rungs:   # Nov 10: an invented man in an apron got through - AI pictures are checked like found ones
            got = self._rungs(text, visual, d, out, (r,))
            if got and self.fits(got[0], visual, text): return got
        got = ok(self.trip_clip(visual, text, d, out, ("scenery",)) if mine else None)
        if got: return got
        got = self.sketch(visual, text, d, out) if rungs else None   # (an AI picture: off with the AI rungs)
        if got and self.fits(got[0], visual, text): return got
        # owner, 2026-11-12: a drawn animation (map, route, chart, stick figures...) is the LAST resort, after every picture
        # rung failed, just before the card (qc_repair._rung_motion; look: motion_look = paper | whiteboard)
        # owner, 2026-11-13: a real 3D model in the studio (like the Volvo) comes ahead of the simple drawings
        for r in (("studio3d",) if self.cfg.get("studio3d_on", True) else ()) + (("physics", "motion") if self.cfg.get("motion_rung", True) else ()) + (("hyperframes",) if self.cfg.get("hyperframes_on", True) else ()):
            got = self._rungs(text, visual, d, out, (r,))
            if got and self.fits(got[0], visual, text): return got
        return self._rungs(text, visual, d, out, ("card",)) if self.cfg.get("interview_card_fallback", True) else None

    def _rungs(self, text, visual, d, out, rungs):
        self.rung_tries += 1
        try:
            from . import qc_repair
            import hashlib   # a folder per line (Nov 8: numbered folders restarted each run, and a Merrimack line got a passport picture)
            rd = "r_" + hashlib.md5(f"{text}|{visual}|{d:.1f}|{','.join(rungs)}".encode()).hexdigest()[:10]
            clip, rung = qc_repair.ladder_clip(self.cfg, text, visual, d, self.kind, self.kd / "ladder" / rd, self.kd.parent,
                                               src=self.src, log=self.log, rungs=rungs)
            if clip:
                shutil.copyfile(clip, out); return out, "rung " + rung, ""   # (Nov 10: the provider / method kept - "rung ai_image: AI image (cloudflare) -> image-to-video")
        except Exception as e:
            if str(e) == "cancelled" or type(e).__name__ == "Paused": raise
            _log(self.log, f"    rungs failed ({type(e).__name__}: {str(e)[:120]}); next")
        return None

    def picture(self, a, b, out, visual="", text="", why=""):
        """A silent video for a..b of the source -> (path, how, credit). Owner, 2026-11-09 ("whenever I or Mr. Clinton
        describe or mention something, I don't want to stare at the floor"): `text` is what is said in this stretch; it picks
        the picture - the place itself remade without the person, a mock-up of a plan, or media of what is mentioned."""
        from . import shots
        d = b - a
        q = self.stretch(text, visual, d) if d >= 3 and text and self.cfg.get("interview_stretch_pictures", True) else None
        if q and q["show"] == "thing" and (self.OWN_WORDS.search(q["visual"] or "") or self.PERSON.search(q["visual"] or "")) and self._at_place():
            q = dict(q, show="here")   # owner, 2026-11-09: "this is definitely not the masjid we talk about" - never another mosque
        if q:
            _log(self.log, f"    {a:.0f}-{b:.0f}s is about: {q['show']} - {q['visual']}")
            q = dict(q, text=text)
            got = self.mockup(q, d, out) if q["show"] == "plan" else self.remake(a, b, q, d, out) if q["show"] == "here" else None
            if q["show"] == "meet":   # owner, 2026-11-11: "Tariq is me and I am being introduced to an imam... could have conjured
                got = self.sketch(q["visual"] or "two men greeting each other", text, d, out, meet=True)   # up an imam and me as
            if got: return got                                                                       # a courtroom style pic"
            if q["show"] == "here":   # the place itself: the nearest good remade frame of this clip, never a drawn / stock stand-in
                got = self.nearby_still(a, d, out)
                if got: return got[0], got[1], "AI-recreated from our footage"
            visual = q["visual"] or visual
        if d >= 4 and visual:
            ow = self._own(visual, d)
            if ow:
                f, st, rot = ow
                W, H = (1080, 1920) if self.kind == "short" else (1920, 1080)   # fill the frame (a portrait clip in a wide video: no black bars)
                vf = (rot + "," if rot else "") + f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},fps=30,format=yuv420p,setsar=1"
                media.ff("-ss", f"{st:.2f}", "-i", f, "-t", f"{d:.2f}", "-an", "-vf", vf, *ENC[:6], out, timeout=900)
                if self.fits(out, visual, text): return self.polish((out, "own footage", ""), d)
        if d >= 3 and (visual or text):   # owner, 2026-11-09: "he is talking as we look at the floor... no rungs fired" - a bad
            got = self.media(visual, text, d, out)   # stretch gets media of what he is saying: trip footage, photo, stock, AI, card
            if got: return got
        if why == "self" and self.scenic:   # a blurred face is still a face: the holder's shots go under the blurred bay instead
            f, st, fl, rot = self.scenic
            vf = (rot + "," if rot else "") + shots.vf_frame(self.kind, None, 0.0, fl) + ",boxblur=20:2,eq=brightness=-0.1"
            media.ff("-stream_loop", "-1", "-ss", f"{st + (a * 1.3) % 20:.2f}", "-i", f, "-t", f"{d:.2f}", "-an", "-vf", vf, *ENC[:6], out, timeout=900)
            return out, "blurred bay", ""
        vf = shots.vf_frame(self.kind, None, 0.0, self.flip) + ",boxblur=24:2,eq=brightness=-0.06"
        media.ff("-ss", f"{a:.2f}", "-i", self.src, "-t", f"{d:.2f}", "-an", "-vf", vf, *ENC[:6], out, timeout=900)
        return out, "blur", ""


def blank_frame(img, lim=0.025):
    """Owner, 2026-11-11 ("why this filthy wall?"): a picture that is mostly a bare wall, floor or shirt - almost no edges
    anywhere (Canny edge share under 2.5% at 640 px). Cheap and local; calibrated on 52 remade frames of the Clinton job
    (bare / stained walls 0.000-0.021, rooms with anything in them 0.03 and up). Unreadable -> False."""
    try:
        from . import shots
        cv2, np = shots._cv()
        im = cv2.imread(str(img)) if cv2 is not None else None
        if im is None: return False
        g = cv2.cvtColor(cv2.resize(im, (640, max(1, int(640 * im.shape[0] / im.shape[1])))), cv2.COLOR_BGR2GRAY)
        return float((cv2.Canny(cv2.GaussianBlur(g, (5, 5), 0), 60, 150) > 0).mean()) < lim
    except Exception:
        return False


def _four_turns(cv2, np, im, order):
    """A 2x2 sheet of one frame turned 0 / 90 / 180 / 270 degrees clockwise (in `order`), labelled A-D."""
    S = 384; cells = []
    rot = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
    for lab, r in zip("ABCD", order):
        x = cv2.rotate(im, rot[r]) if r else im
        h, w = x.shape[:2]; k = (S - 30) / max(h, w)
        x = cv2.resize(x, (max(1, int(w * k)), max(1, int(h * k))))
        c = np.full((S, S, 3), 255, np.uint8)
        y0, x0 = 30 + (S - 30 - x.shape[0]) // 2, (S - x.shape[1]) // 2
        c[y0:y0 + x.shape[0], x0:x0 + x.shape[1]] = x
        cv2.putText(c, lab, (8, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 200), 2)
        cells.append(c)
    return np.vstack([np.hstack(cells[:2]), np.hstack(cells[2:])])


def filmed_at(path):
    """The clip's own recording time (ISO text from its creation_time tag), or "" when it has none."""
    import subprocess
    try:
        j = json.loads(subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)], capture_output=True, text=True, timeout=30).stdout or "{}")
        return str(j.get("format", {}).get("tags", {}).get("creation_time", ""))
    except Exception:
        return ""


def gps_of(path):
    """(lat, lon) from an iPhone video's location tag, or None."""
    import subprocess
    try:
        j = json.loads(subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)], capture_output=True, text=True, timeout=30).stdout or "{}")
        m = re.match(r"([+-]\d+\.\d+)([+-]\d+\.\d+)", j.get("format", {}).get("tags", {}).get("com.apple.quicktime.location.ISO6709", ""))
        return (float(m[1]), float(m[2])) if m else None
    except Exception:
        return None


# Your own footage libraries, used for any job about that place wherever its source video is kept (owner, 2026-11-09:
# "all videos/stills in f:\\bermuda are mine ... treat as pexels with no restrictions"). Each is a catalogue made by
# scripts/label_media.py catalog. A job picks one when the source's GPS is inside the area or the title / note names the place.
TRIP_LIBRARIES = [{"place": "bermuda", "catalog": "F:/bermuda/labeled/catalog", "lat": 32.32, "lon": -64.76, "km": 60}]


def trip_catalog(cfg, src, near, text, log=print):
    """The catalogue of your own clips for this job: the one beside the source (<folder>/labeled/catalog) when there is one,
    else a library from cfg trip_libraries (default TRIP_LIBRARIES) whose area holds the source's GPS or whose place the
    title / note names. None when no library fits (a story from elsewhere gets none of them)."""
    here = Path(src).parent / "labeled" / "catalog"
    if here.is_dir(): return here
    low = str(text or "").lower()
    for lib in cfg.get("trip_libraries") or TRIP_LIBRARIES:
        cat = Path(str(lib.get("catalog", "")))
        if not cat.is_dir(): continue
        inside = False
        if near and lib.get("lat") is not None:
            km = 6371 * math.hypot(math.radians(near[0] - float(lib["lat"])), math.radians(near[1] - float(lib["lon"])) * math.cos(math.radians(float(lib["lat"]))))
            inside = km <= float(lib.get("km", 50))
        named = bool(lib.get("place")) and re.search(r"\b" + re.escape(str(lib["place"]).lower()), low) is not None
        if inside or named:
            _log(log, f"  your {lib.get('place') or cat} footage library: {cat} ({'filmed there' if inside else 'the story names it'})")
            return cat
    return None


def own_clips(src, root, log=print, radius=150.0, cfg=None):
    """Your other clips filmed near the interview (GPS in the files: iPhone ISO6709), with no face in them (the prayer hall,
    hallways): [(file, seconds, rotation)]. Copies with the same length count once. Cached in own_clips.json.
    With cfg each clip is also looked at (shots.vet_clip, owner 2026-11-09): a phone resting on the carpet, a shirt filmed
    sideways, a dashboard are not "the place" - they were the boring floor and the upside-down shots of the Clinton cut."""
    cache = Path(root) / ("own_clips_v3.json" if cfg else "own_clips.json")
    if cache.exists():
        try: return [(Path(f), d, r) for f, d, r in json.loads(cache.read_text(encoding="utf-8"))]
        except Exception: pass
    import subprocess
    def tags(p):
        try:
            j = json.loads(subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(p)], capture_output=True, text=True, timeout=30).stdout or "{}")
            fm = j.get("format", {}); t = fm.get("tags", {})
            m = re.match(r"([+-]\d+\.\d+)([+-]\d+\.\d+)", t.get("com.apple.quicktime.location.ISO6709", ""))
            return float(fm.get("duration") or 0), ((float(m[1]), float(m[2])) if m else None)
        except Exception: return 0.0, None
    _, here = tags(src)
    if not here: return []
    from . import shots
    cv2, np = shots._cv()
    out, seen = [], set()
    files = sorted([f for f in Path(src).parent.iterdir() if f.suffix.lower() in VIDEO_EXT and f.resolve() != Path(src).resolve()],
                   key=lambda f: (f.stem.upper().startswith("IMG_"), f.name))   # named copies first
    for f in files:
        d, ll = tags(f)
        if d < 4 or not ll or round(d, 1) in seen: continue
        if re.search(r"with[-_ ]", f.stem, re.I): seen.add(round(d, 1)); continue   # "...-with-clinton": a person's clip (and its IMG_ copy)
        dist = 6371000 * math.hypot(math.radians(ll[0] - here[0]), math.radians(ll[1] - here[1]) * math.cos(math.radians(here[0])))
        if dist > radius or d > 600: continue
        rot = shots.orient(f, root, log)
        faces = False
        if cv2:
            for k in range(8):   # nobody's face: the place, not people (and never the camera holder)
                tmp = Path(root) / "_own.jpg"
                media.ff("-ss", f"{d * (k + 1) / 9:.2f}", "-i", f, "-frames:v", 1, "-vf", (rot + "," if rot else "") + "scale=640:-2", tmp, timeout=120)
                im = cv2.imread(str(tmp))
                if im is not None and any(x[2] * x[3] > 0.004 for x in shots._faces(cv2, cv2.cvtColor(im, cv2.COLOR_BGR2GRAY), im)): faces = True; break
        if faces: continue
        if cfg is not None:   # looked at: junk is skipped, a sideways / upside-down clip is turned
            v = shots.vet_clip(cfg, f, rot, log)
            if not v[0]: seen.add(round(d, 1)); continue
            rot = v[2] if len(v) > 2 else rot
        seen.add(round(d, 1)); out.append((f, d, rot))
    cache.write_text(json.dumps([(str(f), d, r) for f, d, r in out], indent=1), encoding="utf-8")
    _log(log, f"  own footage near the interview (no faces): {', '.join(f'{f.stem} ({d:.0f}s)' for f, d, _ in out) or 'none'}")
    return out


def still_clip(img, d, out, kind):
    """A still with a slow push-in, silent, d seconds."""
    w, h = (1080, 1920) if kind == "short" else (1920, 1080); n = int(d * 30) + 1
    vf = (f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},"
          f"zoompan=z='min(zoom+0.0005,1.10)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n}:s={w}x{h}:fps=30,format=yuv420p,setsar=1,"
          f"fade=t=in:st=0:d=0.3,fade=t=out:st={max(0.0, d - 0.3):.2f}:d=0.3")
    media.ff("-loop", 1, "-i", img, "-t", f"{d:.2f}", "-an", "-vf", vf, *ENC[:6], out, timeout=900)
    return out


XFADE = 0.5   # seconds: picture changes inside his talk dissolve instead of cutting (owner, 2026-10-24: "transitions too fast")


def _snap(bounds, words, t0, t1, reach=1.2):
    """Move each picture change to the nearest pause between his words (within reach seconds), so the picture never
    changes in the middle of a word or phrase."""
    gaps = [(w1[2] + w2[1]) / 2 for w1, w2 in zip(words, words[1:]) if w2[1] - w1[2] >= 0.12]
    out, last = [], t0
    for b in bounds:
        near = [g for g in gaps if abs(g - b) <= reach and g - last >= 1.5 and t1 - g >= 1.5]
        nb = min(near, key=lambda g: abs(g - b)) if near else b
        if nb - last < 1.5 or t1 - nb < 1.5: continue   # too close to the previous change / the end: merge
        out.append(nb); last = nb
    return out


def shot_plan(an, t0, t1, keep, words, split, legacy=False):
    """A soundbite's shots [(a, b, kind, box, tilt)]: per second, changes snapped to pauses, the creator capped, long covered
    stretches split. legacy=True: the rules before 2026-11-09 evening (no giant-face / chin cover, no split), to tell
    whether a piece made under them would come out the same."""
    from . import shots
    shots.LEGACY[0] = legacy
    try:
        plan = shots.plan_keep(an, t0, t1, min_len=3.0) if keep else shots.plan_shots(an, t0, t1, min_len=3.0)
        if len(plan) > 1 and words:   # snap the changes to pauses
            nb = _snap([p[0] for p in plan[1:]], words, t0, t1)
            old = plan; plan = []
            edges = [t0] + nb + [t1]
            for a, b in zip(edges, edges[1:]):   # each new piece takes the kind of the old shot covering most of it
                best = max(old, key=lambda p: min(b, p[1]) - max(a, p[0]))
                plan.append((a, b, best[2], best[3], best[4]))
            merged = []
            for p in plan:   # neighbours of the same kind join
                if merged and merged[-1][2] == p[2] and merged[-1][3] == p[3]: merged[-1] = (merged[-1][0], p[1], p[2], p[3], p[4])
                else: merged.append(p)
            plan = merged
        plan = shots.cap_vlog(plan, t0, t1, "blur" if keep else "self")   # the creator on screen: as filmed, never for long
        if split and not legacy:   # owner, 2026-11-09: a long covered stretch is several pictures, each of what is said in it
            plan = split_covered(plan, ("blur",) if keep else ("self", "empty"), words)
    finally:
        shots.LEGACY[0] = False
    return plan


def cut_bite_framed(src, t0, t1, out, kind, flip, an, cuts, log=print, ladder=None, visual="", text="", credits=None, words=None,
                    soft_in=True, soft_out=True, keep=False, place=None):
    """A soundbite with its own audio and a framed picture: on the guest, level, covered where the picture is bad (the
    holder, a ceiling, a shirt close-up, the phone on its side). Picture changes sit in pauses and dissolve.
    Owner, 2026-11-09 ("why a full render? the editor splices one beat"): a finished piece is kept unless its shot plan -
    what each stretch shows and how - came out different this time (a rule change); only then is it cut again."""
    from . import shots
    plan = shot_plan(an, t0, t1, keep, words, ladder is not None)
    key = piece_key(src, t0, t1, kind, flip, keep, place, plan, visual, text)
    kf = Path(out).with_suffix(".key")
    tmpd = Path(out).with_suffix("")
    audit = ladder is not None and ladder.cfg.get("interview_beat_audit", True)
    af = Path(out).with_suffix(".audit")
    kept = False
    if media.ok(out, 0.5):
        if kf.exists() and kf.read_text(encoding="utf-8").strip() == key: kept = True
        elif not kf.exists() and Path(out).stat().st_mtime >= RULES_SINCE:   # made with the rules before the close-up rule (Nov 9
            legacy = shot_plan(an, t0, t1, keep, words, ladder is not None, legacy=True)   # evening): kept only if they plan it alike
            if piece_key(src, t0, t1, kind, flip, keep, place, legacy, visual, text) == key:
                kf.write_text(key, encoding="utf-8"); kept = True
            else: _log(log, f"    {Path(out).name}: the close-up / floor rules change its shots -> cut again")
        else: _log(log, f"    {Path(out).name}: {'shot plan changed' if kf.exists() else 'made before plan keys'} -> cut again")
    if kept:   # owner, 2026-11-10: a finished piece is audited once a second too; only the stretches that fail are made again
        if not audit or (af.exists() and af.read_text(encoding="utf-8").strip() == key + AUDIT_TAG): return out
        if not all(media.ok(tmpd / f"s{n:02}.mp4", 0.2) for n in range(len(plan))):
            af.write_text(key + AUDIT_TAG, encoding="utf-8"); return out   # (its stretches are gone: not auditable piecemeal)
        segs = [tmpd / f"s{n:02}.mp4" for n in range(len(plan))]; durs = [b - a for a, b, *_ in plan]
        if not beat_audit(plan, segs, t0, ladder, visual, text, words, credits, log):
            af.write_text(key + AUDIT_TAG, encoding="utf-8"); return out
        _log(log, f"    {Path(out).name}: stretches replaced after the picture audit -> joined again")
    else:
        if credits is not None: credits.clear()   # (the old cut's picture credits go with it)
        tmpd.mkdir(exist_ok=True)
        segs, durs = _cut_stretches(plan, src, t0, out, kind, flip, an, cuts, log, ladder, visual, text, credits, words, keep, place, tmpd)
        if audit: beat_audit(plan, segs, t0, ladder, visual, text, words, credits, log)
    _join_piece(segs, durs, src, t0, t1, out, soft_in, soft_out)
    kf.write_text(key, encoding="utf-8")
    if audit: af.write_text(key + AUDIT_TAG, encoding="utf-8")
    return out


AUDIT_TAG = "|audit2"


def beat_audit(plan, segs, t0, ladder, visual, text, words, credits, log=print):
    """Owner, 2026-11-10: "take a 1 sec snapshot of the beat and compare... in that case reject and give it the rung regimen".
    Every stretch of a piece is looked at once a second: the guest on camera only for being turned (it is turned upright); any
    other picture also against what is said there - a stretch that fails goes down the picture rungs (the stretch's own
    remade place, media of what is said, AI pictures, a sketch, a card). A card is never audited. -> True when anything changed."""
    changed = False
    for n, (a, b, k, g, tilt) in enumerate(plan):
        v = Path(segs[n]); said = said_in(words, a, b) or text
        cr = next((c for c in (credits or []) if abs(float(c[0]) - (a - t0)) < 0.05), None)
        if cr and "card" in str(cr[2]): continue
        mode = "orient" if k in ("guest", "vlog") and not cr else "full"
        ref = "" if mode == "orient" or not ladder.content_audit else ladder.describe((a + b) / 2)   # (Nov 10: an ornate mosque passed)
        ok, turn, why = ladder.audit(v, visual, said, ref=ref, mode=mode)
        if ok and turn:
            ladder.turn(v, turn); changed = True
            ok, turn, why = ladder.audit(v, visual, said, ref=ref, mode=mode)
            ok = ok and not turn
        if ok: continue
        _log(log, f"    {a:.0f}-{b:.0f}s fails the picture audit ({why}) -> down the rungs")
        d = media.dur(v) or (b - a)
        tmp = v.with_name(v.stem + "_new.mp4")
        got = ladder.nearby_still(a, d, tmp) if d < 3 else None   # too short for the rungs: the nearest rebuilt frame of this clip
        _, how, credit = (got[0], got[1], "AI-recreated from our footage") if got else ladder.picture(a, a + d, tmp, visual, said, why="audit")
        Path(tmp).replace(v); changed = True
        if credits is not None:
            credits[:] = [c for c in credits if abs(float(c[0]) - (a - t0)) >= 0.05] + [(a - t0, b - t0, how, credit)]
        _log(log, f"    {a:.0f}-{b:.0f}s -> {how}" + (f" ({credit})" if credit else ""))
    return changed


def _cut_stretches(plan, src, t0, out, kind, flip, an, cuts, log, ladder, visual, text, credits, words, keep, place, tmpd):
    """Each stretch of a soundbite's shot plan made into its own silent clip -> (clips, durations)."""
    from . import shots
    segs, durs, borrowed = [], [], []
    for n, (a, b, k, g, tilt) in enumerate(plan):
        v = tmpd / f"s{n:02}.mp4"; d = b - a
        seen = None
        if keep and k == "none" and ladder is not None and ladder.cfg.get("interview_upright_check", True):
            seen = shots.look(ladder.cfg, src, (a + b) / 2, flip, log)
            if not seen[1]: k = "blur"
        ext = XFADE if n < len(plan) - 1 else 0.0   # each piece runs on under the dissolve into the next
        if keep and place and k == "none": k = "blur"   # a clip filmed mostly on the holder's neck (third floor): only shots of the guest stay
        if keep and k == "blur" and ladder is not None and d >= 3 and ladder.cfg.get("interview_stretch_pictures", True):
            q = ladder.stretch(said_in(words, a, b) or text, visual, d)   # owner, 2026-11-09: "he says they will have a huge
            if q and q.get("show") == "plan":                             # terrace... show a mock-up of it": a plan beats a
                got = ladder.mockup(q, d + ext, v)                         # clean moment of the same clip
                if got:
                    if credits is not None: credits.append((a - t0, b - t0, got[1], got[2]))
                    _log(log, f"    {a:.0f}-{b:.0f}s: about a plan -> {got[1]} ({got[2]})")
                    segs.append(v); durs.append(d); continue
        if keep and k == "blur" and ladder is not None and d >= 3 and holder_in(an, a, b) and ladder.cfg.get("interview_remove_holder", True):
            got = ladder.remake(a, b + ext, {"text": said_in(words, a, b) or text, "visual": visual}, d + ext, v, holder_only=True)   # his face out of this
            if got:                                                              # frame, Ken Burns / parallax - not a stand-in
                if credits is not None: credits.append((a - t0, b - t0, got[1], got[2]))
                _log(log, f"    {a:.0f}-{b:.0f}s: your close-up -> {got[1]}")
                segs.append(v); durs.append(d); continue
        if keep and k == "blur" and ladder is not None and d >= 3 and smudged(an, a, b) and ladder.cfg.get("interview_remove_holder", True):
            got = ladder.clean_frame(a, b + ext, d + ext, v, said_in(words, a, b) or text, visual)   # owner, 2026-11-09: a finger /
            if got:                                                                                # smudge / fuzz: this frame
                if credits is not None: credits.append((a - t0, b - t0, got[1], got[2]))           # cleaned, Ken Burns / parallax
                _log(log, f"    {a:.0f}-{b:.0f}s: finger / smudge -> {got[1]}")
                segs.append(v); durs.append(d); continue
        if keep and k == "blur":   # the holder's close-up / a finger: the nearest clean moment of the SAME clip, else blurred
            cs = None if place else clean_start(an, a, b, d + ext, media.dur(src), taken=borrowed)   # (neck-filmed: "clean" isn't)
            if cs is not None:
                crot = shots.upright_rot(ladder.cfg, src, cs + d / 2, flip, log) if ladder is not None and ladder.cfg.get("interview_upright_check", True) else 0
                media.ff("-ss", f"{cs:.2f}", "-i", src, "-t", f"{d + ext:.2f}", "-an", "-vf", shots.vf_frame(kind, None, 0.0, flip, rot=crot), *ENC[:10], v, timeout=900)
                _log(log, f"    {a:.0f}-{b:.0f}s: same scene from {cs:.0f}s (close-up / finger here)")
                segs.append(v); durs.append(d); continue
            if place:   # nothing clean nearby: the creator's own clip of the same place, same afternoon
                pf, pfl = place[int(a) % len(place)]
                pd = media.dur(pf); pst = (a * 7.3) % max(1.0, pd - d - ext - 1.0)
                prot = "" if pfl else shots.orient(pf, Path(out).parent.parent, log)
                media.ff("-ss", f"{pst:.2f}", "-i", pf, "-t", f"{d + ext:.2f}", "-an", "-vf", (prot + "," if prot else "") + shots.vf_frame(kind, None, 0.0, pfl),
                         *ENC[:10], v, timeout=900)
                _log(log, f"    {a:.0f}-{b:.0f}s: same place, {Path(pf).name} at {pst:.0f}s (close-up here, nothing clean nearby)")
                segs.append(v); durs.append(d); continue
            if ladder is not None and d >= 3:   # owner, 2026-11-09: a floor / ceiling / close-up stretch gets media of what he is
                _, how, credit = ladder.picture(a, b + ext, v, visual, said_in(words, a, b) or text, why="empty")   # saying, not a blur
                if credits is not None: credits.append((a - t0, b - t0, how, credit))
                _log(log, f"    {a:.0f}-{b:.0f}s: floor / ceiling / close-up -> {how}" + (f" ({credit})" if credit else ""))
                segs.append(v); durs.append(d); continue
        if keep and k == "blur" and ladder is not None and d < 3:
            got = ladder.nearby_still(a, d + ext, v)   # Nov 9: "So we're trying" was a black, blurred ceiling
            if got:
                _log(log, f"    {a:.0f}-{b:.0f}s: too short for the rungs -> {got[1]}")
                segs.append(v); durs.append(d); continue
        if keep:   # owner, 2026-10-25: his footage is never swapped - turned upright, blurred where not worth seeing sharp
            rot = int(k[4:]) if k.startswith("turn") else 0
            if not rot and k == "none" and seen: rot = seen[0]   # Nov 9: a faceless shelf filmed upside down
            vf = shots.vf_frame(kind, g if k == "guest" else None, 0.0 if rot else tilt, flip, rot=rot, blur=(k == "blur"))
            media.ff("-ss", f"{a:.2f}", "-i", src, "-t", f"{d + ext:.2f}", "-an", "-vf", vf, *ENC[:10], v, timeout=900)
            if k == "vlog": _log(log, f"    {a:.0f}-{b:.0f}s: the creator on camera (vlog, kept)")
            elif k != "none" and k != "guest": _log(log, f"    {a:.0f}-{b:.0f}s: {'turned upright (' + str(rot) + ' deg)' if rot else 'kept, blurred'}")
            segs.append(v); durs.append(d); continue
        if k == "guest" and g and len(g) > 4 and not shots.holder_out(kind, g): g = g[:4]   # Nov 9: a sliver of the creator beside him is fine
        if k == "self" and ladder is not None and d >= 3 and ladder.cfg.get("interview_remove_holder", True):
            got = ladder.remake(a, b + ext, {"text": said_in(words, a, b) or text, "visual": visual}, d + ext, v, holder_only=True)   # (Nov 9)
            if got:
                if credits is not None: credits.append((a - t0, b - t0, got[1], got[2]))
                _log(log, f"    {a:.0f}-{b:.0f}s: your close-up -> {got[1]}")
                segs.append(v); durs.append(d); continue
        if k in ("self", "empty") and ladder is not None:   # his voice goes on; the picture comes from the ladder
            _, how, credit = ladder.picture(a, b + ext, v, visual, said_in(words, a, b) or text, why=k)
            if credits is not None: credits.append((a - t0, b - t0, how, credit))
            _log(log, f"    {a:.0f}-{b:.0f}s: {'holder in shot' if k == 'self' else 'ceiling / blank / unusable'} -> {how}" + (f" ({credit})" if credit else "")
                 + (f" [{Path(ladder.trip.last).name}]" if how.startswith("trip") and ladder.trip and getattr(ladder.trip, "last", None) else ""))
        elif k in ("self", "empty"):   # cutaway: the sound goes on, the picture shows the guest elsewhere / B-roll
            f, st, fl, gb, rot = cuts.take(d + ext)
            vf = (rot + "," if rot else "") + shots.vf_frame(kind, gb, 0.0, fl)
            media.ff("-ss", f"{st:.2f}", "-i", f, "-t", f"{d + ext:.2f}", "-an", "-vf", vf, *ENC[:10], v, timeout=900)
        else:
            media.ff("-ss", f"{a:.2f}", "-i", src, "-t", f"{d + ext:.2f}", "-an", "-vf", shots.vf_frame(kind, g, tilt, flip), *ENC[:10], v, timeout=900)
        segs.append(v); durs.append(d)
    return segs, durs


def _join_piece(segs, durs, src, t0, t1, out, soft_in=True, soft_out=True):
    """The stretches dissolved together under the soundbite's own sound."""
    T = t1 - t0
    edge = ",".join(([f"fade=t=in:st=0:d=0.3"] if soft_in else []) + ([f"fade=t=out:st={max(0.0, T - 0.3):.2f}:d=0.3"] if soft_out else [])) or "null"
    # (a soft start / end only where his talk meets a fact or a jump in time; two pieces of one continuous stretch join straight)
    if len(segs) == 1:
        vin = ["-i", segs[0]]; flt = ["-filter_complex", f"[0:v]{edge}[vo]"]; vmap = "[vo]"
    else:
        vin = [x for sg in segs for x in ("-i", sg)]
        chain, prev, off = [], "[0:v]", 0.0
        for i in range(1, len(segs)):
            off += durs[i - 1]
            lab = f"[x{i}]"
            chain.append(f"{prev}[{i}:v]xfade=transition=fade:duration={XFADE}:offset={off:.3f}{lab}")
            prev = lab
        chain.append(f"{prev}{edge}[vo]")
        flt = ["-filter_complex", ";".join(chain)]; vmap = "[vo]"
    n_in = len(segs)
    media.ff(*vin, "-ss", f"{t0:.2f}", "-i", src, *flt, "-t", f"{t1 - t0:.2f}", "-map", vmap, "-map", f"{n_in}:a:0",
             "-af", f"loudnorm=I=-16:TP=-1.5:LRA=11,afade=t=in:st=0:d=0.06,afade=t=out:st={max(0.0, t1 - t0 - 0.08):.2f}:d=0.08",
             *ENC, out, timeout=1800)
    return out


COVER_CHUNK = 8.0   # seconds: about one picture per sentence or two where the footage is covered


def split_covered(plan, kinds, words, chunk=COVER_CHUNK):
    """Covered stretches longer than 1.5 chunks cut into ~chunk-second pieces at pauses between words."""
    out = []
    for a, b, k, g, tl in plan:
        if k not in kinds or b - a <= chunk * 1.5: out.append((a, b, k, g, tl)); continue
        n = max(2, round((b - a) / chunk))
        bounds = [a + (b - a) * i / n for i in range(1, n)]
        edges = [a] + (_snap(bounds, words, a, b) if words else bounds) + [b]
        out += [(x, y, k, g, tl) for x, y in zip(edges, edges[1:])]
    return out


def smudged(an, a, b):
    """A stretch spoiled by a finger, a smudge or fuzz (the vision check's "bad"), not a floor / ceiling / blank wall."""
    xs = [x for x in an.get("secs") or [] if a <= x["t"] < b]
    from . import shots
    bad = [x for x in xs if x.get("bad") and x.get("plain", 0) <= 0.6 and x.get("top", 0) <= shots.CEILING_TOP]
    return bool(xs) and len(bad) * 2 >= len(xs)


def holder_in(an, a, b):
    """Is the camera holder's oversized face in this stretch (or a second either side - his chin runs between)?"""
    from . import shots
    for x in an.get("secs") or []:
        if a - 1 <= x["t"] <= b + 1 and not x.get("back") and any(shots.too_close(f) for f in shots.split(x["faces"], x.get("back"))[1]):
            return True
    return False


def said_in(words, a, b):
    """The words spoken between a and b (source seconds)."""
    return " ".join(str(w) for w, s0, e0 in (words or []) if s0 >= a - 0.4 and e0 <= b + 0.4).strip()


def piece_key(src, t0, t1, kind, flip, keep, place, plan, visual="", text=""):
    """Fingerprint of a soundbite piece: its source and times, how it is framed, and its shot plan (kind, framing box, tilt
    per stretch). Same fingerprint = the same picture would be made again, so the finished piece is kept."""
    import hashlib
    sig = [PIECE_VERSION, str(src), round(float(t0), 2), round(float(t1), 2), kind, bool(flip), bool(keep), bool(place),
           [(round(a, 2), round(b, 2), k, [round(float(x), 3) for x in (g[:4] if g else [])], round(float(tl or 0), 1)) for a, b, k, g, tl in plan],
           str(visual or ""), str(text or "")[:300]]
    return hashlib.md5(json.dumps(sig).encode()).hexdigest()


def clean_start(an, a, b, d, dur, reach=45.0, taken=None):
    """Start of the nearest stretch of d seconds in the same clip (within reach) that is clean as filmed - no close-up of the
    camera holder, no finger, not on its side - outside a-b and outside `taken` (moments this piece already borrowed: Nov 9,
    one moment came back six times in a minute); None if there is none. As filmed means uncropped: a "guest" second with his
    oversized face beside the guest is not clean (owner, 2026-11-09: the Gulliver two-shot at 4:59)."""
    from . import shots
    def kind(x):
        k = shots.kind_keep(x)
        if k == "guest" and not x.get("back") and any(shots.too_close(f) for f in shots.split(x["faces"], x.get("back"))[1]): return "blur"
        return k
    by = {x["t"]: kind(x) for x in an.get("secs") or []}
    taken = taken if taken is not None else []
    # front camera (Nov 9): a second with no face found is as likely your chin as a clean shot - only seconds framed on the guest
    good = ("none", "guest") if any(x.get("back") for x in an.get("secs") or []) else ("guest",)
    ok = lambda t0: t0 >= 0 and t0 + d <= dur - 0.2 and all(by.get(t, "none") in good for t in range(int(t0), int(t0 + d) + 1))         and not any(t0 < u1 and t0 + d > u0 for u0, u1 in taken)
    for step in range(0, int(reach) + 1):
        for t0 in (a - d - step, b + step):
            if ok(t0): taken.append((float(t0), float(t0) + d)); return float(t0)
    return None


def scene_window(segs, items, k, d, durs):
    """Where a fact / joke pause takes its picture (owner, 2026-10-25: the real scene, never another place): the same clip
    near the pause, a stretch no soundbite shows (a dropped bit), else the seconds just before. -> (src index, start)."""
    prev = next((items[j]["bite"] for j in range(k - 1, -1, -1) if "bite" in items[j]), None)
    nxt = next((items[j]["bite"] for j in range(k + 1, len(items)) if "bite" in items[j]), None)
    anchor = int(prev[1]) if prev else int(nxt[0]) if nxt else 0
    si = segs[anchor].get("src", 0)
    t = segs[anchor]["end"] if prev else max(0.0, segs[anchor]["start"] - d)
    used = [(segs[int(it["bite"][0])]["start"], segs[int(it["bite"][1])]["end"]) for it in items
            if "bite" in it and segs[int(it["bite"][0])].get("src", 0) == si]
    free = lambda a: a >= 0 and a + d <= durs.get(si, 1e9) and not any(a < u1 and a + d > u0 for u0, u1 in used)
    for step in range(0, 46):
        for a in (t + 2 * step, t - d - 2 * step):
            if free(a): return si, a
    return si, max(0.0, (segs[anchor]["end"] if prev else segs[anchor]["start"]) - d)


def scene_part(src, flip, an, start, wav, out, kind, log=print):
    """Narration over the real scene (muted), turned upright / blurred like the soundbites."""
    d = media.dur(wav) + 0.35
    pic = Path(out).with_name(Path(out).stem + "_pic.mp4")
    cut_bite_framed(src, start, start + d, pic, kind, flip, an, None, log, keep=True)
    media.ff("-i", pic, "-i", wav, "-t", f"{d:.2f}", "-map", "0:v:0", "-map", "1:a:0",
             "-vf", f"fade=t=in:st=0:d=0.3,fade=t=out:st={d - 0.3:.2f}:d=0.3", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,apad", *ENC, "-shortest", out, timeout=900)
    return out


def audit_part(ladder, f, visual, text, wav, log=print):
    """A narration / fact part looked at once a second against its words, like the soundbites (owner, 2026-11-10: the opening
    lines ran over the carpet). A failing picture is made again from the picture rungs (cover_part); a card is never audited."""
    if ladder is None or not ladder.cfg.get("interview_beat_audit", True) or not media.ok(f, 0.5): return
    af = Path(f).with_suffix(".audit")
    if af.exists() and af.read_text(encoding="utf-8").strip() == f"{Path(f).stat().st_size}{AUDIT_TAG}": return None
    ok, turn, why = ladder.audit(f, visual, text)
    got = None
    if not ok or turn:
        _log(log, f"    {Path(f).name} fails the picture audit ({why or 'turned'}) -> down the rungs")
        tmp = Path(f).with_name(Path(f).stem + "_re.mp4")
        got = cover_part(ladder, visual, text, wav, tmp, log)
        if got:
            Path(tmp).replace(f); _log(log, f"    {Path(f).name} -> {got[1]}" + (f" ({got[2]})" if got[2] else ""))
    af.write_text(f"{Path(f).stat().st_size}{AUDIT_TAG}", encoding="utf-8")
    return got


def cover_part(ladder, visual, text, wav, out, log=print):
    """Narration over media of what it is about (the ladder), never over a face. -> (out, how, credit) or None."""
    d = media.dur(wav) + 0.35
    pic = Path(out).with_name(Path(out).stem + "_cov.mp4")
    got = ladder.media(visual, text, d, pic)
    if not got: return None
    pic.with_suffix(".json").write_text(json.dumps({"how": got[1], "credit": got[2]}), encoding="utf-8")   # (the editor shows the rung)
    media.ff("-i", pic, "-i", wav, "-t", f"{d:.2f}", "-map", "0:v:0", "-map", "1:a:0",
             "-vf", f"fade=t=in:st=0:d=0.3,fade=t=out:st={d - 0.3:.2f}:d=0.3", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,apad", *ENC, "-shortest", out, timeout=900)
    return out, got[1], got[2]


PIP_LEAD = 3.0   # seconds of the guest full-frame before the place takes over


def pip_part(bite, bg, out, kind, lead=PIP_LEAD):
    """The guest in a corner over what she describes (owner, 2026-11-08: "if a subject is describing something and it is not
    where she is, show actual media of the scene while she talks in a cut-out"): her face full-frame for the first seconds, then
    the place dissolves in and she talks from an inset at the top right (clear of the captions). Her audio throughout."""
    W, H = (1080, 1920) if kind == "short" else (1920, 1080)
    pw = int(W * (0.46 if kind == "short" else 0.33)) // 2 * 2
    flt = (f"[1:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,format=yuva420p,fade=t=in:st=0:d=0.5:alpha=1,"
           f"setpts=PTS-STARTPTS+{lead}/TB[bg];"
           f"[0:v]split[full][s];[s]scale={pw}:-2,pad=iw+8:ih+8:4:4:white,format=yuva420p,fade=t=in:st={lead}:d=0.5:alpha=1[p];"
           f"[bg][p]overlay={W - pw - 8 - int(W * 0.03)}:{int(H * 0.05)}:eof_action=pass[bp];"
           f"[full][bp]overlay=0:0:eof_action=pass,format=yuv420p[v]")
    media.ff("-i", bite, "-i", bg, "-filter_complex", flt, "-map", "[v]", "-map", "0:a:0", "-t", f"{media.dur(bite):.2f}", *ENC, out, timeout=1800)
    return out


def clip_part(clip, wav, out, kind, root, log=print, start=0.0, flip=False):
    """Narration over one of the creator's own clips of that exact place (named in the note), upright, back camera."""
    from . import shots
    d = media.dur(wav) + 0.35
    rot = "" if flip else shots.orient(clip, root, log)
    vf = (rot + "," if rot else "") + shots.vf_frame(kind, None, 0.0, flip) + f",fade=t=in:st=0:d=0.3,fade=t=out:st={d - 0.3:.2f}:d=0.3"
    loop = ["-stream_loop", "-1"] if media.dur(clip) < start + d else []
    media.ff(*loop, "-ss", f"{start:.2f}", "-i", clip, "-i", wav, "-t", f"{d:.2f}", "-map", "0:v:0", "-map", "1:a:0", "-vf", vf,
             "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,apad", *ENC, "-shortest", out, timeout=900)
    return out


from .qc import qc_part, qc_final  # noqa: E402,F401  (the check lives in qc.py, shared with every video; _qc_frame left qc.py in v2+)


def card_part(text_bg, d, out, kind, cfg=None):
    """A title / end card: a picture (file, start, flip, rotation) blurred and darkened, silent (the music carries it)."""
    from . import shots
    f, st, fl, rot = text_bg
    vf = (rot + "," if rot else "") + shots.vf_frame(kind, None, 0.0, fl) + f",boxblur=18,eq=brightness=-0.18,fade=t=in:st=0:d=0.4,fade=t=out:st={d - 0.5:.2f}:d=0.5"
    media.ff("-ss", f"{st:.2f}", "-i", f, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{d:.2f}", "-map", "0:v:0", "-map", "1:a:0",
             "-vf", vf, *ENC, "-shortest", out, timeout=600)
    return out


def speak(cfg, items, log, tag):
    """The voice for narration and fact lines (off camera, rule 1): the owner's own voice with a US accent when the job asks
    for it (interview_my_voice), else the Kokoro narrator."""
    from . import tts
    if cfg.get("interview_my_voice"): tts.my_voice(cfg, items, log, tag)
    else: tts.kokoro_batch(cfg, items, log, tag)


def voice_stamp(cfg, kd, log=print):
    """Cached narration was read by another voice: drop it (the wavs and the pieces that carry them) so it is read again."""
    from . import tts
    want = tts.my_voice_id(cfg) if cfg.get("interview_my_voice") else f"kokoro:{cfg.get('kokoro_voice') or tts.DEFAULT_VOICE}"
    st = Path(kd) / "narr" / "voice.txt"
    had = st.read_text(encoding="utf-8").strip() if st.exists() else None
    if had is not None and had != want:
        for f in list((Path(kd) / "narr").glob("*.wav")) + list(Path(kd).glob("[0-9][0-9]_narr*.mp4")) + list(Path(kd).glob("[0-9][0-9]_insert*.mp4")):
            f.unlink(missing_ok=True)
        _log(log, f"  narration voice changed ({had} -> {want}): reading it again")
    st.parent.mkdir(parents=True, exist_ok=True); st.write_text(want, encoding="utf-8")


def cut_bridge(cfg, text, picture, out, kind, work, k, log=print):
    """Narration (Kokoro) over a muted picture: (file, start, flip) for B-roll or the next soundbite's footage."""
    if media.ok(out, 0.5): return out
    from . import tts
    wav = Path(work) / "narr" / f"n{k}.wav"; wav.parent.mkdir(exist_ok=True)
    speak(cfg, [(text, wav)], log, "interview narration")
    d = media.dur(wav) + 0.35
    from . import shots
    f, start, flip = picture[:3]; rot = picture[3] if len(picture) > 3 else ""; g = picture[4] if len(picture) > 4 else None
    vf = (rot + "," if rot else "") + shots.vf_frame(kind, g, 0.0, flip) + f",fade=t=in:st=0:d=0.3,fade=t=out:st={d - 0.3:.2f}:d=0.3"
    media.ff("-ss", f"{start:.2f}", "-i", f, "-i", wav, "-t", f"{d:.2f}", "-map", "0:v:0", "-map", "1:a:0",
             "-vf", vf, "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,apad", *ENC, "-shortest", out, timeout=900)
    return out


# ------------------------------------------------------------------ 5 captions + assemble
ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Arial,{fs},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,1,0,0,0,100,100,0,0,1,5,2,2,{ml},{ml},{mv},1
Style: Narr,Arial,{fs},&H0066E0FF,&H0066E0FF,&H00000000,&H96000000,1,1,0,0,100,100,0,0,1,5,2,2,{ml},{ml},{mv},1
Style: Credit,Arial,{cs},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,7,30,30,30,1
Style: Hook,Arial Black,{hs},&H00000000,&H00000000,&H0000D7FF,&H00000000,1,0,0,0,100,100,0,0,3,16,0,8,60,60,{hv},1
Style: Banner,Arial Black,{bs},&H00FFFFFF,&H00FFFFFF,&H002A2AD8,&H00000000,1,0,0,0,100,100,1,0,3,12,0,{ba},{bm},{bm},{bv},1
Style: Strap,Arial Black,{ps},&H00FFFFFF,&H00FFFFFF,&HC0141414,&H00000000,1,0,0,0,100,100,0,0,3,14,0,1,{pm},{pm},{pv},1
Style: Title,Arial Black,{ts},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,6,3,5,80,80,0,1
Style: Sub,Arial,{ss},&H0000D7FF,&H0000D7FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,4,2,5,80,80,0,1
Style: Card,Arial,{ks},&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,1,0,0,0,100,100,0,0,3,18,0,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _t(t):
    t = max(0.0, t); return f"{int(t // 3600)}:{int(t % 3600 // 60):02}:{t % 60:05.2f}"


def _caption_lines(words, max_words):
    """[(text, start, end)] chunks of up to max_words, breaking after sentence ends."""
    out, cur = [], []
    for w, a, b in words:
        cur.append((w, a, b))
        if len(cur) >= max_words or re.search(r"[.!?]$", w):
            out.append((" ".join(x[0] for x in cur), cur[0][1], cur[-1][2])); cur = []
    if cur: out.append((" ".join(x[0] for x in cur), cur[0][1], cur[-1][2]))
    return out


def _writable(p):
    try:
        with open(p, "r+b"): return True
    except OSError: return False


DRESS_VERSION = "1"


def piece_events(p, d, w, h, ps, maxw, prev_banner=None, next_banner=None):
    """The ASS events of one piece, timed from its own start: credit, title lines, hook, banner (fading only where its run of
    pieces starts / ends), name strap, card, chapter, photo credits and captions."""
    ev = []
    if p.get("credit"): ev.append(f"Dialogue: 1,{_t(0)},{_t(d)},Credit,,0,0,0,,{p['credit']}\n")
    for line, style, dy in p.get("titles") or []:   # title / end card text: centred lines
        ev.append(f"Dialogue: 2,{_t(0.3)},{_t(d - 0.3)},{style},,0,0,0,,{{\\fad(300,300)\\pos({w // 2},{h // 2 + dy})}}{line}\n")
    if p.get("hook"):   # owner, 2026-11-09: a strong hook - a bold box that pops in
        ev.append(f"Dialogue: 2,{_t(0)},{_t(d)},Hook,,0,0,0,,{{\\fad(0,250)\\fscx70\\fscy70\\t(0,160,\\fscx106\\fscy106)\\t(160,260,\\fscx100\\fscy100)}}{str(p['hook']).upper()}\n")
    if p.get("banner"):   # one banner across neighbouring pieces: it fades in where the run starts and out where it ends
        fin, fout = (0 if prev_banner == p["banner"] else 250), (0 if next_banner == p["banner"] else 250)
        ev.append(f"Dialogue: 3,{_t(0)},{_t(d)},Banner,,0,0,0,,{{\\fad({fin},{fout})}}{p['banner']}\n")
    if p.get("strap"):   # who is speaking: name, and what he is, on the lower third
        who, role = (list(p["strap"]) + ["", ""])[:2]
        ev.append(f"Dialogue: 3,{_t(0.4)},{_t(min(6.0, d - 0.2))},Strap,,0,0,0,,{{\\fad(300,300)}}{str(who).upper()}"
                  + (f"\\N{{\\fs{int(ps * 0.62)}\\c&H00D7FF&\\fnArial}}{role}" if role else "") + "\n")
    if p.get("card"): ev.append(f"Dialogue: 1,{_t(0.3)},{_t(d - 0.2)},Card,,0,0,0,,{{\\fad(200,200)}}{p['card']}\n")
    if p.get("chapter"): ev.append(f"Dialogue: 1,{_t(0.3)},{_t(min(3.5, d - 0.2))},Card,,0,0,0,,{{\\fad(250,250)}}{p['chapter']}\n")
    for a_, b_, c_ in p.get("credits") or []:
        ev.append(f"Dialogue: 1,{_t(a_)},{_t(min(b_, d))},Credit,,0,0,0,,{c_}\n")
    for txt, a, b in _caption_lines(p.get("words") or [], maxw):
        if a >= d: break
        ev.append(f"Dialogue: 0,{_t(a)},{_t(min(b + 0.15, d))},{p.get('style', 'Cap')},,0,0,0,,{{\\fad(80,60)}}{txt}\n")
    return ev


SHORT_MAX = 178.0   # YouTube files a vertical video over 3:00 as a regular video, not a Short (owner, 2026-11-12: Clinton's was 3:07)


def cap_short(parts, cap=SHORT_MAX, dur=None, log=print):
    """The plan's length check estimates narration, and the title / end cards come on top, so a Short can come out over 3:00.
    Here every piece's real length is known: drop pieces from the end (never the opening hook or the closing card) until the
    Short fits, and never end on a narration line that led into a dropped soundbite. -> the kept parts"""
    dur = dur or (lambda p: media.dur(p["file"]))
    ds = [dur(p) for p in parts]
    kind = lambda p: (p.get("m") or {}).get("type") or ""
    tail = 1 if parts and kind(parts[-1]) == "card" else 0
    keep = list(range(len(parts)))
    total = lambda: sum(ds[i] for i in keep)
    while total() > cap and len(keep) - tail > 2:
        gone = keep.pop(len(keep) - 1 - tail)
        while len(keep) - tail > 2 and kind(parts[keep[-1 - tail]]) in ("narr", "fact"): keep.pop(len(keep) - 1 - tail)
        _log(log, f"  short: over {cap:.0f}s, dropped from the end (piece {gone}); now {total():.1f}s")
    return [parts[i] for i in keep]


def assemble(parts, out, kind, work, cfg, log=print, title=""):
    """parts: [{"file", "words": [(w, start, end)] relative to the part, "style": "Cap"|"Narr"}]."""
    if kind == "short": parts = cap_short(parts, float(cfg.get("interview_short_max", SHORT_MAX)), log=log)
    w, h = (1080, 1920) if kind == "short" else (1920, 1080)
    fs, ml, mv, maxw = (70, 90, 430, 5) if kind == "short" else (54, 160, 60, 9)
    cs, ks = (30, 76) if kind == "short" else (24, 64)
    hs, hv = (92, 300) if kind == "short" else (80, 90)
    ts, ss = (96, 58) if kind == "short" else (96, 52)
    bs, ba, bm, bv = (50, 8, 60, 150) if kind == "short" else (34, 7, 40, 36)   # topic banner: top of a Short, top-left corner of the long cut
    ps, pm, pv = (52, 60, 700) if kind == "short" else (46, 70, 170)             # name strap: lower third, above the captions
    # owner, 2026-11-09: "do only the beat that needs to change". Each piece is dressed (captions, hook, banner, strap, cards,
    # the opening / closing fade) on its own and kept under work/dressed by a key of its file and its overlays; the cut is the
    # dressed pieces joined by stream copy. An edit re-encodes only the pieces whose picture or overlays changed.
    header = ASS.format(w=w, h=h, fs=fs, ml=ml, mv=mv, cs=cs, ks=ks, hs=hs, hv=hv, ts=ts, ss=ss, bs=bs, ba=ba, bm=bm, bv=bv, ps=ps, pm=pm, pv=pv)
    fi, fo = float(cfg.get("interview_fade_in", 0.5)), float(cfg.get("interview_fade_out", 1.0))
    dd = Path(work) / "dressed"; dd.mkdir(parents=True, exist_ok=True)
    import hashlib
    files, made, n = [], 0, len(parts)
    for i, p in enumerate(parts):
        d = media.dur(p["file"])
        ev = piece_events(p, d, w, h, ps, maxw, parts[i - 1].get("banner") if i else None, parts[i + 1].get("banner") if i + 1 < n else None)
        vf = []
        if i == 0 and fi > 0: vf.append(f"fade=t=in:st=0:d={fi}")   # the video fades in from black and out to black
        if i == n - 1 and fo > 0: vf.append(f"fade=t=out:st={max(0.0, d - fo):.3f}:d={fo}")
        st = Path(p["file"]).stat()
        key = hashlib.md5(json.dumps([DRESS_VERSION, str(p["file"]), st.st_size, int(st.st_mtime), header, ev, vf]).encode()).hexdigest()[:14]
        f = dd / f"{i:03}_{key}.mp4"
        if not media.ok(f, 0.3):
            ass = dd / f"{i:03}_{key}.ass"; ass.write_text(header + "".join(ev), encoding="utf-8")
            sub = ass.as_posix().replace(":", "\\:")
            media.ff("-i", p["file"], "-vf", ",".join([f"subtitles='{sub}'"] + vf), "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                     "-pix_fmt", "yuv420p", "-profile:v", "high", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", f, timeout=3600)
            ass.unlink(missing_ok=True); made += 1
        files.append(f)
    keep = {f.name for f in files}
    for old in dd.glob("*.mp4"):
        if old.name not in keep: old.unlink(missing_ok=True)
    lst = Path(work) / f"{kind}_concat.txt"
    lst.write_text("".join(f"file '{f.as_posix()}'\n" for f in files), encoding="utf-8")
    T = sum(media.dur(f) for f in files)
    afade = ",".join(([f"afade=t=in:st=0:d={fi}"] if fi > 0 else []) + ([f"afade=t=out:st={max(0.0, T - fo):.3f}:d={fo}"] if fo > 0 else [])) or "anull"
    media.ff("-f", "concat", "-safe", 0, "-i", lst, "-c:v", "copy", "-af", afade, "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out, timeout=1800)
    _log(log, f"  {kind}: {made} of {n} piece(s) re-dressed, {n - made} reused as they were")
    try:
        from . import music
        if not _writable(out): raise RuntimeError(f"{Path(out).name} is open in another program (a player?); music not added")
        music.mix(out, dict(cfg, music_bed_db=float(cfg.get("interview_music_db", -5)), music_hook_db=float(cfg.get("interview_music_db", -5)),
                                music_duck_threshold=float(cfg.get("interview_duck_threshold", 0.08)), music_duck_ratio=float(cfg.get("interview_duck_ratio", 4))),
                  log, 6.0, seed=title or kind, text=title)   # quieter than a narrated Short: the interview is the star
    except Exception as e:
        _log(log, f"  music skipped ({str(e)[:120]})")
    _log(log, f"Interview {kind}: {out} ({media.dur(out) / 60:.1f} min)")
    return out


# ------------------------------------------------------------------ the whole edit
HOOK = """Pick the cold open for a YouTube {what} made from this interview: the single most surprising, emotional or
curiosity-making line actually said (an exact quote, 6-20 words, from ONE segment), plus a hook line shown on screen over it.
About the footage: {context}
Transcript:
{segments}
Rules: the quote must be copied exactly from the segment; the hook text is under 45 characters, true to what is said, no
clickbait lies, respectful to the people in it; never private details. Reply with JSON only:
{{"seg": <segment number>, "quote": "exact words", "text": "on-screen hook"}}"""


def _norm(w): return re.sub(r"[^a-z0-9]", "", w.lower())


DRESS = """You package a YouTube {what} made from an interview so people stop scrolling and stay.
About the footage: {context}
Video title: {title}
Draft on-screen hook line(s) shown over the opening soundbite: {hooks}
The opening soundbite says: {opening}
Write:
- "hooks": for each draft, a stronger on-screen hook: 3-7 words, concrete (a name, a place, a number, a surprise), creating
  curiosity or tension the video answers. True to what is said and to the footage: no invented facts, no "you won't believe",
  no question the video doesn't answer, respectful to the people in it.
- "banner": the topic banner shown on top of the video, 2-5 words, like a series strap (e.g. "INSIDE A BERMUDA MOSQUE"),
  only facts from the notes above.
- "who": the guest's name as he is introduced (e.g. "Mr. Clinton"), "" if unknown; "role": what he is / where, 2-6 words
  from the notes (e.g. "Muhammad Mosque, Hamilton"), "" if unknown.
Reply with JSON only: {{"hooks": ["..."], "banner": "...", "who": "...", "role": "..."}}"""


def dressing(cfg, context, title, hooks, opening, kind, work, log=print):
    """-> {"hooks": {draft: stronger}, "banner", "who", "role"} (owner, 2026-11-09: "strong hook and banner"); cached."""
    out = Path(work) / f"dressing_{kind}.json"
    if out.exists():
        try:
            j = json.loads(out.read_text(encoding="utf-8"))
            if set(hooks) <= set(j.get("hooks") or {}): return j
        except Exception: pass
    from .llm import LLM
    hooks = [h for h in hooks if h]
    def check(j):
        if not str(j.get("banner") or "").strip(): raise ValueError("banner needed")
        if hooks and len(j.get("hooks") or []) != len(hooks): raise ValueError(f"{len(hooks)} hooks needed")   # (none drafted: ignore)
    try:
        with LLM(dict(cfg), log, lambda: False) as llm:
            j = llm.chat_json("You are a YouTube packaging editor. Reply with JSON only.",
                              DRESS.format(what="Short" if kind == "short" else "mini-documentary", context=str(context)[:3000], title=title,
                                           hooks=json.dumps(hooks) if hooks else "(none)", opening=str(opening)[:400]),
                              max_tokens=400, check=check)
    except Exception as e:
        _log(log, f"  dressing: none ({str(e)[:100]})"); return {"hooks": {}, "banner": "", "who": "", "role": ""}
    res = {"hooks": {h: str(n).strip()[:44] or h for h, n in zip(hooks, j.get("hooks") or [])}, "banner": str(j["banner"]).strip().upper()[:32],
           "who": str(j.get("who") or "").strip()[:30], "role": str(j.get("role") or "").strip()[:40]}
    out.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    _log(log, f"  {kind} dressing: banner \"{res['banner']}\"; strap \"{res['who']} / {res['role']}\"; hooks "
              + "; ".join(f"\"{a}\" -> \"{b}\"" for a, b in res["hooks"].items()))
    return res


def dress(parts, dr, kind):
    """Put the dressing on the pieces: stronger hook text; the topic banner on every story piece after the opener (not on
    cards); the name strap on the guest's first soundbite after the opener and at each chapter."""
    seen_open = strap_done = False
    for p in parts:
        t = (p.get("m") or {}).get("type")
        if p.get("hook"):
            p["hook"] = (dr.get("hooks") or {}).get(p["hook"], p["hook"]); seen_open = True; continue
        if t == "card":
            seen_open = True; continue
        if not seen_open: continue
        if dr.get("banner") and t in ("bite", "narr", "fact", "comment", None): p["banner"] = dr["banner"]
        if t == "bite" and dr.get("who") and (not strap_done or (kind != "short" and p.get("chapter"))):
            p["strap"] = [dr["who"], dr.get("role", "")]; strap_done = True
    return parts


def hook(cfg, segs, context, kind, work, log=print):
    """-> {"t0", "t1", "text", "quote"} for the cold open (cached in hook_<kind>.json), or None."""
    out = Path(work) / f"hook_{kind}.json"
    if out.exists(): return json.loads(out.read_text(encoding="utf-8"))
    import difflib
    from .llm import LLM
    lines = "\n".join(f"[{x['i']}] {x['text']}" for x in segs)
    def check(j):
        i = int(j.get("seg", -1))
        if not 0 <= i < len(segs): raise ValueError("seg out of range")
        if not str(j.get("text") or "").strip() or len(str(j.get("quote") or "").split()) < 4: raise ValueError("quote and text needed")
    try:
        with LLM(dict(cfg), log, lambda: False) as llm:
            j = llm.chat_json("You are a YouTube documentary editor. Reply with JSON only.",
                              HOOK.format(what="Short" if kind == "short" else "mini-documentary", context=context, segments=lines[:60000]),
                              max_tokens=300, check=check)
    except Exception as e:
        _log(log, f"  hook: none ({str(e)[:100]})"); return None
    q = [_norm(w) for w in str(j["quote"]).split() if _norm(w)]
    for lo, hi in ((int(j["seg"]) - 3, int(j["seg"]) + 4), (0, len(segs))):   # near the named segment, else anywhere
        words = [w for x in segs[max(0, lo):hi] for w in x["words"]]
        have = [_norm(w[0]) for w in words]
        m = difflib.SequenceMatcher(None, have, q, autojunk=False).find_longest_match(0, len(have), 0, len(q))
        if m.size >= max(3, len(q) // 2): break
    if m.size < max(3, len(q) // 2): _log(log, "  hook: the quote wasn't found in the transcript; no cold open"); return None
    a = max(0, m.a - (m.b if m.b else 0)); b = min(len(words) - 1, m.a + m.size - 1 + (len(q) - m.b - m.size))
    res = {"t0": max(0.0, words[a][1] - 0.15), "t1": words[b][2] + 0.35, "text": str(j["text"]).strip()[:48], "quote": j["quote"],
           "words": [(w[0], w[1], w[2]) for w in words[a:b + 1]]}
    out.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    _log(log, f"  hook ({kind}): \"{res['text']}\" over \"{res['quote'][:70]}\" ({res['t1'] - res['t0']:.1f}s)")
    return res


def build(cfg, src, work, context, log=print, kinds=("short", "documentary"), flip=None, name="interview", version="", notes=None, extras=None):
    src = Path(src); root = Path(work); root.mkdir(parents=True, exist_ok=True)
    segs = transcribe(src, root, log)
    main_segs = segs
    for x in segs: x.setdefault("src", 0)
    work = root / version if version else root; work.mkdir(parents=True, exist_ok=True)
    from . import place   # place colour from the footage's GPS (owner, 2026-11-06): checked facts + where to use them
    pc = place.colour(cfg, src, root, log)
    if pc:
        notes = list(notes or []) + [dict(n, kind="place") for n in pc["notes"] if n not in (notes or [])]
        context = (str(context or "") + "\n" + pc["context"]).strip()
    tn = topic_notes(cfg, segs, context, root, log)   # facts on what the guest mentions, tied to where it comes up
    if tn: notes = list(notes or []) + [n for n in tn if n not in (notes or [])]
    bf = root / "broll.json"   # probing every clip in the folder is slow on a busy PC: once per folder
    broll = json.loads(bf.read_text(encoding="utf-8")) if bf.exists() else broll_list(src)
    bf.write_text(json.dumps(broll, indent=1), encoding="utf-8")
    pl = plan(cfg, segs, context, broll, work, log, notes=notes)
    bdir = src.parent
    from . import shots
    shots.BACK_CAMERA["on"] = bool(cfg.get("interview_back_camera", False))   # back camera: big close faces are the guest, not the holder
    # owner, 2026-11-09: each clip says which camera filmed it - the front camera saves mirrored (and its big face is you),
    # the back camera doesn't (every face is the guest); the form's setting only for a file that doesn't say
    cam = shots.camera(src)
    if flip is None: flip = cam == "front"
    back = (cam == "back") if cam else shots.BACK_CAMERA["on"]
    _log(log, f"Interview: {Path(src).name} - {cam or 'unknown'} camera" + (", mirrored" if flip else "") + ("; every face on screen is the guest" if back else "; a big close face is the holder"))
    an = shots.analyze(src, root, flip, log)
    if cfg.get("interview_vision_check", True): an = shots.vision_check(an, root, cfg, log)
    for x in an.get("secs") or []: x["back"] = back
    t_main = filmed_at(src)
    # more clips of the same conversation (owner, 2026-10-24: "use all the clips that can be used"): each transcribed and
    # framed on its own, then joined after the main one in the order they were filmed; segment numbers run on
    sources = [(src, flip, an)]
    before, after_ = [], []
    for k, ex in enumerate(extras or [], start=1):
        ex = dict(ex) if isinstance(ex, dict) else {"src": str(ex)}
        ep = Path(ex["src"]); ecam = shots.camera(ep)
        efl = bool(ex["flip"]) if "flip" in ex else ecam == "front"
        if "before" not in ex:   # filmed order from the files' own clocks (the mosque start came before the long drive)
            te = filmed_at(ep); ex["before"] = bool(te and t_main and te < t_main)
        ew = root / "extra" / ep.stem.rstrip("."); ew.mkdir(parents=True, exist_ok=True)
        es = transcribe(ep, ew, log)
        ean = shots.analyze(ep, ew, efl, log)
        if cfg.get("interview_vision_check", True): ean = shots.vision_check(ean, ew, cfg, log)
        for x in ean.get("secs") or []: x["back"] = (ecam == "back") if ecam else shots.BACK_CAMERA["on"]
        sources.append((ep, efl, ean))
        (before if ex.get("before") else after_).append((filmed_at(ep), [dict(x, src=k) for x in es]))
        _log(log, f"  clip {k}: {ep.name} ({len(es)} segments, {ecam or 'unknown'} camera{', mirrored' if efl else ''}{', before the main clip' if ex.get('before') else ''})")
    if before or after_:   # filmed order: clips from before the main one first; numbers run on through all of them
        order = lambda g: [x for _t, grp in sorted(g, key=lambda q: q[0]) for x in grp]   # by clock (none: stays as given)
        segs = order(before) + segs + order(after_)
        segs = [dict(x, i=j) for j, x in enumerate(segs)]
    bfiles = [x for x in sorted(bdir.iterdir()) if x.stem in broll and x.suffix.lower() in VIDEO_EXT]
    nice = next((x for x in bfiles if "bay" in x.stem.lower() or "wave" in x.stem.lower()), bfiles[0] if bfiles else None)
    keep = bool(cfg.get("interview_keep_scene", True))   # owner, 2026-10-25: never swap his footage for another place
    cover = bool(cfg.get("interview_cutaways", False))   # owner, 2026-11-08: narration over media of what it's about; the guest in a corner
    place = [(Path(x["src"]), bool(x.get("flip", False))) for x in cfg.get("interview_place_clips") or [] if Path(x["src"]).exists()]
    card_bg = (src, 30.0, flip, "") if keep or not nice else (nice, 2.0, False, shots.orient(nice, root, log))
    sdur = {i: media.dur(p_) for i, (p_, _, _) in enumerate(sources)}
    tri_all = triage(cfg, segs, context, notes, work, log) if cfg.get("interview_doc_mode", "augment") == "augment" else None
    target = float(cfg.get("interview_target_min") or 0) * 60
    if tri_all and target and not cfg.get("interview_plan_only_no_dives"):   # the longer cut: researched segments up to the target length
        est = total_secs(augment_items(dict(tri_all, dives=[]), segs), segs)
        _log(log, f"  longer cut so far: ~{est / 60:.1f} min of {target / 60:.0f} min wanted")
        dv = deep_dives(cfg, segs, context, work, target - est, log)
        if dv:   # a fact on the same thing as a researched segment would only repeat it
            near = lambda f: any(abs(int(f.get("after", -99)) - d["seg"]) <= 3 for d in dv)
            tri_all = dict(tri_all, dives=dv, facts=[f for f in tri_all.get("facts") or [] if not near(f)])
            _log(log, f"  with researched segments: ~{total_secs(augment_items(tri_all, segs), segs) / 60:.1f} min")
    if tri_all: tri_all = keep_every_clip(tri_all, segs, log=log)
    if tri_all and cfg.get("interview_keep_srcs"):   # clips that are the visit itself (the kebab window): never dropped
        ks = set(int(x) for x in cfg["interview_keep_srcs"])
        tri_all = dict(tri_all, drop=[d_ for d_ in tri_all.get("drop") or [] if not any(segs[i].get("src", 0) in ks for i in range(int(d_[0]), int(d_[1]) + 1))])
    outs, trip = {}, None
    if cfg.get("interview_plan_only"):   # stop after the plans, for a look before hours of rendering
        sp_ = short_plan(cfg, segs, pl, context, notes, work, log, short_secs=int(cfg.get("interview_short_secs", 180)), tri=tri_all)
        rows = []
        for nm, its in (("documentary", augment_items(tri_all, segs) if tri_all else []), ("short", _items(sp_) if sp_ else [])):
            rows.append(f"===== {nm}")
            for it in its:
                if "bite" in it:
                    a_, b_ = int(it["bite"][0]), int(it["bite"][1])
                    rows.append(f"[clip {segs[a_].get('src', 0)} {segs[a_]['start']:.0f}-{segs[b_]['end']:.0f}s] " + " ".join(x["text"] for x in segs[a_:b_ + 1])[:300])
                else: rows.append("   >> " + json.dumps({k_: v_ for k_, v_ in it.items() if k_ in ("insert", "narr", "note", "card")}, ensure_ascii=False))
        (work / "plan_review.txt").write_text("\n".join(rows), encoding="utf-8")
        _log(log, f"plan only: {work / 'plan_review.txt'}"); return {"plan": str(work / "plan_review.txt")}
    for kind in kinds:
        sp = short_plan(cfg, segs, pl, context, notes, work, log, short_secs=int(cfg.get("interview_short_secs", 180)),
                        tri=tri_all) if kind == "short" and cfg.get("interview_short_pass", True) else None
        tri = tri_all if kind == "documentary" else None
        if tri: items = augment_items(tri, segs)
        else: items = (_items(sp) if sp else _items(pl.get("short") or {})) if kind == "short" else [x for c in pl["documentary"]["chapters"] for x in c.get("items", [])]
        vtitle = spell_as_noted((tri or {}).get("title") or pl.get("title", name), context)
        guest = spell_as_noted(str((tri_all or {}).get("guest") or "").strip()[:40], context)
        sub = str(cfg.get("interview_subtitle") or ("A conversation with " + guest if guest else "A conversation"))
        import hashlib   # cuts are cached per plan: a changed plan never reuses another plan's numbered parts
        kd = work / f"{kind}_{hashlib.md5((CUT_VERSION + json.dumps(items, sort_keys=True)).encode()).hexdigest()[:6]}"; kd.mkdir(exist_ok=True)
        parts, rnd = [], random.Random(7)
        from . import tts
        voice_stamp(cfg, kd, log)   # the narration voice changed (your voice on / off): re-voice it
        batch = [(it["narr"], kd / "narr" / f"n{k}.wav") for k, it in enumerate(items) if it.get("narr")] + \
                [(it["insert"], kd / "narr" / f"i{k}.wav") for k, it in enumerate(items) if it.get("insert")]
        if batch:   # all of this video's narration in one Kokoro run (each separate run costs ~2 min of start-up)
            (kd / "narr").mkdir(exist_ok=True); speak(cfg, batch, log, f"interview narration ({kind})")
        used = [(segs[int(x["bite"][0])]["start"], segs[int(x["bite"][1])]["end"]) for x in items if "bite" in x and segs[int(x["bite"][0])].get("src", 0) == 0]
        cuts = Cutaways(src, an, used, bfiles, root, flip, log)
        oc = own_clips(src, root, log, cfg=cfg)
        if trip is None:
            near = gps_of(src)
            trip = TripFootage(trip_catalog(cfg, src, near, f"{name} {context or ''}", log), [src] + [p_ for p_, _, _ in sources[1:]] + [f for f, _, _ in oc], root, log, near=near,
                               radius_km=float(cfg.get("interview_trip_radius_km", 2.0)), cfg=cfg) if cfg.get("interview_trip_footage", True) else False
        ladder = Ladder(cfg, src, flip, kd, kind, log, own=oc, scenic=card_bg, trip=trip or None)
        ladder.gps, ladder.context = gps_of(src), str(context or "")[:600]
        ladder.here = {w.lower()[:5] for w in re.findall(r"\b[A-Z][a-z]{3,}", str(context or ""))} - {"there", "their", "about", "after", "when", "this", "these", "that", "with", "from", "what", "where", "which"}   # the place names in your note
        has_preview = bool(items and items[0].get("preview"))
        hk = hook(cfg, main_segs, context, kind, work, log) if cfg.get("interview_hook", True) and not (kind == "short" and sp) and not has_preview else None
        if kind == "short" and sp and items and "bite" in items[0]:   # the Short's own opener is its cold open: hook text from its thread
            items[0] = dict(items[0], hook_text=str(sp.get("hook") or "")[:48])
        if hk:   # cold open: the most striking line, with the hook text on screen, before the story starts
            f = cut_bite_framed(src, hk["t0"], hk["t1"], kd / "hook.mp4", kind, flip, an, cuts, log, ladder=ladder, keep=keep)
            parts.append({"file": f, "words": [(w, s - hk["t0"], e - hk["t0"]) for w, s, e in hk["words"]], "style": "Cap", "hook": hk["text"],
                          "m": {"type": "bite", "src": str(src), "flip": flip, "si": 0, "t0": hk["t0"], "t1": hk["t1"], "text": hk.get("quote", "")}})
        if (hk or not (kind == "short" and sp)) and not has_preview:   # title card: what this is, before the story (the Short's comes after its opener)
            tt = kd / "title.mp4"
            if not media.ok(tt, 0.5): card_part(card_bg, 3.2, tt, kind)
            parts.append({"file": tt, "words": [], "titles": [(vtitle, "Title", -50), (sub, "Sub", 90)], "m": {"type": "card"}})
        for k, it in enumerate(items):
            if "bite" in it:
                a, b = int(it["bite"][0]), int(it["bite"][1])
                t0, t1 = _cut_points(segs, a, b) if tri else (max(0.0, segs[a]["start"] - 0.15), segs[b]["end"] + 0.3)
                said = " ".join(x["text"] for x in segs[a:b + 1])
                credits = []
                cj = kd / f"{k:02}_bite.json"
                if media.ok(kd / f"{k:02}_bite.mp4", 0.5) and cj.exists(): credits = json.loads(cj.read_text(encoding="utf-8"))
                bsrc, bflip, ban = sources[segs[a].get("src", 0)]
                ladder.src, ladder.flip = bsrc, bflip   # blur fallback from the clip being cut
                f = cut_bite_framed(bsrc, t0, t1, kd / f"{k:02}_bite.mp4", kind, bflip, ban, cuts, log, ladder=ladder,
                                    visual=str(it.get("visual") or ""), text=said, credits=credits,
                                    words=[w for x in segs[max(0, a - 1):b + 2] for w in x["words"]],
                                    soft_in=not (k > 0 and "bite" in items[k - 1] and int(items[k - 1]["bite"][1]) == a - 1),
                                    soft_out=not (k + 1 < len(items) and "bite" in items[k + 1] and int(items[k + 1]["bite"][0]) == b + 1), keep=keep,
                                    place=place if segs[a].get("src", 0) in set(int(x) for x in cfg.get("interview_place_srcs") or []) else None)
                if cover and it.get("pip") and it.get("visual") and t1 - t0 >= PIP_LEAD + 5:   # she describes somewhere else: show it
                    pf = kd / f"{k:02}_pip.mp4"
                    if media.ok(pf, 0.5) and pf.stat().st_mtime < Path(f).stat().st_mtime:   # the soundbite under it was cut again
                        pf.unlink(); (kd / f"{k:02}_pipbg.mp4").unlink(missing_ok=True)
                    if not media.ok(pf, 0.5):
                        got = ladder.media(str(it["visual"]), said, t1 - t0 - PIP_LEAD, kd / f"{k:02}_pipbg.mp4")
                        if got:
                            pip_part(f, got[0], pf, kind)
                            _log(log, f"    picture-in-picture over {got[1]}" + (f" ({got[2]})" if got[2] else "") + f": {it['visual']}")
                            if got[2]: credits.append((PIP_LEAD, t1 - t0, got[1], got[2]))
                    if media.ok(pf, 0.5): f = pf
                cj.write_text(json.dumps(credits), encoding="utf-8")
                if cfg.get("interview_qc", False): qc_part(f, cfg, log, label=f"{kind} {k:02}", kind=kind)
                words = [(spell_as_noted(wd, context), s - t0, e - t0) for x in segs[a:b + 1] for wd, s, e in x["words"]]
                parts.append({"file": f, "words": words, "style": "Cap", "hook": it.get("hook_text", ""), "chapter": it.get("chapter", ""),
                              "credits": [(x[0], x[1], x[3]) for x in credits if x[3]],
                              "m": {"type": "bite", "src": str(bsrc), "flip": bflip, "si": segs[a].get("src", 0), "t0": t0, "t1": t1, "text": said}})
                if it.get("hook_text") or it.get("title_after"):   # the title card goes right after the opener / the preview
                    tt = kd / "title.mp4"
                    if not media.ok(tt, 0.5): card_part(card_bg, 3.2, tt, kind)
                    parts.append({"file": tt, "words": [], "titles": [(vtitle, "Title", -50), (sub, "Sub", 90)], "m": {"type": "card"}})
                _log(log, f"  {kind} {k:02}: soundbite {t0:.0f}-{t1:.0f}s \"{segs[a]['text'][:60]}\"")
            elif it.get("narr") and keep:   # a joke / bridge line over the real scene nearby, muted
                from . import captions
                wav = kd / "narr" / f"n{k}.wav"
                if not wav.exists(): speak(cfg, [(it["narr"], wav)], log, "interview narration")
                si, st = scene_window(segs, items, k, media.dur(wav) + 0.35, sdur)
                f = kd / f"{k:02}_narr.mp4"
                if cover and not it.get("joke") and not media.ok(f, 0.5):   # background lines: over what they are about (a joke stays on the scene)
                    got = cover_part(ladder, str(it.get("visual") or ""), it["narr"], wav, f, log)
                    if got: _log(log, f"    narration over {got[1]}" + (f" ({got[2]})" if got[2] else ""))
                if not media.ok(f, 0.5): scene_part(sources[si][0], sources[si][1], sources[si][2], st, wav, f, kind, log)
                if cover and not it.get("joke"): audit_part(ladder, f, str(it.get("visual") or ""), it["narr"], wav, log)
                try: words = captions.word_times(wav, it["narr"])
                except Exception: words = []
                parts.append({"file": f, "words": words, "style": "Narr", "m": {"type": "narr", "text": it["narr"], "src": str(sources[si][0]),
                                                                                  "flip": sources[si][1], "si": si, "t0": st}})
                _log(log, f"  {kind} {k:02}: narration \"{it['narr'][:60]}\" over clip {si} at {st:.0f}s")
            elif it.get("narr"):
                br = str(it.get("broll") or "")
                if br in broll:
                    bf = next((x for x in bdir.iterdir() if x.stem == br and x.suffix.lower() in VIDEO_EXT), None)
                    pic = (bf, rnd.uniform(0, max(0.0, broll[br] - 8)), False, shots.orient(bf, root, log))   # back camera: not mirrored
                else:   # the guest elsewhere in the video (framed on him), muted
                    f_, st_, fl_, g_, rot_ = cuts.take(8)
                    pic = (f_, st_, fl_, rot_, g_)
                f = cut_bridge(cfg, it["narr"], pic, kd / f"{k:02}_narr.mp4", kind, kd, k, log)
                from . import captions
                try: words = captions.word_times(kd / "narr" / f"n{k}.wav", it["narr"])
                except Exception: words = []
                parts.append({"file": f, "words": words, "style": "Narr"})
                _log(log, f"  {kind} {k:02}: narration \"{it['narr'][:60]}\"" + (f" over {br}" if br in broll else ""))
            elif it.get("insert"):   # a context tidbit: narration over a Commons photo (credited), else over your footage
                from . import tts, captions
                wav = kd / "narr" / f"i{k}.wav"; wav.parent.mkdir(exist_ok=True)
                speak(cfg, [(it["insert"], wav)], log, "interview context")
                f = kd / f"{k:02}_insert.mp4"; credit = ""
                try: had = json.loads((kd / f"{k:02}_insert.json").read_text(encoding="utf-8")).get("credit", "")
                except Exception: had = None
                if media.ok(f, 0.5) and had == "" and not keep: f.unlink(missing_ok=True)   # made without a photo last time: try Commons again
                note_ = (notes or [])[int(it["note"])] if notes and str(it.get("note", "")).isdigit() and int(it["note"]) < len(notes) else {}
                if keep and not media.ok(f, 0.5):   # owner, 2026-10-25: his own footage of that exact place, else the real scene here
                    clips = [Path(c) for c in (note_.get("clips") or []) if Path(c).exists()]
                    if clips:
                        c = clips[k % len(clips)]
                        if c.suffix.lower() in (".jpg", ".jpeg", ".png", ".heic", ".webp"): still_part(c, wav, f, kind)
                        else: clip_part(c, wav, f, kind, root, log, start=float(note_.get("clip_start", 0.0)), flip=bool(note_.get("clip_flip", False)))
                        credit = ""; _log(log, f"    fact over your clip {c.name}")
                    else:
                        got = cover_part(ladder, str(it.get("visual") or ""), it["insert"], wav, f, log) if cover else None
                        if got:
                            credit = got[2]; _log(log, f"    fact over {got[1]}" + (f" ({credit})" if credit else ""))
                        else:
                            si, st = scene_window(segs, items, k, media.dur(wav) + 0.35, sdur)
                            scene_part(sources[si][0], sources[si][1], sources[si][2], st, wav, f, kind, log)
                            _log(log, f"    fact over the scene: clip {si} at {st:.0f}s")
                    (kd / f"{k:02}_insert.json").write_text(json.dumps({"credit": credit}), encoding="utf-8")
                if not media.ok(f, 0.5) and cfg.get("interview_fact_ladder", False):   # owner, 2026-10-27: outside media through the QC repair ladder
                    try:
                        from . import qc_repair
                        d = media.dur(wav) + 0.35
                        clip, rung = qc_repair.ladder_clip(cfg, it["insert"], str(it.get("visual") or ""), d, kind, kd / "ladder" / f"{k:02}", root,
                                                           src=sources[0][0], log=log)
                        if clip:
                            media.ff("-i", clip, "-i", wav, "-t", f"{d:.2f}", "-map", "0:v:0", "-map", "1:a:0",
                                     "-vf", f"fade=t=in:st=0:d=0.3,fade=t=out:st={d - 0.3:.2f}:d=0.3", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,apad",
                                     *ENC, "-shortest", f, timeout=900)
                            credit = ""; _log(log, f"    fact picture from the repair ladder: {rung}")
                            (kd / f"{k:02}_insert.json").write_text(json.dumps({"credit": "", "rung": rung}), encoding="utf-8")
                    except Exception as e:
                        if str(e) == "cancelled": raise
                        _log(log, f"    fact ladder skipped ({type(e).__name__}: {str(e)[:150]}); the usual photo / stock path")
                if not media.ok(f, 0.5):
                    img, credit = commons_photo(str(it.get("visual") or it["insert"])[:80], kd / f"{k:02}_photo.jpg", log, cfg=cfg, line=it["insert"])
                    st_ = None if img else ladder._stock(str(it.get("visual") or "")[:80], it["insert"]) if it.get("visual") else None
                    if img: still_part(img, wav, f, kind)
                    elif st_:   # stock video through the clip checker, under the fact
                        sf, credit = st_; d = media.dur(wav) + 0.35
                        media.ff(*(["-stream_loop", "-1"] if media.dur(sf) < d else []), "-i", sf, "-i", wav, "-t", f"{d:.2f}", "-map", "0:v:0", "-map", "1:a:0",
                                 "-vf", shots.vf_frame(kind, None, 0.0, False, 1080 if kind == "short" else 1920, 1920 if kind == "short" else 1080)
                                 + f",fade=t=in:st=0:d=0.3,fade=t=out:st={d - 0.3:.2f}:d=0.3",
                                 "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,apad", *ENC, "-shortest", f, timeout=900)
                    elif ladder.trip_clip(str(it.get("visual") or ""), it["insert"], media.dur(wav) + 0.35, kd / f"{k:02}_insert_pic.mp4", ("place", "subject", "scenery")):
                        d = media.dur(wav) + 0.35   # your own trip footage under the fact
                        media.ff("-i", kd / f"{k:02}_insert_pic.mp4", "-i", wav, "-t", f"{d:.2f}", "-map", "0:v:0", "-map", "1:a:0",
                                 "-vf", f"fade=t=in:st=0:d=0.3,fade=t=out:st={d - 0.3:.2f}:d=0.3", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,apad", *ENC, "-shortest", f, timeout=900)
                    else:   # no photo / clip: the scenic background (the bay), heavily blurred - never a person's face
                        bf_, bst_, bfl_, brot_ = card_bg
                        d = media.dur(wav) + 0.35
                        media.ff(*(["-stream_loop", "-1"] if media.dur(bf_) < bst_ + d else []), "-ss", f"{bst_ + (k * 3.7) % 20:.2f}", "-i", bf_, "-i", wav,
                                 "-t", f"{d:.2f}", "-map", "0:v:0", "-map", "1:a:0",
                                 "-vf", (brot_ + "," if brot_ else "") + shots.vf_frame(kind, None, 0.0, bfl_) + ",boxblur=20:2,eq=brightness=-0.1",
                                 "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,apad", *ENC, "-shortest", f, timeout=900)
                    (kd / f"{k:02}_insert.json").write_text(json.dumps({"credit": credit}), encoding="utf-8")
                else:
                    try: credit = json.loads((kd / f"{k:02}_insert.json").read_text(encoding="utf-8")).get("credit", "")
                    except Exception: credit = ""
                if cover and keep:   # (Nov 10) a fact's picture is audited like the rest
                    re_ = audit_part(ladder, f, str(it.get("visual") or ""), it["insert"], wav, log)
                    if re_:
                        credit = re_[2]; (kd / f"{k:02}_insert.json").write_text(json.dumps({"credit": credit}), encoding="utf-8")
                try: words = captions.word_times(wav, it["insert"])
                except Exception: words = []
                note = (notes or [{}] * 99)[int(it.get("note", 0))] if notes and str(it.get("note", "")).isdigit() and int(it["note"]) < len(notes) else {}
                if cfg.get("interview_qc", False): qc_part(f, cfg, log, label=f"{kind} {k:02} fact", kind=kind)
                parts.append({"file": f, "words": words, "style": "Narr", "credit": credit, "card": str(it.get("card") or "")[:48],
                              "source": note.get("source", "") or it.get("source", ""), "m": {"type": "fact", "text": it["insert"], "note": it.get("note")}})
                _log(log, f"  {kind} {k:02}: context \"{it['insert'][:60]}\"" + (f" [{credit}]" if credit else ""))
        ec = kd / "end.mp4"   # end card: thanks + subscribe, music carries it out
        if not media.ok(ec, 0.5): card_part(card_bg, 6.0 if kind == "documentary" else 4.0, ec, kind)
        thanks = str(cfg.get("interview_thanks") or (f"Thank you, {guest}" if guest else "Thanks for watching"))   # the guest's name, never one old video's
        parts.append({"file": ec, "words": [], "titles": [t for t in [(thanks, "Title", -90), (str(cfg.get("interview_thanks_sub") or ""), "Sub", 20),
                                                                       ("Subscribe for more journeys", "Sub", 120)] if t[0]], "m": {"type": "card"}})
        if cfg.get("interview_dressing", True):   # owner, 2026-11-09: strong hook and banner
            hooks = [p["hook"] for p in parts if p.get("hook")]
            opening = next(((p.get("m") or {}).get("text", "") for p in parts if p.get("hook")), "")
            dress(parts, dressing(cfg, context, vtitle, hooks, opening, kind, work, log), kind)
        man = {"kind": kind, "out": str(work / f"{name}_{kind}.mp4"), "title": vtitle, "gps": gps_of(src), "parts": parts,
               "sources": [[str(p_), fl_, str(root if i_ == 0 else root / "extra" / Path(p_).stem.rstrip("."))] for i_, (p_, fl_, _a) in enumerate(sources)],
               "cfg": {k_: cfg[k_] for k_ in ("interview_music_db", "interview_duck_threshold", "interview_duck_ratio") if k_ in cfg}}
        (work / f"{name}_{kind}_parts.json").write_text(json.dumps(man, indent=1, ensure_ascii=False, default=str), encoding="utf-8")   # for the Video Studio editor
        if cfg.get("interview_manifest_only") and Path(man["out"]).exists():
            outs[kind] = Path(man["out"]); _log(log, f"  manifest written: {name}_{kind}_parts.json"); continue
        outs[kind] = assemble(parts, work / f"{name}_{kind}.mp4", kind, kd, cfg, log, vtitle)
        if cfg.get("interview_qc", False):
            try: qc_final(outs[kind], cfg, kd, log, every=30.0 if kind == "documentary" else 10.0)
            except Exception as e: _log(log, f"  final QC failed to run ({str(e)[:100]})")
    srcs = sorted({n.get("source", "") for n in (notes or []) if n.get("source")} | {d["source"] for d in (tri_all or {}).get("dives") or [] if d.get("source")})
    (work / "sources.txt").write_text("Sources for the context notes:\n" + "\n".join(srcs), encoding="utf-8")
    (work / "result.json").write_text(json.dumps({k: str(v) for k, v in outs.items()} | {"title": pl.get("title", ""), "sources": srcs}, indent=1), encoding="utf-8")
    return outs
