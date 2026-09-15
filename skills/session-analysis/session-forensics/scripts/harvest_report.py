#!/usr/bin/env python3
"""Generate a harvest report — the P2 assembly that did not exist.

Why it was missing and why that mattered
----------------------------------------
SKILL.md listed three peer assemblies:

    audit report   = P1 (+P3) (+P2)
    harvest report = P2
    handoff packet = S + P1 + P2 + P3

Only `handoff_packet.py` existed. `audit` has a documented procedure, so it is
runnable. `harvest` had neither a generator nor usable material: `top_commands`
buckets raw command prefixes, so on Codex the leaderboard ranked the JS wrapper
and the operation that actually caused an incident (a three-copy rsync run five
times by hand, skipped the sixth) did not appear at all. So "harvest report = P2"
was an unbacked claim in the very skill whose signature table calls that 无证宣称.

What this fills and what it refuses to fill
-------------------------------------------
Filled (code — identity and counting, deterministic, whole-file):
  operation profile, per-operation failure attribution, per-call context cost,
  violation counts for the gates that are machine-checkable.

Left as stubs (agent — equivalence and intent, NOT decidable at this layer):
  whether two DIFFERENT executables were doing the same job (implementation
  forking), and therefore which candidate becomes a gate / skill / harness.

That boundary is measured, not assumed. `distinct_invocations` counts parameter
diversity, not reinvention: rsync scored 2 (only the destination changed) while
sed scored 16 (a different file and range every time). The signal that actually
justified promoting two harnesses — jq vs sed vs python3 hand-rolling one
operation — came from an agent reading the trace, and no counter reproduces it.
Do not add a field that pretends otherwise; state the limit instead.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from session_metrics import scan  # noqa: E402

STUB = "<!-- TODO 需要判断，不可由脚本填写；留空即视为未完成 -->"

# Gates from references/signatures.md that a script can actually count. The rest
# of the gate list needs a trace read and is deliberately absent here.
MACHINE_CHECKABLE_GATES = {
    "js:apply_patch": (
        "用 exec 内嵌补丁串打补丁",
        "补丁全文进两次上下文（arguments + output）；实测 avg 50,276 字符/次，"
        "改用 apply_patch 工具是 avg 214 字符/次，相差约 235 倍",
    ),
    "js:write_stdin": (
        "裸 write_stdin 轮询",
        "每次调用把累积 stdout 全量拖回上下文 → O(n²)；见 G7（--watch / tail -f）",
    ),
}
# Per-call cost, not share. share was falsified as a ranking key.
EXPENSIVE_PER_CALL = ("read_thread", "imagegen", "view_image", "wait", "take_screenshot")


def section_operations(r: dict[str, Any]) -> str:
    shapes = r["evidence_flow"].get("top_shapes") or []
    out = ["## 1. 操作剖面（代码：同一性 + 计数）", "",
           "`calls` 统计的是**操作**而非 exec 调用数——一条复合命令行贡献它包含的每个操作，"
           "因此不会等于 `execs`。", "",
           "| 操作 | 次数 | 不同调用式 | 出错次数 | 出错率 |", "|---|---|---|---|---|"]
    for s in shapes[:15]:
        calls = s["calls"] or 1
        out.append(f"| `{s['shape']}` | {calls} | {s['distinct_invocations']} | "
                   f"{s['failing_calls']} | {s['failing_calls'] / calls:.2f} |")
    if not shapes:
        out.append("| （无 exec 调用） | | | | |")
    out += ["", "⚠️ `不同调用式` = **参数多样性，不是实现分叉**。高值只说明这个操作被广泛参数化。",
            ""]
    return "\n".join(out)


def section_cost(r: dict[str, Any]) -> str:
    vol = r["evidence_flow"].get("output_volume_by_tool") or []
    out = ["## 2. 单次成本剖面（按 per-call 排序，不按占比）", "",
           "占比作为排序键已被证伪：两次调用就能撑爆窗口的工具，占比可能只有 1.2%。", "",
           "| 工具 | 调用 | 总字符 | 单次均值 | ≈ token/次 |", "|---|---|---|---|---|"]
    rows = []
    for item in vol:
        name, calls = item.get("tool"), item.get("calls") or 0
        avg = item.get("avg_chars") or 0
        if not calls:
            continue
        rows.append((avg, name, calls, item.get("megachars") or 0.0))
    for avg, name, calls, mchars in sorted(rows, reverse=True)[:10]:
        flag = "  ⚠️" if name in EXPENSIVE_PER_CALL else ""
        out.append(f"| `{name}`{flag} | {calls} | {mchars:.2f}M | {avg:,.0f} | {avg/4:,.0f} |")
    out.append("")
    return "\n".join(out)


def section_gate_violations(r: dict[str, Any]) -> str:
    shapes = {s["shape"]: s for s in (r["evidence_flow"].get("top_shapes") or [])}
    out = ["## 3. 已知 gate 的违反计数（仅列脚本可判的）", ""]
    hit = False
    for shape, (title, why) in MACHINE_CHECKABLE_GATES.items():
        s = shapes.get(shape)
        if not s:
            continue
        hit = True
        out += [f"### {title} — 本会话 **{s['calls']} 次**（其中出错 {s['failing_calls']}）", "",
                why, ""]
    if not hit:
        out += ["本会话未命中任何脚本可判的 gate。", "",
                "⚠️ 这**不等于**没有违反 gate——gate 清单里多数条目要读 trace 才能判"
                "（见 `signatures.md` 签名表的「检测层」一列）。", ""]
    return "\n".join(out)


def build(path: Path) -> str:
    r = scan(path)
    s, ef = r["scale"], r["evidence_flow"]
    parts = [
        f"# Harvest Report · {path.name}", "",
        "> P2 装配。**脚本只出候选与计数，晋升形态由审计 agent 判定。**",
        "> 判据：跨会话复发 + 稳定序列 + 结果绑定 + 明确边界。前者可数，后三者要读 trace。", "",
        f"规模：{r['size_bytes']/1048576:.1f} MB / {s['lines']} 行 / "
        f"{s['execs']} execs / {s['patches']} patches over {s['files_touched']} files / "
        f"{s['compactions']} 次压缩",
        f"失败 {ef['failures']} · 超时 {ef['timeouts']} · "
        f"仪器改动 {ef['instrument_patches']} vs 业务 {ef['business_patches']}", "",
        section_operations(r),
        section_cost(r),
        section_gate_violations(r),
        "## 4. 晋升判定（agent 填；这一层代码做不到）", "",
        "对 §1 里次数高或出错率高的操作，逐个判断：", "",
        "```",
        "同一操作被 ≥2 种不同 exe 手工重造   → 无权威实现 → harness",
        "  ⚠️ 这一条需要跨 exe 的语义等价判断。实证：jq / sed / python3 三种写法做同一件",
        "     「按行号取窗口」，三者 shape 完全不同，任何计数器都聚不到一起。",
        "「别做 X / X 之后 Y 失效」          → gate（禁止性）",
        "「要做 Z 就按这个序列」              → skill（生成性）",
        "序列里含确定性、昂贵、易错的机器步骤 → harness 托底",
        "```", "",
        STUB, "",
        "## 5. 结果绑定（agent 填）", "",
        "⚠️ **不要用「成功」做绑定。** 本 skill 已证伪两种成功凭据："
        "绿测试（假通过 = 宣称修好但物证里没有覆盖那个表面的验证动作）与 "
        "`repo_commit`（假 provenance = commit 证明不了产出它的尺子的状态）。",
        "",
        "**绑失败更可靠**：失败是物证，不需要叙事背书。§1 的 `出错次数` 与 §2 的单次成本，"
        "合起来就足以说明某个操作值不值得托底——不需要先证明它成功过。", "",
        STUB, "",
        "## 6. 明确不做的", "",
        "- 不自动决定晋升形态（§4 的理由）。",
        "- 不用绝对计数下判断；跨会话复发需要多会话输入，本报告只覆盖单会话。",
        "- 不解决被审对象的技术问题（审计员禁止解题）。", "",
        "## 附：可复现命令", "", "```bash",
        f"python3 {Path(__file__).resolve()} \\\n  {path} --out harvest.md",
        f"python3 {Path(__file__).resolve().parent}/session_metrics.py \\\n  {path} --json-out /tmp/metrics.json",
        "```", "",
    ]
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session", type=Path)
    ap.add_argument("--out", type=Path, help="write markdown here (default: stdout)")
    args = ap.parse_args()
    if not args.session.exists():
        raise SystemExit(f"no such session: {args.session}")
    md = build(args.session)
    if args.out:
        args.out.write_text(md, encoding="utf-8")
        stubs = md.count(STUB)
        print(f"wrote {args.out}  ({len(md.splitlines())} 行, {stubs} 处待填)")
        print("⚠️ §4/§5 留空的 harvest report 只是一张计数表，不是判定。")
    else:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
