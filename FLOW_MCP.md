# Flow MCP for Claude (rate-limit probe)

The owner wants to see whether automating Google Flow hits a rate limit. This sets up the `flow-studio` MCP server
(mcp-google-flow, built in `F:\code\video-blender\workspace\tools\mcp-google-flow`) for Claude sessions opened in this folder.
It doesn't touch Video Studio's pipeline, and the ladder doesn't use it.

**Risk:** the owner's standing rule is that scripting Flow's composer can get the Google account flagged ("unusual activity").
This probe is the owner's deliberate test of that. Run it on purpose, never from a pipeline.

## Setup
- **`.mcp.json`** (project scope) starts the server with:
  - the signed-in profile `F:\code\video-blender\workspace\flow-mcp-session`;
  - output in `F:\videos\flow_mcp\output` and input in `F:\videos\flow_mcp\input`;
  - `FLOW_MCP_MAX_CREDITS=0`, so free stills only;
  - `FLOW_MCP_LANGUAGE=en`.
- **To use the tools from Claude:** open a new Claude Code session in `F:\code\video-studio-test` and approve the `flow-studio` server when asked.
- **Before any run:** close every Chrome window on that profile. A sign-in window left open blocks the server ("Chrome did not open a DevTools port").

## Rate-limit probe
`node scripts/flow_ratelimit.mjs [count=10] [seconds_between=60]`

It makes a new project, then generates free stills one at a time:
1. gets a quote;
2. stops if the still would cost credits;
3. submits;
4. waits for the still to finish.

Every step goes to `F:\videos\flow_mcp\ratelimit_<time>.jsonl` with timings. The run stops at the first refusal that looks like a limit ("rate", "limit", "unusual activity", "try again later", "too many", "quota").

## Known blocker (2026-11-21)
Flow moved to an "Agent" side-panel layout, and the server couldn't find its settings panel (Codex's last run).
- **What to expect:** if the probe logs `quote_failed` with an options or panel error, that's this selector problem, not a rate limit.
- **How to fix it:** run `flow_inspect_ui` (read-only), then put the new labels in a JSON file pointed to by `FLOW_MCP_UI_LABELS` (see the server's README, "When Flow's interface changes").
