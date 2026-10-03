# 多平台适配

心智模型、签名表、失效表**全部与平台无关**——它们只依赖「两条流」这个抽象。
平台差异只在一件事上：**如何把一行原始记录映射成规范化事件**。

## 规范化事件

每个适配器只需产出这一种事件流，之后所有指标计算共用同一份代码：

```
(line_no, flow, kind, name, size)

flow   ∈ narrative | evidence | lifecycle | meta
kind   ∈ user_msg | assistant_msg | thinking | tool_call | tool_output
         | compaction | turn_complete | turn_aborted | turn_error
         | token_count | session_meta | subagent
         （meta 噪声类：ui_noise / queued / duplicate_record / malformed / eof）
name     工具名 / 角色 / 事件名
size     字符数（tool_output 用它算 flood；token_count 用 input_tokens）
```

`turn_error` = 被 harness 重试掉的瞬时请求失败（如 Claude Code `api_error`），
**不折进 turn_aborted**——折进去会把"每轮都完成了"的会话读成"放弃比完成多"
（实测 complete=10 aborted=11 的假头条，2026-07-31）。

映射规则（各平台通用）：

```
narrative   assistant 文本、thinking/reasoning、计划、摘要
evidence    工具调用与其输出、补丁目标、失败/超时、用户消息
lifecycle   轮次开始/结束/中断、压缩
```

## Codex（已实现）

见 `references/codex-jsonl.md`。要点：

```
type=response_item  payload.type=message            → user_msg / assistant_msg
                    payload.type=reasoning          → thinking
                    payload.type=function_call      → tool_call   (name, arguments)
                    payload.type=custom_tool_call   → tool_call   ⚠️ 易漏
                    payload.type=*_output           → tool_output (output)
type=event_msg      payload.type=token_count        → token_count (info.last_token_usage.input_tokens + cached_input_tokens + cache_write_input_tokens)
                    payload.type=task_complete
                                 /turn_aborted
                                 /thread_rolled_back → lifecycle
                    payload.type=sub_agent_activity → subagent    (agent_path, agent_thread_id)
type=compacted                                      → compaction  (payload.replacement_history)
type=session_meta                                   → session_meta ⚠️ 单文件可有数十个
```

存储：`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`（5548 个 / 48 GB）+
`~/.codex/archived_sessions/`（扁平 / 13 GB）。索引 `session_index.jsonl` 仅覆盖 2980/5548。

Codex 还会把环境块、插件清单和完整 Skill 加载块（`<skill>...</skill>`）以
role=user 写入；适配后的 objective trace 必须把它们归为 ambient，而不是用户目标。

## Claude Code（已实现）

存储：`~/.claude/projects/<escaped-cwd>/<session-uuid>.jsonl`
实测规模：**1307 个文件 / 2.2 GB**（比 Codex 小一个量级，全量扫描可行）。

顶层记录是**扁平**的，没有 Codex 那层 `payload`：

```
top-level keys: cwd, entrypoint, gitBranch, isSidechain, message,
                parentUuid, requestId|promptId, sessionId, type
```

映射：

```
type=user        message.role=user      message.content[]
type=assistant   message.role=assistant message.content[]
type=system                                            → lifecycle/meta
type=attachment  attachment.type=queued_command
                   origin.kind=human                   → user_msg（extra.queued=true）
                   其余（peer / task-notification 无 origin）→ meta/queued（drill 可见，不入目标轨迹）
                 其他 attachment 类型                   → 当前忽略（未映射）
type=ai-title / last-prompt / queue-operation          → meta（噪声，过滤）

message.content[] 的 item.type:
   text        → narrative
   thinking    → narrative      （对应 Codex 的 reasoning）
   tool_use    → tool_call      item.name, item.input
   tool_result → tool_output    item.content
```

实测某 400 行样本：`thinking 66 / text 62 / tool_use 94 / tool_result 93`，
工具 `Bash 56 / Edit 17 / Read 16 / Write 3 / mcp__* / AskUserQuestion`。

平台差异要点：

- **subagent 存储布局已换代（⚠️ 2026-08-10 实测修正）**：2026-08 布局把子 agent 写到
  `<session-dir>/subagents/agent-*.jsonl` **独立文件**（workflow 的再深一层
  `subagents/workflows/wf_*/agent-*.jsonl`），`isSidechain: true` 只出现在那些文件内部；
  本语料 9 个会话有 `subagents/` 目录，**最大的 40 个主会话文件里内联 sidechain 记录为 0**。
  后果：只审主文件会漏掉全部子 agent 物证，且 `sub_agents` 计数恒空——
  **主文件 `sub_agents=0` ≠ 没用子 agent，必须 `ls <session-dir>/subagents/`**（子 agent 的
  spawn 仍以 `Task`/`Agent` tool_call 留在主文件里，可作存在性线索）。
  旧会话仍可能是内联形态（本条修正前的记述），适配器对两种形态都保留 `subagent` 标记。
- **补丁目标从 `Edit`/`Write` 的 `input.file_path` 取**，不是从 `*** Update File:` 文本取。
  Codex 那条正则陷阱在此不适用，但要注意 `MultiEdit` 类工具的批量结构。
- **exec 对应 `Bash` 的 `input.command`。**
- **token 用量在 assistant 记录的 `message.usage`**，不是独立的 `token_count` 事件。
  `input_tokens` 是未缓存输入，`cache_read_input_tokens` 是缓存读取，
  `cache_creation_input_tokens` 是本轮新写入缓存；适配器把三者分别保留为
  `uncached_input_tokens` / `cached_input_tokens` / `cache_write_input_tokens`，并把三者之和
  作为该请求的完整输入上下文。判断 Fork 是否复用了长前缀时，必须看子会话**首个新请求**的
  `cached_input_tokens`，不能把复制进子 transcript 的父会话旧 usage 当成新命中。
  `drill.py` 的 token 行同时显示 model / session / request 标识，便于把模型切换与
  Fork 后的新请求从复制的父 usage 中分离；还必须比较 timestamp：Claude Prompt Cache
  默认 TTL 为 5 分钟、可选 1 小时，超过 TTL 的同模型完整 Fork 也会重写长历史缓存，
  这只能证明缓存已过期，不能证明 Fork 不具备复用能力。
- `ai-title` / `custom-title` 仍属于 UI 元数据，但适配器保留其标题文本，供按 Claude Code
  `/resume` 界面标题定位真实 session UUID；标题不是用户消息，不进入 objective trace。
- Claude Desktop 的跨会话转发会把 peer 内容包装成
  `<cross-session-message from="...">...</cross-session-message>`，但仍以 `role=user`
  落盘；另一种实测包装是
  `Another Claude session sent a message: <agent-message ...>`。它们都是控制面 peer 消息，
  不是人类目标，必须归 ambient；否则 P3 会把另一个 session 的条件句或建议误当成 Owner 改需求。
- **压缩标记已确认两种**：顶层 `isCompactSummary: true`，以及
  `type=system / subtype=compact_boundary`。二者都映射为 `compaction`。
- **轮次结束是近似值**：`type=system / subtype=stop_hook_summary` 映射为 `turn_complete`；
  `api_error` 与 `model_refusal_fallback` 映射为 **`turn_error`（不是 turn_aborted）**——
  它们是被 harness 重试掉的瞬时失败，轮次通常照常完成（2026-07-31 修复；
  本条旧文在修复后仍写着 turn_aborted 近两周，2026-08-10 审计发现——文档载体
  没进当次修复清单：改行为时，说明它的文档要一起改）。
- `parentUuid` 构成消息树，可用于重建分支/重试，Codex 没有等价物。

## Kimi Code（已实现）

存储：`~/.kimi-code/sessions/**/session_<uuid>/agents/<agent>/wire.jsonl`。
每个 agent 一份独立文件；需要完整主/子 agent 图时必须遍历同一 `agents/` 目录。

⚠️ **raw grep 陷阱：`llm.request` / `config.update` / `context.append_message` 记录内嵌全量
对话快照**，任何对 wire 原文的 grep 命中的多数是历史回声而非真实事件。实测 `9c6cfb21`
（29 MB main wire）：`event consume` raw 命中 202 行，真实调用 **0** 次（host 实际用的是
`team-run events`，drill 渲染确认）——命中数随会话长度增长，与事件发生次数无关。
Kimi wire 上：raw grep 只配做「存在性粗筛」，计数与定位必须走 drill/session_events。

⚠️ host 会话 reload 会把受跟踪的后台任务标记为 lost，但**不杀子进程**（实测于一个治理项目，
2026-08-05：supervisor PID 37123 在 reload 后仍存活、租约 current=true；两个后台任务全报 lost）。
用 progress_probe/任务状态监控 worker 时，lost 通知 ≠ DEAD：先 `ps -p <pid>` + 查租约/heartbeat
再下结论，否则会把活着的生产者误判成死亡并错误重启。

⚠️ **刚起的会话曾被判 `unknown` 而拒绝解析**（2026-08-25 修）。`detect_format` 的 Kimi
投票集原本只有 `context.` / `usage.` / `turn.` / `tools.` / `permission.` / `config.`，
漏了 `llm.` / `interaction.` / `profile.`，也漏了 `metadata`。而每份 wire 的**第一行都是**
`metadata`（本地 672/672，键集恒为 `{type, protocol_version, created_at}`）。
后果：一个只写了 header + `profile.bind` 的新会话**零票**→ `unsupported format: unknown`
→ `progress_probe` 直接抛栈。**而"刚 spawn 的 silent worker 现在在干什么"正是探针的核心用途**
——它在最该用的那一刻是瞎的。实测 5 个文件命中。
现按 `metadata` + **`protocol_version` 存在**投票（裸 `metadata` 太泛，不能认领）；
1610 个文件跨四平台复核，只有那 5 个 `unknown → kimi`，零翻转。

映射：

```
type=metadata                                      → session_meta
type=turn.prompt                                   → user_msg
type=context.append_message                        → user_msg / assistant_msg
type=context.append_loop_event
  event.type=tool.call                             → tool_call
  event.type=tool.result                           → tool_output
  event.type=content.part / part.type=think|text   → thinking / assistant_msg
  event.type=step.end                              → token_count + turn lifecycle
```

平台差异要点：

- 工具补丁目标从 `args.path` 或 `args.file_path` 取；命令从 `args.command` 取。
- `tool.result` 不重复工具名，只给 `toolCallId`；适配器用该 ID 与先前的 `tool.call`
  关联，不能靠“最近一次调用”猜测（同一步可先发多次 call、再批量返回 result）。
- token 用量来自 `step.end.usage` 的 `inputOther + inputCacheRead + inputCacheCreation`。
- `finishReason=stop|end_turn` 映射为 `turn_complete`；
  `aborted|cancelled|error` 映射为 `turn_aborted`。
- 当前实测语料没有可确认的压缩标记，所以 `compactions` 必须返回 `null`，不能返回 `0`。

## DeepSeek Harness / dsh（已实现）

存储：`~/.dsh/sessions/<cwd-slug>/<session-id>/session.jsonl.zstd`。
完整字段真值表见 `references/dsh-jsonl.md`；此处只列会改变操作方式的四点。

⚠️ **唯一压缩存储的平台，且默认失效模式是静默截断。** 多帧追加 zstd：
缺 `read_across_frames` 的解码器只返回第一帧**并报告成功**（27692 行读成约 20 行，
显示为一个干净的短会话）。`open_session_text()` 因此宁可响亮拒绝也不退化。
`size_bytes` 是压缩后大小，规模一律看 `size_bytes_decompressed`（实测 6.8 MB → 13.4 MB）。

⚠️ **行数不是规模。** `text-chunks`/`reasoning-chunks`/`tool-call-chunks` 是流式 delta 的
打包行（信封放大 ~56×），内容会在收尾的 `assistant/message` 里再出现一次已组装版本。
计入叙事流 = 重复计数（与 Claude Code 重发压缩块同型）。适配器计为 `ui_noise` 并保留
`n_deltas`/`span_ms`。实测 `session-22dbccff`：chunk row 22210 行 vs assistant/message 263 条。

⚠️ **`workspace.json` / `session_projcache.json` 只列根会话，子 agent 一个都没有。**
实测派生存储 9 个 vs 磁盘 19 个，缺的 10 个全是子 agent（父 27692 行 / 四个孩子共 9040 行）。
拿派生存储当会话清单会漏掉约 1/4 的工作量且看起来完整——这就是标准 B 类。遍历目录。
子 agent 与父会话**同目录**，只有 header 的 `delegationDepth > 0` 能区分（裸 uuid 目录名是惯例，不是契约）。

✅ **ambient 在本平台是结构事实。** `user/message.source.kind` 直说这条 user 角色消息为何存在；
只有字面 `user` 是人。`MessageSourceMap` 声明为 merge-extensible，集合开放不可穷举，
所以规则是「非 `user` 一律视为注入」——把新 kind 误判成注入只丢信号，误判成人则把 harness
文本抬成 Owner 意图。实测 34 条 user 角色消息里只有 11 条是人，其中 10 条 `goal` 是 agent
拿自己的目标再驱动自己（`round` 最高 7，计入 `self_pump_rounds`，**不**并入 `pump_share`）。

映射：

```
session（header）                          → session_meta（delegation_depth 在 extra）
session/end-seed                           → session_meta   ← 之前的事件来自 seed，不是本次干的
user/message                               → user_msg（extra.ambient 来自 source.kind）
assistant/message                          → thinking / assistant_msg + token_count
tool/call → tool_call     tool/result → tool_output
turn/end completed|error|其它              → turn_complete | turn_error | turn_aborted
llm/retry                                  → turn_error
compaction/summary|prune                   → compaction（带 shadowed_tokens / shadowed_events）
compaction/start|end                       → ui_noise（括号，用于配对：未配对 = 没关上的压缩）
goal/change → ui_noise(goal_change)        approval/decided → ui_noise(approval:<decision>)
text-chunks|reasoning-chunks|tool-call-chunks → ui_noise（回声，不是物证）
```

平台差异要点：

- **能力位比其它三家都全**：`compaction`/`turn_lifecycle`/`context_window` 全为真，
  外加三个独有位 `compaction_loss` / `structural_ambient` / `seed_boundary`。
- `compaction/summary` 带 `shadowedSeqs` / `shadowedRange` / `shadowedTokenCount`：
  **压缩损失是读出来的，不是估出来的**。这是本 skill「压缩致盲」主张唯一可测量的平台。
  其它平台 `shadowed_*` 必须报 `null`。
- 未配对的 `compaction/start` 不是压缩，是没关上的压缩 → `compactions_unclosed`；
  `end.error` → `compactions_failed`。两者都不许计进 `compactions`。
- 适配器**必须有状态**：`request/context` 只在路由或容量变化时记一条，无状态解析会让
  第一条之后全部 `window=None`。因此 dsh 走 `_ADAPTER_FACTORIES`，每次 `iter_events` 一个新实例。
- token 占用 = `usage.inputTokens + cacheReadTokens`；output/reasoning 是生成不是占用。
- `subagent/descriptor.provider` 是**孵化方式**（spawn/fork），模型在 `agentProvider`/`agentModel`。
- 补丁工具 `{edit, write, str_replace_editor}`，路径键 `file_path`；exec 工具 `{bash, pwsh}`。
- `progress_probe` 的字节尾窗对压缩文件无效（帧中间无法按字节定位），已改为整流；
  dsh 因为打包 chunk 所以日志相对内容很小，代价可接受，但冷启动窗口确实无界。
- 读包内 `.d.ts` 求证时必须 `grep -R`：pnpm 包目录是符号链接，`grep -r` 不会进入，
  返回空 ≠ 不存在（本次调查已被骗过一次）。

## 适配器变更闸

⚠️ 修改 `iter_events` 或任何平台适配器后，既有代表会话的指标必须逐项对齐。
重构测量仪器却不校准，正是本 skill 反复检出的 B 类病
（仪器变更使全部历史证据失效）。**改完 `iter_events` 后，下表三个会话的指标必须完全一致，
否则视为回归。**

> 修订（2026-08-25，dsh 接入时发现）：本闸此前写的是「必须与本文档记录的数值完全一致」，
> 但**本文档从未记录过那些数值**——闸引用了一个不存在的东西，自己就是一条 B 类裂缝，
> 而且三次适配器改动都没人执行得了它。现补齐基准；今后改基准必须连同 commit 一起改。

⚠️ dsh 的**压缩计量路径没有真实语料**：本地 19 个会话全部跑在 1M 窗口内，一次都没压缩过，
所以 `compaction/*` 的映射来自包内 `.d.ts` 契约而非观测。该路径改由合成夹具钉住：

```bash
python3 scripts/selftest_dsh.py     # 非零退出即回归
```

夹具同时写**两个追加 zstd 帧**——单帧解码器只读第一帧正是本平台最危险的失效模式，
`lines` 读成 5 而不是 14 就会当场暴露。改契约时改夹具，**绝不为了让它过而改**。

### 回归基准（2026-08-25 实测，dsh 接入前后逐字节一致）

```
Codex        ~/.codex/sessions/**/rollout-2026-07-23T19-22-31-019f8eb6-….jsonl   (72 MB)
  lines=12869  compactions=18  execs=1617  patches=446 over 175 files  session_meta=124
  assistant=350 reasoning=2444   user: substantive=52 pump=3 resume=4 ambient=8
  turns: complete=36 aborted=7

Claude Code  ~/.claude/projects/-Users-<name>-<project>/<id>.jsonl           (52 MB)
  lines=7506   compactions=6   execs=825   patches=130 over 73 files   session_meta=0
  assistant=512 reasoning=1007  user: substantive=166 pump=9 resume=10 ambient=92
  turns: complete=181 aborted=0 retried-api-errors=106

Kimi         ~/.kimi-code/sessions/**/main/wire.jsonl                             (5.6 MB)
  lines=3084   compactions=None（不是 0）  execs=102  patches=11 over 5 files  session_meta=1
  assistant=764 reasoning=1015  user: substantive=85 pump=0 resume=0 ambient=105
  turns: complete=3 aborted=0

dsh          ~/.dsh/sessions/**/session-22dbccff-….jsonl.zstd   (6.8 MB 压缩 / 13.4 MB 解压)
  lines=27692  compactions=0  compactions_unclosed=0  execs=178  patches=112 over 18 files
  session_meta=3（1 header + 2 次 end-seed）  assistant=157 reasoning=219
  user: substantive=10 ambient=23  self_pump_rounds=10 (max_goal_round=7)
  turn_errors=6（llm/retry 4 + turn/end error 2）  window=1000000 peak=528475
  goal_changes=5  sub_agents: c4ad3631×2, 65de0161×1
```

跑法（逐平台比对第 5–7 行即可，不必全量 diff）：

```bash
python3 scripts/session_metrics.py <session> | sed -n '5,7p'
```

## 用户的话藏在哪（ledger.py 的依据，2026-09-29 实测）

- Claude Code：中途插话不是 type=user，是 `attachment.queued_command` 且 `origin.kind=="human"`；
  AskUserQuestion 的回答在 tool_result 里（以 `The user answered:` 开头）；拒绝工具调用也在 tool_result 里。
  `isMeta` = harness 注入的 skill 全文；`isCompactSummary` = 压缩摘要；`queue-operation` 是同一句话的 UI 回声。
- Codex：人话在最后一个 `## My request:` 之后；`<send_user_message_question_reply>` 是结构化回答。过滤掉的每类都计数写在 ledger.md 表头。
