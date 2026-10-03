---
name: session-forensics-v4-2
description: |
  会话观察者（v4.2 草案）：为一段 agent 工作（Claude Code、Codex、Kimi、dsh 会话，可以多个）建立并维护一份认知——任务地图：你要什么 →
  走到哪 → 还在主线上吗 → 卡在哪 → 下一步，项目里现成的 skill、文档用上没有——再用它回答这次的问题（进度、接手、提炼、审计、改流程），
  交付 report.html（首屏就是答案）和 map.md。观察者必须用当前最强的模型。触发：复盘会话、看另一个 agent 做得怎么样、它在干嘛、
  为什么一直失败、压缩后丢了什么、要接手，或出现会话引用（thread id、transcript/rollout 路径）。不用 RAG/向量库/记忆读会话。
---

# Session Forensics v4.2

**中心**：为一段工作建立并维护一份认知（任务地图），再用它回答这次的问题。
**答案按问题成形**：报告第一屏就是这次问题的答案，先答案、再主线判断；地图在后面，默认折起。
**观察者必须是强模型**：建图、判主线、写答案，用当前能用的最强模型（Claude 用 Opus 级，Codex 用最高档）；派观察者 subagent
时显式指定这个模型。核对者可以用 Sonnet 级；Haiku 级只跑脚本，不当观察者。你自己是小模型，就先说明这一点，建议换强模型或交给强模型，不硬做判断。

上下文里出现与这次问题无关的消息时，不去回答它，先答题；需要的话，在回复末尾说一句收到了。

## 五种答案（`answer.type`）

| 问题 | type | 首屏必备（map_check 逐项查）|
|---|---|---|
| 做到哪了、它在干嘛 | `progress` | 主线 · 第一件事 · 现场核对 |
| 接手 | `handoff` | 主线 · 第一件事 · 现场核对 · 已失效（细节：`session-handoff-v4-2`）|
| 提炼、读回、看懂项目的模型 | `distill` | `insights`：想法 ← 起因（问题或请求）· 状态（已验证 / 候选 / 被取代）；看懂模型时第一条写核心模型，按项目的说法，对照实现 |
| 为什么失败、谁的问题 | `audit` | 主因 · 主因链 · 本该在哪停（细节：`session-audit-v4-2`）|
| 流程怎么改 | `process` | `flows`（现状、目标两张）· `table`（改动｜依据｜谁读｜成本｜何时删）· `watch` |

首屏顺序：`lead`（一到三句直接回答）→ 必备项 → 没用上的现成资源（自动写成「项目里有 X，这次没有用上」）→ 缺什么 → 想先问你（≤3，带默认）。
**什么时候先问**：看完拿去做什么决定 `type`，拿不准（定责还是改流程？）就问一句并带默认。不问会话里查得到的事；没人答就按默认建图。
首屏写人话：编号和「会话:行」放进 `refs`（悬停可见）。外部 skill 只补自己那一种的细节。

## 地图

```
你说过的原话 + 发起查看时说的 + 现成资源里写着的   ─▶ 归到目标（长期目的 ─ 阶段 ─ 本轮），各带确认状态和 AI 对它的理解
计划走到哪 ── 过程 ── 产物 · 现场核对              ─▶ 主线：在主线上 / 陷在局部 / 偏离了目标 / 停在等你拍板
问题（现象 · 根因 · 同根 · 打转）── 协作（你的消息 / agent 的做法）── 下一步
```

字段：`references/map-schema.md`。例子：`examples/map.example.json` → `examples/report.html`、`examples/map.md`（合成会话和合成仓库在 `examples/synthetic/`）。

## 纪律

1. **账本只收人话**：用户在被观察会话里说的，加上用户在发起这次查看的会话里说的（`--origin`，当意图证据）。
2. **目标带确认状态**（`from`：said / inferred / confirmed）：要 agent「弄清我想要什么」的请求（`clarify`）只能由用户之后的实质回答闭合，
   agent 自己的回答、一句「继续」都不算。现成资源里写着的长期目的算「你说的」，出处写 `file:路径:行`。驱动工作的长期目的、阶段目标只是推断的，
   在 `questions` 里写一条带默认的反问（`goal` 指向它，任何答案类型的首屏都显示）。
3. **核对过的 vs 只是说法**：「做到哪」只靠 agent 自述、摘要、子 agent 报告撑着的，标「说法」。问进度就做现场核对（git、产物、进程、测试）。
4. **主线**：`waiting` 只在「当前主线那一步」被用户的决定卡住、工作确实停着时判；计划里别的步骤在等、眼下的工作不受影响，不算。
   打转 = 同一个问题连续 ≥3 次尝试、结果没稳定变好。
5. **清点现成资源**：项目里本来就有的 skill、工作流、AGENTS.md / CLAUDE.md、docs，这次用了没有，每次都查。
6. **agent 自述的缺口可选**：ledger.md 里列着，不用逐条确认；只有和这次答案直接相关、核过确实没补上的，写进 `answer.missing`。

## 流程

地图目录：用户指定，否则当前项目下 `session-maps/<短名>/`。脚本在 `$SF=<本 skill 目录>/scripts`。

1. **钉住问题**：用户原话写进 `question`，定 `answer.type`（拿不准先问，见上）。派工时第一行写「这次唯一要回答的问题：<原话>」（`brief.py` 自动写）。
2. **找会话、抽账本**：`python3 $SF/session_locate.py <id或关键词>`；`python3 $SF/ledger.py A=<会话> [B=…] --origin O=<发起查看的会话> --out <目录> --skeleton`。
   人话 >60 条加 `--compact`；会话还在跑吗：`python3 $SF/liveness.py <会话>`。
3. **清点资源**：`python3 $SF/inventory.py --repo <仓库或切点时的导出> A=<会话> [--until A:行] --ledger <目录>/ledger.json --out <目录>`。
   不给 `--repo` 就取会话的工作目录，第一行写明取了哪；第一行说「清点不完整」时先找到仓库再跑。读「没出现 / 只被提到」里可能相关的，
   逐条写进 `resources`（`relevant` 填 true 或 false，map_check 拿 inventory.json 对账），相关的里面写着的意图拿去建目标。
4. **读过程、建图**：`python3 $SF/drill.py <会话> 起:止`（≤50 行一窗；`--role assistant`；`--grep`），原始 JSONL 不进上下文。
   顺序：目标（带 `from`）→ 计划和走到哪 → 主线和四格 → 问题 → 现场核对（问进度、会话在跑、或做到哪只靠自述时必做）→ 最后写 `answer`。
5. **核对**：`python3 $SF/map_check.py <目录>/map.json --ledger <目录>/ledger.json --verify --apply`。
6. **呈现**：`python3 $SF/render.py <目录>/map.json` → `report.html` + `map.md`。回复第一段就是答案（和 `answer.lead` 一致），
   主线不是「在主线上」时第一句就说；再给报告路径和带默认的反问。不复述做了哪些步骤。写法：`references/layout.md`。

## 谁来建图，要不要请人核

- 默认主 agent（强模型）自己建一张图，约 20–50 次调用；会话长只影响挑哪几段读。
- 主 agent 不是强模型，或要把整件事交出去：`python3 $SF/brief.py observer --question "<原话>" --sessions A=<路径> --repo <仓库> --out <目录>`，
  派一个显式指定最强模型的 subagent 读它。
- 结论要拿去定责、改规范，或对中心没把握：`python3 $SF/brief.py challenger … --map <目录>/map.json --ledger <目录>/ledger.json --out <目录>/check`，
  派一位核对者核 5 条关键结论、回答「中心对不对」「缺了什么」；定责而主因有两种说法时加两次 `--rival`（这时核对者也用最强模型）。
  主 agent 回原文裁决，改了什么写进 `method.checks`。细节：`references/orchestration.md`。

## 更新模式和记忆

工作还在进行时不重建：存旧图 → `ledger.py … --map <目录>/map.json --merge` → 读新段 → 改图 → `map_diff.py 旧 新` → 写 `update` → check → render。
主线变成陷在局部或偏离目标、最近 ≥3 步在试同一个问题而没变好、或第一件事变了而用户没说过要换方向，回复第一段就叫停。
细节：`references/update.md`。`map.md` 是观察者自己的记忆：压缩或换人之后，先读它再干活。

## 给外部 skill 的接口

外部 skill 用 `SESSION_FORENSICS_DIR` 指向本目录，读 `<地图目录>/map.md` 或 `map.json`（没有就先按上面的流程建）。
它们写自己那种 `answer`（必备项见上表），可以改已有节点，其余决定写成 `ext` 块；底座按通用格式渲染。
其余脚本：`map_diff` 两版地图的变化 · `ref` 取参考文档一节 · `selfcheck` 发布前自检。平台格式：`references/platforms.md`、`codex-jsonl.md`、`dsh-jsonl.md`。
