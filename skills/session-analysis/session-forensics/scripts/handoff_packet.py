#!/usr/bin/env python3
"""Generate a handoff packet skeleton from a session's evidence flow.

What this can and cannot do
---------------------------
Sections 1, 2, 4, 5 and the appendix are pure evidence — the script fills them.
Sections 3, 6, 7, 8, 9, 10 require judgement (what was falsified, which
assumptions were never tested, what the next minimal experiment is) and are
emitted as marked stubs for a human or an auditing agent to complete.

**Never delete the stubs to make the packet look finished.** An unfilled §6
（已证伪）is the single most costly omission: without it the next agent inherits
the previous agent's wrong conclusions as established fact.

Completeness is a GATE, not a warning (修订记录 #8)
---------------------------------------------------
`--check <packet.md>` refuses (exit 1) while judgement stubs remain in
§0 / §1-P3判定 / §6 / §7 / §8 / §10. Run it before every handoff; a packet that
has never passed `--check` is not a packet, it is a template. Three field
incidents (#6 任务没续上, #7 packet 得了自己诊断的病, Example project owner-可见性缺格)
recurred while completeness was mere discipline — per the promotion ladder
(复发 ≥2 → gate) it is now enforced.

§6 is pre-filled with evidence candidates (修订记录 #8): the departing agent's
world model is by definition decoupled from the evidence (that is *why* a
handoff is happening), so asking it to recall its own falsified claims from
memory is the same self-audit this skill declares impossible. The script mines
user dissatisfaction phrasing (wide-recall seed, see calibrate.py) as
"待质证" rows; the packet author confirms or strikes them against reverts /
corrections in the trace — 质证 instead of 回忆.

`--post-mortem`: when the executing agent is already dead (disconnect, quota,
crash) nobody can be interviewed. §3/§9 are then marked 不可得 instead of TODO,
and §6 must be filled by the observer from evidence only.

Per the template's first principle the output is an INDEX, not a summary: every
figure carries a line number or a reproducible command, so the reader can return
to the original instead of trusting this file.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from session_metrics import scan  # noqa: E402
from calibrate import mine_candidates  # noqa: E402
from harvest_report import section_operations  # noqa: E402

STUB = "<!-- TODO 需要判断，不可由脚本填写；留空即视为未完成 -->"
NA_POST_MORTEM = "（不可得：post-mortem 交接，本人已不可采访；接手方不要在此处等待补充。）"

# Sections whose unfilled stubs make `--check` refuse. §3/§9 only warn: they are
# interview material and may legitimately be 不可得 (see --post-mortem). §2's
# owner-可见性/孤儿产物 stubs warn too — they need repo access the packet author
# may not have; the check names them so the omission is at least visible.
CHECK_REQUIRED = ("0", "6", "7", "8", "10")
P3_MARKER = "**P3 判定**"


def load_baseline(skill_dir: Path) -> dict[str, Any] | None:
    p = skill_dir / "local" / "baseline.json"
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
    return None


def quantile_of(value: float, dist: dict[str, Any] | None) -> str:
    """Where does this session sit in the user's OWN corpus? Absolute values are
    not interpretable across environments; only the local percentile is."""
    if not dist or not isinstance(value, (int, float)):
        return ""
    for label in ("p95", "p90", "p75", "p50", "p25"):
        if label in dist and value >= dist[label]:
            return f"≥{label}"
    return "<p25"


def section_resume_point(r: dict[str, Any]) -> str:
    """§0 — the first thing the receiver reads: is the current goal still the initial one?

    §1 records the evolution but nothing forced §10 to inherit it, so an auditor
    would diagnose "legitimate evolution" and then order the next steps by its
    own findings. Measured on CASE-F: the user's deliverable got pushed to
    item 3 while tool fixes were labelled blocking. Putting the delta at the
    very top makes skipping the re-alignment visible.
    """
    subs = r["objective_trace"]["substantive"]
    first = subs[0] if subs else None
    last = subs[-1] if subs else None
    out = ["## 0. 任务续接点 —— 当前目标是否还是初始目标", "",
           "```"]
    if first:
        out.append(f"初始目标（L{first[0]}）  {first[1][:150]}")
    if last and last is not first:
        out.append(f"最后一条（L{last[0]}）  {last[1][:150]}")
    out += ["```", "",
            "**当前活的目标（逐条对照 §1 后填写，不许照抄初始目标）**：", "", STUB, "",
            "**接手方第一件事**（必须服务当前目标；审计员发现的工具问题属于支撑项）：", "",
            STUB, ""]
    return "\n".join(out)


def section_objectives(r: dict[str, Any]) -> str:
    subs = r["objective_trace"]["substantive"]
    lines = ["## 1. 原始目标（逐字，未转述）", "",
             "| 行号 | 原话 | 性质 |", "|---|---|---|"]
    truncated = False
    for ln, msg in subs[:40]:
        cell = msg[:150].replace("|", "/")
        if len(msg) > 150:
            cell += " …（逐字全文见 §1 附）"
            truncated = True
        lines.append(f"| L{ln} | {cell} | {STUB} |")
    if len(subs) > 40:
        lines.append(f"| … | 另有 {len(subs)-40} 条，见 `objective_trace.substantive` | |")
    pumps = r["objective_trace"]["pump_genuine"]
    resumes = r["objective_trace"]["pump_resume"]
    lines += ["", f"已过滤：ambient 注入 {r['scale']['user_ambient']} 条、"
                  f"纯推进 {len(pumps)} 条、网络续跑 {len(resumes)} 条（后者是环境噪声，非用户意图）。",
              "", "**P3 判定**（目标演化是否全部由 user message 引入；若否，指出 agent 自漂起点）：",
              STUB, ""]
    if truncated:
        # The table is for scanning; it cannot hold a long instruction. But §1's
        # contract is 逐字 — a receiver who inherits a sentence cut at 150 chars
        # inherits a different goal and has no marker telling them so. So the
        # table gets a preview + pointer, and the full text is emitted below.
        lines += ["### §1 附：逐字全文（表格里被截断的那几条）", ""]
        for ln, msg in subs[:40]:
            if len(msg) <= 150:
                continue
            lines += [f"**L{ln}**", "", "> " + msg.replace("\n", "\n> "), ""]
    return "\n".join(lines)


def section_state(r: dict[str, Any]) -> str:
    b = r["b_class"]
    ev = r["evidence_flow"]
    out = ["## 2. 当前真实状态（只从物证重建，不引用 agent 宣称）", "",
           f"格式 `{r['format']}`　能力 `{r['capabilities']}`", "",
           "### 仪器与工作树", ""]
    out.append(f"触及工作树 {len(b['worktrees'])} 个：")
    for w in b["worktrees"][:10]:
        out.append(f"- `{w}`")
    if b["forked_files"]:
        out += ["", f"**跨工作树分叉文件 {b['forked_count']} 个**（同名文件在多处各自演化）：", ""]
        for name, roots in list(b["forked_files"].items())[:12]:
            out.append(f"- `{name}` → {', '.join(roots)}")
    else:
        out += ["", "未检出跨工作树分叉。"]
    out += ["", "### 改动分布", "",
            f"- 仪器 `tools/ scripts/ harness/ tests/`：{ev['instrument_patches']} 次",
            f"- 业务：{ev['business_patches']} 次",
            "", "改动最多的文件：", ""]
    for f, c in ev["top_patched"][:8]:
        out.append(f"- {c:>4}× `{f}`")
    out += ["", "### owner 可见性（同一条命令在各副本的实测结果）", "", STUB,
            "", "### 会话结束后落盘的产物（`stat` 产物目录，mtime > session mtime）", "", STUB, ""]
    return "\n".join(out)


def section_data(r: dict[str, Any], base: dict[str, Any] | None) -> str:
    s, rt, c = r["scale"], r["rates"], r["context"]
    bm = (base or {}).get("metrics", {})
    out = ["## 4. 关键实测数据（会死于压缩的数字全部在此）", "", "```",
           f"lines={s['lines']}  compactions={s['compactions']}  execs={s['execs']}  "
           f"patches={s['patches']} / {s['files_touched']} files",
           f"session_meta={s['session_meta_records']}  malformed={s['malformed']}",
           f"turns: complete={s['turn_complete']}  aborted={s['turn_aborted']}",
           f"narrative: assistant={s['assistant_msgs']} thinking={s['reasoning_items']}",
           f"user: substantive={s['user_substantive']} pump={s['user_pump']} "
           f"resume={s['user_resume']} ambient={s['user_ambient']}", "```", "",
           "| 指标 | 本会话 | 本地语料分位 |", "|---|---|---|"]
    for k, v in rt.items():
        if v is None:
            out.append(f"| `{k}` | 平台不支持 | — |")
        elif isinstance(v, (int, float)):
            out.append(f"| `{k}` | {v} | {quantile_of(v, bm.get(k))} |")
        else:
            out.append(f"| `{k}` | {v} | |")
    out += ["", "### 上下文洪水（预算去哪了）", "", "```"]
    for row in r["evidence_flow"]["output_volume_by_tool"][:6]:
        out.append(f"{row['tool']:<20} {row['calls']:>5} calls  {row['megachars']:>8.2f}M chars  "
                   f"{row['share']*100:>5.1f}%  avg {row['avg_chars']:,}")
    out += ["```", ""]
    if c["segments"]:
        out += ["### 上下文轨迹", "", "```",
                f"window={c['window']}  floor {c['floor_first']} → {c['floor_last']}  peak={c['peak_max']}"]
        segs = c["segments"]
        for i in range(0, len(segs), max(1, len(segs) // 6)):
            g = segs[i]
            out.append(f"  seg{i:<3} after_line={g['after_line']:<7} floor={g['floor']:<8} peak={g['peak']}")
        out += ["```", ""]
    rec = [x for x in r["objective_trace"]["recurring_asks"] if x["verdict"] == "restated"]
    if rec:
        out += ["### 重述诉求（第一次说的没落地；已扣除网络重发）", ""]
        for cl in rec[:5]:
            for ln, m in cl["members"]:
                out.append(f"- L{ln}: {m[:130]}")
            out.append("")
    if r["sub_agents"]:
        out += ["### 子 agent", "", "```"]
        for a, n in r["sub_agents"][:8]:
            out.append(f"{n:>4}  {a}")
        out += ["```", ""]
    return "\n".join(out)


def section_p2_profile(r: dict[str, Any], skill_dir: Path, session: Path) -> str:
    """§5.5 — the P2 slice. The assembly formula says handoff = S+P1+P2+P3, but
    until 修订记录 #8 nothing in this script supplied P2: §5 carried baselines
    (relabelled "P2" in 修订记录 #2) while the actual reusable-sequence material
    lived only in harvest_report. The superset was a claim. Embed the operation
    profile (cheap: same scan result) + the command for the full harvest."""
    profile = section_operations(r).replace("## 1. 操作剖面", "### 操作剖面")
    return "\n".join([
        "## 5.5 P2 · 可复用序列原料（操作剖面）", "",
        "接手方读法：高频且低错的操作是**该固化成 harness 的候选**；高频高错是**环境搏斗**，",
        "接手前先修环境。完整 harvest（含单次成本剖面、gate 违反计数）：", "",
        "```bash",
        f"python3 {skill_dir}/scripts/harvest_report.py \\\n  {session} --out harvest.md",
        "```", "",
        profile,
    ])


def section_falsified(r: dict[str, Any], session: Path, post_mortem: bool) -> str:
    """§6 pre-fill. The departing agent cannot be trusted to recall its own
    falsified claims (its world model is decoupled — that's why we're handing
    off), but the trace holds evidence of falsification: the user's own
    dissatisfaction phrasing. Mine it with the wide-recall seed (calibrate.py)
    and emit 待质证 rows; the author confirms against reverts/corrections
    instead of recalling from memory. Wide seed ≈ candidates, NOT labels —
    precision comes from 质证, which is exactly what the packet author owes."""
    subs = r["objective_trace"]["substantive"]
    cands = mine_candidates(subs, session.name)[:8]
    out = ["## 6. 已证伪 ⚠️ 必需项", "",
           "**上一个 agent 说错的话。** 不写，接手方会把错误结论当既定事实继续用。",
           "这是唯一一节专门用于阻止叙事流被继承。"]
    if post_mortem:
        out += ["", "（post-mortem 交接：本节由观察者**只从物证**填写，禁止引用前任叙事流。）"]
    out += ["", "| 提过的 | 被什么证伪 | 状态 |", "|---|---|---|"]
    for c in cands:
        tags = ",".join(c["tags"][:2])
        text = c["text"][:100].replace("|", "/").replace("\n", " ")
        out.append(f"| 疑似（L{c['line']}，{tags}）：{text} | 待质证：向后找更正/回滚/重做物证 | 候选 |")
    out.append(f"| {STUB} | | |")
    out += ["",
            "预填行来自不满句式**宽召回种子**（calibrate.py），是候选不是结论：",
            "逐行质证后改写「被什么证伪」列并把状态改为 `废弃/降级/反转`，误报行删除。",
            "确认过的本人句式库（若已 `confirm_patterns.py --ingest`）比种子更准。", ""]
    return "\n".join(out)


def build(path: Path, skill_dir: Path, post_mortem: bool = False) -> str:
    r = scan(path)
    base = load_baseline(skill_dir)
    interview_stub = (NA_POST_MORTEM if post_mortem else STUB)
    parts = [
        f"# Handoff Packet · {path.name}", "",
        "> 物证优先的**索引**，不是摘要。每条结论挂行号或可复现命令；接手方按指针回原文。",
        "> 带 TODO 标记的小节需要判断，脚本不填。**交接前必须通过 "
        "`handoff_packet.py --check <本文件>`；§0/§1-P3/§6/§7/§8/§10 残留 TODO 即拒绝。**",
        ("> （post-mortem 模式：§3/§9 标记为不可得，§6 由观察者从物证填写。）" if post_mortem else ""),
        "",
        section_resume_point(r), section_objectives(r), section_state(r),
        "## 3. 心智模型 / 设计定稿\n\n" + interview_stub +
        "\n\n（唯一允许以叙事为主的一节，因此必须最短。）\n",
        section_data(r, base),
        "## 5. 基线 / 对照\n\n" +
        (f"来自 `local/baseline.json`：{base.get('sessions_kept','?')} 个会话，"
         f"语料 {base.get('generated_from')}。\n\n"
         "⚠️ 基线是环境属性。换机器、换工具链、换时间段都必须重新校准。\n"
         if base else "⚠️ 未找到 `local/baseline.json`。先跑 `calibrate.py`，否则所有分位为空，"
                      "绝对值不可解释。\n"),
        section_p2_profile(r, skill_dir, path),
        section_falsified(r, path, post_mortem),
        "## 7. 未证伪的假设\n\n一直当真、从未验证的东西；标注为何未验证、决定性证据在哪。\n\n" + STUB + "\n",
        "## 8. 失效表\n\n当前已知失效边 + 本会话违反了哪几条。\n\n" + STUB + "\n",
        "## 9. 明确非目标\n\n" + interview_stub + "\n",
        "## 10. 下一步 —— 按 **§0 认定的当前目标** 排序，不按审计员优先级\n\n"
        "写完回头对一次：**第 1 项服务的是用户当前目标吗？** 若第 1 项是修工具 / 修副本 /\n"
        "修 parser 而当前目标是产出某个交付物，顺序就错了——除非能说清交付物物理上依赖它。\n"
        "审计员发现的问题写进「支撑项」，不自动获得优先级。\n\n"
        "最后必须有一条：**什么结果会证伪当前整条路线。**\n\n" + STUB + "\n",
        "## 附：可复现命令\n\n```bash\n"
        f"python3 {skill_dir}/scripts/session_metrics.py \\\n  {path} --json-out /tmp/metrics.json\n"
        f"python3 {skill_dir}/scripts/handoff_packet.py \\\n  {path} --out handoff.md\n"
        f"python3 {skill_dir}/scripts/handoff_packet.py --check handoff.md\n```\n",
    ]
    return "\n".join(p for p in parts if p != "")


def check_packet(packet: Path) -> int:
    """The gate. Exit 1 while judgement stubs remain in required sections.

    Returns 0 only when §0, §1's P3 判定, §6, §7, §8, §10 contain no stub.
    §2/§3/§9 stubs (and per-row 性质 stubs in §1's table) are reported as
    warnings — they need repo access or an interview and may be legitimately
    unavailable; the point is that the omission is *named*, not silent."""
    text = packet.read_text(encoding="utf-8")
    sec = None
    stub_by_sec: dict[str, int] = {}
    p3_unfilled = False
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^## (\d+)(?:\.\d+)?[\.\s]", line)
        if m:
            sec = m.group(1)
            continue
        if STUB in line and sec is not None:
            stub_by_sec[sec] = stub_by_sec.get(sec, 0) + 1
            if sec == "1":
                back = "\n".join(lines[max(0, i - 3):i])
                if P3_MARKER in back:
                    p3_unfilled = True
    failures, warnings = [], []
    for s in sorted(stub_by_sec, key=lambda x: int(x)):
        n = stub_by_sec[s]
        if s in CHECK_REQUIRED:
            failures.append(f"§{s}: {n} 处未填")
        elif s == "1":
            if p3_unfilled:
                failures.append("§1: P3 判定未填（表内性质列可后补，判定不行）")
            else:
                warnings.append(f"§1: 表内性质列 {n} 处未标（可后补，但接手方无法区分纠正与扩展）")
        else:
            warnings.append(f"§{s}: {n} 处未填（需 repo 访问或采访本人；post-mortem 用 --post-mortem 标注不可得）")
    for w in warnings:
        print(f"⚠️  {w}")
    if failures:
        for f in failures:
            print(f"✗ {f}")
        print("REFUSED：packet 未完成，不可交接。填完后重跑 --check。")
        return 1
    print("✓ 必填节全部完成，可交接。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("session", type=Path, nargs="?")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--check", type=Path, metavar="PACKET_MD",
                    help="验收一份已生成的 packet：必填节残留 TODO 即 exit 1")
    ap.add_argument("--post-mortem", action="store_true",
                    help="执行 agent 已死（断线/额度/崩溃）：§3/§9 标不可得，§6 由观察者从物证填")
    args = ap.parse_args()
    if args.check:
        return check_packet(args.check.expanduser())
    if not args.session:
        ap.error("需要 session 路径（或用 --check PACKET_MD 验收已生成的 packet）")
    skill_dir = Path(__file__).resolve().parent.parent
    text = build(args.session.expanduser(), skill_dir, post_mortem=args.post_mortem)
    if args.out:
        args.out.expanduser().write_text(text, encoding="utf-8")
        todo = text.count(STUB)
        print(f"wrote {args.out}  ({len(text.splitlines())} 行, {todo} 处待填)")
        print("⚠️ §0/§1-P3/§6/§7/§8/§10 未填完的 packet 不可交接。交接前验收：")
        print(f"   python3 {Path(__file__).name} --check {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
