---
name: xhs-ai-chat
description: "Use ego-browser to chat with 小红书点点 at xiaohongshu.com/ai_chat, retrieve complete replies, and preserve context across multi-turn follow-ups. Use for 点点/小红书 AI conversations, not ordinary 小红书 search, posting, or social interaction."
---

# 小红书点点多轮聊天

Use ego-browser as the only browser execution layer. Before the first browser action in a turn, read the installed `ego-browser` skill completely from its path in the current environment’s skill catalog (prefer the project-local installation; if unavailable, ask the user to provide the dependency) and follow its task-space, handoff, and completion rules. Do not substitute Chrome control, Playwright, a private API replay, or direct cookie access.

Read [references/conversation-state.md](references/conversation-state.md) before opening or resuming a conversation. Read [references/page-contract.md](references/page-contract.md) before sending a message or when the page fails to expose a usable chat input.

## Interpret the request

- Forward the user's question faithfully. Improve or expand it only when the user asks.
- Treat “继续问 / 追问 / 接着刚才” as continuation in the current 点点 conversation.
- Treat “新会话 / 重新开始” as a fresh conversation. Do not silently mix it with an earlier thread.
- If multiple registered 点点 conversations could match and the user did not identify one, show the short titles and exact URLs and ask which to resume.

## Conversation state

1. Treat `conversationId` as the durable identity and the exact URL as its recovery address. Ego task-space and tab IDs are temporary execution caches.
2. Use `scripts/conversation_registry.py`, resolved relative to this skill, to map a stable logical key to the `conversationId`, exact URL, display label, and most recent task-space ID. Never store prompt or reply content there.
3. For a follow-up, resolve the requested or current registered conversation before opening Ego. Reuse a live task space only when its URL has the expected `conversationId`. If it is gone, create a new task space and open the saved exact URL.
4. Never silently create a new 点点 conversation when the user asked to continue. A redirect from the saved URL to bare `/ai_chat` is a failed recovery that must be reported.
5. Open `https://www.xiaohongshu.com/ai_chat?from=sidebar&conversationId=` only for an explicitly new conversation. After the first successful turn, read the resulting `conversationId` and immediately register it with `--make-current`.
6. After every successful turn, upsert the exact URL and latest task-space ID and make that logical conversation current.
7. If login, CAPTCHA, or user control is required, hand off the task space and explain the single action needed. Resume only after explicit user confirmation, using the ego-browser takeover flow.

## Send and collect a reply

1. Observe with `snapshotText()` and, when needed, a compact DOM check. Locate the visible, enabled `textarea[name="aiSearchTextarea"]`; never use the first matching textarea blindly.
2. Fill the visible textarea, then read its value back and verify it exactly matches the intended message before sending.
3. Follow the active browser/host confirmation policy at the moment the message will be transmitted. Sending a prompt is an external action.
4. Submit through the visible bound send control. Prefer a current semantic ref or a visible `[data-hp-bound="1"]` submit element; use Enter only when the page's current UI shows that Enter sends.
5. Immediately re-observe. Verify that the user's message appears in the conversation or that the input clears and response generation begins.
6. Poll at roughly 1–2 second intervals. A reply is complete only when its visible text is unchanged for two consecutive polls and no visible stop/generating control remains. Allow up to 120 seconds unless the user requested another limit.
7. Extract only the newest assistant reply from the conversation area, preserving lists and line breaks. Treat underlined, pointer-interactive terms and the `AI总结…篇笔记` control as latent source links even when they are not `<a>` elements. Expand them through the visible UI and collect stable 小红书 profile or `/explore/<note-id>` links from the resulting drawer.
8. Return the complete reply by default, followed by a clearly labeled link list. Strip temporary `xsec_token` and tracking parameters when a stable profile ID or note ID is available. If a name does not resolve to a unique profile, label it as unverified and provide only the related-note or search entry; never guess an account.
9. If generation times out, return the visible partial reply labeled as partial and keep the same task space for continuation. Do not resend automatically.

After delivering a reply, finish with a dedicated `completeTaskSpace(task.id, { keep: true })` heredoc so the browser state has the best chance of remaining warm. The registry and `conversationId`, not retention alone, guarantee recoverability. On a later follow-up, try the registered task space first, then reconstruct from the exact saved URL if needed. Use `{ keep: false }` only when the user ends the conversation or asks to close it.

## Report the result

Lead with 点点's complete answer unless the user explicitly asks for a summary. Include 小红书 links carried by the answer or exposed by its related-note UI, and distinguish verified creator profiles from related notes or search-only results. Briefly mention whether the conversation remains available for follow-up. Do not expose internal selectors, task-space mechanics, raw page dumps, cookies, request signatures, or unrelated account data.
