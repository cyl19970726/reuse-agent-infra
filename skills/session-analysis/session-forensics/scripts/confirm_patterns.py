#!/usr/bin/env python3
"""Turn mined candidates into a confirmed, per-user phrasing library.

Why a human step is unavoidable
-------------------------------
Two hand-written attempts were measured against real sessions:

    v1 narrow-ish   precision ~43%  (fired on neutral "还没完成 prd 的创建")
    v2 tightened    precision ~80%  but recall <50% — it missed all seven real
                    complaints in one session ("好烂", "很烂",
                    "别人听到都会跑路了", "怎么是全英文的", ...)

Dissatisfaction is a semantic judgement expressed in one person's idiom. Regex
cannot chase it, and a wrong label is worse than a missing one because it
poisons every downstream correlation. So the pipeline is:

    calibrate.py   mine candidates with a WIDE seed filter   (high recall)
    --review       emit a checklist                          (human decides)
    --ingest       learn this user's phrasing from the marks (precision earned)

The learned library lives in local/patterns.json and is never distributed.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

SKILL = Path(__file__).resolve().parent.parent
LOCAL = SKILL / "local"
HEADER = """# 不满句式确认清单

把**真的表达了不满**的行改成 `[x]`；不是的留 `[ ]`。两者都有用——
被否掉的句子用来排除误报特征，所以**不要删除未选中的行**。

判据：这句话是不是在说「你做的这个东西不行」。
- 算：好烂 / 为什么又 / 你没有按 / 还是不行 / 别人看了会跑 / 怎么是英文的
- 不算：陈述现状（还没完成 X）、纯指令（去补一下 Y）、修辞提问（为什么要分块）

标完后运行：
```bash
python3 "$SESSION_FORENSICS_DIR/scripts/confirm_patterns.py" --ingest
```
"""


def load_candidates() -> list[dict[str, Any]]:
    p = LOCAL / "dissat_candidates.jsonl"
    if not p.is_file():
        raise SystemExit(f"未找到 {p}，先跑 calibrate.py")
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def do_review(limit: int) -> None:
    cands = load_candidates()
    by_tag: dict[str, list[dict[str, Any]]] = {}
    for c in cands:
        by_tag.setdefault(c["tags"][0], []).append(c)

    lines = [HEADER, ""]
    total = 0
    for tag in sorted(by_tag, key=lambda t: -len(by_tag[t])):
        group = by_tag[tag][:limit]
        lines.append(f"\n## {tag}  ({len(by_tag[tag])} 条，列出 {len(group)})\n")
        for c in group:
            text = c["text"].replace("\n", " ")[:170]
            lines.append(f"- [ ] `{c['session'][:34]}:L{c['line']}` {text}")
            total += 1
    out = LOCAL / "patterns_review.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out}  ({total} 条待标, 共 {len(cands)} 条候选)")
    print("标完后跑 --ingest。未标记的行会被当作『不是不满』用于排除误报。")


CHECK_RE = re.compile(r"^- \[( |x|X)\] `([^`]+)` (.*)$")


def ngrams(s: str, n: int = 3) -> set[str]:
    s = re.sub(r"\s+", "", s)
    return {s[i:i + n] for i in range(max(0, len(s) - n + 1))}


def do_ingest(min_support: int) -> None:
    review = LOCAL / "patterns_review.md"
    if not review.is_file():
        raise SystemExit(f"未找到 {review}，先跑 --review")

    confirmed: list[str] = []
    rejected: list[str] = []
    for line in review.read_text(encoding="utf-8").splitlines():
        m = CHECK_RE.match(line.strip())
        if not m:
            continue
        (confirmed if m.group(1).lower() == "x" else rejected).append(m.group(3))

    if not confirmed:
        raise SystemExit("没有任何 [x] 标记，未生成 patterns.json（空标签比坏标签好，但也没用）")

    # A discriminative n-gram appears in several confirmed lines and in no
    # rejected line. Requiring zero rejected support keeps precision high; the
    # human-confirmed literals below carry recall.
    pos = Counter()
    for t in confirmed:
        pos.update(ngrams(t))
    neg: set[str] = set()
    for t in rejected:
        neg |= ngrams(t)

    derived = sorted(
        ((g, c) for g, c in pos.items() if c >= min_support and g not in neg),
        key=lambda gc: -gc[1])

    payload = {
        "confirmed_count": len(confirmed),
        "rejected_count": len(rejected),
        "confirmed_phrases": confirmed[:400],
        "rejected_phrases": rejected[:400],
        "derived_ngrams": [{"ngram": g, "support": c} for g, c in derived[:120]],
        "note": "本人语料专属。derived_ngrams 要求在确认句中出现 >=min_support 次、"
                "且在否决句中出现 0 次，因此偏精确；召回靠 confirmed_phrases。",
    }
    out = LOCAL / "patterns.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    print(f"  确认 {len(confirmed)} 条 / 否决 {len(rejected)} 条 / 判别性 n-gram {len(derived)} 个")
    if derived[:12]:
        print("  最强特征:", "  ".join(g for g, _ in derived[:12]))
    if len(confirmed) < 15:
        print("  ⚠️ 确认样本 <15，derived 特征不稳定，记为单例观察，先别用于统计。")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--review", action="store_true", help="生成待标清单")
    ap.add_argument("--ingest", action="store_true", help="读回标记，学出本人句式库")
    ap.add_argument("--limit", type=int, default=40, help="每类最多列出多少条")
    ap.add_argument("--min-support", type=int, default=2)
    args = ap.parse_args()

    LOCAL.mkdir(parents=True, exist_ok=True)
    if args.ingest:
        do_ingest(args.min_support)
    else:
        do_review(args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
