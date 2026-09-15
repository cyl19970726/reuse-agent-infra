# X Grok 页面契约与恢复规则

这些细节于 2026-08-27 在 `https://x.com/i/grok` 的已登录页面观察得到。它们是有证据的回退方案，不是永久 API。始终优先使用新鲜的 `snapshotText()` 与当前可见状态。

## 入口与会话恢复

- 新会话入口是 `https://x.com/i/grok`。
- 已保存会话使用 `https://x.com/i/grok?conversation=<数字ID>`。
- 顶部 `button[aria-label="Chat history"]` 打开 History；Chats 页的会话条目直接链接到带 `conversation` 参数的 URL。
- 恢复时打开登记的精确 URL，并验证最终 URL、历史消息和真实 composer。若参数消失并回到裸 `/i/grok`，视为恢复失败。

## 登录与可用信号

- 已登录页面可显示 X 主导航、账户菜单和 `main` 内的 Grok 区域。
- 最强的可用信号是可见、启用的 `textarea[placeholder="Ask Grok (AI agent)"]`。
- 页面可能同时存在不可见 textarea 或文件输入。拒绝 `disabled`、零尺寸、不可见或不在 Grok composer 中的候选。
- 若真实输入框没有出现，检查一次页面文本和截图，识别登录、challenge、CAPTCHA、订阅门槛或站点错误。不要循环刷新。

可见性回退谓词：

```js
const visible = el => {
  const rect = el.getBoundingClientRect()
  const style = getComputedStyle(el)
  return rect.width > 0 && rect.height > 0 &&
    style.display !== 'none' && style.visibility !== 'hidden'
}

const composer = [...document.querySelectorAll(
  'textarea[placeholder="Ask Grok (AI agent)"]'
)].find(el => visible(el) && !el.disabled)
```

## 发送契约

- 空 composer 通常显示语音入口；填入文字后，观察到的发送按钮为 `button[aria-label="Grok something"]`。
- 填入后从同一可见 textarea 回读值并逐字校验，再按当前授权政策提交。
- 提交后必须观察到：用户消息进入会话，或输入框清空且开始生成。若状态不明，不要再次点击。
- `Auto` 是当前默认模式按钮。用户指定其他模式时，通过可见 UI 选择，并在发送前验证选中状态；不要猜测未显示的模式。

## 完成与最新回复定位

当前页面不使用稳定的 `article` 结构，因此不要依赖 `article:last-child`。每条完成的助手回复都会有独立操作区，常见按钮包括：

- `button[aria-label="Copy text"]`
- `button[aria-label="Share"]`
- `button[aria-label="Like"]`
- `button[aria-label="Dislike"]`
- 最后一条完成回复还可能有 `button[aria-label="Regenerate"]`

建议流程：

1. 发送前记录 Grok 主会话区的规范化文本与 `Copy text` 按钮数量。
2. 发送后轮询新尾部文本、停止/生成控件和操作区数量。
3. 从最后一个 `Copy text` 按钮向上寻找最小祖先：它包含实质回复文本、只包含这一条回复的操作区，且不包含 composer。
4. 对该容器连续两次取规范化文本。文本非空、两次相同、没有可见 stop/generating 控件，且最终操作区已出现时，才判定完成。
5. 克隆容器并移除 `button`、输入控件和纯装饰元素，再提取文本、列表、表格和链接。`Thoughts` 是可折叠推理入口，默认不展开、不返回。

若新回复尚未出现 `Copy text`，可临时用发送前后的尾部文本差异追踪进度，但不能仅凭固定等待判定完成。

## 引用与完整链接提取

- Grok 的正文可直接包含 `https://x.com/<handle>`、X 帖子链接和外部网页链接。
- `data-testid="grok_citation_web_result"` 可作为网页引用的辅助信号，但不可代替可见文本和实际 `href`。
- 回答下方可能同时出现 `N post(s)` 和 `N web page(s)`。默认逐个展开全部来源面板；不要只返回徽标数字，也不要因为正文已有几个链接就跳过面板。
- `Relevant Posts` 面板使用 `article[data-testid="tweet"]`。主帖的稳定 URL 通常来自时间戳锚点 `/handle/status/<id>`；`/analytics` 仅是同一帖的衍生链接，应还原为主 status URL。
- 帖子卡可包含 quoted post。引用卡可能是 `div[role="link"][tabindex="0"]` 而没有 `href`。通过可见卡片打开它，从最终页面 URL 或帖子时间戳取得被引帖的 status URL，然后回到已登记的 Grok 会话精确 URL继续抽取。
- 从 `Relevant Posts` 面板同时收集主帖作者、正文中提及账号和回复对象的 profile 链接。只在当前来源面板范围内提取，排除 X 全局导航和当前登录账号等无关链接。
- `Relevant Web Pages` 面板中的每个来源是实际 `a[href]`，其中可能混入 X status 链接。收集全部结果，不只收集非 X 网页或首屏结果。
- 若面板可滚动或虚拟化，滚动面板容器并重复观察，直到结果条目数及规范化 URL 集合连续两次不变。面板标题、徽标数字与实际已加载条目数不一致时，以完全加载后的条目集合为准，并说明仍无法加载的差额。
- 返回引用时先输出完整 X 链接，再输出完整外部网页链接。保留可见作者/标题/摘要上下文；按规范化 URL 去重，不按显示文本去重。

规范化规则：

- X 帖子：`https://x.com/<handle>/status/<id>`，移除 `/analytics`、`/photo/N`、查询参数和 fragment。
- X 账号：`https://x.com/<handle>`，排除 `/home`、`/explore`、`/i/*`、`/compose/*` 等导航路径。
- 外部网页：保留实际目标 URL，只移除明确的普通追踪参数；不要改写会改变内容身份或访问能力的参数。

## 失败策略

- 一次聚焦加载后仍无可见输入框：截图检查一次，然后为登录/challenge/CAPTCHA 执行 handoff，或报告站点错误。
- 文本仍留在输入框且没有用户消息：不要重复提交；检查按钮禁用、验证提示或发送失败。
- 输入框已清空但 120 秒内没有完整助手输出：返回可见部分并标为“部分回复”，保留会话登记，不要自动重发。
- 用户接管、任务空间 inactive 或不再分配给 agent：立即停止，等待用户明确确认后再接管。
- 不读取 Cookie、localStorage、私有 headers、请求签名或内部 GraphQL 载荷，也不把观察到的端点改造成非官方 API 客户端。
