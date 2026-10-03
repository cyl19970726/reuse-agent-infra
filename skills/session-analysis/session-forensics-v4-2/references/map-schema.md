# 任务地图 map.json：字段和例子

一份地图描述**一段 agent 工作**（一个或多个会话），并用它回答**一个问题**。例子：`examples/map.example.json`
（由 `examples/synthetic/` 里的合成会话和合成仓库建出，渲染为 `examples/report.html`、`examples/map.md`）。
骨架由 `ledger.py --skeleton` 生成，在骨架上补；结构由 `scripts/map_check.py` 检查。下文例子取自那个虚构项目：给书架小应用加 CSV 导入。

## 通用约定

- **证据位置**：`A:20`、`A:4-9`（会话简称:行，简称在 `sessions` 里定义）；`file:<路径>:<行>`（仓库文件，相对顶层 `repo`）；
  `site:<序号或 id>`（现场核对的一项）。`file:` 和 `site:` 是外部证据。
- 能放证据的字段：`at`、`span`、`refs`、`basis_refs`、`also`、`confirm_refs`。`refs` 里可以放 `{"at": "A:21", "says": "原文摘录"}`，
  `map_check --verify` 拿摘录对原文；节点的 `quote` 对应它的第一个位置，同样核对。
- `basis`：`物证`（默认）或 `说法`。`map_check --verify --apply` 把原文对不上的、以及「做到哪」（`headline.progress`、过程、产物）
  只引用 agent 的话或压缩摘要的节点改成 `说法`。交付物就是 agent 那条消息时，写 `basis: "物证"` 加 `basis_note`，核对跳过它。
- id 全图唯一：请求 R、发起时说的 I、目标 G、计划 K、步骤 S、产物 X、问题 P。编号只在正文和 map.md 里出现，首屏写人话。

## 顶层

| 字段 | 必填 | 说明 |
|---|---|---|
| `schema` `title` `question` `updated` | 是 | `task-map/1.2`；标题一句话；`question` 用提问人的原话；时间 ISO |
| `answer` | 是 | 首屏的答案，见下 |
| `sessions` | 是 | `[{alias, path, platform, when, ledger_through, read, state, note, role}]`；`role: "origin"` 是发起查看的会话 |
| `headline` | 是 | 主线 `alignment` + 四格 `want` / `progress` / `problem` / `next`（每格 `{text, refs}`）|
| `requests` `goals` `next` | 是 | 见下 |
| `resources` | 该有 | 现成资源清点（`inventory.py`），没有相关的写 `[]`；缺这个字段 map_check 提醒 |
| `repo` `subject` `origin` `plan` `layers` `process` `assets` `problems` `collab` `questions` `site` `method` `update` `ext` | | 见下 |

## answer：首屏的答案

```json
"answer": {"type": "progress",
 "lead": "解析和去重做完了，测试 4 个全过，但没有一个测试导入重复的书；页面按 agent 猜的流程改了，你还没确认，浏览器里也没人看过。现在停在等你定预览列。",
 "points": [{"label": "主线", "text": "解析和去重跟着计划走完；页面这一步停下来等你拍板。", "refs": ["A:37"]},
            {"label": "第一件事", "text": "你回答导入流程是不是「上传 → 预览 → 确认」、预览表显示哪些列。", "refs": ["A:30"]},
            {"label": "现场核对", "text": "测试重跑 4 个全过，其中没有测重复的书；页面没人在浏览器里看过。", "refs": ["site:1", "site:2"]}],
 "missing": ["页面没在浏览器里看过：agent 自己说还没验证，之后只请求了接口状态码。"]}
```

- `type` 只有五种，必备项见 SKILL.md 的表：`progress` `handoff` `distill` `audit` `process`。看懂项目的模型用 `distill`。
- 必备项由 `label` 恰好是这个词的 point 满足；`flows`（≥2 张）/ `table` / `watch` / `insights` 查对应部分非空；
  `主线` 也可以由 `headline.alignment` 满足，`现场核对` 也可以由 `site` 满足。外部 skill 可以写 `must` 换掉默认清单。
- `lead`：一到三句，直接回答 `question`；发起查看时用户说了最在意什么，要正面回应。
- `missing`：答案自己知道缺的。agent 自述的缺口只有和这次答案直接相关、核过确实没补上的才写进来。
- `insights`（提炼）：`[{text, from: ["P1", "R3"], state, refs}]`，`from` 必填（页面显示成它们的文字），`state`：`verified` / `candidate` / `superseded`。
  问「看懂项目的模型」时，第一条写核心模型，用项目自己的说法；`refs` 同时指向说它的文档和实现它的代码，对照过实现、一致才 `verified`，
  对不上的写进 `missing`。
- `flows`（改流程）：`[{title: "现状", steps: ["解析", {"text": "问去重规则", "mark": "new"}]}, {title: "目标", …}]`；`mark`：`new` / `changed` / `cut`。
- `table`：`{title, cols, rows}`，改流程时列为 改动｜依据｜谁读｜成本｜何时删，最多 3 行。`watch`：下一轮复盘看哪几个数，每条写清怎么数。

## resources：现成资源

```json
"resources": [{"what": "一个导入类功能的 skill（shelf-import）", "path": ".claude/skills/shelf-import/SKILL.md", "used": "no",
  "relevant": true, "says": "导入按三段做：先要样本、去重先问、改页面前确认流程", "note": "三段正好对上这次的三个问题",
  "refs": ["file:.claude/skills/shelf-import/SKILL.md:3-5"]}]
```

- 从 `inventory.py` 的输出里挑：和这次工作直接相关的写 `relevant: true`，看过、判断无关的写 `relevant: false`。`relevant` 必须是 true 或 false。
- 对账：地图目录里有 `inventory.json` 时，其中没用上的 skill、工作流，以及和你的话有重合的其他资源，每份都要在 `resources` 里有一条，
  否则 map_check 提醒。`inventory.py` 没给 `--repo` 时取会话的工作目录，推不出来就说清点不完整，这时先找到仓库。
- `used`：`loaded` 加载了 / `auto` harness 自动加载 / `read` 读过 / `mentioned` 只被提到 / `no` 没出现。
- `relevant: true` 且 `used` 是 `mentioned` 或 `no` 的，首屏自动写一句「项目里有 X，这次没有用上」，`note` 跟在后面。

## requests：用户到底说过什么

```json
{"id": "R5", "at": "A:29", "quote": "你先搞清楚我要的导入流程是什么样的，再改页面。", "kind": "correction",
 "clarify": true, "goal": "G3", "status": "partial", "basis_text": "agent 复述了一个流程就改页面，你没确认过",
 "basis_refs": ["A:30"], "also": ["A:34"], "also_ids": ["R6"]}
```

- `kind`：`request` / `correction` / `question` / `approval` / `interjection` 中途插话 / `answer` / `command` / `interrupt`。ledger 给的是猜测，要改对。
- **归属必做**：每条要么写 `goal`，要么被某个目标的 `requests` 列出（可以写区间 `"R10-R40"`）。账本里每条人话都要在 `at` 或 `also` 里。
- `status`：纠正、插话、打断逐条判断，其余可以随目标；只把 `open` / `partial` / `dropped` 标出来。写状态时给 `basis_text` + `basis_refs`。
- **`clarify`**：要 agent 弄清用户意图的请求，只能由用户确认来闭合：`done` 的依据里要有这条之后、用户说了实质内容的一行；
  否则 `map_check --verify --apply` 改成 `partial`。批准类（「继续」）已折进前一条的 `also`，编号记在 `also_ids`。

## goals：用户要达成什么

```json
{"id": "G1", "level": "purpose", "text": "把已有书单一次搬进书架，不产生重复的书", "requests": ["R1"], "from": "said",
 "refs": ["O:1", "file:.claude/skills/shelf-import/SKILL.md:6"], "status": "partial",
 "ai": {"text": "把导入当成解析问题：先让解析测试全绿", "refs": ["A:2", "A:10"], "gap": "你最在意不重复，计划里却没有去重"}}
```

- `level`：`purpose` 长期目的 / `stage` 阶段目标 / `task` 本轮任务。`from` 必填：`said` 你说的（出处：请求、`O:` 行，或写着这个意图的
  资源 `file:路径:行`）/ `inferred` 推断的（写 `ask` 当反问候选）/ `confirmed` 你确认过的（写 `confirm_refs`）。
- `ai`：AI 对这个目标的理解（它以为自己在追什么）和 `gap` 差在哪；没有偏差可以不写。

## plan、process、assets

```json
"plan": {"refs": ["A:2", "A:3"], "now": "K2", "steps": [
  {"id": "K1", "do": "写 CSV 解析", "goal": "G1", "state": "done", "via": ["S1", "S2", "S3"]},
  {"id": "K2", "do": "接到导入页", "goal": "G3", "state": "waiting", "via": ["S5", "S6"]}]}
```

- 计划取自用户给的步骤或用户批准过的 agent 计划。`state`：`done` / `doing` / `waiting` 等你拍板 / `todo` / `dropped` / `changed`。
- `now`：当前主线那一步。**主线判 `waiting` 的条件**：`now` 那一步是 `waiting`（没写 `now` 时：有 waiting、没有 doing），工作确实停着。
  别的步骤在等、眼下的工作不受影响，不判 waiting。`via`：执行这一步的过程步骤；不被任何 `via` 指到的是计划外的。
- `process`：`{id, span, layer, title, did, result, problems, refs}`。`layer` 只是时间线的纵轴，同一层连着几步不等于打转。
- `assets`：`{id, what, path, status: current|stale|superseded|missing, refs}`。

## headline.alignment、problems

- `alignment.state`：`on_track` 在主线上 / `stuck` 陷在局部（同一个问题打转、计划外步骤连着出现）/ `drifted` 偏离了目标 / `waiting`。
  `text` 一句话说为什么，不重复状态词。`on_track` 但有没解决的问题时，页面显示黄色的「过程在主线，结果有偏差」。
- `problems`：`{id, title, symptom, root, root_kind: input|judgment|norm|tool, root_basis: 物证|推断, resolved: yes|partial|no, same_root, refs}`。
  打转 = 同一个问题连续 ≥3 次尝试、结果没稳定变好：写 `spin: {steps, note}`；试过多次但不算，写 `not_spin: "理由"`。同一种失败只写一个问题。

## 其余

- `origin`：`[{id: "I1", at: "O:1", quote, use}]`，发起查看时用户说的话。不进请求账本，建目标时当意图证据引用。
- `collab`：`{"user": [{text, better, refs}], "agent": [{text, better, refs}]}`，两栏，对事不对人。
- `next`：`[{do, for: ["G3", "P2"]}]`。`questions`：≤3 个，`{q, why, default, for: [答案类型], goal}`，`default` 必填；
  写了 `goal`（问一个推断的目标）的在任何答案的首屏出现；写了 `for` 的只在那几类答案的首屏出现；都没写的在进度 / 接手 / 审计的首屏出现。
  驱动工作的 `purpose` / `stage` 目标是 `inferred` 而没有对应反问，map_check 提醒。
- `site`：`{checked, items: [{id, what, state, how}]}`，地图是历史，现场是现在。
- `method`：`{how, checks, cost, unread: [{what, why}]}`，写读者能懂的话；请人核过的写核了什么、改了什么。
- `update`：`{since, story, changes}`，见 `references/update.md`。
- `ext`：外部 skill 的其余决定，`[{by, title, place: top|process|problems|end, items: [{label, text, refs}] 或 cols + rows}]`。
  首屏必备项写进 `answer`，不写进 `ext`。
