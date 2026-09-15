# 签名表与基线

## 使用方式

**只排序，不判决。** 输出「top-N 异常项 + 其在基线分布中的分位」，把判断留给读的人。
这既符合"审计员不解题"，也让结论不依赖尚未验证的阈值。

> ⚠️ **本文件中的所有具体数字都来自一个特定语料**（`~/.codex/sessions`，2026-07，单一用户）。
> 它们是**示例，不是常数**。基线、不满句式、洪水画像都是环境属性——换一个人、换一套工具链、
> 换一种语言，分布完全不同。**用别人的基线读自己的会话，正是本 skill 要检测的 B 类裂缝。**
>
> 首次使用请先跑本地校准生成 `<skill>/local/baseline.json`：
> ```bash
> python3 "$SESSION_FORENSICS_DIR/scripts/calibrate.py" \
>   ~/.codex/sessions ~/.codex/archived_sessions ~/.claude/projects \
>   ~/.kimi-code/sessions ~/.dsh/sessions
> ```
> ⚠️ `calibrate.py` **覆盖** `local/baseline.json`：漏传一个语料根 = 用缺项基线读全部会话。
> 这条命令与 SKILL.md §1 是同一条，改一处必须改两处（"同一教训只修一个载体"已犯过一次）。
> 之后只用**分位**解读指标，不用绝对值。详见 `references/self-upgrade.md` §分发层 vs 本地层。

## 示例基线（60 个 session，各取前 6000 行，2026-07，单一用户语料）

```
                 p50     p75     p90     p95     max
compactions       10      20      30      30      30
max patch/file     1       2      16      18      49
max repeat cmd     8      41     189     226     226
exec calls        89     864     921     953    1073
forked files       0       1       4       8      11
```

关键：`exec` 从 p50=89 跳到 p75=864（十倍），会话本身强双峰。
`maxcmd/exec` 归一化后 p50=0.09 → p90=0.21，**24 倍压缩到 2.3 倍**
→ **约 90% 的原始信号是"会话规模"，仅约 10% 是真异常。绝对计数一律禁用。**

## 定性签名（无阈值，发生即命中，判别力最强）

| 签名 | 检测 | 属类 | 检测层 |
|---|---|---|---|
<!-- 检测层：`代码` = 同一性/计数，确定性可全量；`agent` = 等价/意图判断，代码这层做不到；
     `代码→agent` = 脚本出料、判定必须由审计 agent 做。这一列是为了阻止
     「文档说发生即命中、实际要人读」这种无证宣称。 -->
| **仪器分叉** | 同一 basename 在 ≥2 个工作树根下被 patch（基线 p50=0） | B | 代码 |
| **仪器-产物共变** | 同窗口内 patch 同时命中 `tools/` 与业务路径 | B | 代码 |
| **证据代际断裂** | 证据产出时刻 < 其依赖仪器的最后修改时刻 | B | 代码（mtime/stat） |
| **定义分叉** | 同一术语在 ≥2 份权威文档中被独立编辑（需语义判断） | B | **agent**（表内自己写了「需语义判断」） |
| **目标失联** | 最终目标与初始目标无关，且变更点追溯不到 user message | A/P3 | 代码→agent（脚本给首尾，「还有关系吗」要判） |
| **无证宣称** | 出现 fixed/passed/works，前 K 条物证无对应验证动作 | A | **agent**（「对应」是语义等价） |
| **纠正复发** | 同一诉求被用户重述 ≥2 次 **且中间有 assistant 回应** → 方法论失效，非执行问题（见下方混淆项） | A | 代码（近似；trigram 已知会漏，见 §已知实现局限） |
| **假 provenance**（单例观察 n=1） | audit/证据 JSON 里写着 `repo_commit: <sha>`，但产出它的仪器 `git log --all -- <仪器>` 为**空**（从未提交） | B | 代码（一条 git log） |
| **孤儿产物**（单例观察 n=1） | 产物 mtime > session 文件最后 mtime → 子 agent 在父会话终止后仍在写盘，该证据**无人看过** | B | 代码（stat mtime） |
| **owner 视野落后一代**（单例观察 n=1） | owner 常驻目录里的状态板文件与领先分支的同名文件逐行冲突，且落后方是 owner 唯一可见的那份 | B | 代码（跨副本 diff） |
| **实参漂移**（单例观察 n=1） | 同一 derivation 函数被 ≥2 个 caller 调用（生产端 + 独立验证端），新增的作用域/豁免参数只传给了其中一个 → 两端对同一份产物推出**相反的终态** | B | 代码→agent（rg 出 caller，是否漂移要判） |
| **判据按构造不可达成**（单例观察 n=1） | Stage 的 Success Criteria 依赖一个同一份 plan 里已被判为 Blocked 的前提 → 该 Stage 在写下的那一刻就永不可能变绿，而计划本身自洽 | B | 代码→agent（脚本可抓「判据引用的产物 = 另一 Stage 的 Blocked 输出」，是否真不可达要判） |
| **仪器重复既有测试层**（repo 级，代码可算，**判别力最强的新签名**） | 新建测试层断言的字段 ∩ 既有测试文件里出现的字段 ／ 新建总字段 > 0.5 —— 高重叠意味着新层在重测已被便宜地测过的东西 | B | 代码（提取断言字段名 + grep 既有测试） |
| **仪器压过业务**（repo 级，非会话级） | 仪器 LOC / 业务 LOC 与 harness commit / 业务 commit 同时 >1，且专为验收而生的机制**产出 0 份制品** | B | 代码（`git log --name-only` + `wc -l`，不需要读 session） |
| **协作底座缺物化状态**（单例观察 n=1，来源 `CASE-H` 二审） | 多 agent 会话的协作工具面**全是** lifecycle / 消息 / observation，没有任何一个带 `status`+`owner`+依赖边的共享对象 → 团队状态只活在 Host 上下文里，每次压缩后必须 O(历史) 重推导。判据：**新成员能否只靠一次读调用知道全局在干什么** | B | 代码→agent（工具注册表可枚举，「是否真的没有可读状态」要判） |
| **参照本身带病**（n=2 同会话跨域，来源 `CASE-S`，详见下节） | 验收标准把**现役版/上一版**当正确性来源（「复现已验收片的 X」「与旧版对齐 N%」），而现役版从未对**源头设计**校验过 → 新旧同时偏离时该判据恒为绿，缺陷永不触发 | B | 代码→agent（脚本可 grep「对齐/复现/vs 旧版/vs 现役」类判据语句，「该参照是否校验过源头」要判） |
| **符合性检查排在一致性检查之后**（同源，n=1 但后果最重） | 会话先跑「产物 vs 自己 / 产物 vs 上一版」（贵），最后才跑「产物 vs 用户原始设计源」（最便宜，且源从第一天就在手上） | B | 代码→agent（脚本可给两类检查的首次出现行号并比先后，「哪个是源头」要判） |
| **失效仪器的读数被选择性留用**（单例观察 n=1，来源 `CASE-S`） | 同一物件同时出现在「已打回/失效」与「暂按可信」两栏：证据图因资产格空白被打回，但由它产出的数字读数被留用，且背书理由是"与观感一致"（软佐证）；而自列根因中有会连带使该读数无效的项 | A | **agent**（"这个数是否依赖那条失效路径"是语义判断） |
| **数字锚定的目视确认**（单例观察 n=1，来源 `CASE-S`，会话自查所得） | 两份看似独立的证据（读数 + 肉眼确认）被当成可信度相乘，但目视是在**读到数字之后**形成的 → 它看到的是数字要它看的东西，不独立。判据：**两条轨之间是否存在先后依赖；有则实为一条轨** | A | **agent**（先后依赖要读时序判） |
| **配准假象的沉默一半**（单例观察 n=1，来源 `CASE-S`，B 类典型） | 比对仪器按**绝对坐标带**取样，两个 artifact 的同名特征处在不同绝对位置 → 跨部位比对。结果离谱者被查出并修法，但同机制产出**貌似合理**的读数已进 PASS 栏且无人回溯；新法只向前生效 | B | 代码→agent（脚本可列同仪器产出的全部读数，「哪些跨了部位」要判） |
| **估计器把待测量当先验**（单例观察 n=1，来源 `CASE-S`，机制通用） | 估计器的判据里含有对**被测量本身**的假设（例：配准用「最近 \|Δz\| 优先」＝预设偏移小，而偏移正是被测量）→ 在该量大的地方必然错且自信；**且它对以该量为变量的对照全部免疫**（平移后"最近"仍是同一个错误的最近，不变性完好保存了错误）。唯一现形通道是把**中间量**（配准到的 z）外部输出 | B | **agent**（"判据是否含被测量的先验"是语义判断） |
| **可寻址面 > 权限面**（单例观察 n=1，来源 `CASE-S`，被审方发现并命名） | 工具的便利路径能写它不该碰的对象（例：`--ruler-raw` 便利开关写了 v1 归档文件名，单跑覆盖证据链）。与「验证范围虚标」相邻但不同：虚标是**读的人误解**，这个是**写的人误伤**，且会真的毁掉物证 | A/B | 代码（可枚举工具的写入目标 ∩ 受保护路径） |
| **法条被加载但未被路由到注意力**（来源 `CASE-S`，属「验证范围虚标」类第 4 实例；⚠️初版把它误写成"未被加载"，已由被审方证伪，见 `local/gates.md` G25 证伪节） | 律**默认已在上下文里**（`CLAUDE.md=@AGENTS.md` 全量加载），但入口段不路由到它，实际靠派活时手写点名获得显著度 → 未点名者在上下文压力下静默降显著。比"没加载"更阴险：连"文件里没有"这个可查证据都不给。判据：**「若这次忘了点名，谁会挡住违反者？」答"没有人"即无路由** | B | 代码→agent（法条计数与入口段路由可脚本核对；「有无动作触发点」要判） |
| **注入面盲区**（**n=2，来源 `CASE-S`，审计员自身仪器缺陷**） | 审计员从 transcript 的**缺席**推断"被审对象没有 X"。但 `CLAUDE.md`/`@import`/memory/skill 是 harness 注入、不经工具调用，在 transcript 里是 `ambient` 而主脚本**明确过滤 ambient** → 主仪器看不见"默认有什么"。判据：**凡结论形如「它没有 X / X 不在它路径上」，必须来自配置层直接读取** | B | 代码（`cat CLAUDE.md` + @import 链，一条命令） |
| **恰好全零 = 未测到**（**n=2 跨域，来源 `CASE-S`，够格立法**） | 读数为**精确的零**时未区分「测得为零」与「未测到」。零是唯一一个**看起来像正常读数的缺失值**——NaN/空/报错都会被看见，只有零会安静通过每一道求和、每一次取均值、每一张绿表 | A | 代码（可扫精确 0 值 + 其产出路径是否有"无样本"分支） |
| **轮询重放**（同源 n=1） | 成员状态轮询返回的是**完整最终报告**而非状态码，且已完成成员永不退出列表 → 同一份已知结果被反复灌入上下文；单次轮询体积随已完成数**单调增长**，换 run 才归零（锯齿） | B | 代码（按 `(agent, report[:4000])` 哈希去重后数重复次数） |
| **行数截断假象**（单例观察 n=1，来源 `CASE-O` L18501–L18503） | 用 `rg ... | head -N` 给 repo-wide 搜索“限流”，但命中超长单行 JSON/日志时 `head` 只限制行数、不限制字节；实测一次调用仅 74 行却报告 original token count 3,366,449、截去 12,417,220 bytes。调用者叙事上以为已有界，物证上仍发生单次洪水 | A | 代码→agent（脚本可报单次 output 字节与行数；是否因超长行绕过行数闸要看命令形状） |
| **blocker 级联吞噬目标**（单例观察 n=1，来源 `CASE-N` L69420–L84632，14h 窗口） | 用户下达目标 G 后，窗口内**新建 Issue/子目标 ≥3 个且每个都标记为 G 的 blocker**（本例 ISSUE-A→B→C→D→E→F，16:08→21:31 五小时六连锁），而 G 的启动事件计数 = **0**，直到用户问"为什么这么慢"。这是判据封闭性的活体：每个新 blocker 都被解释成"还差一步"，合同里没有「链条本身该升层」这个类别。放大器：会话自立的「每缺陷单独 Issue + exact-SHA review + PR/merge 再回目标」合同把每环成本乘上全仪式（gate-tiering 失效边复发）。判据：**第 K 个连锁 blocker（K≥3）出现时有没有升层动作（问用户/缩范围/接受风险）**；没有即命中。⚠️命中后必须做 §1.5 配置层核对：本例复核发现六连锁是仓库合同（defect-to-repair loop + 每缺陷一 Issue/Task + dogfood freshness 重启规则）的**忠实执行**，升层缺失是合同没有该条款（修理链无预算，ATTENTION_REQUIRED 终点是 Brain 不是人）——归责在流程不在 agent；跳过配置层直接归责 agent 即 G26 复发 | B | 代码→agent（脚本可数窗口内 issue-create 事件与 G 启动事件；"是否真 blocker、该不该升层"要判） |

### 「仪器重复既有测试层」的实测来源（`example-e2e-console`，2026-07-28，5 天代价）

```
e2e 断言字段              383 个
既有页面单测已断的         334 个   → 87%
按线：module-a 100% / module-b 98% / module-c 96% / module-d 81%
页面单测跑完               1 秒（57 文件 8363 行）
一条 e2e claim             60~90 秒 + fixture + 数据库 + DevTools
业务代码写了               1 天
e2e 脚手架写了             5 天
```

**当天修的 10 个仪器缺陷全部只为支撑这种重复**（网络证据窗口、fixture namespace 冲突、
账号级锁、断言时刻、崩溃清理、信号清理……），没有一个是为了证明产品对不对。
5 天里**确认的产品 bug = 0**，找到的全是仪器自己的问题。

**这条签名一天就能算，而且能在第 1 天算。** 它是本表里唯一一条能在「投入变大之前」
就否掉整个方向的。检测法：抽新层断言的字段名（如 `expectDataJson` 的 key），
对既有测试文件全文 grep，算命中率。

⚠️ **名字会决定形态。** owner 从第一天要的是「模拟真人使用、每次点击截图、看图发现问题」，
做出来的是逐字段断言系统 —— 因为它被叫做 **e2e**，而 e2e 的判据是「通过/不通过」，
于是任何人看到它都会想「再加一条断言」。改叫**走查（walkthrough）**、产出改成
「一串能看的证据」之后，同样的覆盖从 65 条 claim / 40+ fixture 降到 10 条旅程 / 每条 1 个前置状态，
依赖闭包从 35,871 行降到 1,275 行（**4%**）。
**给一个机制命名时，先问这个名字会邀请别人往里加什么。**

### 「判据按构造不可达成」与「仪器压过业务」的实测来源（`example-e2e-console`，2026-07-28 测）

- 判据不可达成：`SYNTHETIC-COMMIT docs: Stage 2 判据从「绿 run」改成「差分一致」——原判据按构造不可能达成`。
  原判据要求「一次绿 run」，而同一份 `IMPLEMENTATION_PLAN.md` 里 Stage 2/3 都标着
  `Blocked（DevTools 授权 / owner 密钥）`——绿 run 依赖的正是那两项。**两条信息都在 plan 里，
  但没有任何机制把它们对撞。** 修法不是解阻塞，是把判据换成阻塞态下仍可证伪的东西（新旧路径差分一致）。
- 仪器压过业务：仪器 42,263 行（`tools/validation-harness`）+ 12,768 行（`scripts/`）vs 业务 12,740 行
  （`miniprogram`）；自 2026-06-01 的 1,423 个 commit 中 546 个动仪器、242 个动业务（**2.26 : 1**）；
  同期 41 道机器闸 / 4,655 行，而它们要替代的那件事（读屏幕）写了 56 行、全仓引用 **0 次**，
  `agent-review.json` 落盘 **0 份**。根因是一条从未进过任何 ADR 的隐性公理
  「证据必须确定性、机器可复算」把 LLM 判断按定义判成非法——**不是有人选了机器闸，是别的选项在公理层面非法**。
  → 这条签名不需要读 session，纯 repo 即可算，适合作为「该不该开审计」的前置筛。

### 实参漂移的实测来源（`CASE-AA`，Claude Code，2026-07-25 19:40→00:29 本地）

```
机制            status-gate.mjs::summarizeStatus 有两个 caller：run-scenario.mjs（写 manifest）
                与 validate-evidence.mjs（独立复核 manifest）。会话末尾给 summarizeStatus 加了
                新参数 outOfScopeFixtures，只在 run-scenario.mjs 一侧计算并传入。
                → manifest 自报 completed / blockers=[] / maxClaim=auto_verified_candidate；
                  validate-evidence 仍按旧参数推导，判 source_invalid，四条 failure 全是
                  "must be derived as partial"。CLI 终态 blocked。
为何执行 agent 看不见   lint=0、全量 874 test 绿（L1022）→ 单元层完全无信号；
                        差异只在「两个 caller 传参不同」这一跨文件事实上。
                        随后 L1023 跑验收、L1025 会话被 401 掐断，终态从未被任何 agent 读到。
检测成本        rg 出 derivation 函数的全部 caller，比对新增参数是否每个 caller 都传。零解析。
与「定义分叉」的区别    定义分叉是两份**实现**各自演化；实参漂移是**同一份实现**被喂了不同实参，
                        共享代码反而掩盖了它——看 diff 只看到「加了个可选参数，默认值安全」。
```
n=1，记为单例观察，未晋升 gate。但它同时命中既有的 **孤儿产物**（终态产物 mtime 00:29 = 会话死亡时刻，
无人看过）——两条签名叠加时，产物里的自报状态**必然**被下一代当既定事实继承，这是最贵的一种裂缝。

⚠️ **后续修正（同日）：签名定位了缺陷类，但没有定位修复点。**
「补上漏传的实参」是错的解法——独立盲审判 reject：真正的缺陷在**两个 caller 的共同上游**
（runner 给 backend 侧做了作用域过滤、page 侧漏了），补参数等于在闸里豁免一条本就不该产生的记录，
命中该仓「禁为过 CI 改闸」红线。修上游后两端自然一致，闸一行没动。
**通用教训：实参漂移是「两端不一致」的症状，修复点可能在两端之外。看到漂移先问「谁本该产生这份输入」，
不要默认对齐两端即可。** 审计员给出的是缺陷类，不是补丁——这条正是「禁止解题」的实证理由。

### 三条新签名的实测来源（`CASE-C`，2026-07-23→25，9439 行/83 MB）

```
假 provenance     tools/example_asset_lock.py 会话内被 patch 11 次、产出 v001–v009 全部证据，
                  git log --all -- 该文件 = 空；而 example_builder_audit.json 写着
                  "repo_commit": "SYNTHETIC-SHA"。同批未提交的还有 compose_plate / finalize_evidence /
                  verify_structure + 其测试。检测成本：一条 git log，零解析。
孤儿产物          session 最后一条记录 L9439 = wait_agent（等 /root/example_builder），
                  文件 mtime 19:45；audit/blend/20 张渲染的 mtime = 19:46:35–19:46:49。
                  即完整一版证据包在父 agent 断线后落盘，status=BUILDER_CLOSED，无人查看。
                  → 多 agent 会话特有。审计时必须 stat 产物目录，不能只读 session。
owner 视野落后    ROUTE_MATRIX.md 在 main（owner 常驻）与 codex/kiln-durable-promotion-v1 之间
                  30 行差异，且状态口径相反：main=「DRAFT_READY / ⏸ Phase 2A」，
                  分支=「CANONICAL_CLOSED / VERIFIER_CLOSED」。用户在 L8743 问
                  「我希望知道目前的情况」——这个提问本身就是该签名的外部症状。
```
三者共同机制仍是**缺少失效传播**，但检测面各不相同（版本库 / 文件系统时间 / 跨副本 diff），
故分列而不合并。均为 n=1，**记为单例观察，未晋升 gate**。

## 定量签名（用 rate，阈值待标注集确定）

| 指标 | 含义 | 观测样本 |
|---|---|---|
| `instrument_patch_share` | 改仪器占全部改动的比例 | 0.91（"读 session"任务里 91% 在改工具）/ 0.25 |
| `narrative_to_evidence` | (assistant+reasoning) / (exec+patch) | 1.92 / 1.64 — 疑似最佳"空转"度量，缺基线 |
| `timeout_rate` `failure_rate` | 除以 exec 数 | 0.38/0.15、0.26/0.33、e2e 仅 0.04/0.02 |
| `pump_share` | **真** pump / 全部真实用户消息（已扣除网络续跑） | 0.18 |
| `resume_share` | 中断后续跑 / 全部真实用户消息（环境噪声，非病理） | 0.12 |
| `pump_gap_median_lines` | 相邻真 pump 的行距中位数 | 96.5（尾部曾连续 6 次，间隔仅 7 行） |
| `compactions_per_1k_lines` | | 1.46 / 2.04 |
| `max_patch_share` `max_cmd_share` `forked_share` | | |

### "继续"泵（本表最新增）

用户被降格成 while 循环的计数器：agent 每前进极短距离就停下等人踩一脚。
实证：某 session 尾部 `L8777 / 8813 / 8820 / 8827 / 8834 / 8842` 连续 6 次"继续"，间隔 7–8 行。
它与用户主观感受的"进度怎么这么慢"是同一件事的两面——**"慢"的物证形态就是"继续"泵**。

## ⚠️ 环境噪声混淆项（两条签名都栽过，必须先排除再计数）

用户重述与"继续"**大部分可能只是网络中断后的重发/续跑**，与方法论无关。
不做区分就会把环境故障误报成 agent 病理。判据在物证流里是干净的：

**"继续" —— 用轮次生命周期区分**

```
两条用户消息之间有 task_complete 且无 turn_aborted / thread_rolled_back
    → PUMP：agent 正常收工后停下等指令（真信号）
无 task_complete，或出现 turn_aborted / thread_rolled_back
    → RESUME：轮次根本没结束，用户只是把它续上（环境噪声，丢弃）
```

Codex 事件：`task_started` / `task_complete` / `turn_aborted` / `thread_rolled_back`。
实证：某 session `task_started=36` 但 `task_complete=31`，另有 `turn_aborted=2`、`thread_rolled_back=1`。

**重述 —— 用「中间有没有回应」区分**

```
文本归一化后完全相同 且 中间零 assistant 消息 且 零工具调用   → RESEND（网络），丢弃
文本相似但措辞有变   且 中间 ≥1 条 assistant 回应             → RESTATED（回应了仍没解决），计数
其余                                                        → UNCLEAR，展示但不计数
```
依据：人几乎不会一字不差地重打一遍；措辞变化本身就是重新表达的证据。

**修正前后实测差距（同一 session）**

| 指标 | 修正前 | 修正后 |
|---|---|---|
| pump 计数 | 15 | **9**（另 6 条为 RESUME） |
| `pump_share` | 0.30 | **0.18**（新增 `resume_share` 0.12） |
| 重述簇 | 2 | 2（均判为 RESTATED，人工核对一致） |

结论未被推翻，但**量级虚高了 67%**。这是"环境噪声混入病理信号"的通用教训：
任何以"用户不得不再说一次"为基础的签名，都必须先扣掉环境故障。

**TodoList/TaskList 同值重复 —— 状态声明，不是死循环（REPEATING 计数前必须排除）**

progress_probe 的 `repeated` 行在两个健康 PRODUCING member 上各报 3 次连续相同 TodoList 调用
（example-governance-project `TEAM-RUN-A` 双 member，2026-08-05；同窗口 edits=5/10、正常产出）。
TodoList 是状态声明不是环境动作，状态收敛期同值重发是正常形态。判据：重复调用为
TodoList/TaskList 类 且 同窗口存在 state-changing 编辑 → 从 REPEATING 计数里剔除。
（n=2 member，单会话；若跨会话再遇则固化为探针内置过滤。）

## 上下文洪水（`flood_share` / `flood_tool`）—— 目前判别力最强的信号

按工具聚合 `function_call_output` 的字符量。它把"压缩为什么这么频繁"从"会话太长"这种
不可行动的解释，变成**一个具名工具的定量归因**。

实测（窗口均为 258,400）：

```
CASE-E   view_image    11 calls   13.4M chars  57.2%   avg 1,220,810 chars/次 ≈ 30 万 token
           read_thread    5 calls    8.0M chars  33.9%   avg 1,592,379 chars/次 ≈ 40 万 token
           exec+exec_cmd 178 calls    2.1M chars   8.8%
CASE-C   view_image    46 calls   55.1M chars  86.8%   avg 1,198,454      ← 会话仍在跑时测的中途值
```

⚠️ **边界修正（不删旧值）**：上面那行 `CASE-C` 是会话未结束时的快照。当时以为会话终止后的复测为：

```
CASE-C(终值)  view_image      51 calls  58.34M chars  87.1%  avg 1,143,889
                exec_command   396 calls   2.34M chars   3.5%
                list_agents     99 calls   2.25M chars   3.4%  avg    22,751   ← 新洪水源
                exec           298 calls   1.97M chars   2.9%
                take_screenshot  3 calls   1.37M chars   2.0%  avg   456,012   ← 新洪水源
```

结论方向未变（比例仅动 0.3pp），但**教训是：对仍在运行的会话取的指标是下界，不是终值**；
若要引用绝对值必须标注是否终态。

⚠️ **第二次边界修正（2026-07-26）**：所谓“终值”后来也被续写推翻。同一 session 文件从
9439 行 / 83 MB 增长到 **10045 行 / 88.8 MB**，`view_image` 从 51 次增长为：

```
CASE-C(后续快照)  view_image  60 calls  63.38M chars  87.8%  avg 1,056,344
```

因此“终态”也必须绑定**采样时间 + 行数或文件哈希**；除非记录已归档且不可再续写，否则绝对值只能称
“截至某时的快照”，不能称终值。这是“引用对象后来变化”的又一个 B 类实例。

**两个新洪水源**（来源 `CASE-C`，均 n=1）：
- `take_screenshot` avg 456,012 字符 ≈ 11 万 token/次。它与 `view_image` 是**同一机制的第二个实例**
  （未降采样图像进上下文），因此既有的"看图前必须降采样"gate 应按机制而非按工具名执行。
- `list_agents` avg 22,751 字符 × 99 次 = 2.25M。单次不大，**靠频次成为第三大消耗**。
  多 agent 会话特有，与既有 `wait` gate 同源：**编排类工具的轮询必须有界**。

**真正干活的工具只占 4–9% 的上下文预算。** 这两个会话"什么都记不住"不是因为工作复杂，
而是把 87% / 91% 的预算花在了看图与读上一个会话上。

### 三个会话三种洪水画像 —— 这正是该指标有判别力的证据

```
CASE-E   view_image 57.2%  + read_thread 33.9%   干活的 exec 仅 8.8%
CASE-C   view_image 86.8%                        干活的 exec_command 仅 3.7%
CASE-D   exec       79.5%  + wait        16.4%   （多 agent 会话）
CASE-Q   Read       98.8%                        干活的 Bash 仅 1.3%（Claude Code）
```

**`Read` 是 Claude Code 上 `view_image` 的同机制第二实例**（来源 `CASE-Q`，已按 uuid 去重）。
Claude Code 没有独立的看图工具——`Read` 一个 PNG 直接回 base64。按扩展名拆开后判别力极高：

```
.png   70 calls   21.22M chars   avg 303,154 ≈ 7.6 万 token/次   ← 占 Read 全部成本的 99.6%
.md     9 calls    0.05M chars   avg   5,514 ≈ 1.4 千 token/次
.py     3 calls    0.02M chars   avg   7,076
```
70 张图 ≈ **530 万 token ≈ 21 个 200K 窗口**。同一会话里正确做法就在手边：`SendUserFile`
30 次，**25 字符/次**——给人看图是免费的，给自己看图是 7.6 万 token/张。
且该会话尾部**已经拼了 contact sheet**（4 帧 1264×535）却仍花 48.7 万字符 ≈ 12 万 token 读它，
紧接着又整读一张单帧 28.3 万字符 —— **拼图是必要条件不是充分条件，没降采样等于没拼**。

`flood_tool` 会随会话类型改变，因此它不是"会话长度"的代理变量——它回答的是
**预算到底去哪了**，而这是可行动的。

### 由此晋升的四条 gate（均已跨会话复发 ≥2 次）

```
view_image(未降采样 PNG)   → 单次 ~30 万 token ≈ 1.2 个窗口。看图前必须降采样。
Read(PNG，Claude Code)     → 同一机制的第二平台实例，单次 ~7.6 万 token。规则相同：
                             自审图先降采样（contact sheet 后仍要压宽）；给 owner 看用
                             SendUserFile（25 字符/次），**永远不要为了给人看而 Read**。
read_thread(turnLimit=10)  → 单次 ~138 万 token ≈ 5.4 个窗口。禁止通读，只许有界钻取。
用 exec 内嵌补丁串打补丁    → exec 输出把补丁全文回显，实测 avg 50,276 字符/次；
                             改用 apply_patch 工具实测 avg 214 字符/次 —— 相差约 235 倍。
                             且补丁全文会进两次上下文（arguments 一次、output 一次）。
                              **复发（`CASE-K-CHILD`，2026-08-11 会话）**：≥155 个 `const patch =
                              "*** Begin Patch..."` exec（证据流前缀簇 79+45+31），叠加
                              ego-browser heredoc 快照，驱动 1907 次 exec / 31.07M 字符 =
                              98.7% 洪水、21 次 compaction、上下文地板 22.9K→71.3K。
wait(等待子 agent/长命令)   → 实测 avg 168,100 字符 ≈ 4.2 万 token/次；331 次共约 1390 万 token。
                             多 agent 会话特有。轮询必须有界，不能裸等。
                             **复发第 3 次**（`CASE-H` 二审）：718 次 / 57.0M 字符 / avg 79,418，
                             其中扇出期 avg 156,854 —— 占该段非图片上下文的 71.7%。
                             形态确认：参数是 `{"cell_id":N,"yield_time_ms":30000}`，等的是
                             **exec cell（长命令/成员进程）**，不是 agent 状态；`write_stdin`
                             空轮询（`chars:""`）1771 次 / 4.7M 是同一病的另一形态。
                             ⇒ 归因时必须与「成员状态轮询」分开记，否则会把进程保姆的账
                             算到「缺任务看板」头上（本次一审就差点这么算，见解析陷阱）。
一次看 N 张图              → 不要逐张 view_image / Read。先用 PIL 拼成一张带标签的 contact sheet
                             再读：N 次调用塌成 1 次，且**保留了横向对比**——逐张看时
                             「只有这一张不一样」恰恰是最难发现的。
```

**contact sheet 的实操约束**（来源：`CASE-AA` 后续会话，2026-07-26，用户提出）：
拼图不是单纯为省 token，降采样有下限——**要读的证据往往就是图上的小字**。实测按宽 500px
（原图 780px，约 2/3）拼 3 张为 1500×999、676KB，中文错误文案仍清晰可读；再小就读不出了。
所以规则是「拼成一张 + 保住可读比例」，不是「压到最小」。该次拼图直接命中一条业务假绿：
三张里唯一一张显示「兑换资格同步失败」，而它在 manifest 里是 `passed`。

`read_thread` 那次已经设了 `turnLimit:10` + `includeOutputs:false` + `maxOutputCharsPerItem:20000`
**仍然**返回 554 万字符——**它的参数不足以约束体积**，不能依赖参数自保。

**⚠️ 削减源尺寸可能是精确的零削减**（来源 `CASE-S`，Claude Code，2026-08-11，n=2 同形状，
详见 `local/gates.md` G20）。Claude Code 的 harness **把图像长边一律压到 2000px**——
该会话 9 处尺寸注记全部落在长边 2000（`4284x5712 → 1500x2000`、`1750x4022 → 870x2000`、
`1400x2965 → 944x2000`）。所以：

```
源侧削减若不越过这条上限 → 送达量不变 → 削减 = 0
```

实测：被审会话按「≤1500px 宽」新规出了筛查版 `1400x2965`，与全分辨率原件 `2420x5126`
**长宽比相同（0.4721 vs 0.4722）**，压完**都是 944x2000、逐像素相同**；那次 Read 仍花
**676,913 字符 = 修规前均值 355,253 的 1.9 倍**。真正的成本维度是**单张图的栏数/内容密度**
（同会话 L1412 送 2000x1650=3.3Mpx 反比这张 1.9Mpx 便宜——5 栏竖排对照条细节密度高得多）。

⇒ 上面 contact sheet 那条要连着读：拼图省的是**调用次数**，不是单次体积。
验证判据（可证伪）：改动前后 `[Image: original AxB, displayed at CxD]` 里的 **CxD 是否变化**；
不变则该优化无效，**不得计入「已处理」**。

**修订（同源会话第 3 轮，2026-08-11）—— 分辨率整个维度都不省钱。** 该会话按上条改了口径
（长边 ≤1200px、≤3 栏）后复测，**仍近乎零削减**：

```
修规前均值   长边压 2000        355,253 字符   1.00x
规则 v1     944x2000           676,913 字符   1.91x
规则 v2     1200x1194(原生)     636,361 字符   1.79x    ← 像素砍 24%，成本只降 6%
```

机制是**每像素字节数反升**（0.27 → 0.33 B/px）：八拍定格挤进更小画布，每像素熵更高，JPEG 压不动。
所以准确的说法是：

```
Read 图像成本 ≈ 一次要看几件东西，与把它们缩到多大基本无关。
```

唯一真省的是**减少"要看的东西"**：中间检查点只拉当前在判的那一两拍。
边界：**终版验收看全 N 拍是必要开销**，是"完整验收一次的地板价"，不判为浪费——
优化目标应是"减少全量 pass 的次数"，不是"把每张图变小"。

⚠️ **前提（第 4 轮补，本条曾造成实际损害，务必连着上面那段一起读）**：
以上全部成本结论，**只在可读下限之上成立**。上面那段"降采样有下限、要读的证据往往就是
图上的小字"不是补充说明，是**前置条件**。

实测反例（同源会话 `CASE-S`）：被审方按上述成本方向做出 **1200×1194 八拍合板**，
该尺度下**手只剩二三十像素**，手部三种病（糊成手套团 / 缩进袖口 / 张开呈蜘蛛形）
全落在盲区，五支片带病交付，最终由 Owner 用三张放大截图打回。
**给出该成本方向的正是本文档**——只搬了省钱半段、跳过了可读下限半段。

```
筛图尺寸下限 = 本次要判的「最小特征」所需像素（由判定对象决定，不由成本决定）
操作序：先定最小特征要多少像素 → 倒推一张放几帧 → 最后才谈成本
```

**该弧线的收束（第 6 轮实测，同源会话）——按分区裁既更便宜又更看得清，两个目标不冲突：**

```
八拍/多栏合板（挤在一张）    636K–677K 字符/次     手只剩二三十像素，缺陷全落盲区
单分区裁片（一次判一件事）   187K 字符/次          约 1/3.4 成本，且该分区看得清
修规前的混合均值                     355K 字符/次
```

⇒ 正向处方（不是禁令）：**按「待判决点」切图，一张只放一件要判的事**。
它同时满足可读下限与成本——早先的两难是伪两难，根源是把"一次看全"当成了必须。
"一次要看几件东西"才是成本主项；分区裁把它降到 1，于是分辨率反而能给足。

先定图多大、再看特征剩多少像素 = 错误路径。详见 `local/gates.md` G20 边界一节，
含"单维度建议会把对象推到该维度极值、而极值处正是另一维度失效点"的推广形式。

### 第五条 gate —— 派生副本失效传播（复发第 2 次，按 SKILL.md §8 提前晋升）

```
改了源副本 → 所有派生副本 + 其中的结论同时作废，必须同批传播才算生效。
```
两次实测，同一根因、不同形态：

```
n=1  scratchpad/session_metrics.py(307 行) 与 skill/scripts/session_metrics.py(473 行) 并存，
     草稿副本落后 166 行                                  → 处置：删非权威副本
n=2  CASE-F：.claude(源) → .codex/skills → public-skill-repo(GitHub) 三副本，
     最后三次写回后未 rsync/未 commit，4 文件分叉，
     两条已证伪结论仍在 2/3 副本里生效                     → 见上节 md5 表
```
→ **凡审计"其产物会被复制/发布"的对象（skill、模板、脚本、契约文档），
必须把「源改动 → 派生副本 → 已发布副本」当成一条失效边显式量一次。**
一条被推翻的结论只修了源副本 = **没修**，因为加载派生副本的 agent 会继续传播它。

**复发第 4 次（2026-08-10，来源审计会话 `CASE-Y`）——发布腿**：`sync_skill.sh --check`
报三副本字节一致（绿），而 `public-skill-repo` 的 `origin/main` 停在 2026-07-26，落后 **15 天 / 592 行
/ 9 文件**——GitHub 上继续分发着此后已被证伪两代的 `timeout_rate` 一代实现与旧 signatures。
根因：`verify()` 只比**工作树**，commit/push 腿仍是纪律。已修：`publish_verify()` 把
「repo 树脏 / HEAD≠origin/main」变成 REFUSED（含 `--check` 与无参 sync；commit 不 push 也非零退出）。
**字节一致 ≠ 已发布**——校验必须覆盖到读者真正加载的那一端。

### 第六条 gate —— 跨载体修复传播（复发第 2 次，2026-08-10 晋升；来源 `CASE-Y`）

```
修一条判据/结论时，必须先枚举它的全部载体——同一逻辑的每个脚本、陈述它的每份文档——
再动手；漏掉任何一个载体，修复就没有发生。sync 的字节一致只保证「同一文件的三份拷贝」，
保证不了「同一教训的多个宿主」。
```

两次实测，同根因（修复无失效传播机制），不同载体对：

```
n=1  代码→代码   「子串匹配原理上做不了超时/失败判定」在 session_metrics 修满三代
                 （HARNESS_TIMEOUT_RE / EXEC_OWNERS），progress_probe.py 里同一判据
                 原封未动 → 健康的只读审计会话被判 WAIT_LOOP（见下方解析陷阱）
n=2  代码→文档   turn_error 修复（2026-07-31）落了代码与 falsified.md，platforms.md 仍写
                 api_error→turn_aborted；USER_MSG_CAP 400→8000 修复落了代码与 self-upgrade，
                 signatures「已知实现局限」仍写「尚待补齐」——两处过时文均存活约两周
```

操作化：动手前 `rg` 同一正则/结论/字段名在 `scripts/ references/ SKILL.md` 的全部命中，
把命中清单当修复清单；修复后逐项划掉。这是「派生副本失效传播」gate 的推广：
副本一致（sync 管）≠ 载体一致（本 gate 管）≠ 已发布（publish_verify 管）。

### 数据面分叉：同一业务实体在 ≥2 个 store 并存（来源 `CASE-H`，Codex，1640 MB / 83766 行，n=1 单例观察）

仪器分叉签名原本只量**文件面**（同 basename 在多工作树被 patch）。本会话出现同构的**数据面**形态：
example company的 3 个 org_units + 11 个 standing agents（全同 id）同时存在于
`~/.harness/companies/example-company/` 和 `~/.harness/projects/example-project/` 两个 store，
且 example-company store 内还有两个 `parent_unit_id=null` 的 root org 并存（契约文档写明 one root unit）。
无失效传播机制 → 改一边另一边继续投影旧真相。用户可见形态：「org 显示不是基于真实数据」（L81866）。
脚本不测 store 内容，此签名当前只能由审计 agent 在钻取时人判。复发第 2 次则晋升 gate 并考虑给
session_metrics 加 store-dup 探测。

### 代签审计链：署名 actor 无执行躯体（同源 `CASE-H`，n=1 单例观察）

「无证宣称」签名的数据层形态：company store 1894 条审计事件全部署名 org agents
（单个名字下最多 1496 条），但署名者的真实执行记录 = 9 个 MemberRun（4 个通信冒烟 + 5 个只读审计），
0 次业务产出——写入的物理执行者是 host 会话本身，带 `--actor <别人>` 旗子跑 CLI。
Assignment 的 delivered/acknowledged 同为 host 代收件人所写。
检测法（agent 判，脚本不可判）：对质「审计链署名分布」vs「该署名者的 runtime/run 记录」，
署名量 ≫ 执行量即命中。病理：审计链把「以谁的名义」记成「谁干的」，schema 缺
acting/performing 双字段；等真身上线后无法区分自写与代签。

### Worker 越过评审闸：自合并 + 自验收（来源 example-governance-project `TEAM-RUN-A`，Kimi Code host + deepseek members，2026-08-05，n=1 单例观察）

「代签审计链」的镜像形态：不是代别人签，而是**自己签自己**。物证链：
`work_operations.jsonl` 记录 lane A work `submitted`(actor=member_run) → `accepted`(actor=operator:cli)，
中间 0 个 host 评审轮次；member 的 wire.jsonl 里有 `gh pr merge EXAMPLE` 执行，发生在 host 评审之前；
host 随后 `work accept --expected-version 3` 被 VERSION_CONFLICT 挡住（实际已是 v4）。
机制：member 持有完整 harness CLI + gh auth，`operator:cli` actor 不区分 host/member——
与代签审计链同一个归因缺口（署名无执行躯体）。本例内容损失为零（host 事后补审，质量合格），
但闸已失效：评审从「合并前置」退化成「合并后补」。
检测层：代码→agent。脚本可数 member wire.jsonl 里的 `gh pr merge` / `work accept` 调用；
是否构成越闸要对质 host 评审时序（事件流 seq）判。复发第 2 次则晋升 gate
（候选修法：store 侧区分 member/host actor，或 accept 要求非提交方 actor）。

### 会话文件名日期 ≠ 活动日期（同源 `CASE-H`，机制确定）

rollout 文件按**创建日**归档（`2026/07/29/rollout-2026-07-29T...`），但 resume 持续追加：
该文件尾部记录时间戳为 2026-07-31T10:06Z，跨 3 天。按日期窗定位/统计时以**尾部时间戳**判活跃度，
不要用路径日期；`--since` 过滤会漏掉「老文件里的今天」。

### 协作底座缺物化状态 + 轮询重放的实测来源（`CASE-H` 二审，1918 MB / 96048 行，比一审多 12282 行）

被审对象是本仓自己的 Agent Team。**MCP 工具面 26 个，任务对象 0 个**：
lifecycle 9 / 消息 7 / steer 2 / observation 2 / wave 5 / mission 6。
工作描述的载体由 schema 自证：`objective` *"seeds each member's **assignment message body**"*，
`send_message.kind ∈ {assignment, message, handoff, control}` —— **assignment 是一个 message kind，
不是一行 record**。因此它不可变、点对点、无 status、无 owner、无依赖边、无「未分配」表示。

代价（剔除图片洪水后的非图片上下文；不剔会被稀释到 5.8%，见下方解析陷阱）：

```
区段                非图片    严格看板形状重推导     真正改状态    比值
L0–30k  扇出期      77.4M     2.9M  ( 3.7%)        1.7M (2.2%)   1.7:1
L30k–96k 调度期     63.8M    14.0M  (21.9%)        5.1M (8.0%)   2.7:1   ← 逐段爬升 27.7%→35.1%
```
「严格看板形状」= `team-run status/inbox/events/list` + `git log/status/diff/worktree list` + `ps/pgrep`，
即**只读、不可能改变任何状态**的调用。加上进程保姆轮询后全会话 78.5M 字符 ≈ 76 个满窗口。

轮询重放（64 次成员状态轮询）：132 个不同 (成员, 报告)，**97 个（73%）被重复投递 ≥2 次**，
报告字节 **70% 是纯重复**；`/root/skill_inventory` 与 `/root/skill_references` 各 16 次。
单次轮询体积 1,349 → 44,624 字符（33 倍）单调增长。

失效传播缺失的量化：**`steer_member` 22 次 / `spawn` 659 次 = 3.3%**。
成员一旦拿到私有 assignment，其引用的计划即使已不成立也基本不会被纠正 —— 这是 B 类裂缝
（引用了不再成立的东西 + 无失效传播）**长在协作底座本身**，所以每加一层就复发一次。

层级放大（每次 spawn 附带的通信调用数）：
```
L0–50k   0.3 → 1.0 → 0.9 → 0.9 → 0.9      单层
L50–60k  4.2   ← org agent 层接入（L60971 org-agent-member-projection-v2）
L70–80k  6.5   ← 峰值。同段出现 L77888 company-assignment-bridge（+ pr277 第二版）
```
`company-assignment-bridge` 是**证据本身**：WorkItem 有看板语义（行），assignment 是消息（边），
两者接不上，于是专门 spawn 成员开工作树造桥，造了两版仍没接上。
→ 通用命题：**消息协作复杂度是参与方之间的边（O(n²)），共享状态是行（O(n)）；
加层时边平方增长、行线性增长。** 因此「多层级 agent team」必须先有行，再谈边。

控制面健康度：2445 次调用中 SPAWN 659 / SEND 341 / READ_MAIL 449 / **REPAIR 465** / STEER 22
/ WAVE 375 / MISSION 134。**投递修复 465 = 消息管道 1277 的 36%** ——
`reconcile_delivery` / `manual_ack` / `unacked_messages` 这些工具存在本身即证据：
投递不是构造上可靠的。输出里症状计数 `stale/duplicate/conflict` 2125、`no mail/idle` 1881、`unacked` 395。

### 单层串行委派爆炸（n=1 单例观察，未晋升 gate）

来源会话 `CASE-J`：用户在 L6397 明确把最大 agent 深度限定为 1、
同一时间一个 writer、Gate 只读。规则之后确实未再出现 `/root/a/b` 型二层 task，但 L6400 之后仍创建了
**46 个不同的顶层 agent**；单个 provider-admit PR 出现 `provider_admit_gate` 到
`provider_admit_gate_r10` **11 轮 Gate**。会话终值采样于 10,162 行：`spawn_agent=81`、
`wait_agent=1025`、其中空轮询 **667** 次；PR EXAMPLE 已积累 9 个远端提交后 Rust CI 仍红，随后又产生
未推送的第 10 个本地修复提交。

这条单例说明：**限制树深只消除了层级放大，不会自动限制串行 handoff / review-fix ping-pong。**
它不证明“少用 agent”必然更好，也不支持新的绝对阈值；后续应分别测量
`agents / immutable revision` 与 `gate rounds / PR`，复发第 2 次再决定是否晋升 gate。

⚠️ **本条不证明「加了看板就会好」**——反事实未验证，无同类对照运行。它证明的是
*代价存在且可归因于缺少共享状态*。另：`WORKTREE_SCAN`（调度期 10.8%）有一部分是 git worktree
隔离的固有成本，有板也消不掉全部。

### 上游合同把委派者自己的诊断固化为公理：子线程执行正确、目标错误（来源 `CASE-P-CHILD` ← 父 `CASE-P-PARENT`，Codex，2026-09-02/03，n=1 单例观察）

- 用户原话（父 L8434 14:58Z）："为什么我们的3d工作台做不到这种质量…是我们的技术架构还是什么有问题吗"——是**诊断问句**，不是构建指令。
- 父线程 L8538 自答"参考模型也不是工业 CAD 级，高级只因放进了演示系统"→ L8566"不是 Three.js 做不到，是目标做错了"→ L8584 `create_thread` 合同约 1800 字，
  把「GLB 只是外观基准、不能虚构生产 CAD」写成子线程**前提**。user message 里没有任何一句确认过"模型没问题"。
- 子线程按合同忠实执行：2h29m（15:11→17:41Z），5 subagent，6 个 rollout 共 78 MB；实现者 84 patch / 348 exec / 2 次 compaction；
  4 次 submission、3 轮视觉+技术双验收，终判 Pass、视觉 8.2/10。本地基线分位：failure_rate p10、flood_share p90、compactions p25——**执行层不病态**。
- 用户 09:34（L1594）一句"不是很合理的整机…比上个版本也差很多"；01:45 子线程自认"最根本的判断错误：优化了查看器却没做产品工程"（L1833）。
- 裂缝类型 B：子线程引用的前提在用户处从未成立；用户的否决落在子线程，**父线程至审计时仍持有"模型没问题、只重做查看器"的结论**，没有失效传播。
- 判据（agent 层）：委派合同里出现「必须承认 X」「不能 Z」这类由委派者**刚刚自己得出**的诊断，而父线程 user message 无对应确认 → 合同前提未质证，子线程原理上无法反驳。
  与 memory `feedback_axiom_must_be_contestable` 同一条：要求反复不落地时，去找那条把它判成非法的隐性公理——本例公理由上游 agent 写入。

### 评审矩阵状态覆盖缺口：回归只在未列入矩阵的相机×阶段组合可见（同源 `CASE-P-CHILD`，n=1 单例观察）

- 第三轮为消除侧壳法线条纹，把 `HeadShell` 换成"同包络封闭参数面"（编排者 L1143 / L1344 自述）。评审矩阵覆盖：侧视条纹、hero/前/侧/内部相机、`内部→S4`、`侧视→S5`、`选件→整机`。
- 视觉验收 L446 写"侧视轮廓完整，没有改变主要产品比例"——侧视下椭球与开口壳轮廓确实相同，**陈述为真但状态不覆盖**。
- 用户截图 = hero 相机 + 前后罩分离态 → 裸露封闭椭球（"巨大的蛋"，L1708）。编排者用户指出后 2.5 分钟定位、6 分钟回退（01:34:29→01:40:23Z）。
- 与「判据封闭性」同族：上一轮失败项（条纹）成了本轮唯一门槛，"整机轮廓"从不在任何门槛里。判据：修复类 submission 的评审只复验失败项、不重跑全状态矩阵。检测层：agent。

### 同一图片 `detail:"original"` 重复读取（同源 `CASE-P-CHILD`，可脚本计数）

- `submission-3/desktop-side-1440x1000.png` 在 L1090 / L1220 / L1306（16:51→17:16Z，25 分钟内）三次以 original 读入，各 31–36 万字符；L1264（126 万）与 L1312（81 万）两次批量读再含同图。
- 该会话 exec 均值 12.5 万字符/次 = 本地基线 exec 均值 2.1 万的 **6×**；窗口峰值 22.6 万 / 25.8 万 → L1548 compaction，距最终交付报告 L1586 **仅 57 秒**，交付宣称写在压缩后。
- 子签名判据（代码层）：同 path + `detail:"original"` ≥3 次 / 30 min。降采样下限由待判对象决定：本例判整机轮廓，≤1200px 副本足够；判小字/条纹按 gates L781「拼成一张 + 保住可读比例」。

## 地板抬升：早先结论的边界修正

早先在 18 次压缩的会话上得出"地板抬升不是杀手"（26,280 → 42,224，+61%）。
在 60 次压缩的会话上**该结论不成立**：

```
seg  0  floor 22,480      seg 40  floor 57,680
seg 24  floor 36,675      seg 48  floor 73,695
seg 32  floor 43,470      seg 60  floor 76,979      +242%，且加速
```
前 24 段每段涨约 590，后 8 段每段涨约 2,000。终值 76,979 / 258,400 = **窗口的 30% 在开工前已耗尽**。

→ 修正表述：**地板抬升在 ~20 次压缩内可忽略，超过 ~40 次后成为主要约束，且斜率递增。**
判据用 `floor / window`，不用压缩次数。

**175 次压缩的新极值**（`CASE-H` 二审，同一 258,400 窗口）：

```
L0      22,480          L48394   87,207
L29018  63,956 (2.8x)   L85128  106,896  ← 峰值
L37903  76,191          末尾    103,144  (4.59x)
```
`floor/window` = **40%**，可用窗口从 91% 缩到 60%，且**全程从未回落**。
斜率不是简单递增：L0→L29018 每千行涨约 1,430，之后趋缓（L48k→末尾每千行仅约 330）——
即**地板在前 30% 就吃掉了大部分增量**，后期是高位平台而非持续加速。
→ 再修正：斜率递增只在前段成立；长会话的正确心智是「早期一次性抬到高位，然后锁死」。

### Compaction 与目标漂移：不要混淆因果

实测 `CASE-D` 的第 60 次 compaction：`replacement_history` 仍有 **229 条 role=user 消息**。
因此“目标原话被 compaction 删掉 → 目标漂移”不是 Codex 上的真实机制。

真实风险是**关系退化**：用户目标仍在，但没有结构化 `supersedes` 链；82 个子 agent 的任务与结果又
经历父目标→子任务→子报告→父摘要的多级投影。Compaction 丢弃的是支撑取舍的物证与跨段关系，
使最近局部目标更容易主导当前工作集。故：

```
compaction 多                         ≠ 已证明目标漂移
初始/最终动作不一致且无 user 变更依据   = P3 可确认的 agent 自漂
目标多且全部有 user 依据                = 合法演化，但需要 handoff 重建 supersedes 图
```

## 已知解析陷阱（踩过）

### Fork 文件继承父级旧 `token_count`：按文件取最大缓存量会制造假命中（2026-08-11 修复，来源 `CASE-L-CHILD` / `CASE-L-PARENT`、`CASE-K-CHILD` / `CASE-K-PARENT`）

Codex CLI Fork 的新 rollout 会先复制父会话历史，其中包含父级已经发生过的 `token_count`。
实测子会话 `CASE-L-CHILD` 在自身 `session_meta` 之前带有父级旧记录：
`input=187122 / cached=184704`；但它在新 `session_meta`、新 user message 之后产生的首个真实请求为
`input=189629 / cached=6912`。父会话 `CASE-L-PARENT` Fork 前末次请求则为
`input=190694 / cached=188800`。

因此 `max(cached_input_tokens)`、文件内任意 `cached_input_tokens > 0` 都不能证明 Fork 子请求命中长前缀；
必须以子线程最新 `session_meta` 为边界，只读取其后新 turn 对应的 `last_token_usage`。为消除临时解析器分叉，
`session_events.py` 现保留 `cached_input_tokens` / `cache_write_input_tokens`，`drill.py` 有界显示这些字段及
`session_meta` 的 thread/session/fork lineage。

独立第二组复现：父 `CASE-K-PARENT` 末次请求
`input=169358 / cached=167680`，Fork 子 `CASE-K-CHILD` 首个新请求
`input=171093 / cached=6912`；子文件同时复制了父级 `cached=167680` 的旧记录。

**Gate（n=2）**：任何 Fork 缓存审计若未先证明 child `session_meta.forked_from_id == parent.id`，
并将取样限定为该 child 边界后首个新 turn 的 `last_token_usage`，必须拒绝给出“命中/未命中长前缀”结论。

### Kimi 控制面消息伪装成 user objective + 索引字段失配（2026-08-08 修复，来源 `CASE-AF`）

Kimi 的 `turn.prompt` 会把三类控制面文本按 user message 落盘：`<notification ...>` 后台任务通知、
`Continue working toward the active goal.` Goal 自动续跑提示、以及
`User activated the skill ... <skill-loaded args="...">`（真实请求在 `args`，后面拼完整 Skill 正文）。
旧解析器把它们全部计入目标轨迹，导致主 agent 的实测值：

```
修复前  substantive=90  ambient=168  recurring_ask_clusters=4
修复后  substantive=61  ambient=197  recurring_ask_clusters=1
```

其中修复前的 4 个“重述簇”有 3 个纯属控制面事件（10 条完成通知、2 条失败通知、17 条 Goal
自动续跑）；修复后只保留用户真实重述的 review/rebase 诉求。Skill 激活消息不整条丢弃，
`unwrap_user()` 只提取 `args`，从而保留最初审计目标并阻止完整 Skill 正文进入目标轨迹。

同一 session 还暴露 Kimi 索引 schema 分叉：`session_index.jsonl` 用 `sessionId/sessionDir`，
而 `session_locate.py` 只显示 Codex 的 `id/thread_name`，命中结果打印成 `?` 且无路径。
修复后同一查询直接显示 `session_d53e...` 与其 session 目录。定位器必须按索引来源兼容字段，
不能把“索引命中但渲染为空”误判为会话不存在。

### FAIL_RE 假阳性：读到含 error:/FAILED 的内容也被计为失败（2026-08-05 发现，来源 `CASE-I` team）

`FAIL_RE = exit code:[1-9]|Traceback|FAILED|error:`（case-insensitive）作用于工具输出全文，
不区分「执行报错」与「输出内容里出现这些字样」。实测分桶（前置命令启发式）：
member `CASE-I-MEMBER-1` failures=72 中真 build/test 仅 9、读文档假阳性 38；
`CASE-I-MEMBER-2` 38 中真 8、假 22；host `CASE-I` 182 中真 8、假 49、未细分 129。
具体物证：eb28 L255 一次 sed 读设计文档的输出因正文含 "error:" 被计失败。
→ failure_rate 绝对值系统性虚高，**「X 是最大浪费源」类结论必须先按前置命令分桶再下**。

2026-08-26 补充（来源 `CASE-O` L18980–L19420）：owner 闸仍挡不住结构化成功输出里的
`failedPosts: 0`，因为裸 `FAILED` 会匹配字段名前缀。progress_probe 在 33 次成功查询窗口中把
24 次计失败并误报 FAILING；给 `FAILED` 加双侧词边界后变为 1/33、判为 INVESTIGATING。
同一修复使整场 session 的 failure_rate 0.1645→0.0843。正则已在 `session_metrics.py` 与
`progress_probe.py` 同步修复，并按五语料根重跑校准（109 sessions；新 failure_rate
p10/p25/p50/p75/p90 = 0.0749/0.1063/0.1407/0.2389/0.3991）。旧基线不可再与新读数混用。

2026-08-30 补充（来源 Kimi `CASE-AE`）：
`session_metrics.py` 的 `failure_rate = failures / execs` 仍有一条更上游的定义域裂缝——
`failures` 对**所有 tool_output** 跑 `FAIL_RE`，分母 `execs` 却只含 Bash/exec。agent-0 只有
11 次 Bash，但 28 次 FetchURL 与 20 次 WebSearch 输出引用大量 `error:`/`FAILED`，得到
`failures=22, failure_rate=2.0`，比率越过 1。加 `EXEC_OWNERS` 分子闸后变为
`failures=2, failure_rate=0.1818`；20 个非执行命中保留在
`non_exec_failure_markers`（FetchURL 17 / WebSearch 3），不静默丢失。主会话同一修复为
`42/245=0.1714 → 8/245=0.0327`，另保留 Agent 33 / Read 1 个非执行命中。
这与 `timeout_rate` 的分子/分母定义域事故同型：**任何 rate 的分子必须是分母事件集的子集，
不只是 owner 正则看起来“已经有闸”就算完成。** 修复后必须重跑本地基线，旧 failure_rate
分位不可与新值混用。本条为单例解析 bug，机制由代码与越界比率确定，不晋升行为 gate。

### WORKTREE_RE 三段截断：codex worktrees 下的 B 类 fork 检测全盲（2026-08-05 修复，来源 `CASE-I` / `CASE-I-MEMBER-2`）

`WORKTREE_RE = ^(/Users/[^/]+/[^/]+/[^/]+)/` 只取路径前 3 段，`~/.codex/worktrees/*` 下所有
worktree 折叠成同一个"根" `/Users/example/.codex/worktrees`。后果两条：

```
worktrees touched 少报   member CASE-I-MEMBER-2：报 1，实际 2（example-worktree-a / example-worktree-b）
                        host CASE-I：报 3，实际 5
B 类 fork 检测全盲       member 9645 同一相对路径 crates/harness-store/src/lib.rs 在 2 个 worktree
                        被 patch（12x + 5x）→ 报 "none"；修复后 forked_count=1
                        host CASE-I：报 0 → 修复后 forked_count=4，其中 index.html
                        (apps/agent-dashboard/web/) 同相对路径跨 recursive-implementation-v1 /
                        company-os-wave3-dashboard（L3693 物证）
```

修复：`worktree_of` 对 `.codex/worktrees/<name>` 路径多取一段。**读旧报告时注意：
2026-08-05 之前任何 codex worktrees 会话的 `worktrees touched` 与「B class: none」都不可信。**
残留局限：basename 启发式下 `lib.rs`/`main.rs` 这类泛名会跨不同子目录误配，命中后必须人工核对相对路径。

### 分母陷阱：一条无关洪水会把任何「占比」结论稀释到看不见（来源 `CASE-H` 二审，当场踩到并修正）

第一版把「重推导占上下文的比例」算成 **5.8%**，据此几乎得出「代价不显著」的反向结论。
真相是分母被污染了：该会话 401.3M 字符里 **243.6M（61%）是视觉设计评审的截图**
（`view_image` avg 545,043 字符/次，单次最大 11.59M），且**集中在前 3 万行**，与调度毫无关系。

```
不分桶            重推导 = 5.8%          → 「不显著」
剔除图片 + 分区段  调度期 = 21.9%（严格）  → 「每改 1 份状态先读 2.7 份」
```

→ **凡是要给出「X 占上下文 N%」的结论，先把 flood_tool 单列成自己的桶并从分母剔除，
再按会话的**制度区段**（本例：视觉评审期 vs 调度期）分开算。**
一个会话往往有多个互不相干的成本制度，混在一起算占比 = 用一个制度的分母去除另一个制度的分子。
判据：若 `flood_share > 0.5` 且 flood 集中在某个连续行段，则**禁止**在全会话尺度上报任何占比。

### Kimi 的 tool.result 归属

Kimi 同一步可以先连续记录多条 `tool.call`，随后才批量写对应的 `tool.result`。
`tool.result` 没有工具名，只有 `toolCallId`；若只记“最近一次调用”，大部分输出会被归到 `?`。
来源会话 `CASE-Z` 的修复前后：

```
修复前   ?             13 calls   0.04M chars   74.6%
修复后   按 toolCallId 归属；? = 0 calls
```

补丁可能通过 `exec` 以内嵌 JS 字符串执行（`const patch = "*** Begin Patch\n*** Update File: ..."`），
其中换行是字面的 `\` + `n`。贪婪的 `(.+)` 会吞掉整个补丁体并把它当成文件路径，
**污染 patch 计数、文件数与整个仪器分叉检测**。实测差异：

```
修复前   patches=1370  files=1066  forked_share=0.028
修复后   patches=2063  files= 459  forked_share=0.179     ← 6.4 倍
```
正则必须写成 `([^\n\"\\]+)`。

### Claude Code 在 compaction 前把喂进去的记录整块重发（来源 `CASE-Q`，Claude Code，51 MB / 2745 行，n=1 但机制确定、可字节验证）

`compact_boundary` 行之前会出现一整块**逐字节重复**的旧记录：`uuid`、`timestamp`、
`parentUuid` 全部与首次出现时相同。本例是单一连续块 `L2185-2458`，274 行 = 文件的 **10.1%**。

不去重的后果，实测（同一文件，修复前 → 修复后）：

```
execs      354 → 310      (-12.4%)
patches     80 →  65      (-18.8%)   files 27 → 28
Read        92 →  83
"逐字节重复的 bash 重跑"  45 次 → 0 次      ← 全部是幻影
```

那 45 次"重跑"里包含**整段开场定位序列**（`ls ~/.claude/projects`、`ls -d ~/example-project*`
"Look for example project directory"），出现在 L2199 —— 读起来像"agent 在 2200 行后忘了
自己整天在哪个仓库干活、重新冷启动定位"，是一条极有说服力的病理叙事。**它没有发生。**
`uuid` 在 L32 与 L2199 完全相同。差一点就把一个文件格式行为写成行为签名。

→ 判据：任何 Claude Code 计数指标必须**先按 `uuid` 去重**。`session_events.iter_events()
已实现（重复行改发 `meta/duplicate_record`，计数可见，不静默丢弃）。Codex rollout 无 `uuid`
字段，不受影响。**看到"重复执行同一命令"先查 uuid，再谈重跑。**

### ambient 前缀名单会吞掉真目标（双向失真，来源 `CASE-F`，Codex，n=1 单例观察但机制确定）

`is_ambient()` 的实现是 `any(m in text[:400] for m in AMBIENT_MARKERS)` —— **整条二值判定**。
它对"整条都是注入"有效，对"**注入包裹了真请求**"失效：

```
Codex 在用户附图时把真实请求包成：
    # Files mentioned by the user:
    ## <图片路径>
    ## My request for Codex:
    <真正的用户指令>          ← 命中前缀 "# Files mentioned by the user:" 后整条被丢弃
```

实测 `CASE-F`：**L9 是真初始目标**（含"个人支持请求"这一决定全局优先级的约束），
被判为 ambient → `objective_trace.first_substantive` 误报为 L73。
**"首尾一刀"整个建立在 `first_substantive` 上，此陷阱会让初始目标判定系统性错位。**

同一会话反向也错一次：L90「然后继续你的工作」是纯推进，`PUMP_TOKENS` 只收录短应答词、
不含带动词的推进句式 → 计入 substantive，`pump_share` 因此报 0.0（假值）。

⚠️ **最危险的一点：总数看起来是对的。** 该会话 `substantive=9`，而正确成员集
（L9/L73/L121/L276/L365/L400/L514/L526/L552）也恰好是 9 条 —— 一个假阴性与一个假阳性互相抵消，
**计数无异常、成员全错**。因此校验 ambient/pump 必须核对**成员**，不能只核对计数。

**已修复（2026-07-26）**：把包裹型标记从 `AMBIENT_MARKERS` 拆到新的 `WRAPPER_MARKERS`
（`marker → body delimiter` 二元组），新增 `unwrap_user()` 先剥壳再判 ambient；
壳内无正文时才计 ambient。`PUMP_TOKENS` 补入带动词的推进句式，长度上限由白名单自动导出
（原硬编码 `<=8` 恰好卡掉 8 字的「然后继续你的工作」）。修复前后（`CASE-F`）：

```
修复前   substantive=9  ambient=2  pump=0  resume=0
         成员 = [73,90,121,276,365,400,514,526,552]   ← 少 L9、多 L90
修复后   substantive=9  ambient=1  pump=0  resume=1
         成员 = [ 9,   121,276,365,400,514,526,552] + 73  ← 正确成员集
         first_substantive: L9「…个人支持请求…」
```
⚠️ **验收时的一处偏差要记住**：预期 L90 落到 `pump`，实测落到 `resume`。
原因是 `is_pump` 先命中、随后被"环境噪声扣除"规则（`task_complete` 邻接）判为网络续跑。
两者都不进 `substantive`，结论不变；但**「pump ≥ 1」这个验收式写错了，正确写法是
`pump + resume ≥ 1` 且 L90 不在 substantive 成员集里**。验收式只盯一个桶，会被合法的桶间再分类误判为失败。

跨平台回归（三平台，无变化）：Claude Code 诞生会话 `CASE-U` 仍为
`substantive=18 pump=4 ambient=2`（命中其 Stage 1 原验收标准）；
Kimi `wire.jsonl` 仍报 `compactions=None`（非 0）；Codex `CASE-E` 仍为 `substantive=9 pump=2 resume=3`。
（⚠️ 2026-08-10 起 `CASE-U` 的契约值为 `23/4/2`——queued_command 实装后该会话 5 条排队的
人类消息可见了；18 是 queued-盲解析器的读数，引用必须带日期边界。见 §2026-08-04 第 3 条实装记录。）

### 上一处修复只堵了一半：`WRAPPER_MARKERS` 绑死前导 marker（来源 `CASE-D`，Codex，777 MB / 40543 行）

2026-07-26 早些时候引入的 `unwrap_user()` 用的是 `marker → delimiter` **二元组**，
要求前导 marker 必须是 `# Files mentioned by the user:` 才剥壳。但 Codex 会在**任意** ambient 块
之后追加同一个分隔符：

```
<in-app-browser-context source="ambient-ui-state">
  ...自动注入的 UI 状态...
</in-app-browser-context>

## My request for Codex:
接下来完整的review下我们这部分的架构还有没有一些架构问题存在      ← 真请求
```

`<in-app-browser-context` 与 `<environment_context>` 都在 `AMBIENT_MARKERS` 里，
而 `# Files mentioned by the user:` 不在 head → `unwrap_user` 不触发 → 整条丢弃。

实测规模：**138 条 ambient 里 108 条携带真请求**，`substantive` 少报 **94 → 187（少 50%）**。

```
修复前   substantive=94   pump=25  resume=6  ambient=138
修复后   substantive=187  pump=37  resume=9  ambient=30
```

⚠️ **这一次的失真不在"首"，在"尾"**——与 `CASE-F` 正好相反。`first_substantive` 恰好正确
（L11 未被包裹），但**最后一条、决定会话终局的指令 L40395 被吞掉**。后果是
「首尾一刀」把一次**用户明确要求的架构 review**读成了 **agent 自漂**：
尾部 20% 全是 review 动作，而可见的最后一条用户指令是 L39871「提交 pr -> review」，
两者对不上 → 会误判为"agent 自己跑去做架构审查"。

→ **纪律：`first_substantive` 正确不代表 objective trace 正确。首尾一刀要两端都验壳。**

**已修复（2026-07-26）**：新增 `STANDALONE_DELIMS = ("## My request for Codex:",)`，
在 `unwrap_user()` 里**先于** `WRAPPER_MARKERS` 全文搜索，命中即剥壳，不看前导 marker。
分隔符本身就是充分条件——它只由 harness 产生，前缀是什么都不影响它后面是真请求。

### `forked_share` 的适用边界（边界修正，不推翻旧值）

`forked_share` 只测**会话内 patch 落在多少个副本路径上**，测不到"**源副本被改、派生副本落后**"。
实测 `CASE-F`：`forked_share=0.0000`（会话内所有 patch 都只打在 `.claude/skills/` 一份上），
而同一时刻磁盘上 3 份副本里 **4 个文件 md5 不同**：

```
                             .claude   .codex   public-skill-repo
references/mental-model.md    2b5234   2b5b22   2b5b22     DIVERGED
references/signatures.md      4e6e44   910c4e   910c4e     DIVERGED
references/self-upgrade.md    d61f52   c4f8ec   c4f8ec     DIVERGED
scripts/session_metrics.py    cf26f0   3b8d32   3b8d32     DIVERGED
grep -c "229 条"      →  1        0        0
grep -c "P2 仍是半自动" →  1        0        0
```

即两条**已证伪结论**仍在两个已分发副本里生效。
→ 与 `local/gates.md` G1 同一根因（单会话视野测不到仓库层副本），但形态相反：
G1 是"同名仪器在上百个树里"，这里是"派生副本落后一代"。
**审计任何"改了会被 rsync/发布出去"的对象时，必须在会话外补一次副本 md5 对照，不能只看 `forked_share`。**

### Claude Code 把 UI/图片事件记成 user message（来源 `CASE-Q`，Claude Code，97.8 MB / 4947 行）

同一会话里，`<local-command-caveat>`、`/model`、`<local-command-stdout>`、加载 skill 时的
`Base directory for this skill:`、5 条 `[Image: ...]` 和 7 条
`[Request interrupted by user]` 都以 role=user 落盘。旧解析把它们全部放进
`objective_trace.substantive`，并把图片元数据与中断通知聚成两个“用户重述诉求”。

修复前后（同一文件）：

```
修复前   substantive=83  pump=10  resume=2  recurring_ask_clusters=2
修复后   substantive=66  pump=10  resume=3  recurring_ask_clusters=0
```

其中 `/session-forensics <真实请求>` 不能整条丢弃：解析器只取 `<command-args>`；
`/model` 属配置态，明确归 ambient。Claude Code 自动注入的
`Continue from where you left off.` 归推进词，再由轮次生命周期落入 resume。
**目标 trace 的成员必须排除 UI/图片/中断事件；只看 role=user 不等于看到了用户意图。**

### Claude Desktop 跨会话 peer 消息伪装成 user objective（2026-08-20 修复，来源 `CASE-T`）

Claude Desktop 把另一个 session 转来的内容包装成 `<cross-session-message from="...">`，
但仍以 `role=user` 落盘。旧解析器没有这个 ambient marker，于是把 12 条制片 Lead 的条件句、
交付通知和更正全部计入 Owner 目标；其中 L8230 的「**若 Owner 选**直接播放成片」是条件问题，
却被 P3 当成用户已选择该路线，正好遮住随后把带楼成片部署进 AR 的自漂。

同一文件修复前后：

```
修复前  substantive=168  pump=3  resume=8  ambient=57
修复后  substantive=156  pump=4  resume=7  ambient=69
         被剔除行 = 7881/7901/7916/7923/8044/8055/8070/8108/8156/8206/8217/8230
```

`pump/resume` 的一条重分类来自 peer 消息移出目标流后生命周期邻接关系恢复，不影响
「peer 不是 Owner」这个成员级判据。跨平台回归：Claude Code 契约会话 `CASE-U`
仍为 `substantive=23 pump=4 ambient=2`。

**已修复**：`session_metrics.py` 的 `AMBIENT_MARKERS` 加入 `<cross-session-message`。
纪律：P3 的“user message 是唯一外部物证”中的 user 必须是**人类**，不能只信存储层 role 名。

### 同一 peer 事件的纯文本包装漏网（2026-08-20 修复，来源 `CASE-V`）

同一天又观测到 Claude Desktop 的第二种跨会话包装：
`Another Claude session sent a message: <agent-message ...>`。它没有
`<cross-session-message>` 标记，旧解析器因此把主会话 L1274 的 ISSUE-A teammate 完成通知
计成 Owner 目标。修复前后（同一 4294 行 / 9.5 MB 文件）：

```
修复前  substantive=55  ambient=56  objective trace 含 L1274 peer 通知
修复后  substantive=54  ambient=57  L1274 移出目标轨迹；其余 54 条逐行不变
```

**已修复**：`AMBIENT_MARKERS` 加入纯文本前缀 `Another Claude session sent a message:`
（实测冒号后有换行才进入 `<agent-message>`），并在 `unwrap_user()` 的通用 request-delimiter
剥壳之前直接拒绝该外层记录；否则 teammate payload 内引用的 request delimiter 会绕过 ambient 判定。
不以裸 `<agent-message>` 判 ambient，避免把 Owner 自己引用或讨论该标签的文本误删。机制仍是上一条“peer 伪装成 user objective”，
不是新 gate；本条记录的是同一外部事件的第二个载体，防止只修 XML 形态。

### Codex skill 加载块伪装成 user objective（来源 `CASE-I`，Codex，10 MB / 活跃文件）

Codex 在用户点名 Skill 后会把完整 Skill 内容包装为 `<skill>...</skill>`，并以 role=user
写进 rollout。它不是用户重述目标。旧解析器没有 `<skill>` ambient marker，于是把该自动注入
计入 `objective_trace.substantive`；本次 Mission/Wave 取证的初次测量因此把 L11 的 Skill 全文
列为第二条“用户目标”。

同一活跃会话的修复前后（采样行数不同，但期间没有新增 user message）：

```
修复前  L5324  substantive=17  ambient=1  objective trace 含 L11 <skill> 注入
修复后  L5472  substantive=16  ambient=2  L11 消失，first_substantive 仍为真实 L9
```

跨 200 倍规模回归：`CASE-H`（2268 MB / 109032 行）
仍能解析 `substantive=423 pump=63 resume=16 ambient=57`，且首条真实目标保持为 L12。

**已修复（2026-08-05）**：`session_metrics.py` 的 `AMBIENT_MARKERS` 加入 `<skill>`。
这是一类平台注入，不是包裹真实请求的 wrapper；不得把其正文作为用户目标继承。

### 分子横跨全工具、分母只有 exec —— `timeout_rate` 越界（来源 `CASE-G`，Codex，202 MB / 14116 行）

`timeout_rate = timeouts / execs`，但 `timeouts` 是对**每一条 tool_output** 做
`re.search(r"timed out|timeout")`。分子的定义域比分母大，比值可以越出 [0,1] 的语义范围。

```
修复前   timeout_rate = 556 / 1091 = 0.5096      ← 高于全语料 max 0.3424，不可能存在
修复后   timeout_rate =  69 / 1092 = 0.0632      ← 约 p60，正常
```

逐工具归因 556 个命中：

```
wait_agent    481   {"message":"Wait timed out.","timed_out":true}   ← 有界轮询的正常返回
exec_command   49   真·命令超时
exec           20   其中若干只是输出里打印了字符串 `timeout_rate`
write_stdin     3 / list_agents 3 / js 1 / run 1
```

两条独立的污染，任一条单独成立就够把指标毁掉：

1. **把工具的正常返回当故障。** `wait_agent` 说 "Wait timed out" 是轮询在正常工作。
2. **自指污染。** 审计一个**跑过本 skill 的会话**时，被审会话输出里的指标名 `timeout_rate`
   命中了本 skill 自己的正则。审计器读到自己的词表就报警——这是本 skill 长在自己身上的
   B 类裂缝，和 `drill.py` 那次（仪器分叉长在核心步骤上）同构。

→ **已修**：分子加 `EXEC_OWNERS` 白名单闸。非 exec 侧的命中**不丢弃**，改记为新字段
`poll_timeouts`（按工具名分列）并单独打印——它本身是 G7 空转轮询的直接证据，
只是不属于 `timeout_rate`。
→ **通则**：任何 `X_rate = 分子 / 分母` 的指标，**分子的定义域必须是分母的子集**。
本仓已踩两次（另一次见 `forked_share` 的边界修正）。写新指标时先写这一行断言。

### 外层 `exec` 吞掉内层工具归属（2026-08-12 发现，来源 `CASE-M`，Codex，n=1 单例观察）

新工具面会把 `tools.view_image(...)`、`tools.image_gen__imagegen(...)`、`tools.apply_patch(...)`
等调用写在一个外层 `exec` 的 JavaScript 体内。规范化层能从命令体识别操作形状（本会话截至
7040 行识别出 `js:view_image=42`、`js:apply_patch=225`），但 `function_call_output` 的 owner
仍只有外层 `exec`，于是单次成本表把 **1322 次 / 57.76M 字符 / 99.5%** 全记为 `exec`。
有界钻取则直接看到同一 `exec` 输出内含 base64 图片（例如 L3931 的 imagegen 输出约 1.45M 字符）。

因此对这种嵌套工具记录，`flood_tool=exec` 只证明**封装归属**，不能证明真实洪水源是普通命令；
`top_shapes` 能证明内层工具发生过，却不能把输出字符可靠拆回每个内层调用。修复前不得用该桶给
`view_image` / `imagegen` / 普通 shell 排成本。候选修复需要让规范化事件保存内层 call 边界与对应
output；若一个外层调用包含多个内层工具且输出已合并，则必须显式报 `nested_exec_mixed`，不能猜分摊。
本条为 n=1 解析盲点，**未晋升 gate，也不改变既有非嵌套会话的成本结论**。

## 2026-08-25 · dsh 接入带出的四条解析陷阱（来源：DeepSeek Harness 适配，n=19 文件全量对齐）

前三条是**新平台带来的新陷阱**，第四条是老陷阱的新化身。

1. **压缩层的默认失效是静默截断，不是报错。** `session.jsonl.zstd` 是多帧追加；
   缺 `read_across_frames` 的解码器**只返回第一帧并报告成功**——27692 行读成约 20 行，
   而且长得像一个干净的短会话。这是我们自己制造的 B 类裂缝（引用一个已不再成立的东西）。
   ⇒ 规则：**宁可响亮拒绝，不许退化成单帧读**。同理 `size_bytes` 是压缩后大小，
   一切按字节的直觉必须换成解压长度（实测 6.8 MB → 13.4 MB，2×）。

2. **行数不是规模（新形态）。** chunk row 把 token 级 delta 打包（信封放大 ~56×），
   内容又会在收尾的 `assistant/message` 里以已组装形式再出现一次。
   实测：chunk row 22210 行 vs assistant/message 263 条。
   按行数这是"两万事件的巨型会话"，按物证是 263 步 349 次工具调用。
   ⇒ 与 Claude Code 重发压缩块**同型**（见 §2026-08-10）：计为噪声、保留计数、不进叙事流。
   这是同一条失效边第二次以不同外衣出现，因此它已经不是观察，是闸。

3. **派生存储会以"完整"的样子给出不完整的清单。** `workspace.json` /
   `session_projcache.json` 只列根会话：实测列 9 个、磁盘 19 个，缺的 10 个**全是子 agent**
   （父 27692 行，四个孩子共 9040 行 ≈ 1/4 工作量）。没有任何字段提示它不全。
   ⇒ 与 `local/falsified.md` 里"派生副本必腐且缺失效传播"是同一条；
   派生存储**只配定位，不配计数**。

4. **符号链接骗过 grep。** pnpm 包目录是符号链接，`grep -r <pat> .` 不会进入，
   返回空 ≠ 不存在。本次调查确实被骗过一次（把"找不到声明"读成"未声明"），
   靠显式路径 grep 的结果对照才发现。⇒ 求证包内 `.d.ts` 一律 `grep -R`。
   更一般地：**"搜不到"从来不是证据**，这与 SKILL §1.5「缺席不可推断」是同一条。

附带一个正向发现（罕见）：dsh 是四个平台里唯一能把「压缩致盲」从论断变成**可测量量**的。
`compaction/summary` 直接给 `shadowedSeqs` / `shadowedTokenCount`——被遮蔽的事件与 token 数
是读出来的。⇒ 能力位 `compaction_loss`；其它平台该项必须 `null`。
本 skill 长期只能引用的 ~200:1 估计，在这一个平台上可以被真正校验。

## 统计口径纪律（本节由三次连续踩错逼出来）

**1. gate 的依据是单次成本，不是出现频率。**
`view_image` 在某语料 2026-07 的 29 个大会话里只有 2 个用到——但用到的那个是
133 次调用 / 1790 万字符 / 占该会话输出 35.5% / 32 次压缩。
**低频高危正是 gate 最该管的东西**，用"出现频率低"降低 gate 优先级是错的。
对应地，描述一个洪水源要说清**触发条件**（"做图片工作流时"），而不是基础发生率。

**2. 「主导工具」是赢者通吃口径，回答不了「X 花了多少」。**
某月 `exec` 在 73/86 会话里排第一，不代表 `view_image` 不烧钱——它可以占 30% 而
`exec` 占 50%，照样"不主导"。问成本就用**体积占比**，问画像才用主导工具。

**3. 基线必须分时间窗，否则它自己就是过期引用。**
跨 8 个月的语料混合了已经改变的用法，拿它判断本周的会话＝**引用一个不再成立的分布**，
正是本 skill 要检测的 B 类裂缝，出现在校准环节自身。
`local/baseline.json` 应按月/季分层，至少要报告趋势。

**4. 计数 vs 比率必须一致。**
排序榜曾用 `dissat_count`、相关性用 `dissat_rate`，导致 `floor_share` 榜看起来富集不满
（8 个里 4 个），实际只是大会话用户消息多、命中机会多。同一个结论里混用两种口径＝假信号。

## 已证伪（不要重犯）

| 曾提出的阈值 | 被什么推翻 | 状态 |
|---|---|---|
| compaction ≥3 → 强制审计/handoff | **p50 = 10** | 废弃，会命中近 100% 会话 |
| 单点资源饱和 ≥20 次 | 某 case 的 39 次仅在 p75 | 废弃 |
| 66 条 timeout = "实锤" | 归一化后 timeout_rate 仅 0.04，是其他会话的 1/6~1/10 | **结论反转** |
| 修补震荡 ≥5 次 | p75=2 而 p90=16，5 是从单个 case 倒推 | 降级为占位符 |
| "三条签名已在某 case 验证 ✅" | 那是用它调出来的 n=1 自证 | 不构成验证 |
| pump_share = 0.30 | 未扣除网络中断续跑 | 修正为 0.18（虚高 67%） |
| "jq 手搓是上下文元凶" | jq 仅占输出量 1% | **错判**，真凶是原生 `read_thread` / `view_image` |
| "地板抬升不是杀手" | 60 次压缩下 +242%，占窗口 30% | **补边界**：≤20 次可忽略，>40 次成为主要约束 |
| "view_image 是主要洪水源" | 全语料只在 12/110 会话主导 | **改述**：低频高危——只在图片工作流出现，一出现即灾难级（133 次调用 / 1790 万字符 / 35.5%） |
| "12/110 说明它不重要" | 用出现频率给 gate 降权是错的 | **同样废弃**，见 §统计口径纪律 第 1 条 |
| **"结构指标能识别病态会话"** | **n=101 全部 \|r\|<0.22；排序榜不富集；连同源泄漏变量都塌到 ±0.06** | **主张收窄，见下** |

**所有定量阈值当前地位 = 占位符，不是阈值。**

## 最大的一次收窄：这个 skill 不是分类器

n=101 全语料验证结论：**没有任何结构指标能预测「会话是否有问题」。**
`instrument_patch_share`、`forked_share` 的 TOP-8 会话，用户表达的不满全部为零。

关键诊断在泄漏列：`pump_share` 与 `recurring_ask_clusters` 是**从用户消息直接派生的**，
它们也塌到 ±0.06 —— 说明**问题在标签而非预测变量**（标签召回率实测 <50%：某会话有
7 条明确不满「好烂」「很烂」「别人听到都会跑路了」，全部漏检）。

定位因此收窄为 **证据提取器 + 钻取路由器**：

```
仍然成立（直接测量，不需统计支持）
  单次工具成本 / 预算流向 / 仪器分叉的存在 / 地板轨迹 / 200:1 压缩比 / 心智模型
不成立（已放弃）
  用指标判定会话好坏；用指标排序挑出病态会话
```

判断由读它的人或审计 agent 做；指标只负责**把 776 MB 压成 4 KB 可读物证，让判断成为可能**。

⚠️ 遗留：「用户是否表达不满」可能根本不是好的验收标准——用户可能对技术上很糟的会话满意
（因为看不见），也可能对健康会话不满（因为需求变了）。更诚实的标准是**产物质量**
（PR 是否被 revert、测试是否真绿、后续是否返工），但需接外部信号。**当前未验证，不要假装已验证。**

## 尚未验证的前提（最大的方法论缺口）

1. **无对照组**：不知道签名测的是"病理"还是"会话复杂度"。基线里存在比已知卡死会话更极端的样本
   （forked=11 / maxpatch=49 / maxcmd=226），但不知它们是否也卡住了。
2. 唯一出路是**标注集**：标 20–30 个「卡住 / 正常」，算每条签名的精确率与召回率，从标注集反推阈值。
3. **最小可反驳实验**：跑全量指标；若分布呈单峰且与「卡住」标签无关，整条签名路线被证伪，需回到纯语义读法。

## 已知实现局限

- `find_recurring` 用 trigram Jaccard ≥0.45 聚类，措辞差异大的重述会漏（实证：同一授权诉求说了 3 次，
  只聚出 2 条，第 3 次因换了说法未入簇）。宁漏勿误报。
- `is_pump` 只覆盖中英文常见应答词，需随语料扩充。
- `INSTRUMENT_RE` 用路径启发式判定"仪器"，跨项目需校准。
- **P2 仍是半自动。** 当前脚本会给出 `top_tools / top_commands / top_patched /
  output_volume_by_tool`，可发现“高频、昂贵、反复手工执行”的候选，但尚未实现跨会话 tool-sequence
  motif、成功结果绑定与稳定边界提取。“复发 ≥3 次且有稳定操作序列 → skill（+harness）”目前是
  人工/审计 agent 的晋升合同，不是自动判定器。Token 成本只能排候选优先级，不能单独决定是否晋升。
- **目标 trace 的逐字合同已于 2026-07 补齐**（`USER_MSG_CAP` 400→8000 + handoff §1 附逐字全文，
  见 self-upgrade 升级记录）。残留边界：>8000 字的粘贴型消息仍截断（带标记）；表格 150 字只是
  扫读预览，逐字以 §1 附为准。⚠️ 本条旧文在修复落地后仍写着「尚待补齐」约两周（2026-08-10
  审计发现）——文档载体没进当次修复的清单，实证归入下方「跨载体修复传播」gate。

## 2026-08-04 · Claude Code queued_command 三连发现（来源：Example corpus 认知考古 CASE-AB + 验收员复核）

1. **queued_command 对 objective_trace 不可见**：Owner 在 agent 忙时排队的消息落盘为
   `type:"attachment"` + `attachment.type:"queued_command"`，`message.content` 为 null——
   substantive 过滤器一条不报。实测 CASE-AB 漏 20 条人类提问（含任务点名要找的那条 L4303）。
2. **机器判据存在：`attachment.origin.kind`**——human/peer 二分字段。实测 78 条中
   human 20 / peer 7 / 无 origin 51，与人工判读完全吻合。⚠ 教训：第一轮考古声明了
   "无机器判据"这个**假盲区**，被验收员读原始 JSONL 证伪——声明假盲区比漏真盲区更糟
   （会让下一个 agent 放弃已经能做的事）。声明"无判据"前必须先枚举记录的全部字段。
3. **drill.py --grep 与文件级 grep 不等价**：--grep 走 iter_events 规范化层，漏
   attachment/queue-operation 类记录（实测目标串在文件里存在而 --grep 报 no match）。
   考古 queued 内容需直读该行 JSON（有界窗口纪律不变）。
→ ~~待办~~ **已实装（2026-08-10，来源审计会话 `CASE-Y`）**：`session_events` 现对
   `attachment.type=queued_command` 按 `origin.kind` 分流——human → `user_msg`（extra.queued），
   其余（peer / task-notification 无 origin）→ meta/`queued`（drill 可见、不入目标轨迹）。
   实测 `CASE-AB`：substantive **101→121（恰 +20）**，与第 1 条人工数出的漏检完全吻合，
   L4303 已入目标轨迹；`CASE-U` 18→23（该会话 7 条 queued = human 5 / no-origin 2，+5 全为 human）；
   `CASE-F`（9 条成员集）与 Kimi `compactions=None` 契约值不变。drill --grep 亦可命中 queued 正文。
   ⚠️ 由此 **`CASE-U` 的回归契约值更新为 `substantive=23 pump=4 ambient=2`**（2026-08-10 起），
   旧值 18 是 queued-盲解析器的读数，引用旧值必须带日期边界。

## 2026-08-09 · 死于成功之后：最后一个副作用已生效但 turn_aborted，任务被误判为"没完成"（来源 `CASE-J`，Codex，13 MB / 10319 行，n=1 单例观察但机制确定）

会话最后一个动作是 `gh pr merge EXAMPLE --merge`（02:57），紧接 `turn_aborted`，最终 assistant 汇报永远没有发出。
GitHub 对质：PR EXAMPLE 实际 **02:57:36 已 MERGED**，merge tree 与冻结头 tree 字节一致（SYNTHETIC-SHA）。
用户由此提问"这个任务为什么一直完成不了"——**表象来自叙事流缺一条尾巴，不来自物证流**。

- 检测层：代码→agent。脚本给出「尾部最后一个 state-changing 调用 + 其后是否有 assistant 汇报 + turn_aborted」；
  "该副作用是否已在外部系统生效"必须去外部（gh/CI/文件系统）对质。
- 审计纪律：接手一个"没完成"的会话，先对质**最后一个副作用命令的外部结果**，再接受"未完成"这个前提。
  这是 B 类裂缝的镜像：不是引用了已失效的东西，而是**外部已成立的东西没被引用**。
- 同会话另证：`--watch` 型轮询 gate 第 2 次复发换皮出现——`gh run watch`（"Refreshing every 15 seconds"）
  挂在 exec session 里、用 `write_stdin` 每 30s 拉一次缓冲，尾部连续 ≥10 轮。grep `write_stdin` × "Refreshing"
  可代码检测。gate 原文只写了 exec_command 直跑 `--watch/-f`，未覆盖"watch 挂后台 + write_stdin 泵"这个形态。

## 2026-08-10 · 对本 skill 自身的全量审计：三条解析陷阱 + 一条平台换代（来源审计会话 `CASE-Y`，Claude Code）

### progress_probe 自指污染误报 WAIT_LOOP（已修）

探针的 `POLL_TIMEOUT_RE`/`FAIL_RE` 对**全部** tool_output 做裸子串扫描。审计会话只是在
**读**讨论超时的文档（signatures/falsified 正文含 "Wait timed out." 等字样），就被判成：

```
修复前   WAIT_LOOP（4 empty polls, 零编辑）——4 个命中全是 Read 输出里的文档正文
修复后   同窗口 empty-polls=0；真轮询 fixture（wait_agent × 4 返回 timed_out:true）仍判 WAIT_LOOP
```

修法与 `timeout_rate` 三代教训同源：**owner 闸**——poll 只认 WAIT_LIKE 工具的输出、
fail 只认 EXEC_LIKE 工具的输出（归属按位置规则，output 属于最近一次 call）。
该教训 2026-07-31 已在 session_metrics 修满三代，探针里原封未动 →「跨载体修复传播」gate 的 n=1。

### Claude Code subagent 已迁出主文件（平台换代，B 类）

platforms.md 原断言「子会话与主会话同一文件内交织（isSidechain）」。实测 2026-08 布局：
subagent 在 `<session-dir>/subagents/agent-*.jsonl` **独立文件**；本语料 9 个会话有该目录，
**最大 40 个主会话文件里内联 sidechain 记录 = 0**。审计纪律：主文件 `sub_agents=0` ≠ 没用
子 agent，必须 `ls <session-dir>/subagents/`。这是「引用了一个已不再成立的东西」长在
本 skill 的平台真值表上——存储布局是外部依赖，它换代时没有任何失效传播机制通知这张表。

### worktree_of 非 /Users 根全盲（已修，基线语义不变）

`WORKTREE_RE` 原只匹配 `/Users/...`：`/root/*`、`/home/*` 全部折叠为伪根 `?`，
跨树 fork 检测在这些路径上**结构性失明**（与 2026-08-05 修的 `.codex/worktrees` 三段截断同构）。
已扩展：`/Users|/home` 取 home 下两级（语义与已校准基线一致，不动存量读数）、
`/root` 沙箱扁平取一级（`/root/<workspace>`）。边界：**曾对含 /root 补丁的会话报过
`forked=0 / B class: none` 的旧报告不可信**；此类会话在新码下 forked_share 合法上升不算回归。

## 单次成本速查（自 SKILL.md 迁移，2026-08-18；详情见上文§上下文洪水）

| 工具 | 实测单次成本 | 规则 |
|---|---|---|
| `read_thread` | 554 万字符 ≈ 138 万 token（已设 turnLimit:10 等限制仍如此） | 只许有界钻取（cursor + 极小 turnLimit）；同一件事本 skill 脚本花 ~4 KB，相差约 2000 倍 |
| `imagegen` | 均值 225 万字符/次；`CASE-D` 两次调用 411 万字符 ≈ 4 个窗口 | 生成图落盘取路径，不把返回体带进上下文；按 per-call 排序洪水源，不按 share |
| `view_image` | 均值 81.6 万字符/次，未降采样单次可达 120 万字符 ≈ 30 万 token | 看图前先降采样；两个被审会话里它占全部工具输出 57% 和 87% |
| `--watch`/`tail -f` + `write_stdin` | `CASE-D` 实测 wait 331 次共 5564 万字符 ≈ 1400 万 token 等 CI | 每次 write_stdin 拉回全量缓冲 → O(n²)；等待用有界轮询（最后答案来自一次 `gh run view`） |
