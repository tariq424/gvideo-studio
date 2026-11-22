// Download every finished image of one Flow project (owner, 2026-11-21: "download the 10 flow images automatically ... use
// sparingly to avoid rate limits"). ONE off-screen Chrome session, one project open, one asset listing, then the downloads one
// at a time with a pause between them (standard quality = free). Nothing is generated.
//
//   node scripts/flow_download_project.mjs <project_url> [prefix] [gap_s=8]
import { spawn } from "node:child_process";
import { appendFileSync, mkdirSync } from "node:fs";
import { Client } from "file:///F:/code/video-blender/workspace/tools/mcp-google-flow/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js";
import { StdioClientTransport } from "file:///F:/code/video-blender/workspace/tools/mcp-google-flow/node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js";

const [url, prefix = "agent", gapArg = "8"] = process.argv.slice(2);
if (!url || !url.startsWith("https://flow.google.com/project/")) { console.error("usage: flow_download_project.mjs <flow project url> [prefix] [gap_s]"); process.exit(2); }
const gap = Math.max(3, Number(gapArg)) * 1000;
const PROFILE = "F:\\code\\video-blender\\workspace\\flow-mcp-session", OUT = "F:\\videos\\flow_mcp", PORT = 9333;
const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
mkdirSync(`${OUT}\\output`, { recursive: true });
const logFile = `${OUT}\\download_${new Date().toISOString().replace(/[:.]/g, "-")}.jsonl`;
const log = (o) => { const l = { t: new Date().toISOString(), ...o }; console.log(JSON.stringify(l)); appendFileSync(logFile, JSON.stringify(l) + "\n"); };
const text = (r) => (r?.content || []).map((c) => c.text || "").join(" ");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

spawn(CHROME, [`--user-data-dir=${PROFILE}`, `--remote-debugging-port=${PORT}`, "--lang=en-US", "--no-first-run",
  "--window-position=-32000,-32000", "--window-size=1440,1000", "about:blank"], { detached: true, stdio: "ignore" }).unref();   // off-screen
let up = false;
for (let i = 0; i < 30 && !up; i++) { await sleep(1000); try { up = (await fetch(`http://127.0.0.1:${PORT}/json/version`)).ok; } catch {} }
if (!up) { log({ ev: "stop", why: "Chrome did not open a DevTools port (close any Chrome on the Flow profile)" }); process.exit(1); }

const env = { ...process.env, FLOW_MCP_CDP_URL: `http://127.0.0.1:${PORT}`, FLOW_MCP_HOME: PROFILE, FLOW_MCP_OUTPUT_DIR: `${OUT}\\output`,
  FLOW_MCP_INPUT_DIRS: `${OUT}\\input`, FLOW_MCP_MAX_CREDITS: "0", FLOW_MCP_LANGUAGE: "en" };
const client = new Client({ name: "flow-download", version: "1.0" });
await client.connect(new StdioClientTransport({ command: "C:\\Program Files\\nodejs\\node.exe",
  args: ["F:\\code\\video-blender\\workspace\\tools\\mcp-google-flow\\dist\\main.js"], env }));
const call = (name, args = {}) => client.callTool({ name, arguments: args }, undefined, { timeout: 10 * 60 * 1000 });
const closeChrome = async () => { try { const v = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json();
  const ws = new WebSocket(v.webSocketDebuggerUrl); await new Promise((r) => ws.addEventListener("open", r));
  ws.send(JSON.stringify({ id: 1, method: "Browser.close" })); await sleep(1000); } catch {} };

let saved = 0, failed = 0;
try {
  const o = await call("flow_open_project", { url }); log({ ev: "open", error: !!o.isError, text: text(o).slice(0, 200) });
  if (o.isError) throw new Error("could not open the project");
  await sleep(4000);
  const a = await call("flow_list_assets", {}); let tiles = []; try { tiles = JSON.parse(text(a)); } catch {}
  const imgs = tiles.filter((t) => t.kind === "image" && t.ready);
  log({ ev: "assets", tiles: tiles.length, ready_images: imgs.length });
  for (const [n, t] of imgs.entries()) {
    if (n) await sleep(gap);   // sparing: one download at a time, a pause between
    const d = await call("flow_download", { index: t.index, quality: "standard", file_name: `${prefix}_${String(n + 1).padStart(2, "0")}` });
    const dt = text(d); if (d.isError) failed++; else saved++;
    log({ ev: "download", index: t.index, ok: !d.isError, text: dt.slice(0, 220) });
    if (/unusual activity|rate limit|too many|try again later/i.test(dt)) { log({ ev: "stop", why: "Flow pushed back; stopping" }); break; }
  }
} catch (e) { log({ ev: "error", why: String(e).slice(0, 200) }); }
log({ ev: "end", saved, failed });
await client.close().catch(() => {}); await closeChrome();
