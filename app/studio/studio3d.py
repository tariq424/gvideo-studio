"""3D studio rung (owner, 2026-11-13: "more elaborate creation like the one you did for the car / the Volvo" ahead of the simple
drawings): when a line is about ONE physical thing (a car, an aircraft, a phone, an engine, a chair...), a real 3D model of it
turns in a photo studio, Cycles on the GPU, with an exploded view when the line is about how it is built.

Order in every ladder: after every picture rung (photos, stock, AI images and videos, held stills), BEFORE the drawn graphics
(physics, motion templates) and the card. A line about a person, a place, an event or an idea is not taken.

Model sources, first match wins:
  1. the owner's library  <studio3d_models_dir>/<name>/model.json  {"names": [...], "file": "src/scene.gltf", "credit": "...",
     "paint": [r, g, b], "paint_material": name}  - e.g. the owner's own grey Volvo XC60
  2. Poly Haven (CC0, no key): models whose name / tags match, then the model picks one that truly IS the thing (or none)
  3. Sketchfab (only with a sketchfab_api_token in the vault): downloadable CC0 / CC-BY / CC-BY-SA models, same pick
Downloads are cached under <studio3d_models_dir>/_cache. A CC-BY model's credit is burned into the clip's corner.
Never raises (cancel aside): returns None and a reason so the ladder moves on."""
import glob, json, os, re, shutil, subprocess, time, zipfile
from pathlib import Path
import requests

UA = {"User-Agent": "VideoStudio/1.0 (3D studio rung)"}
PH = "https://api.polyhaven.com"
SF = "https://api.sketchfab.com/v3"
OK_LICENSES = {"cc0", "by", "by-sa"}   # no NonCommercial (the channel is monetised), no NoDerivatives (parts are repainted / split)

PICK = """A narrated video needs a picture for this line. Would a 3D model of ONE physical object, turning in a photo studio, show
what the line is about? Yes for a vehicle, aircraft, ship, machine, engine, device, tool, instrument, furniture, a product.
No for people, animals, places, buildings in their setting, events, weather, money amounts, ideas or feelings.

LINE: "{vo}"

Reply JSON only:
{{"thing": "<the object as specifically as the line names it, e.g. 'Volvo XC60', 'Boeing 737', 'espresso machine'> or null",
 "noun": "<its plain generic noun, e.g. 'car', 'aircraft', 'coffee machine'>",
 "words": ["<3-6 search words, most specific first>"],
 "explode": <true only when the line is about its parts, how it is built or what is inside>}}"""

CHOOSE = """The line is about: {thing} ({noun}).
LINE: "{vo}"
Which of these 3D models is a fair picture of THAT thing (the same kind of object - a toy, a part of it, a different kind of
object or a model that only shares a word is NOT fair)? Reply JSON only: {{"pick": <number> or null, "why": "<short>"}}
{rows}"""


def _llm(cfg, log):
    from .llm import LLM
    c = dict(cfg)
    if c.get("script_provider") == "claude" and not c.get("anthropic_api_key"):
        c["script_provider"] = "deepseek" if c.get("deepseek_api_key") else "local"
    return LLM(c, log, lambda: False)


def models_dir(cfg): return Path(cfg.get("studio3d_models_dir") or r"F:\videos\models")


def blender_exe(cfg):
    p = cfg.get("blender_exe") or shutil.which("blender")
    if p and Path(p).is_file(): return str(p)
    roots = [os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Blender Foundation"),
             os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Blender Foundation")]
    hits = sorted((h for r in roots for h in glob.glob(os.path.join(r, "*", "blender.exe"))), reverse=True)   # newest version first
    return hits[0] if hits else None


# ------------------------------------------------------------------ 1. which thing
def pick(cfg, vo, log):
    """-> {"thing", "noun", "words", "explode"} or None (the line is not about one object)."""
    def chk(j):
        if j.get("thing") and not isinstance(j.get("words"), list): raise ValueError("words must be a list")
    try:
        with _llm(cfg, log) as llm:
            j = llm.chat_json("You pick pictures for a documentary. Reply with JSON only.", PICK.format(vo=vo[:600].replace('"', "'")),
                              max_tokens=300, check=chk)
    except Exception as e:
        log(f"    studio3d: picker unavailable ({str(e)[:80]})"); return None
    if not j.get("thing"): return None
    words = [str(w).lower().strip() for w in (j.get("words") or []) if str(w).strip()]
    noun = str(j.get("noun") or "").lower().strip()
    return {"thing": str(j["thing"]).strip(), "noun": noun, "words": words or [noun], "explode": bool(j.get("explode"))}


def _choose(cfg, p, vo, cands, log):
    """The model picks which candidate (if any) really is the thing. cands: [{"name", "tags"}] -> index or None."""
    if not cands: return None
    rows = "\n".join(f"{i}. {c['name']} - tags: {', '.join(c.get('tags') or [])[:160]}" for i, c in enumerate(cands))
    try:
        with _llm(cfg, log) as llm:
            j = llm.chat_json("You check picture matches. Reply with JSON only.",
                              CHOOSE.format(thing=p["thing"], noun=p["noun"], vo=vo[:400].replace('"', "'"), rows=rows), max_tokens=200)
    except Exception as e:
        log(f"    studio3d: match check unavailable ({str(e)[:80]})"); return None
    k = j.get("pick")
    return k if isinstance(k, int) and 0 <= k < len(cands) else None


# ------------------------------------------------------------------ 2. find a model
def _toks(s): return set(re.findall(r"[a-z0-9]+", str(s).lower()))


def library(cfg, p, vo):
    """The owner's own models (<name>/model.json). A model.json name in the THING picks it; a name only in the line counts
    when the thing is the same kind of object (model.json "nouns") - Nov 14 test video: a 737 line that also mentioned
    "my Volvo" got the Volvo. -> {"file", "credit", "paint", "src"} or None"""
    hit = lambda n, t: re.search(r"(?<![a-z0-9])" + re.escape(n.lower()) + r"(?![a-z0-9])", t.lower())
    for mj in sorted(models_dir(cfg).glob("*/model.json")):
        try: m = json.loads(mj.read_text(encoding="utf-8"))
        except Exception: continue
        names = m.get("names", [])
        same_kind = (p.get("noun") or "").lower() in [x.lower() for x in m.get("nouns", [])]
        if any(hit(n, p["thing"]) for n in names) or (same_kind and any(hit(n, vo) for n in names)):
            f = mj.parent / m["file"]
            if f.is_file():
                return {"file": f, "credit": m.get("credit", ""), "paint": m.get("paint"), "paint_material": m.get("paint_material"),
                        "yaw": m.get("yaw", 0), "src": f"library ({mj.parent.name})"}
    return None


def _score(p, name, tags, cats):
    nt, tg, ct = _toks(name), set(str(t).lower() for t in tags), set(str(c).lower() for c in cats)
    sc = 0
    for i, w in enumerate(p["words"] + [p["noun"]]):
        wt = max(1, 4 - i)
        if not w: continue
        if _toks(w) and _toks(w) <= nt: sc += 3 * wt
        if w in tg: sc += 2 * wt
        if w in ct: sc += wt
    return sc


def polyhaven(cfg, p, vo, log):
    try:
        allm = requests.get(f"{PH}/assets", params={"t": "models"}, headers=UA, timeout=30).json()
    except Exception as e:
        log(f"    studio3d: Poly Haven unreachable ({str(e)[:60]})"); return None
    ranked = sorted(((_score(p, a.get("name", k), a.get("tags", []), a.get("categories", [])), k) for k, a in allm.items()), reverse=True)
    ranked = [(s, k) for s, k in ranked if s >= 4][:6]
    if not ranked: return None
    cands = [{"id": k, "name": allm[k].get("name", k), "tags": allm[k].get("tags", [])} for _, k in ranked]
    i = _choose(cfg, p, vo, cands, log)
    if i is None: return None
    aid = cands[i]["id"]
    d = models_dir(cfg) / "_cache" / "polyhaven" / aid
    gl = d / f"{aid}.gltf"
    if not gl.is_file():
        try:
            files = requests.get(f"{PH}/files/{aid}", headers=UA, timeout=30).json()["gltf"]
            res = next(r for r in ("1k", "2k", "4k") if r in files)
            g = files[res]["gltf"]
            d.mkdir(parents=True, exist_ok=True)
            for rel, info in list(g.get("include", {}).items()) + [(gl.name, g)]:
                tgt = (d / rel).resolve()
                if d.resolve() not in tgt.parents: continue   # never write outside the cache folder
                tgt.parent.mkdir(parents=True, exist_ok=True)
                r = requests.get(info["url"], headers=UA, timeout=120); r.raise_for_status(); tgt.write_bytes(r.content)
        except Exception as e:
            log(f"    studio3d: Poly Haven download failed ({str(e)[:80]})"); shutil.rmtree(d, ignore_errors=True); return None
    return {"file": gl, "credit": "", "paint": None, "yaw": 180, "src": f"Poly Haven CC0 ({cands[i]['name']})"}


def _sf_token(cfg):
    if cfg.get("sketchfab_api_token") and cfg["sketchfab_api_token"] != "vault": return cfg["sketchfab_api_token"]
    try:
        from .config import _vault_get
        return _vault_get("sketchfab_api_token")
    except Exception: return ""


def sketchfab(cfg, p, vo, log):
    tok = _sf_token(cfg)
    if not tok: return None
    H = dict(UA, Authorization=f"Token {tok}")
    try:
        q = " ".join([p["thing"]] if p["thing"] else p["words"][:2])
        res = requests.get(f"{SF}/search", params={"type": "models", "q": q, "downloadable": "true", "count": 24,
                                                   "sort_by": "-likeCount"}, headers=H, timeout=30).json().get("results", [])
    except Exception as e:
        log(f"    studio3d: Sketchfab search failed ({str(e)[:60]})"); return None
    res = [r for r in res if ((r.get("license") or {}).get("slug") or "") in OK_LICENSES and (r.get("faceCount") or 0) < 3_000_000][:6]
    cands = [{"id": r["uid"], "name": r.get("name", ""), "tags": [t.get("name", "") for t in r.get("tags", [])],
              "user": (r.get("user") or {}).get("displayName", ""), "lic": r["license"]["slug"]} for r in res]
    i = _choose(cfg, p, vo, cands, log)
    if i is None: return None
    c = cands[i]
    d = models_dir(cfg) / "_cache" / "sketchfab" / c["id"]
    gl = next(iter(sorted(d.rglob("*.gltf"))), None) if d.is_dir() else None
    if not gl:
        try:
            url = requests.get(f"{SF}/models/{c['id']}/download", headers=H, timeout=30).json()["gltf"]["url"]
            z = d.with_suffix(".zip"); d.mkdir(parents=True, exist_ok=True)
            r = requests.get(url, headers=UA, timeout=600); r.raise_for_status(); z.write_bytes(r.content)
            with zipfile.ZipFile(z) as zf:
                for n in zf.namelist():
                    t = (d / n).resolve()
                    if d.resolve() in t.parents and not n.endswith("/"):
                        t.parent.mkdir(parents=True, exist_ok=True); t.write_bytes(zf.read(n))
            z.unlink(missing_ok=True)
            gl = next(iter(sorted(d.rglob("*.gltf"))), None)
        except Exception as e:
            log(f"    studio3d: Sketchfab download failed ({str(e)[:80]})"); shutil.rmtree(d, ignore_errors=True); return None
    if not gl: return None
    credit = "" if c["lic"] == "cc0" else f'3D model "{c["name"]}" by {c["user"]} (CC {c["lic"].upper()}, Sketchfab)'
    return {"file": gl, "credit": credit, "paint": None, "yaw": 180, "src": f"Sketchfab {c['lic']} ({c['name']})"}


HY3D_PY = r"F:\code\hunyuan3d\venv\Scripts\python.exe"


def _slug(t): return re.sub(r"[^a-z0-9]+", "_", str(t).lower()).strip("_")[:50] or "thing"


def _run(cmd, limit, log, label):
    """A helper process (Blender / Hunyuan3D), hidden, no console window. -> (returncode, last lines)"""
    t0 = time.time(); tail = []
    pr = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                          errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for line in pr.stdout:
        tail = (tail + [line.rstrip()])[-12:]
        if line.startswith(("SHAPE", "PAINTED", "BUILT")): log(f"    studio3d: {line.strip()}")
        if time.time() - t0 > limit:
            pr.kill(); log(f"    studio3d: {label} passed {int(limit)} s; stopped"); return -9, tail
    return pr.wait(), tail


def _gpu(label):
    try:
        from . import gpu_lock; return gpu_lock.holder(label)
    except Exception:
        import contextlib; return contextlib.nullcontext()


def from_views(cfg, views, work, log, name="model", front="-y", through=()):
    """Photos of one object -> a painted .blend. views: {"front"|"left"|"back"|"right"|"top"|"bottom": image} (front required;
    2+ side views use the multi-view model). Shape: Hunyuan3D-2 / 2mv from front / left / back / right; paint: every view
    projected (blender_paint.py). through: views that reuse the opposite side's photo (mirrored for the shape). -> Path or None"""
    py = cfg.get("hy3d_python") or HY3D_PY; exe = blender_exe(cfg)
    if not Path(py).is_file() or not exe: log("    studio3d: Hunyuan3D-2 or Blender not installed"); return None
    work = Path(work); work.mkdir(parents=True, exist_ok=True)
    blend = work / f"{name}.blend"
    if blend.is_file(): return blend
    from PIL import Image, ImageOps
    tights = {k: work / f"{k}_tight.png" for k in views}
    todo = {k: [str(views[k]), str(t)] for k, t in tights.items() if not t.is_file()}
    if todo:   # cut-outs: rembg lives in the Hunyuan3D environment
        jf = work / "cut_job.json"; jf.write_text(json.dumps({"cut": todo}), encoding="utf-8")
        rc, tail = _run([py, Path(__file__).with_name("hy3d_worker.py"), jf], 900, log, "cut-out")
        if rc != 0 or not all(t.is_file() for t in tights.values()):
            log("    studio3d: cut-out failed: " + " | ".join(tail[-3:])[:240]); return None
    sq = {}
    for k in ("front", "left", "back", "right"):   # square, padded views for the shape model
        if k not in tights: continue
        im = Image.open(tights[k]); s_ = int(max(im.size) * 1.15)
        c = Image.new("RGBA", (s_, s_), (0, 0, 0, 0)); c.alpha_composite(im, ((s_ - im.width) // 2, (s_ - im.height) // 2))
        if k in through: c = ImageOps.mirror(c)
        c.resize((512, 512), Image.LANCZOS).save(work / f"{k}_sq.png"); sq[k] = str(work / f"{k}_sq.png")
    glb = work / f"{name}_raw.glb"
    if not glb.is_file():
        jf = work / "shape_job.json"; jf.write_text(json.dumps({"views": sq, "out": str(glb), "seed": 7}), encoding="utf-8")
        log(f"    studio3d: Hunyuan3D shape from {len(sq)} view(s)")
        with _gpu("studio3d (Hunyuan3D)"):
            rc, tail = _run([py, Path(__file__).with_name("hy3d_worker.py"), jf], float(cfg.get("hy3d_timeout", 1200)), log, "Hunyuan3D")
        if rc != 0 or not glb.is_file():
            log("    studio3d: Hunyuan3D failed: " + " | ".join(tail[-3:])[:240]); return None
    pv = [{"img": str(t), "view": k, "through": k in through} for k, t in tights.items()]
    jf = work / "paint_job.json"
    jf.write_text(json.dumps({"mesh": str(glb), "out": str(blend), "front": front, "up": "+z", "views": pv, "name": name}), encoding="utf-8")
    rc, tail = _run([exe, "-b", "--factory-startup", "--python", Path(__file__).with_name("blender_paint.py"), "--", jf], 300, log, "paint")
    if rc != 0 or not blend.is_file():
        log("    studio3d: painting failed: " + " | ".join(tail[-3:])[:240]); return None
    return blend


def photo3d(cfg, p, vo, log, images=None, job=None):
    """Source 4: a photo of the thing -> Hunyuan3D-2 shape, painted with that photo. The picture the ladder already made for
    the line (images), else a clean product shot from the cloud image makers. Only the photographed side is real, so the
    clip's orbit stays near that side. -> model dict or None"""
    if not cfg.get("hy3d_on", True): return None
    d = models_dir(cfg) / "_cache" / "photo3d" / _slug(p["thing"])
    if (d / "model.blend").is_file(): img, gen = None, False
    else:
        img = next((Path(i) for i in (images or []) if Path(i).is_file()), None); gen = img is None
        if gen:
            from . import cloudgen
            d.mkdir(parents=True, exist_ok=True); img = d / "photo.png"
            if not img.is_file():
                r = cloudgen.generate(cfg, f"Product photo of a {p['thing']}: the whole {p['noun'] or 'object'} in frame, seen "
                                           "straight from the front, plain white studio background, soft even light, no people, no text",
                                      img, "1:1", log, f"studio3d {p['thing']}", job=job)
                if not r.get("ok") or not img.is_file():
                    log(f"    studio3d: no photo to model from ({str(r.get('why'))[:60]})"); return None
    b = from_views(cfg, {"front": img} if img else {}, d, log)
    if not b: return None
    return {"file": b, "credit": "", "paint": None, "yaw": 180, "orbit": 60, "generated": True,
            "src": f"photo -> Hunyuan3D-2 ({'AI product shot' if gen else 'the line picture'})"}


CODE = """Write a Blender Python script (Blender 4.x / 5.x) that BUILDS a recognisable 3D model of: {thing} ({noun}).
It will be shown turning in a photo studio{explode}. Rules:
- only `import bpy, bmesh, math, mathutils, random`; no file, network or OS access; do not save, render, add lights or cameras
- real-world size in metres; the object stands on z = 0; its FRONT faces -Y; up is +Z
- build it from separate, named parts (primitives + bmesh + modifiers: bevel, subdivision, solidify, mirror, array), 10 to 60
  objects, each named for the part it is ("left_door", "wheel_front_left", "engine_block"...)
- every part gets a material (Principled BSDF) with believable colour, metallic and roughness; glass for windows
- Blender 4+ Principled BSDF input names: "Base Color", "Metallic", "Roughness", "Alpha", "Transmission Weight",
  "Coat Weight", "Emission Color", "Emission Strength" (NOT the old "Transmission", "Specular", "Clearcoat", "Emission")
- prefer bpy.ops.mesh.primitive_cube_add / primitive_cylinder_add / primitive_cone_add / primitive_uv_sphere_add /
  primitive_torus_add with location=, rotation=, scale= (then bpy.context.active_object); avoid bmesh. If you must use bmesh:
  bmesh.ops.create_cone (there is NO create_cylinder) and call bm.verts.ensure_lookup_table() before indexing
- apply nothing; keep every object a plain mesh; no drivers, no handlers, no keyframes
- proportions matter more than small detail; smooth shading on curved parts (bpy.ops.object.shade_smooth())
Reply with ONE ```python code block and nothing else."""
BLENDER_VER = "5.x"
CODE_BAD = re.compile(r"\b(open|exec|eval|compile|__import__|getattr|setattr|globals|locals|subprocess|socket|requests|urllib|"
                      r"shutil|ctypes|pathlib)\b|\bos\.|\bsys\.|save_as_mainfile|save_mainfile|render\.render|"
                      r"^\s*import\s+(?!(bpy|bmesh|math|mathutils|random)\b)|^\s*from\s+(?!(bpy|bmesh|math|mathutils|random)\b)", re.M)


def looks_like(cfg, image, p):
    """Is the render recognisably the thing? -> (bool, why). Uses the app's picture checker (footage._vision: the local
    qwen2.5-VL model by default, or vet_provider)."""
    from . import footage
    from .llm import parse_json
    vp = cfg.get("vet_provider")
    c = dict(cfg, vet_provider=vp if vp not in (None, "", "off") else "local")
    ask = (f'This is a 3D render. Is the object in it recognisably a {p["thing"]} ({p["noun"]})? Judge the shape only, not '
           'realism. Reply JSON only: {"match": true or false, "why": "<a few words>"}')
    j = parse_json(footage._vision(c, str(image), ask)) or {}
    return bool(j.get("match") is True or str(j.get("match")).lower() == "true"), str(j.get("why") or "")


def coded(cfg, p, vo, log):
    """Source 5: the model writes a Blender script that builds the thing from named parts (simple, geometric things: a
    phone, a gear, a house, a rough aircraft) - real parts, so the exploded view works. The script is screened (Blender
    building blocks only), built, rendered once and must be recognised by the vision check before it is used."""
    # Nov 14 test video: DeepSeek-written airliners (fuselage standing on end) passed the local picture check and reached a
    # Short. "auto" = only with Claude writing the model (an Anthropic key); True / False force it.
    on = cfg.get("coded_model_on", "auto")
    if on is False or (on == "auto" and not cfg.get("anthropic_api_key")): return None
    exe = blender_exe(cfg)
    if not exe: return None
    d = models_dir(cfg) / "_cache" / "coded" / _slug(p["thing"]); d.mkdir(parents=True, exist_ok=True)
    blend = d / "model.blend"
    if (d / "rejected.txt").is_file(): return None
    if not blend.is_file():
        from .graphics import _gen_llm
        ask = CODE.format(thing=p["thing"], noun=p["noun"], explode=" and taken apart into its parts" if p["explode"] else "")
        wrap = d / "run.py"
        wrap.write_text("import bpy, runpy\n"
                        "bpy.ops.wm.read_factory_settings(use_empty=True)\n"
                        f"runpy.run_path(r'{d / 'build.py'}', run_name='__main__')\n"
                        "for o in list(bpy.context.scene.objects):\n"
                        "    if o.type in ('LIGHT', 'CAMERA'): bpy.data.objects.remove(o, do_unlink=True)\n"
                        "obs = [o for o in bpy.context.scene.objects if o.type == 'MESH']\n"
                        "assert obs, 'no meshes'\n"
                        f"bpy.ops.wm.save_as_mainfile(filepath=r'{blend}')\n"
                        "print('BUILT', len(obs), flush=True)\n", encoding="utf-8")
        critique = ""
        for rnd in range(int(cfg.get("coded_model_rounds", 2))):   # design rounds: the checker's reason goes back to the writer
            err, built = "", False
            for attempt in range(3):   # a script that fails to build goes back to the writer, with Blender's error
                extra = (f"\n\nYour previous model was shown to a viewer, who said it is NOT recognisably a {p['thing']}: {critique}\n"
                         "Rebuild it with true real-world proportions." if critique else "")
                extra += (f"\n\nYour previous script failed in Blender {BLENDER_VER}:\n{err}\nFix it and send the whole script again."
                          if err else "")
                try:
                    with _gen_llm(cfg, log) as llm:
                        t = llm.chat("You write Blender Python. Reply with one code block.", ask + extra, max_tokens=8000)
                except Exception as e:
                    log(f"    studio3d: model writer unavailable ({str(e)[:80]})"); return None
                m = re.search(r"```(?:python)?\s*\n(.*?)```", t, re.S)
                code = (m.group(1) if m else t).strip()
                bad = CODE_BAD.search(code)
                if bad:
                    log(f"    studio3d: written model refused (uses {bad.group(0).strip()!r})")
                    (d / "rejected.txt").write_text("unsafe: " + bad.group(0)); return None
                (d / "build.py").write_text(code, encoding="utf-8")
                rc, tail = _run([exe, "-b", "--factory-startup", "--python", wrap], 180, log, "model build")
                if rc == 0 and blend.is_file(): built = True; break
                err = "\n".join(x for x in tail if x.strip() and not x.startswith(("Blender ", "Blender quit")))[-1200:]
                log(f"    studio3d: written model did not build (try {attempt + 1}): " + (err.splitlines()[-1][:160] if err else "?"))
                blend.unlink(missing_ok=True)
            if not built:
                (d / "rejected.txt").write_text("build failed: " + err[-400:]); return None
            ok, still = False, d / "check.png"
            r = render(dict(cfg, studio3d_samples=16), blend, d / "check.mp4", 3, 960, 540, False, log, yaw=180)
            if r:
                from . import media
                still.unlink(missing_ok=True)
                media.ff("-ss", "1.5", "-i", r, "-frames:v", 1, still, check=False, timeout=120)
                try:   # the same checker the other picture checks use (local qwen2.5-VL unless vet_provider says otherwise)
                    ok, critique = looks_like(cfg, still, p)
                    log(f"    studio3d: written model check (round {rnd + 1}): {'YES' if ok else 'NO'} - {critique[:90]}")
                except Exception as e:
                    log(f"    studio3d: no vision check ({str(e)[:60]}); written model not used"); break
            if ok: break
            blend.unlink(missing_ok=True)
        if not ok:
            blend.unlink(missing_ok=True); (d / "rejected.txt").write_text("not recognisable: " + critique[:300]); return None
    return {"file": blend, "credit": "", "paint": None, "yaw": 180, "generated": True, "src": "model written in code"}


def find(cfg, p, vo, log, images=None, job=None):
    """Library -> Poly Haven -> Sketchfab (token) -> photo -> Hunyuan3D-2 -> a model written in code. A line about the
    thing's parts tries the written model before the photo one (a generated shape is one fused shell: nothing to explode)."""
    real = [lambda: library(cfg, p, vo), lambda: polyhaven(cfg, p, vo, log), lambda: sketchfab(cfg, p, vo, log)]
    gen = [lambda: photo3d(cfg, p, vo, log, images, job), lambda: coded(cfg, p, vo, log)]
    for fn in real + (gen[::-1] if p.get("explode") else gen):
        try: m = fn()
        except Exception as e:
            if str(e) == "cancelled": raise
            log(f"    studio3d: source failed ({type(e).__name__}: {str(e)[:80]})"); m = None
        if m: return m
    return None


def tag(how):
    """On-screen tag: a generated model (from a photo or written in code) is GENERATED; a real model is a 3D RENDER."""
    return "GENERATED · 3D" if ("Hunyuan3D" in (how or "") or "written in code" in (how or "")) else "3D RENDER"


# ------------------------------------------------------------------ 3. render
def _credit_png(text, W, H, out):
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); dr = ImageDraw.Draw(im)
    fs = max(14, H // 60)
    try: font = ImageFont.truetype("arial.ttf", fs)
    except Exception: font = ImageFont.load_default()
    x0, y0, x1, y1 = dr.textbbox((0, 0), text, font=font)
    x, y = W - (x1 - x0) - fs, H - (y1 - y0) - fs
    dr.rectangle((x - fs // 2, y - fs // 3, x + (x1 - x0) + fs // 2, y + (y1 - y0) + fs // 2), fill=(0, 0, 0, 110))
    dr.text((x, y - y0), text, font=font, fill=(255, 255, 255, 220))
    im.save(out)


def render(cfg, model, out, secs, W, H, explode, log, paint=None, credit="", paint_material=None, yaw=0, orbit=None, az=0, shots=None, side=-1,
           exposure=0.0, sound=None):
    """Blender renders the studio clip to `out` (limited-range yuv420p, BT.709). -> Path or None"""
    from . import media
    exe = blender_exe(cfg)
    if not exe: log("    studio3d: Blender not found"); return None
    out = Path(out); wd = out.parent / (out.stem + "_frames"); wd.mkdir(parents=True, exist_ok=True)
    fps = int(cfg.get("studio3d_fps", 24))
    n = int(round(min(max(float(secs), 3.0), float(cfg.get("studio3d_max_secs", 10))) * fps))
    job = {"model": str(model), "frames_dir": str(wd), "w": W, "h": H, "fps": fps, "frames": n, "explode": bool(explode),
           "samples": int(cfg.get("studio3d_samples", 32)), "paint": paint, "paint_material": paint_material, "yaw": yaw, "orbit": orbit, "az": az,
           "shots": (cfg.get("studio3d_shots", True) and float(secs) >= 6) if shots is None else shots, "side": side, "exposure": exposure,
           "cuts_out": str(out.parent / (out.stem + "_cuts.json"))}
    jf = out.parent / (out.stem + "_job.json"); jf.write_text(json.dumps(job), encoding="utf-8")
    script = Path(__file__).with_name("blender_studio3d.py")
    cmd = [exe, "-b", "--factory-startup", "--python", str(script), "--", str(jf)]
    t0 = time.time(); tail = []
    try:
        from . import gpu_lock
        lock = gpu_lock.holder("studio3d (Blender)")
    except Exception:
        import contextlib; lock = contextlib.nullcontext()
    limit = float(cfg.get("studio3d_timeout", 1500))
    for attempt in range(2):   # a Blender crash part-way (seen live, 4/192 frames) gets one more go: finished frames are kept
        with lock if attempt == 0 else _gpu("studio3d (Blender, retry)"):
            pr = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for line in pr.stdout:
                tail = (tail + [line.rstrip()])[-12:]
                if line.startswith(("DEVICE", "DONE")): log(f"    studio3d: {line.strip()}")
                if time.time() - t0 > limit:
                    pr.kill(); log(f"    studio3d: render passed {int(limit)} s; stopped"); return None
            pr.wait()
        frames = sorted(wd.glob("*.png"))
        if pr.returncode == 0 and len(frames) >= n: break
        log(f"    studio3d: Blender failed ({len(frames)}/{n} frames, try {attempt + 1}): " +
            " | ".join(x for x in tail[-8:] if x.strip() and not x.startswith("FRAME"))[:400])
    else:
        return None
    log(f"    studio3d: {n} frames in {time.time() - t0:.0f} s")
    args = ["-framerate", fps, "-i", wd / "%04d.png"]
    vf = "scale=in_range=full:out_range=tv,format=yuv420p"
    if credit:
        cp = out.parent / (out.stem + "_credit.png"); _credit_png(credit, W, H, cp)
        args += ["-i", cp]; fc = ["-filter_complex", f"[0:v][1:v]overlay=0:0,{vf}[v]", "-map", "[v]"]
    else: fc = ["-vf", vf]
    media.ff(*args, *fc, "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-color_range", "tv", "-colorspace", "bt709",
             "-color_primaries", "bt709", "-color_trc", "bt709", "-movflags", "+faststart", out, timeout=900)
    if not media.ok(out): return None
    shutil.rmtree(wd, ignore_errors=True)
    if sound: add_sound(out, n / fps, sound, out.parent / (out.stem + "_cuts.json"), log)
    return out


def add_sound(mp4, secs, kind, cuts_json, log):
    """A sound bed under a stand-alone clip (the ladder's clips play under the narration with no sound of their own):
    "jet" = idling jet engines (rumble, hiss, turbine whine), "studio" = quiet room tone; a soft whoosh on every cut."""
    from . import media
    try: cj = json.loads(Path(cuts_json).read_text(encoding="utf-8"))
    except Exception: cj = {}
    cuts, fps = cj.get("cuts", [])[1:], cj.get("fps", 24)
    d = f"{secs:.2f}"
    if kind == "jet":
        src = [f"anoisesrc=color=brown:amplitude=0.9:d={d},lowpass=f=500,volume=0.9",
               f"anoisesrc=color=pink:amplitude=0.5:d={d},highpass=f=2200,lowpass=f=7000,volume=0.18",
               f"sine=f=3900:d={d},tremolo=f=6:d=0.3,volume=0.035", f"sine=f=118:d={d},volume=0.12"]
    else:
        src = [f"anoisesrc=color=brown:amplitude=0.4:d={d},lowpass=f=300,volume=0.4"]
    for c in cuts:
        t = max(0.0, c / fps - 0.35)
        src.append(f"anoisesrc=color=pink:amplitude=0.8:d=0.7,bandpass=f=900:width_type=o:w=2,afade=t=in:d=0.3,"
                   f"afade=t=out:st=0.3:d=0.4,volume=0.55,adelay={int(t * 1000)}:all=1")
    fc = ";".join(f"{s}[a{i}]" for i, s in enumerate(src)) + ";" + "".join(f"[a{i}]" for i in range(len(src))) + \
         f"amix=inputs={len(src)}:normalize=0:duration=longest,afade=t=in:d=0.6,afade=t=out:st={max(secs - 0.8, 0):.2f}:d=0.8," \
         "alimiter=limit=0.8[a]"
    tmp = Path(mp4).with_name(Path(mp4).stem + "_snd.mp4")
    try:
        media.ff("-i", mp4, "-filter_complex", fc, "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                 "-t", d, "-movflags", "+faststart", tmp, timeout=300)
        if media.ok(tmp): os.replace(tmp, mp4); log(f"    studio3d: sound added ({kind}, {len(cuts)} whooshes)")
    except Exception as e:
        log(f"    studio3d: no sound ({str(e)[:80]})")


def clip(cfg, work, vo, key, W, H, secs, log, images=None, job=None):
    """The whole rung for one line. -> (clip path, how) or (None, why)"""
    if not cfg.get("studio3d_on", True): return None, "3D studio off"
    if not vo: return None, "no narration"
    work = Path(work); work.mkdir(parents=True, exist_ok=True)
    out = work / f"{key}_studio3d.mp4"
    note = work / f"{key}_studio3d.json"
    from . import media
    if media.ok(out):
        try: return out, json.loads(note.read_text(encoding="utf-8")).get("how", "3D studio render")
        except Exception: return out, "3D studio render"
    if note.is_file():   # tried before for this line: same answer, no second model call
        try:
            j = json.loads(note.read_text(encoding="utf-8"))
            if not j.get("ok"): return None, j.get("why", "tried before")
        except Exception: pass
    def fail(why):
        try: note.write_text(json.dumps({"ok": False, "why": why}), encoding="utf-8")
        except OSError: pass
        return None, why
    p = pick(cfg, vo, log)
    if not p: return fail("the line is not about one physical object")
    m = find(cfg, p, vo, log, images, job)
    if not m: return fail(f"no 3D model of {p['thing']}")
    log(f"    studio3d: {p['thing']} -> {m['src']}{' (exploded view)' if p['explode'] else ''}")
    r = render(cfg, m["file"], out, secs, W, H, p["explode"], log, paint=m.get("paint"), credit=m.get("credit", ""),
               paint_material=m.get("paint_material"), yaw=m.get("yaw", 0), orbit=m.get("orbit"))
    if not r: return fail(f"render of {p['thing']} failed")
    how = f"3D studio: {p['thing']} ({m['src']}{', exploded' if p['explode'] else ''})"
    try: note.write_text(json.dumps({"ok": True, "how": how, "thing": p, "model": str(m["file"])}), encoding="utf-8")
    except OSError: pass
    return r, how
