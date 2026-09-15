---
name: x-grok-chat
description: "Use ego-browser to chat with Grok inside X at x.com/i/grok, retrieve complete answers and every cited X post/profile and web source URL, and preserve context across multi-turn follow-ups. Use for X Grok, Twitter Grok, 推特 Grok, or the common 'gork' typo; do not use for the local Grok Build CLI, ordinary X search, posting, or social interaction."
---

# X Grok 多轮聊天

Use ego-browser as the only browser execution layer. Before the first browser action in a turn, read the installed `ego-browser` skill completely from its path in the current environment’s skill catalog (prefer the project-local installation; if unavailable, ask the user to provide the dependency) and follow its task-space, handoff, and completion rules. Do not substitute Chrome control, Playwright, X private API replay, the local `grok` Build CLI, or direct cookie access.

Read [references/conversation-state.md](references/conversation-state.md) before opening or resuming a conversation. Read [references/page-contract.md](references/page-contract.md) before sending a message or when the page fails to expose a usable composer.

## Interpret the request

- Forward the user's question faithfully. Improve or expand it only when the user asks.
- Treat “继续问 / 追问 / 接着刚才” as continuation in the current registered Grok conversation.
- Treat “新会话 / 重新开始” as a fresh conversation. Do not silently mix it with an earlier conversation.
- Interpret “Twitter/X 的 gork” as X Grok unless the user explicitly means the local Grok Build CLI.
- Leave the visible model/mode on `Auto` unless the user requests another available mode. Verify any requested mode in the current UI before sending.
- If multiple registered conversations could match and the user did not identify one, show their short titles and exact URLs and ask which to resume.

## Preserve conversation state

1. Treat the numeric `conversation` query value as the durable identity and the exact URL as its recovery address. Ego task-space and tab IDs are temporary execution caches.
2. Use `scripts/conversation_registry.py`, resolved relative to this skill, to map a stable logical key to the conversation ID, exact URL, display label, and most recent task-space ID. Never store prompt or reply content there.
3. For a follow-up, resolve the requested or current registered conversation before opening Ego. Reuse a live task space only when its URL has the expected `conversation` value. If it is gone, create a new task space and open the saved exact URL.
4. Never silently create a new Grok conversation when the user asked to continue. A redirect from the saved URL to bare `/i/grok` is a failed recovery that must be reported.
5. Open `https://x.com/i/grok` only for an explicitly new conversation or when no prior conversation exists. After the first successful turn, read the resulting numeric `conversation` value and immediately register it with `--make-current`.
6. After every successful turn, upsert the exact URL and latest task-space ID and make that logical conversation current.
7. If login, challenge, CAPTCHA, subscription gating, or user control is required, hand off the task space and explain the single action needed. Resume only after explicit user confirmation through the ego-browser takeover flow.

## Send and collect a reply

1. Observe with `snapshotText()` and, when needed, a compact DOM check. Locate the visible, enabled `textarea[placeholder="Ask Grok (AI agent)"]`; reject hidden, zero-size, disabled, or offscreen decoy controls.
2. Fill the visible textarea, then read its value back and verify it exactly matches the intended message.
3. Follow the active browser/host confirmation policy at the moment the prompt will be transmitted. Sending a prompt is an external action.
4. Submit through the visible bound control. On the observed UI, a filled composer exposes `button[aria-label="Grok something"]`; prefer a fresh semantic ref or this current accessible label. Use Enter only when the current UI shows that Enter sends.
5. Immediately re-observe. Verify that the user's message appears, or that the composer clears and response generation begins. Do not click twice when the first submission is uncertain.
6. Poll at roughly 1–2 second intervals. A reply is complete only when the newest assistant text is non-empty and unchanged for two consecutive polls, no visible stop/generating control remains, and the newest response exposes its final action row such as `button[aria-label="Copy text"]`. Allow up to 120 seconds unless the user requested another limit.
7. Extract only the newest assistant reply. Start from the last response action row, find the smallest ancestor containing that one response and no composer, clone it, remove buttons and decorative controls, and preserve the remaining lists, tables, headings, line breaks, and anchors. Exclude collapsed reasoning or `Thoughts` unless the user explicitly asks for it.
8. Before reporting, enumerate every citation-summary control belonging to the newest reply, including labels such as `N post(s)` and `N web page(s)`. Open every such panel through the visible UI and collect all scoped source links. Do this by default; a count badge is not a substitute for the underlying URLs.
9. In a Relevant Posts panel, collect each top-level post permalink, profile link, mentioned-account link, and nested quoted-post permalink. A quoted post may be a clickable `div[role="link"]` with no `href`; open it through the visible UI, record the resulting canonical status URL, then return to the saved conversation URL and continue extraction. In a Relevant Web Pages panel, collect every result anchor and retain any X status/profile links mixed into the web results. Scroll a source panel until its result count and URLs stop changing when the panel is lazy-loaded or virtualized.
10. Deduplicate by canonical URL. Never discard an X link because the same source is represented by a handle, card, timestamp, analytics link, or quoted-post container. Strip `/analytics`, ordinary tracking parameters, and fragments from reported status URLs while preserving the status ID.
11. Return the complete reply followed by two exhaustive sections: `X / Twitter 原始链接（完整）` first, then `其他网页来源（完整）`. Include every collected URL rather than a selected subset, and pair it with the visible author/title or short source context when available.
12. If generation or source-panel extraction times out, return the visible answer and links labeled as partial, state which panel remains incomplete, and keep the conversation registered. Do not resend automatically.

After delivering the result, finish with a dedicated `completeTaskSpace(task.id, { keep: false })` heredoc unless the ego-browser rules give a concrete reason to keep or hand off the live page. The registry and exact conversation URL, not task-space retention, guarantee recovery.

## Report the result

Lead with Grok's complete answer unless the user explicitly asks for a summary. Always include the exhaustive, deduplicated X and web-source sections collected from both the reply body and its source panels; never replace them with source counts or an abbreviated selection. Briefly mention whether the conversation was registered and remains available for follow-up. Do not expose internal selectors, task-space mechanics, raw page dumps, cookies, request signatures, private headers, or unrelated account data.
