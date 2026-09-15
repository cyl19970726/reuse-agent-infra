# 点点页面契约与恢复规则

These details were observed on `https://www.xiaohongshu.com/ai_chat` on 2026-08-21. Treat them as evidence-backed fallbacks, not permanent API guarantees. Always prefer a fresh `snapshotText()` and current visible state.

## Existing-conversation recovery

When resuming, open the exact registered URL containing `conversationId`. After load, verify that the final URL still contains the expected value, conversation history is present, and the genuine composer exists. If the site redirects to bare `/ai_chat`, treat recovery as failed and do not send into the blank conversation.

## Loading and login signals

- A successful authenticated page can initially render only the 小红书 left navigation while the main area is blank. On the observed page, the chat UI appeared after a parameterized navigation and additional settling time.
- A usable new-conversation screen contains a greeting such as `想探索些什么？` and a visible `textarea[name="aiSearchTextarea"]`.
- The left navigation showing `我` is supporting evidence of login, but the visible usable textarea is the stronger readiness signal.
- If the input never appears, inspect the screenshot and page text once for login, CAPTCHA, an error, or user-control state. Do not loop navigation or keep refreshing.

## Hidden decoy controls

The page may insert paired controls through its anti-abuse layer:

- a hidden textarea with `aria-hidden="true"`, `tabindex="-1"`, near-zero opacity, and a fixed placeholder;
- a visible textarea with `data-hp-bound="1"`; its placeholder can rotate and should not be treated as a stable selector;
- a hidden submit SVG beside a visible submit SVG carrying `data-hp-bound="1"`.

Therefore:

- reject any candidate with `aria-hidden="true"`, `tabindex="-1"`, hidden geometry, or no visible bounding box;
- prefer the visible candidate with `data-hp-bound="1"`;
- do not select the first textarea or first `.submit-button` from DOM order;
- after filling, verify the value on the same visible element before submission.

Useful fallback DOM predicates:

```js
const visibleInput = [...document.querySelectorAll('textarea[name="aiSearchTextarea"]')]
  .find(el => el.getAttribute('aria-hidden') !== 'true' &&
              el.getAttribute('tabindex') !== '-1' &&
              el.getBoundingClientRect().width > 0 &&
              el.getBoundingClientRect().height > 0)

const visibleSubmit = [...document.querySelectorAll('.submit-button, [data-hp-kind^="xhs-search-submit"]')]
  .find(el => el.getAttribute('aria-hidden') !== 'true' &&
              el.getAttribute('tabindex') !== '-1' &&
              el.getBoundingClientRect().width > 0 &&
              el.getBoundingClientRect().height > 0)
```

Use these predicates only for inspection or to select a current target; perform interaction through ego-browser helpers whenever possible.

## Completion and extraction

Do not use a fixed sleep as proof of completion. Track the newest assistant message across polls:

1. Record the conversation area's normalized visible text immediately after submission.
2. Poll for a new trailing assistant block or a change after the user's latest prompt.
3. While a stop/generating control is visible or text is still changing, continue waiting.
4. Declare completion only after the newest assistant text is non-empty, stable for two consecutive polls, and generation controls have disappeared.
5. Preserve links by collecting anchor text and `href` values from inside the newest assistant block.

If a precise assistant-message selector cannot be established from the fresh DOM, use the smallest container that contains the current conversation, compare before/after snapshots, and remove the known latest user prompt. Validate the extraction against a screenshot before returning it.

## Latent links and reference drawers

Point点 can encode linked concepts as `<u>` elements with `cursor: pointer` rather than anchors. The response can also expose a pointer-interactive `.progress-wrapper[data-has-reference="true"]` labeled like `AI总结99篇笔记生成`.

- After generation completes, enumerate visible pointer-interactive terms inside the newest assistant response, especially creator names and the reference-progress control.
- Click a relevant underlined term through ego-browser. It opens a `.drawer` titled `"<term>"相关笔记`.
- Wait until `.drawer section.note-item` is non-empty; switching drawers faster than their asynchronous load can produce a false empty result.
- Creator links appear as `a.author[href*="/user/profile/"]`; note cards include a stable hidden `a[href^="/explore/"]` and may also expose tokenized `/search_result/…` links.
- Prefer stable forms: `https://www.xiaohongshu.com/user/profile/<id>` and `https://www.xiaohongshu.com/explore/<note-id>`. Remove `xsec_token`, `xsec_source`, channel, and other tracking parameters from reported links.
- Match creator names exactly after removing zero-width characters. A related-notes drawer is a search surface, so the first card is not automatically the creator's own account.
- If no exact `a.author` match exists, do not claim a profile match. Report the term as unresolved and, if useful, include stable related-note links or the visible 小红书 search URL with an explicit `站内搜索` label.
- Close the drawer via the visible `[aria-label="关闭"]` control before opening another term, and wait for the next drawer's real cards before extracting.

## Failure policy

- No visible input after one focused load and readiness wait: inspect once, then hand off for login/CAPTCHA or report the site error.
- Message remains in input after submission: do not click repeatedly; inspect whether the send control is disabled or a validation/error notice appeared.
- Input cleared but no assistant output within 120 seconds: return the visible partial state and keep the task space.
- User takes control or the space becomes inactive: stop immediately and wait for explicit confirmation before reclaiming it.
- Never extract cookies, local storage, request signatures, or private headers, and never convert observed endpoints into an unofficial API client.
