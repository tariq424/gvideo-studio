// Factory-style Flow batch through automation (owner, 2026-11-21: "same kind of batch request" as the Video Factory):
// ONE message with every scene, pasted into Flow's Agent side panel of a new project and sent once. Then it watches the
// project fill and logs any refusal that looks like a rate limit / "unusual activity".
//
//   node scripts/flow_agent_batch.mjs inspect            -> new project, list the Agent panel's controls (sends nothing)
//   node scripts/flow_agent_batch.mjs send [batch.txt] [max_scenes]
//
// Chrome is started plainly with a DevTools port and attached to, as the flow-studio MCP does (a Playwright-launched Chrome
// is treated as signed out). Close any Chrome on the Flow profile first.
import { spawn } from "node:child_process";
import { readFileSync, appendFileSync } from "node:fs";
import { chromium } from "file:///F:/code/video-blender/workspace/tools/mcp-google-flow/node_modules/playwright-core/index.mjs";

const PROFILE = "F:\\code\\video-blender\\workspace\\flow-mcp-session", OUT = "F:\\videos\\flow_mcp", PORT = 9333;
const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const mode = process.argv[2] || "inspect";
const logFile = `${OUT}\\agent_batch_${new Date().toISOString().replace(/[:.]/g, "-")}.jsonl`;
const LIMIT = /unusual activity|rate limit|try again later|too many requests|quota|temporarily unavailable|reached your limit|limit reached/i;
const log = (o) => { const l = { t: new Date().toISOString(), ...o }; console.log(JSON.stringify(l)); appendFileSync(logFile, JSON.stringify(l) + "\n"); };

spawn(CHROME, [`--user-data-dir=${PROFILE}`, `--remote-debugging-port=${PORT}`, "--lang=en-US", "--no-first-run", "--window-position=-32000,-32000", "--window-size=1440,1000", "https://flow.google.com/"]   // off-screen: never in the owner's way,
      { detached: true, stdio: "ignore" }).unref();
let browser;
for (let i = 0; i < 30 && !browser; i++) { await new Promise((r) => setTimeout(r, 1000)); try { browser = await chromium.connectOverCDP(`http://127.0.0.1:${PORT}`); } catch {} }
if (!browser) { log({ ev: "stop", why: "Chrome did not open a DevTools port (close any Chrome on the Flow profile)" }); process.exit(1); }
const ctx = browser.contexts()[0]; const page = ctx.pages().find((p) => p.url().includes("flow.google")) || ctx.pages()[0];
const quit = async () => { try { const cdp = await browser.newBrowserCDPSession(); await cdp.send("Browser.close"); } catch { try { await quit(); } catch {} } };
await page.goto("https://flow.google.com/", { waitUntil: "domcontentloaded" }); await page.waitForTimeout(6000);
log({ ev: "home", url: page.url() });
if (page.url().includes("/about")) {   // signed-in users get the app from "Create with Google Flow"
  const c = page.getByRole("button", { name: /create with google flow/i }).first();
  if (await c.count()) { await c.click(); await page.waitForTimeout(6000); log({ ev: "app", url: page.url() }); }
}
const np = page.getByRole("button", { name: /new project/i }).first();
if (!(await np.count())) { await page.screenshot({ path: `${OUT}\\agent_no_new_project.png` }); log({ ev: "stop", why: "no New project button (signed out?)", url: page.url() }); await quit(); process.exit(1); }
await np.click(); await page.waitForTimeout(8000);
log({ ev: "project", url: page.url() });
const agentBtn = page.getByRole("button", { name: /^flow agent$/i }).first();   // the composer's sparkle toggle ("Flow agent")
if ((await agentBtn.count()) && (await agentBtn.getAttribute("aria-pressed")) !== "true") { await agentBtn.click(); await page.waitForTimeout(3000); }
log({ ev: "agent", on: (await agentBtn.count()) ? await agentBtn.getAttribute("aria-pressed") : "no toggle" });
await page.screenshot({ path: `${OUT}\\agent_panel.png` });

const boxes = await page.evaluate(() => [...document.querySelectorAll('textarea,[contenteditable=true],[role=textbox]')].filter((e) => e.offsetParent)
  .map((e, i) => { const r = e.getBoundingClientRect(); return { i, tag: e.tagName, aria: e.getAttribute("aria-label"), ph: e.getAttribute("placeholder") || e.getAttribute("data-placeholder"),
    x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width) }; }));
log({ ev: "textboxes", boxes });
const btns = await page.evaluate(() => [...document.querySelectorAll('button,[role=button],[role=switch]')].filter((e) => e.offsetParent && e.getBoundingClientRect().y > 700)
  .map((e) => ({ aria: e.getAttribute("aria-label"), pressed: e.getAttribute("aria-pressed") ?? e.getAttribute("aria-checked"), role: e.getAttribute("role"),
    text: (e.innerText || "").trim().slice(0, 40), title: e.getAttribute("title"), x: Math.round(e.getBoundingClientRect().x) })));
log({ ev: "composer_buttons", btns });
if (mode === "inspect") { await quit(); process.exit(0); }

// send: the Agent panel's box is the right-hand one (the composer sits under the grid on the left)
const batchFile = process.argv[3] || "F:\\videos\\google-flow-videos\\agent_batch.txt", maxScenes = Number(process.argv[4] || 0);
let batch = readFileSync(batchFile, "utf-8");
if (maxScenes) {   // keep the instructions and the first N scenes
  const parts = batch.split(/\n(?=Scene \d+)/); batch = [parts[0], ...parts.slice(1, maxScenes + 1)].join("\n");
}
const scenes = (batch.match(/^Scene \d+/gm) || []).length;
const right = boxes.sort((a, b) => b.x - a.x)[0];
if (!right) { log({ ev: "stop", why: "no text box found" }); await quit(); process.exit(1); }
const box = page.locator('textarea,[contenteditable=true],[role=textbox]').filter({ visible: true }).nth(right.i);
await box.click(); await page.keyboard.insertText(batch); await page.waitForTimeout(1000);
const t0 = Date.now(); await page.keyboard.press("Enter"); await page.waitForTimeout(3000);
if ((await box.innerText().catch(() => "")).trim().length > 50) {   // Enter made a new line instead of sending: press the send arrow
  const go = page.getByRole("button", { name: /start generation|send/i }).last(); if (await go.count()) await go.click();
}
log({ ev: "sent", scenes, chars: batch.length, box: right });
let last = -1, still = 0;
for (let m = 0; m < 60; m++) {   // watch up to 30 min
  await page.waitForTimeout(30000);
  const body = await page.evaluate(() => document.body.innerText);
  const tiles = await page.locator('img[src*="googleusercontent"], video').count();
  const hit = (body.match(LIMIT) || [])[0];
  log({ ev: "watch", mins: +((Date.now() - t0) / 60000).toFixed(1), tiles, limit_text: hit || "" });
  if (hit) { await page.screenshot({ path: `${OUT}\\agent_limit.png` }); log({ ev: "stop", why: `Flow said: ${hit}` }); break; }
  still = tiles === last ? still + 1 : 0; last = tiles;
  if (tiles >= scenes && still >= 2) break;
}
await page.screenshot({ path: `${OUT}\\agent_done.png` });
log({ ev: "end", url: page.url(), tiles: last, scenes });
await quit();
