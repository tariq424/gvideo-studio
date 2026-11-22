// Read-only: open Flow on the signed-in profile, list visible buttons / text boxes and take screenshots, to find the "New
// project" button and the Agent side panel's message box (owner, 2026-11-21: test the factory-style one-message batch).
import { chromium } from "file:///F:/code/video-blender/workspace/tools/mcp-google-flow/node_modules/playwright-core/index.mjs";
const PROFILE = "F:\\code\\video-blender\\workspace\\flow-mcp-session", OUT = "F:\\videos\\flow_mcp";
const ctx = await chromium.launchPersistentContext(PROFILE, { channel: "chrome", headless: false, viewport: { width: 1440, height: 900 }, args: ["--lang=en-US"] });
const page = ctx.pages()[0] || await ctx.newPage();
const url = process.argv[2] || "https://labs.google/fx/tools/flow";
await page.goto(url, { waitUntil: "domcontentloaded" }); await page.waitForTimeout(8000);
await page.screenshot({ path: `${OUT}\\inspect_1.png` });
const dump = async (tag) => {
  const items = await page.evaluate(() => [...document.querySelectorAll('button,[role=button],textarea,[contenteditable=true],input[type=text],a[href*="project"]')]
    .filter((e) => e.offsetParent).slice(0, 80).map((e) => ({ tag: e.tagName, role: e.getAttribute("role"), aria: e.getAttribute("aria-label"),
      ph: e.getAttribute("placeholder"), text: (e.innerText || "").trim().slice(0, 60), href: e.getAttribute("href") })));
  console.log(`== ${tag} (${page.url()})`); for (const i of items) console.log(JSON.stringify(i));
};
await dump("landing");
if (process.argv[3] === "new") {
  const btn = page.getByRole("button", { name: /new project/i }).first();
  if (await btn.count()) { await btn.click(); await page.waitForTimeout(8000); await page.screenshot({ path: `${OUT}\\inspect_2.png` }); await dump("new project"); }
  else console.log("no New project button");
}
await ctx.close();
