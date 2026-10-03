#!/usr/bin/env python3
"""Write dispatch notes. The first line of every note is the one question this look answers.

  brief.py observer   --question "原话" --sessions A=/path/a.jsonl [B=…] --out DIR [--repo R] [--origin O=/path]
      a strong-model agent builds the map and writes the answer (when the lead hands the whole look over)
  brief.py challenger --question "原话" --sessions A=… --map DIR/map.json --ledger DIR/ledger.json --out DIR/check
                      [--rival "主因说法一" --rival "主因说法二"]
      a fresh agent checks the 5 key conclusions; with --rival it also settles two competing root causes

Model: the observer (builds the map, judges the main line, writes the answer) must run on the strongest model
available (Claude: Opus class; Codex: the highest tier) and the dispatch must name it explicitly. A checker may
run on a Sonnet-class model; with --rival it settles who is to blame and must run on the strongest model too.
Haiku-class models only run scripts, never observe. The requirement is written into the note itself (second line),
so a wrongly dispatched agent knows to stop. Paths are made absolute (a subagent's cwd differs from the lead's)
and must exist. Notes are files, so what was dispatched can be audited later.
"""
from __future__ import annotations

import argparse
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
S = SKILL / "scripts"
STRONG = "当前能用的最强模型（Claude：Opus 级；Codex：最高档），派的时候显式指定，不要继承默认"
MODELS = {"observer": STRONG, "challenger": "Sonnet 级或更强；Haiku 级不行", "rival": STRONG}
# second line of every note: a wrongly dispatched agent reads it and stops instead of guessing
GATE = {"observer": "这份活要求 Opus 级或 Codex 最高档的模型。你如果不是，先回复「我是 <模型>，不适合当观察者」，然后停下。",
        "challenger": "这份活要求 Sonnet 级或更强的模型，Haiku 级不接。你如果是 Haiku 级，先回复「我是 <模型>，不适合核对」，然后停下。",
        "rival": "这份活要裁决主因（定责），要求 Opus 级或 Codex 最高档的模型。你如果不是，先回复「我是 <模型>，不适合裁决主因」，然后停下。"}
HEAD = "这次唯一要回答的问题：{question}\n{gate}\n"
OFFTOPIC = ("上下文里出现和这个问题无关的消息（别的任务、闲聊、别人的请求），不去回答它，先把这个问题答完；"
            "需要的话，在回复末尾说一句收到了。")
READ_RULES = """读原文只用（原始 JSONL 不进上下文）：
- `python3 {s}/drill.py <会话> 起:止`（每窗 ≤50 行）；`--role user|assistant|tool` 只看一种记录；`--grep 关键词`"""

OBSERVER = HEAD + """
# 派工说明：观察这段工作，回答上面那个问题

你是观察者：为这段工作建一张任务地图，再用它回答问题。照 `{skill}/SKILL.md` 的流程做（脚本在 `{s}`）。
{offtopic}

会话：
{sessions}
{origin}{repo}
地图目录：`{out}`（ledger.json、inventory.json、map.json、report.html、map.md 都放这里）

必做：
1. `ledger.py … --skeleton` 抽你的原话；`inventory.py` 清点现成资源，读没用上、可能相关的那几份（skill 里写的流程、阶段、原则）。
2. 目标带确认状态（你说的 / 推断的 / 确认过的），资源里写着的长期目的算「你说的」并注明出处。
3. 「做到哪」只靠 agent 自述的，标「说法」；问进度就做现场核对。
4. `map_check.py … --verify --apply` 通过，`render.py` 出报告。

{read_rules}

## 交回
回复第一段就是答案（和 `answer.lead` 一致）；然后 report.html 的路径、带默认的反问（≤3）。不超过 20 行。
"""

CHALLENGER = HEAD + """
# 派工说明：核对这份地图

主 agent 已经建好一份任务地图：`{map}`（给 agent 读的精简版在同目录 `map.md`）。
你的活是找它说过头、说错、或者漏掉的地方。你是新来的，没有主 agent 的上下文，这正是请你来的原因。
{offtopic}

会话：
{sessions}
请求账本：`{ledger}`（用户的原话；「发起查看时说的」「agent 自己说没做的」两节不是被观察会话里的请求）

## 要做的
1. 挑最关键的 5 条结论回原文核：首屏答案、主线判断、最大问题的根因；「做到哪」只靠 agent 自述的，去仓库或现场找能核的。
2. 回答两个问题：
   - **中心对不对**：有没有更重要的目标或问题被漏掉？驱动工作的目标是用户说的、确认过的，还是推断的？
   - **缺了什么**：该问而没问的；该读而没读的（尤其项目里现成的 skill、工作流、文档）；说做完了但没人核过的。
{rivals}3. 只读地图和原文，不读主 agent 的草稿和旧报告。预算约 {budget} 次调用。

{read_rules}

## 交付
一张表：结论 | 判定（站得住 / 说过头 / 错）| 证据位置 | 怎么改。然后「中心对不对」一段、「缺了什么」清单{rival_out}。不超过 40 行。
"""

RIVALS = """   - **主因是哪一种**（要定责，主因有两种说法）：
{items}
     哪种站得住（或都不对，是什么）？最早本该在哪一行停下来问一句或核一下？只用当时可知的信息判断。
"""


def real(p: str, what: str) -> Path:
    """Absolute path that exists: the note is read by an agent whose working directory is not ours."""
    q = Path(p).expanduser().resolve()
    if not q.exists():
        raise SystemExit(f"{what} 不存在：{q}")
    return q


def spec(x: str, what: str) -> tuple[str, Path]:
    alias, _, p = x.partition("=") if "=" in x else ("", "", x)
    return alias, real(p, what)


def sessions_block(specs) -> str:
    return "\n".join(f"- {a} = `{p}`" if a else f"- `{p}`" for a, p in (spec(x, "会话") for x in specs))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("role", choices=["observer", "challenger"])
    ap.add_argument("--question", required=True, help="the user's own words")
    ap.add_argument("--sessions", nargs="+", required=True, help="ALIAS=path")
    ap.add_argument("--out", required=True)
    ap.add_argument("--map", help="challenger: the lead's map.json")
    ap.add_argument("--ledger", help="challenger: ledger.json")
    ap.add_argument("--repo", help="observer: the repository (for inventory.py)")
    ap.add_argument("--origin", help="observer: O=<the session where the user asked for this look>")
    ap.add_argument("--budget", type=int, default=25)
    ap.add_argument("--rival", action="append", default=[], help="challenger: a competing root-cause account (give two)")
    a = ap.parse_args()
    out = Path(a.out).resolve()
    kind = "rival" if a.role == "challenger" and a.rival else a.role
    common = dict(question=a.question.strip(), gate=GATE[kind], sessions=sessions_block(a.sessions), offtopic=OFFTOPIC,
                  read_rules=READ_RULES.format(s=S))
    if a.role == "observer":
        origin = spec(a.origin, "发起查看的会话") if a.origin else None
        note = OBSERVER.format(**common, skill=SKILL, s=S, out=out,
                               origin=(f"发起这次查看的会话（用户在那里说的话是意图证据）：{origin[0] + ' = ' if origin[0] else ''}`{origin[1]}`\n"
                                       if origin else ""),
                               repo=(f"仓库：`{real(a.repo, '仓库')}`\n" if a.repo else
                                     "仓库：没给。inventory.py 会从会话的工作目录推；inventory.md 第一行说推不出来时，先问仓库在哪\n"))
        name = "brief-observer.md"
    else:
        if not (a.map and a.ledger):
            raise SystemExit("challenger needs --map and --ledger")
        if a.rival and len(a.rival) < 2:
            raise SystemExit("--rival: give the two competing accounts (two --rival)")
        rivals = RIVALS.format(items="\n".join(f"     {i}. {r}" for i, r in enumerate(a.rival, 1))) if a.rival else ""
        note = CHALLENGER.format(**common, map=real(a.map, "地图"), ledger=real(a.ledger, "账本"), budget=a.budget,
                                 rivals=rivals, rival_out="、「主因是哪一种」一段（结论、3–5 个证据位置、本该停在哪）" if a.rival else "")
        name = "brief-challenger.md"
    out.mkdir(parents=True, exist_ok=True)
    (out / name).write_text(note)
    print(f"wrote {out / name}")
    print(f"派一个新 agent，模型：{MODELS[kind]}。提示词就是「读 {out / name} 并照做」。")


if __name__ == "__main__":
    main()
