# DeepSeek Harness (dsh) 会话字段真值表

来源：`@deepseek-ai/dsh@0.1.0-rc.6` 自带 `.d.ts` 与生成文件（不是从语料反推），
2026-08-25 实测本地 19 个会话文件全部对齐。凡本文写「实测」的数字都可复算。

## 环境

```
CLI            dsh   (pnpm 全局)
存储           ~/.dsh/sessions/<cwd-slug>/<session-id>/session.jsonl.zstd
派生存储       ~/.dsh/storages/{workspace,session_projcache}.json
配置           ~/.dsh/settings.yaml     （agent-default-model：provider/model/reasoningEffort）
包源           ~/.dsh/profiles/node_modules/@deepseek-ai/*   ← 权威 .d.ts 在这里
```

⚠️ pnpm 的包目录是**符号链接**：`grep -r <pattern> .` 不会进入，返回空 ≠ 不存在。
必须用 `grep -R`。本次调查已被这一条骗过一次（把「找不到声明」误当「未声明」）。

## 压缩层（唯一一个压缩存储的已支持平台）

`session.jsonl.zstd` 是**多帧追加**：每次 flush 追加一个独立 zstd frame，不是一个大帧。

| 读法 | 结果 |
|---|---|
| `zstd -dc` / `zstdcat` | 正确，跨帧 |
| `zstandard.ZstdDecompressor().stream_reader(f, read_across_frames=True)` | 正确 |
| 同上但省略 `read_across_frames` | ⚠️ **只返回第一帧，且报告成功** |

第三行是本平台最危险的失效模式：**静默截断**。实测 `session-CASE-R` 共 27692 行，
只读首帧会得到约 20 行并显示为一个「干净的短会话」。这正是本 skill 定义的 B 类裂缝
（引用了一个已不再成立的东西），而且是我们自己制造的。
⇒ `session_events.open_session_text()` 宁可**响亮拒绝**也不退化成单帧读。

`size_bytes` 是压缩后大小，**不是规模**。实测 6.8 MB on disk = 13.4 MB 解压。
任何按字节的直觉（成本、洪水占比、"这会话大不大"）必须用解压长度，
所以 metrics 同时给 `size_bytes` 与 `size_bytes_decompressed`。

## 两套词汇共存于同一文件

```
header      {"type":"session","version","id","createdAt","cwd","delegationDepth"}
session事件  44 种带命名空间的类型（"tool/call"、"compaction/summary" …）
chunk row    3 种故意不带斜杠的打包行（"text-chunks"/"reasoning-chunks"/"tool-call-chunks"）
```

权威清单：`dsh-session/lib/types/known-event-types.js`（生成文件，`KNOWN_SESSION_EVENT_TYPES`）。
读路径遇到集合外的类型且无 `ignorable` 标记会**拒绝解释整个日志**——即"这是更新的 harness 写的"。

### chunk row：是回声，不是物证

`chunk-rows.d.ts` 原话：provider 按 token 流式下发，JSON 信封比 payload 大得多
（**实测 ~56×**），所以把连续同块 delta 打包成一行。字段：

```
{type:"reasoning-chunks", seq0, time0, data:{turn, step, index, dt:[...], texts:[...]}}
{type:"tool-call-chunks", seq0, time0, data:{turn, step, index, dt:[...], id, name?, args:[...]}}
成员 k 还原为 seq = seq0+k，time = time0 + sum(dt[:k])（dt 可为负：墙钟回拨）
```

⚠️ **这三种行的内容会再次出现在收尾的 `assistant/message` 里（已组装）。**
把它们当叙事事件计入 = 把同一段输出数到两遍——与 Claude Code 重发压缩块那个已记录缺陷同型。
适配器把它们计为 `ui_noise` 并保留 `n_deltas` / `span_ms`：**计数，但不进叙事流**。

实测 `session-CASE-R`：chunk row 22210 行 vs `assistant/message` 263 行。
按行数看这是个"两万多事件的巨型会话"，按物证看是 263 步、349 次工具调用。

## `user/message`：ambient 是**结构事实**，不是文本猜测

`data.source.kind` 直接说明这条 user 角色消息为何存在。

| kind | 是谁 | 处理 |
|---|---|---|
| `user` | 人 | **唯一的外部物证** |
| `agent-instructions` | AGENTS.md / CLAUDE.md 注入 | ambient |
| `plugin` | 沙箱策略等快照 | ambient |
| `skill-catalog` | 注入的可用 skill 列表 | ambient |
| `skill-invocation` | 渲染后的 skill 正文 | ambient |
| `goal` | **agent 用自己的 goal 再驱动自己**（带 `round`） | ambient + 自泵计数 |
| `subagent-report` | 子 agent 自己选择上报的内容 | ambient + subagent 物证 |
| `subagent-settled` | runtime 陈述子 agent 如何结束 | ambient + subagent 物证 |
| `coordinator` | 别的 agent 发给它的消息（`relay`） | ambient + subagent 物证 |

`MessageSourceMap` 明确声明为 merge-extensible（"plugins add their own kinds"），
**这个集合是开放的，不可穷举**。⇒ 规则只能是「不是字面 `user` 的一律视为注入」。
方向是刻意选的：把新 kind 误判成注入只丢一个信号，误判成人则把 harness 文本抬成 Owner 意图，
而后者正是本 skill 存在的理由。

两个不能合并的细分：
- `subagent-report` vs `subagent-settled` 在源码里被刻意分成两个 kind，理由原文是：
  report 是孩子**选择说的话**，settled 是管理器**陈述孩子的下场**，合并会把孩子没说过的话记在它头上。
- **dsh 不需要 Kimi 那种 skill 正文剥离**：`dsh-skill/types` 原话——用户自己的话走一条普通
  user 消息，渲染后的 skill 正文另发一条带 `skill-invocation` 的注入。两者从不融合。

实测 `session-CASE-R`：34 条 user 角色消息里只有 11 条是人；其中 10 条 `goal`
是 agent 自己的目标续轮（`round` 最高到 7）。把这 23 条当成用户意图 = 目标追踪全错。

## compaction：唯一能**测量**压缩损失的平台

```
compaction/start    {compactionId, turn|null}         同步追加，持锁
compaction/summary  {compactionId, summary, shadowedRange{start,end},
                     shadowedSeqs[], shadowedTokenCount, provider, model, usage?}
compaction/prune    {shadowedRange, shadowedSeqs[], shadowedTokenCount}   无模型的剪枝
compaction/end      {compactionId, turn|null, error?}  释放锁
```

协议（源码原文）：`summary`/`prune` 之后**必须紧跟**那条执行 surface 替换的 `user/message`；
前一条计量事件就是这次替换的"影子价格"。

⇒ 本 skill 关于「压缩致盲」的核心主张在 dsh 上是**读出来的，不是估出来的**：
`shadowed_tokens` / `shadowed_events` 精确给出被遮蔽的 token 数与事件数。
其它平台这两项必须报 `null`（能力位 `compaction_loss`）。

判读闸：
- **没有配对 `end` 的 `start` 不是一次压缩，是一次没关上的压缩**（summarizer 死在锁里）。
  计入 `compactions_unclosed`，绝不计入 `compactions`。
- `end.error` 非空 = 尝试失败。计入 `compactions_failed`。

## surface：模型看见的 ≠ 实际发生的

message 类事件带 `surfaceOp`：`'append'` 或 `{op:'replace', start, end}`。
`surface.d.ts` 原话：模型可见 surface 会**故意遮蔽被替换区间**，所以它是重建人类
transcript 的错误来源——落地的替换会抹掉用户当时确实看过的对话。

```
人类 transcript  ← 只取 surfaceOp === 'append' 的事件（append-origin）
模型当时看到的    ← 折叠全部 surface 操作后的结果
两者之差         ← 就是「执行 agent 原理上看不见的东西」，可直接计算
```

这是四个平台里唯一能把「压缩致盲」从论断变成可计算量的地方。

## `session/end-seed`：不是所有历史都是这次干的

payload 为空，位置和 `time` 就是全部含义。**最后一条**之前的事件来自 seed
（resume / fork / replay），本次生命周期一件都没做。

⇒ 把它们算作本次的功会虚增每一项努力指标。适配器把它发成 `session_meta`，
于是 `session_meta_records = 1(header) + end-seed 条数`，天然就是 resume/fork 次数。
实测 `session-CASE-R` = 3（一个 header + 两次续接）。

源码还给了一条附带用途：判断某个 `compaction/start` 是否属于已结束的生命周期——
seed 历史和活的工作在字节上无法区分，只有这个边界能分。

## 子 agent：同目录、靠 header 区分、**派生存储里查不到**

```
delegationDepth == 0   根会话   目录名形如 session-<uuid> 或 star-<uuid>
delegationDepth >  0   子 agent 目录名是裸 <uuid>（同一个 cwd-slug 目录下）
```
目录名前缀只是惯例，**header 才是契约**。子会话另有 `subagent/descriptor`：

```
{version, mode:'one-shot'|'continuable', provider:'spawn'|…,
 label, agentProvider, agentModel, persona?, toolFilter?}
```
⚠️ 这里的 `provider` 是**孵化方式**（spawn/fork），不是模型厂商；模型路由在
`agentProvider`/`agentModel`。把 `provider` 读成模型会给每个孩子贴错标签。

⚠️⚠️ **`workspace.json` / `session_projcache.json` 只列根会话。**
实测：派生存储列 9 个，磁盘上 19 个，缺的 10 个**全部**是子 agent。
puzhi-fan 一例：父会话 27692 行，四个孩子合计 9040 行——只审父会话会漏掉约 1/4 的工作量，
而且看起来是完整的。⇒ 遍历目录；派生存储只配用来**定位**，绝不用来**计数**。

（这本身就是本 skill 的标准 B 类形态：引用了一份不再完整的派生副本，且无失效传播。）

## `session_projcache.json`：好用的定位索引，危险的结论来源

每个根会话预存：`sessionStats`（turns/steps/llmMs/toolMs/ttftMs/decodeMs/decodeTokens/
pendingCalls）、`title`、`goal`、`tokenUsage`、`contextPressure`、`contextBreakdown`、
`subagentTiming`、`subagent`、`permissions`、`todos`、`plan`。

它是 projection **cache**。按本 skill 的一贯判据：派生副本必腐，且缺失效传播。
⇒ 定位可用；任何结论必须回到日志本身。

## 其余映射（→ 规范化 Event）

```
session                       → session_meta（extra: session_id/cwd/delegation_depth）
session/end-seed              → session_meta(name=end_seed)
user/message                  → user_msg（extra.ambient 来自 source.kind）
assistant/message             → thinking（reasoning 块）/ assistant_msg（text 块）
                                + token_count（usage.inputTokens + cacheReadTokens）
tool/call                     → tool_call（arguments 是模型原样产出的未解析字符串）
tool/result                   → tool_output（isError 或 data.error 任一为真即失败）
turn/end reason.kind
  completed                   → turn_complete
  error                       → turn_error
  interrupted | aborted | …   → turn_aborted
llm/retry                     → turn_error（重试过的请求失败；不折进 abort）
compaction/summary | prune    → compaction（带 shadowed_* 计量）
compaction/start | end        → ui_noise（括号标记，用于配对检查）
goal/change                   → ui_noise(name=goal_change)，text=objective，extra.revision
approval/decided              → ui_noise(name=approval:<decision>)，extra.denied
subagent/descriptor           → subagent
其它 log-only 记账             → ui_noise(name=<原类型>)
```

工具名（源码实测）：
`bash` `pwsh`（exec）/ `read` `write` `edit` `read_image` `str_replace_editor`（fs，路径键
`file_path`）/ `glob` `grep` / `todo_write` / `skill` / `web_fetch` `web_search` /
`ask_user_question` / `job_list` `job_output` `job_kill` / `create_goal` `get_goal` `update_goal` /
`ralph`。补丁工具集 = `{edit, write, str_replace_editor}`。

## token 口径

`assistant/message.usage = {inputTokens, outputTokens, cacheReadTokens, reasoningTokens}`。
上下文占用取 `inputTokens + cacheReadTokens`（模型被喂进去的），
**不含** output/reasoning——那是生成不是占用，折进去会虚报压力。

窗口在 `request/context {provider, model, contextWindow}`，**只在路由或容量变化时才记一条**。
⇒ 适配器必须有状态：某一步的窗口是"最近一次宣布的那个"，无状态解析会让第一条之后
全部报 `window=None`（平台明明有这个能力，却被静默丢掉）。
实测 deepseek-v4-pro：`contextWindow = 1000000`。
