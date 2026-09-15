---
name: session-forensics
description: |
  给 agent 会话配「观察者」：读完整会话历史（Codex rollout JSONL、Claude Code transcript、
  Kimi wire、DeepSeek Harness / dsh 的 zstd 会话，含 subagent/team 日志），
  用「物证流 vs 叙事流」对质，找出执行 agent 自己
  原理上看不见的问题。四种能力：① audit——审计求解过程是否病态（空转、卡住、目标漂移、
  gate 膨胀、修工具循环、假通过）；② handoff——交接包（压缩/中断/换人/跨 CLI 时，
  --check 闸保证完成度）；③ progress probe——运行中探针（silent worker / subagent 现在
  在干什么，秒级判定，防空等）；④ harvest——沉淀（从历史挖出该固化成 skill/gate/harness
  的东西）。当出现：读 session 历史、复盘会话、审计另一个 agent、为什么进度这么慢、
  为什么一直失败、agent 卡住了、subagent 在干嘛、compaction 之后丢了什么、
  要交接给下一个 agent、~/.codex/sessions、~/.dsh/sessions、rollout jsonl、session.jsonl.zstd、
  dsh 会话、deepseek harness 历史、agent 复盘 —— 时使用本 skill。
  ⚠️ 不要用向量检索/RAG/agent-memory 读 session（实证判死，见 §禁止事项）。
---

# Session Forensics

把 agent 会话当**物证记录**读，不当聊天记录读。

## 心智模型（一分钟版；完整版见 references/mental-model.md）

一份会话里有两条流：

```
叙事流  reasoning / assistant message / plan / 摘要 / compaction summary   = 辩护，永远自洽
物证流  exec / apply_patch / 测试输出 / 耗时 / 失败 / commit / 用户纠正     = 事实，不自洽但不说谎
```

**所有失败都是两条流之间的裂缝。裂缝是本 skill 唯一的一等对象。**

```
A 类 时序裂缝   宣称 vs 自己的动作        局部可查    原理上可自察（虽难）
B 类 代际裂缝   引用 vs 被引用物的现状     全局才可查   原理上不可自察   ← 本 skill 存在的理由
```

B 类通用形式：**引用了一个已不再成立的东西，而系统没有失效传播机制**（cache invalidation 缺失）。

执行 agent 看不见 B 类，三条互相独立的机制，任何一条单独成立就够：
**判据封闭性**（计划里没有"计划错了"这个类别，每次失败都被解释成"还差一步"）、
**类型不足**（计划类型是 `[Action]`，风险住在 `Action → PastConclusions → Invalid`，计划制定时
结论集为空）、**压缩致盲**（实测 ~200:1，摘要保留"做了什么"、丢弃"做了多少次/在几个副本里"，
而后者就是全部病理信号）。

→ 外部 agent 的不可替代性 = **工作集是全局的**。这是复杂度分工，不是"第二双眼睛"。

## 唯一不变量

> **任何结论都必须由物证流支撑；叙事流只能作为「待质证的宣称」进入分析。**

"读两遍"是手段不是目的。禁止的是"只读上一遍的摘要"，不是"没读完全文"。

## 上下文边界纪律（违反即失败）

```
GB 级物证  →  [脚本进程内消化]  →  KB 级指标  →  [上下文]
```

**任何原始 JSONL 不许穿过这条边界。** 绝不用 Read 打开 rollout 文件——它们最大 1.5 GB。
实测：读 72 MB session 只耗约 4 KB 上下文（~20000:1）。钻取按行号取窗口，每次 ≤50 行。

单次成本红线（实测数字与案例见 signatures.md §单次成本速查）：
**`read_thread`** 单次 ≈138 万 token，只许有界钻取；**`imagegen`** 均值 225 万字符/次、
**`view_image`** 未降采样 ≈30 万 token/次，图一律落盘取路径、看图先降采样；
**`--watch`/`tail -f` + `write_stdin`** 是 O(n²)，等待用有界轮询。
排洪水源按 **per-call 成本**，不按 share（share 已降级，见 `local/falsified.md`）。

## 结构：探针 + 装配

```
探针（可独立跑、可组合）          装配
  P1  裂缝 / 失效边检测            audit report   = P1 (+P3) (+P2)
  P2  可复用序列提取（沉淀）        harvest report = P2
  P3  目标演化追踪                 handoff packet = S + P1 + P2 + P3   ← 超集
  S   状态重建（完整版仅 handoff）
```

## 流程

先把 skill 根目录设为绝对路径：`SESSION_FORENSICS_DIR="<absolute path>"`。

### 0. 定位

```bash
python3 "$SESSION_FORENSICS_DIR/scripts/session_locate.py" <thread-id>
python3 "$SESSION_FORENSICS_DIR/scripts/session_locate.py" --since 2026-07-24 --min-mb 20 --deep
```
按最便宜优先搜：文件名 → 索引 → 时间窗+体积 → 文件头。索引覆盖不全，未命中≠不存在。
不要对全库跑 `rg`。

### 1. 本地校准（首次必做一次）

```bash
python3 "$SESSION_FORENSICS_DIR/scripts/calibrate.py" \
  ~/.codex/sessions ~/.codex/archived_sessions ~/.claude/projects \
  ~/.kimi-code/sessions ~/.dsh/sessions
```
⚠️ `calibrate.py` **覆盖** `local/baseline.json`：漏传一个语料根 = 用缺项基线读全部会话。
现有基线生成于 dsh 接入之前，dsh 会话的分位数在重跑之前不可解释。
基线、不满句式、洪水画像都是**环境属性不是通用常数**，必须本地生成到 `<skill>/local/`。
**用别人的基线读自己的会话，正是本 skill 要检测的 B 类裂缝。** 解读一律用本地分位，
绝对值不可解释。权威源/派生副本规则见 self-upgrade.md §分发层。

### 1.5 配置层先读（审 repo 里的 agent 时必做）

```bash
cat <repo>/CLAUDE.md && ls <repo>/.claude/skills/
```
**transcript 回答「做了什么」，答不了「有什么可用」**——harness 注入面（CLAUDE.md/memory/skill）
在 transcript 里以 ambient 出现且被主仪器过滤。
⇒ 凡结论形如「它没有 X / X 不在它的路径上」，必须来自配置层直接读取，不得从 transcript 的
缺席推断。跳过此步已产生 2 次错误结论（G26）。

### 2. 测量（零阈值假设）

```bash
python3 "$SESSION_FORENSICS_DIR/scripts/session_metrics.py" <session.jsonl> --json-out /tmp/metrics.json
```
多会话可一次传入横向对比。**必须绝对路径**。

### 3. 首尾一刀（audit 先做这个，成本近零）

`objective_trace.first_substantive`（初始目标）和最后 20% 的动作放一起：**还有关系吗？**

```
最终目标 ≠ 初始目标
  ├─ 变更可追溯到某条 user message → 合法演化，动作是「重新对齐」
  └─ 追溯不到                      → agent 自漂，动作是「纠正」
```
user message 是唯一来自系统外部的物证，叙事流吸收不掉它。**用户改需求本身是一条失效边**。
⚠️ **「重新对齐」是动作不是标签**：判出"合法演化"就停手 = 只做了诊断，任务没续上；
必须用**当前目标**重排后续工作（实证见 handoff-packet.md 修订记录 #6）。

### 4. 提供证据，不做判定

给出 top-N 异常项及其**本地基线分位**（见 references/signatures.md）。
⚠️ **本 skill 不是分类器**：n=101 验证，没有指标能预测"会话好坏"（全部 |r|<0.22）。
指标说清**发生了什么**，好坏由读者判。
⚠️ 定性签名"发生即命中"≠"脚本会告诉你"——签名表有**检测层**一列（代码/agent/代码→agent），
读表前先看那一列，否则把"要人判的"当成"脚本会报的"，这正是无证宣称本身。

### 5. 有界钻取

```bash
python3 "$SESSION_FORENSICS_DIR/scripts/drill.py" <session.jsonl> --grep "rsync -a" --limit 10
python3 "$SESSION_FORENSICS_DIR/scripts/drill.py" <session.jsonl> 664:700 --cap 400
```
**用 drill.py，不要手写 jq/sed/python 一次性脚本**——它把 ≤50 行/窗口、≤3 窗口/会话从纪律
变成拒绝执行。（由来：同一取窗口操作曾长出 4 个互不相同的副本，见 self-upgrade.md §瘦身迁移。）

### 5.5 进度探针：silent worker 在干什么（运行中用法，防空等）

```bash
python3 "$SESSION_FORENSICS_DIR/scripts/progress_probe.py" <worker-session.jsonl> [--after-line N]
```

```
"有没有发生事情？" → 控制面（harness wait / 任务通知）
"它现在在干什么？" → 本探针（控制面答不了）
```

```
PRODUCING 有编辑→别打扰   DEAD 无写入无记录→查进程   WAIT_LOOP 空轮询≥3→它在等别人
REPEATING 同调用≥5→先drill再steer   FAILING 失败≥50%零编辑→考虑steer
INVESTIGATING 早期正常→晚点再探   IDLE 游标后无新记录→别重发任务   UNCLEAR→用drill看
```
判据带 owner 闸（poll 只认 wait 类输出、fail 只认 exec 类输出，正在读错误文档的健康 worker
不会被误判）。成本实测 392 MB / 1.5 s；探针打在**最近的窗口**上（`--after-line` 增量）。
只答"是否病态"，不答"怎么修"。

### 6. 终点是一个动作，不是报告

```
继续 / 干预（回到第 K 个决策点）/ 升层（问题被误当成执行问题）/ 停止（交回给人 + 那个必须由人回答的问题）
```

### 7. 交接与晋升

```bash
python3 "$SESSION_FORENSICS_DIR/scripts/handoff_packet.py" <session.jsonl> --out handoff.md
python3 "$SESSION_FORENSICS_DIR/scripts/handoff_packet.py" --check handoff.md   # 交接闸
```
脚本填物证节，判断节留桩。**交接前必须过 `--check`**：§0/§1-P3/§6/§7/§8/§10 残留 TODO 即
REFUSED（三次事故后按晋升梯落闸）。§6 由脚本从不满句式种子**预填待质证候选**（离场 agent
凭记忆列自己的错误 = 本 skill 判为不可能的自察）；§5.5 内嵌 P2 操作剖面（兑现超集公式）；
执行 agent 已死用 `--post-mortem`（§3/§9 标不可得）。全部由来见 handoff-packet.md 修订记录 #8–11。

沉淀：

```bash
python3 "$SESSION_FORENSICS_DIR/scripts/harvest_report.py" <session.jsonl> --out harvest.md
```
⚠️ 晋升判定绑**失败**不绑"成功"（绿测试与 repo_commit 两种成功凭据都已被证伪）。

```
观察 1 次                  → handoff packet
跨会话复发 2 次             → 失效边 / gate      （禁止性："别做 X / X 之后 Y 失效"）
复发 ≥3 次且有稳定操作序列   → skill（+harness）  （生成性："要做 Z 就按此序列"）
复发但根因是「缺一个名字」    → 技法 / 命名        （消解性：给弱档一个合法名字）
```
第四档判别：复发是"有人做了不该做的事"还是"有人没有词可用"？后者立闸只会既拦不住又让法典
更大——立新档比立新闸便宜，从源头消灭借词（三例实证见 references/handoff-packet.md 与 gates G25）。

### 7.5 收尾：改完 skill 必须传播（不跑等于没改）

```bash
bash "$SESSION_FORENSICS_DIR/scripts/sync_skill.sh" [--check | --commit "<msg>" --push]
```
此维护脚本仅适用于明确采用三副本同步的环境，运行前须显式设置 `SESSION_FORENSICS_SRC`（源技能目录）、`SESSION_FORENSICS_CODEX`（已授权的目标技能目录，优先项目本地）和 `SESSION_FORENSICS_REPO`（发布仓库）。仓库相对路径默认 `skills/session-analysis/session-forensics`，可用 `SESSION_FORENSICS_SUBPATH` 覆盖。全局写入仍需用户明确授权；普通使用技能无需运行同步脚本。脚本依赖 macOS `md5`、`rsync` 和 Git。脚本逐关拒绝：编译闸 →
rsync → 全树 md5 三副本比对 → local/ 泄漏检查 → 发布态闸（repo 脏 / HEAD≠origin/main 即
REFUSED）→ push 后校验。（同一失效边曾复发四次，见 self-upgrade.md §瘦身迁移。）

### 8. 自我升级（每次使用后必做）

读完任何会话，给结论**之前**答五问：新洪水源/新签名？阈值被反例推翻？数字不合理（先疑解析
bug）？有东西复发第 2 次（立即晋升 gate）？**我上一轮的建议造成了什么？**
有则按 references/self-upgrade.md 路由写回。**不写回 = 知识只留在叙事流里，下次归零。**
两条硬规则（由 1200×1194 事故逼出，见 self-upgrade.md）：
①任何「减少 X」的建议必须同时写出「X 的下限由什么决定」，否则不得发出；
②引用自己的文档必须读完整节——只读半段 = 压缩致盲长在自己身上。

## 禁止事项

- **禁止解题。** 只答"求解过程是否病态"，不答"bug 怎么修"——一解题就掉回序列空间。
- **禁止 RAG / 向量检索 / agent-memory 读 session。** 实证判死：向量只能嵌叙事流（自然语言），
  而价值在物证流（改了 12 次、跑了 39 次、分叉 4 副本）——它和 compaction 犯同一个错误。
- **禁止绝对计数下判断**（已被反例推翻两次，一律用 rate）。
- **禁止把 dossier 当第二遍的输入**（dossier 本身已是新叙事流）。
- **不足 3 次复发不晋升为 skill**（防过早抽象）。

## 质量闸（不满足则分析作废）

结论只有编年史没有裂缝 / 用户目标从 assistant 摘要推断 / "通过"未绑 revision 与验证表面 /
遗漏 reviewer·subagent 反证 / 失败无隐藏假设与预防机制 / 答不出"当前路线为何存在、备选为何被否"。

⚠️ **G27 审计交付四节闸**（由 `CASE-N` 审计连续两轮只交指标编年史、错误链靠用户追问才产出而晋升，
2026-08-27）。本闸此前只有法条没有触发点——正是 G25「法条被加载但未被路由到注意力」长在本 skill
自己身上（"若这次忘了点名，谁会挡住违反者？没有人"）。因此 **audit 报告交付前必须逐条写出，缺一节
即 REFUSED**（与 handoff `--check` 同等强度，人肉执行）：
① **目标合同**：哪几条 user message（行号+原文），不得从 assistant 摘要推断；
② **执行偏差**：执行形态与合同的最大裂缝是什么；
③ **错误链 ≥1 条**：每个错误绑定物证行号 + 换算成本（返工轮次/小时/被剥夺的决策）；
④ **升层点**：它本该在哪一步停下来问人，而实际没有。
脚本产出的 rates/flood/基线是**输入不是交付物**——只交指标表 = 把"要人判的"当成"脚本会报的"，
这是审计员自己的便宜路径终点陷阱：脚本付完成本的地方，恰是判断刚要开始的地方。

## 支持的平台

| 平台 | 状态 | 存储 | 能力缺口 |
|---|---|---|---|
| Codex | 已实现 | `~/.codex/sessions/**` + `archived_sessions/` | 2025-08~09 老格式判 `codex_legacy` 显式拒绝（codex-jsonl.md） |
| Claude Code | 已实现 | `~/.claude/projects/<cwd>/<uuid>.jsonl` | 无 context_window；轮次生命周期近似；**subagent 在 `<session-dir>/subagents/` 独立文件，必须遍历** |
| Kimi Code | 已实现 | `~/.kimi-code/sessions/**/wire.jsonl` | **无压缩标记**（报 `null` 不报 0）；子 agent 独立文件 |
| DeepSeek (dsh) | 已实现 | `~/.dsh/sessions/**/session.jsonl.zstd` | **唯一压缩存储**（多帧 zstd，缺 `read_across_frames` 会静默只读首帧）；行数≠规模（chunk row 是回声）；派生存储只列根会话，子 agent 全靠 header `delegationDepth>0` |

格式自动探测；平台不支持的指标返回 `null` **绝不返回 0**。详见 references/platforms.md。

## 脚本

| 脚本 | 用途 |
|---|---|
| `session_locate.py` | 定位会话（**先用它，不要 `find`**） |
| `calibrate.py` | 本地基线（首次必跑） |
| `session_metrics.py` | 主测量，GB → KB |
| `drill.py` | 有界钻取，硬闸 ≤50 行/窗口 |
| `progress_probe.py` | 运行中探针，定长输出 + 游标 |
| `handoff_packet.py` | handoff 装配 + `--check` 交接闸 + `--post-mortem` |
| `harvest_report.py` | harvest 装配（per-call 成本 + gate 违反计数） |
| `sync_skill.sh` | 三副本传播 + 发布，逐关拒绝 |
| `selftest_dsh.py` | dsh 未被语料覆盖的路径（压缩计量/多帧/source.kind）合成夹具自测 |
| `session_events.py` | 跨平台规范化事件层 |
| `batch_scan.py` `confirm_patterns.py` | 阈值标注集 / 本人不满句式库 |

## 参考

- `references/mental-model.md` — 完整心智模型、失效表、RAG 为何必然失败
- `references/signatures.md` — 签名表、基线、洪水画像、单次成本速查、已证伪、解析陷阱
- `references/self-upgrade.md` — 五问、写入路由、分发层规则、瘦身迁移存档
- `references/platforms.md` — 多平台适配与字段映射
- `references/codex-jsonl.md` — Codex rollout 字段真值表
- `references/dsh-jsonl.md` — DeepSeek Harness 字段真值表（压缩层 / chunk row / source.kind / compaction 计量）
- `references/handoff-packet.md` — packet 模板与实战修订记录（#1–11）
