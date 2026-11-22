// Flow batch probe (owner, 2026-11-21): ask for the images in ONE request from the same prompt window, not one ask per image.
// One flow_run_shot_list call carries every prompt (shared settings, `variants` images each); approved once, then Flow
// renders them. Logs timings and any refusal that looks like a rate limit, then downloads every image (standard = free).
//
//   node scripts/flow_batch.mjs [prompts=5] [variants=4]      (default 5 x 4 = 20 images from one ask)
//
// Close any Chrome window on the Flow profile first. FLOW_MCP_MAX_CREDITS stays 0: free stills only.
import { appendFileSync, mkdirSync } from "node:fs";
import { Client } from "file:///F:/code/video-blender/workspace/tools/mcp-google-flow/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js";
import { StdioClientTransport } from "file:///F:/code/video-blender/workspace/tools/mcp-google-flow/node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js";

const n = Math.min(20, Number(process.argv[2] || 5)), variants = Math.min(4, Number(process.argv[3] || 4));
const outDir = "F:\\videos\\flow_mcp"; mkdirSync(outDir + "\\output", { recursive: true }); mkdirSync(outDir + "\\input", { recursive: true });
const logFile = `${outDir}\\batch_${new Date().toISOString().replace(/[:.]/g, "-")}.jsonl`;
const LIMIT = /rate|limit|unusual activity|try again later|too many|quota|temporarily/i;
const log = (o) => { const l = { t: new Date().toISOString(), ...o }; console.log(JSON.stringify(l)); appendFileSync(logFile, JSON.stringify(l) + "\n"); };
const text = (r) => (r?.content || []).map((c) => c.text || "").join(" ");
const PROMPTS = ["A quiet harbour at dawn, fishing boats, soft mist", "An old brick mill by a canal in autumn", "A desert road at sunset with long shadows",
  "A snowy pine forest under a clear night sky", "A busy market street with fruit stalls, morning light", "A lighthouse on a rocky coast in a storm",
  "A rice terrace in the mountains at sunrise", "A rainy city crossing at night with neon reflections", "A wooden cabin by a still lake",
  "A vineyard on rolling hills in late summer"].map((p) => p + ", photorealistic, cinematic, 16:9");

const env = { ...process.env, FLOW_MCP_HOME: "F:\\code\\video-blender\\workspace\\flow-mcp-session", FLOW_MCP_OUTPUT_DIR: outDir + "\\output",
  FLOW_MCP_INPUT_DIRS: outDir + "\\input", FLOW_MCP_MAX_CREDITS: "0", FLOW_MCP_LANGUAGE: "en" };
const client = new Client({ name: "flow-batch-probe", version: "1.0" });
await client.connect(new StdioClientTransport({ command: "C:\\Program Files\\nodejs\\node.exe",
  args: ["F:\\code\\video-blender\\workspace\\tools\\mcp-google-flow\\dist\\main.js"], env }));
const call = (name, args = {}) => client.callTool({ name, arguments: args }, undefined, { timeout: 40 * 60 * 1000 });

const shots = Array.from({ length: n }, (_, i) => ({ prompt: PROMPTS[i % PROMPTS.length] }));
log({ ev: "start", prompts: n, variants, images: n * variants, log: logFile });
let r = await call("flow_new_project"); log({ ev: "project", text: text(r).slice(0, 200), error: !!r.isError });
if (r.isError) { await client.close(); process.exit(1); }
const t0 = Date.now();
const q = await call("flow_run_shot_list", { shots, mode: "image", aspect: "16:9", variants, confirm: false });
const qt = text(q); let qj = {}; try { qj = JSON.parse(qt); } catch {}
const quote = qj?.quote?.quoteId || (qt.match(/"?quote_?[iI]d"?\s*[:=]\s*"?([\w-]+)/) || [])[1];
const credits = Number(qj?.quote?.credits ?? 0);
log({ ev: "quote", secs: Math.round((Date.now() - t0) / 1000), quote, credits, error: !!q.isError, text: q.isError ? qt.slice(0, 500) : "" });
if (q.isError || !quote || credits > 0) { log({ ev: "stop", why: q.isError ? "quote failed" : credits > 0 ? `would cost ${credits} credits` : "no quote" }); await client.close(); process.exit(1); }
const g = await call("flow_run_shot_list", { shots, mode: "image", aspect: "16:9", variants, confirm: true, quote_id: quote });
const gt = text(g);
log({ ev: "submitted", secs: Math.round((Date.now() - t0) / 1000), error: !!g.isError, limit_like: LIMIT.test(gt), text: gt.slice(0, 600) });
const w = await call("flow_wait", { expected_total: n * variants, timeout_minutes: 30 });
const wt = text(w); let ready = 0; try { ready = JSON.parse(wt).filter((x) => x.ready).length; } catch {}
log({ ev: "rendered", secs: Math.round((Date.now() - t0) / 1000), ready, of: n * variants, error: !!w.isError, limit_like: LIMIT.test(wt), text: w.isError ? wt.slice(0, 400) : "" });
let saved = 0;
for (let i = 0; i < ready; i++) {
  const d = await call("flow_download", { index: i, quality: "standard", file_name: `batch_${String(i).padStart(3, "0")}` });
  if (!d.isError) saved++; else log({ ev: "download_failed", i, text: text(d).slice(0, 200) });
}
log({ ev: "end", secs: Math.round((Date.now() - t0) / 1000), ready, saved });
await client.close();
