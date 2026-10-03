# 更新模式：已有地图 + 新事件 → 新地图 + 变化

地图不是一次性报告。工作还在进行时，观察者持有地图，隔一段时间把新发生的事并进去，
看"陷进一个局部问题、丢了全局"有没有在发生。压缩或换人之后，`map.md` 就是观察者自己的记忆。

## 目录

```
<地图目录>/
  ledger.json  ledger.md     首次的账本（请求 / 发起查看时说的；agent 自述的缺口只作参考）；skeleton.json 原始骨架
  inventory.json inventory.md 现成资源清点（切点变了就重跑）
  delta/                     更新时抽的新账本
  map.json                   当前地图（唯一正本）
  report.html  map.md        render.py 的输出
  history/map-<时间>.json    每次更新前存的旧图
  check/                     请人核对时的派工说明（brief.py）
```

## 步骤

1. **存旧图**：`mkdir -p history && cp map.json history/map-$(date +%m%d-%H%M).json`
2. **只抽新的，并进地图**：`ledger.py A=<会话> [B=… 新会话加简称] --out <dir>/delta --map <dir>/map.json --merge`
   - 每个会话从地图里的 `sessions[].ledger_through + 1` 往后抽（要改就用 `--since A:行,B:行`）。
   - 编号不重排：已在地图里的请求沿用原 id（在别人 `also` 里的标"另见"），新请求用全文件里的编号，被占用才顺延。
   - `--merge` 把新请求的骨架（批准类已折进前一条）追加进 `map.json`，并更新 `ledger_through`。
     表头的剥离计数只算新的这段。续开文件开头重发的同一句不会变成新请求。
3. **读新段**：按 delta 的 segment 统计挑段读。会话还在跑时先 `liveness.py` 看状态。
4. **改地图**：
   - 新请求加进 `requests`，归到目标；老请求的状态按新证据改。
   - 目标被用户改写时，旧目标标 `superseded`，新目标引用那条请求。不要悄悄改旧目标的文字。
   - 新步骤加到 `process` 末尾，标 `layer`（只是纵轴）和它在试的 `problems`；属于计划哪一步就加进那一步的 `via`，
     计划走到哪（`doing` / `waiting`）跟着改。
   - 重新判断主线（`alignment`）：新步骤连着在计划外、或在同一个问题上反复尝试而结果没变好，就不再是 `on_track`；
     当前主线那一步（`plan.now`）停下来等用户拍板、工作确实停着，才写 `waiting`；别的步骤在等不算。
   - 问题：已解决的改 `resolved`，新问题加进来，看 `same_root`；被 ≥3 步试过的，判断是打转（`spin`）还是不是（`not_spin` 加理由）。
   - 新段里读了、加载了哪些现成资源：`inventory.py … --until <新切点>` 重跑，`resources` 跟着改。
   - 目标的确认状态：用户这段时间确认了哪个推断的目标，改成 `confirmed` 并写 `confirm_refs`。
   - 更新 `answer`、`headline`、`next`，`sessions[].read`、`updated`。
5. **算变化**：`map_diff.py history/<上一版>.json map.json --json delta/diff.json`（问题按内容配对，重新编号也认得出）
6. **写"这段时间发生了什么"**：把 `update` 写进 map.json：
   ```json
   "update": {"since": "09-29 10:00", "story": "两三句话：这段时间主要在做什么、离目标近了还是远了",
              "changes": ["R12 open → done", "新问题 P6：……", "最近 3 步都在试 P4，失败数没降"]}
   ```
   `changes` 从 map_diff 输出里挑重要的，不全抄。
7. `map_check.py --ledger delta/ledger.json --verify --apply`（新抽的这段都要有归属），然后 `render.py`。

## 什么时候要叫停

map_diff 出现下面任一条，回复第一段就要说，不等到报告里：
- **主线变成"陷在局部"或"偏离目标"**；
- **新步骤连着 ≥3 步不在计划里**，或**最近 ≥3 步都在试同一个问题**、结果没有稳定变好（打转）；
- **第一件事变了**，而用户没说过要换方向；
- 有 `open` 的请求连续两次更新都没动，而 agent 在做别的；
- 目标被改写但没有对应的用户请求（AI 自己换了目标）。

叫停的写法：说现象（带位置）、说它离哪个目标远了、给一个具体的回看问题。不下命令。

## 同一件事，多个会话

一件工作跨多个会话时（续开、换 CLI、子 agent），全部放进同一张地图，各自一个简称。
Codex 的续开文件名是 `rollout-…-<线程id>_<子id>.jsonl`，用 `session_locate.py <线程id>` 一次找全，按时间给 A、B、C。
每个会话写一句 `note`（这个会话主要做了什么）和 `when`，报告会列"每个会话做了什么"，过程表和时间线按会话分段。

## 还没验证的

编号沿用和并入（`--map --merge`）在合成会话上切两半测过。在工作进行中隔一段时间反复更新、看叫停是否及时，还没做过。
想看某个时刻会话是什么状态（比如回看"那时候它是不是卡住了"），用 `liveness.py <会话> --at <时间>`。
