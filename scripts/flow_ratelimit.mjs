// Flow rate-limit probe (owner, 2026-11-21: "the idea is to see if we hit rate limit").
// Drives the flow-studio MCP server (same config as .mcp.json) and generates FREE stills one after another, logging each
// submit: when it was sent, how long it took, and what Flow said. Stops at the first refusal that looks like a limit
// ("rate", "limit", "unusual activity", "try again later", "too many") or at the first still that would cost credits.
//
//   node scripts/flow_ratelimit.mjs [count=10] [seconds_between=60]
//
// Before running: close any Chrome window open on the Flow profile (F:\code\video-blender\workspace\flow-mcp-session),
// otherwise the server cannot open it ("Chrome did not open a DevTools port"). FLOW_MCP_MAX_CREDITS stays 0: stills only.
import { appendFileSync, mkdirSync } from "node:fs";
import { Client } from "file:///F:/code/video-blender/workspace/tools/mcp-google-flow/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js";
import { StdioClientTransport } from "file:///F:/code/video-blender/workspace/tools/mcp-google-flow/node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js";

const count = Number(process.argv[2] || 10), gap = Number(process.argv[3] || 60);
const outDir = "F:\\videos\\flow_mcp"; mkdirSync(outDir + "\\output", { recursive: true }); mkdirSync(outDir + "\\input", { recursive: true });
const logFile = `${outDir}\\ratelimit_${new Date().toISOString().replace(/[:.]/g, "-")}.jsonl`;
const LIMIT = /rate|limit|unusual activity|try again later|too many|quota|temporarily/i;
const log = (o) => { const line = { t: new Date().toISOString(), ...o }; console.log(JSON.stringify(line)); appendFileSync(logFile, JSON.stringify(line) + "\n"); };
const text = (r) => (r?.content || []).map((c) => c.text || "").join(" ");
const PROMPTS = ["A quiet harbour at dawn, fishing boats, soft mist, photorealistic", "An old brick mill by a canal in autumn, photorealistic",
  "A desert road at sunset with long shadows, photorealistic", "A snowy pine forest under a clear night sky, photorealistic",
  "A busy market street with fruit stalls, morning light, photorealistic"];

const env = { ...process.env, FLOW_MCP_HOME: "F:\\code\\video-blender\\workspace\\flow-mcp-session", FLOW_MCP_OUTPUT_DIR: outDir + "\\output",
  FLOW_MCP_INPUT_DIRS: outDir + "\\input", FLOW_MCP_MAX_CREDITS: "0", FLOW_MCP_LANGUAGE: "en" };
const client = new Client({ name: "flow-ratelimit-probe", version: "1.0" });
await client.connect(new StdioClientTransport({ command: "C:\\Program Files\\nodejs\\node.exe",
  args: ["F:\\code\\video-blender\\workspace\\tools\\mcp-google-flow\\dist\\main.js"], env }));
const call = async (name, args = {}) => { const r = await client.callTool({ name, arguments: args }, undefined, { timeout: 20 * 60 * 1000 }); return r; };

log({ ev: "start", count, gap_s: gap, log: logFile });
let r = await call("flow_session_info"); log({ ev: "session", text: text(r).slice(0, 400) });
r = await call("flow_new_project"); log({ ev: "project", text: text(r).slice(0, 300), error: !!r.isError });
if (r.isError) { log({ ev: "stop", why: "could not create a project" }); await client.close(); process.exit(1); }
let done = 0;
for (let i = 0; i < count; i++) {
  const prompt = PROMPTS[i % PROMPTS.length], t0 = Date.now();
  const q = await call("flow_generate", { prompt, mode: "image", aspect: "16:9", variants: 1, confirm: false });
  const qt = text(q); const quote = (qt.match(/"?quote_id"?\s*[:=]\s*"?([\w-]+)/) || [])[1]; const price = Number((qt.match(/"?(?:price|credits)"?\s*[:=]\s*(\d+)/i) || [])[1] || 0);
  if (q.isError || !quote) { log({ ev: "quote_failed", i, text: qt.slice(0, 500), limit_like: LIMIT.test(qt) }); break; }
  if (price > 0) { log({ ev: "stop", i, why: `this still would cost ${price} credits (cap is 0)` }); break; }
  const g = await call("flow_generate", { prompt, mode: "image", aspect: "16:9", variants: 1, confirm: true, quote_id: quote });
  const gt = text(g);
  if (g.isError) { log({ ev: "submit_failed", i, secs: (Date.now() - t0) / 1000, text: gt.slice(0, 500), limit_like: LIMIT.test(gt) }); break; }
  done++;
  const w = await call("flow_wait", { expected_total: done, timeout_minutes: 10 });
  const wt = text(w);
  log({ ev: "still", i, secs: Math.round((Date.now() - t0) / 1000), ok: !w.isError, wait: wt.slice(0, 200), limit_like: LIMIT.test(gt + " " + wt) });
  if (w.isError || LIMIT.test(gt + " " + wt)) { log({ ev: "stop", i, why: "Flow refused or looked rate-limited" }); break; }
  if (i < count - 1) await new Promise((res) => setTimeout(res, gap * 1000));
}
log({ ev: "end", stills_made: done });
await client.close();
