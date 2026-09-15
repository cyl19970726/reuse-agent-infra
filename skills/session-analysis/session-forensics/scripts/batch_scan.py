#!/usr/bin/env python3
"""Batch-scan sessions and build a labelling set for threshold validation.

The open question the signature table cannot answer on its own: do the structural
metrics measure PATHOLOGY, or do they just measure SESSION SIZE? Answering it needs
labels, and the labels must come from a different measurement channel than the
predictors — otherwise the correlation is circular.

Channel separation used here:
    label      = dissatisfaction expressed by the user in natural language
                 (semantic signal, authored by a human outside the agent)
    predictor  = structural rates from the action stream
                 (statistical signal, derived from what the agent did)

Known leakage: pump_share and recurring_ask_clusters are themselves derived from
user messages, so they are NOT channel-independent. They are reported but excluded
from the correlation summary.
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

# Phrases in which the user says, in their own words, that the work is not working.
# Deliberately conservative: these should read as complaints to a human, not as
# neutral instructions. Precision matters more than recall — a false label is worse
# than a missing one.
# v2 — precision first. v1 scored ~43% precision because it fired on neutral
# descriptions of incomplete work ("还没完成 prd 的创建") and on plain instructions.
# Every pattern below must be addressed AT the agent (second person, or an
# accusatory interrogative). Statements about the state of the work are not labels.
# A false label is worse than a missing one: it destroys the correlation outright.
DISSAT_PATTERNS = [
    (r"为什么(你|我们)?.{0,14}(没|不|又|还是|一直|这么慢)", "why-failing"),
    (r"你(没有|根本没|一直没|又|怎么没|是不是没)", "you-failed-to"),
    (r"(基本上|完全|根本).{0,4}(没有|不是|做不到).{0,12}(我们|想要|要的|期待)", "flatly-not"),
    (r"不是我(想|要).{0,4}的", "not-what-i-wanted"),
    (r"(这|那)(个|里|样)?(做得?)?(不对|错了|不行|很差|太差)", "wrong"),
    (r"(还是|仍然|又).{0,8}(不行|失败|错了|有问题|没解决)", "still-broken"),
    (r"(重来|重新做一?遍|推翻重|白做|白干)", "redo"),
    (r"我(不是|已经).{0,4}(说过|讲过|提过|强调过)", "i-already-said"),
    (r"(进度|速度).{0,6}(这么|太)(慢|差)", "too-slow"),
]

AMBIENT_HINT = re.compile(r"^<|^# Files mentioned|^AGENTS\.md")


def dissatisfaction(substantive: list[tuple[int, str]]) -> dict[str, Any]:
    hits = []
    for ln, msg in substantive:
        if AMBIENT_HINT.match(msg):
            continue
        for pattern, tag in DISSAT_PATTERNS:
            if re.search(pattern, msg):
                hits.append({"line": ln, "tag": tag, "quote": msg[:120]})
                break
    n = max(len(substantive), 1)
    return {"count": len(hits), "rate": round(len(hits) / n, 4), "hits": hits[:6]}


def row_of(path: Path, r: dict[str, Any]) -> dict[str, Any]:
    s, n, c = r["scale"], r["rates"], r["context"]
    d = dissatisfaction(r["objective_trace"]["substantive"])
    floor_share = 0.0
    if c["window"] and c["floor_last"]:
        floor_share = round(c["floor_last"] / c["window"], 4)
    return {
        "session": path.name,
        "mb": round(r["size_bytes"] / 1e6, 1),
        "lines": s["lines"],
        "compactions": s["compactions"],
        "execs": s["execs"],
        "user_substantive": s["user_substantive"],
        # label channel (semantic, human-authored)
        "dissat_count": d["count"],
        "dissat_rate": d["rate"],
        "dissat_hits": d["hits"],
        # predictor channel (structural, action-derived) — channel-independent
        "failure_rate": n["failure_rate"],
        "timeout_rate": n["timeout_rate"],
        "narrative_to_evidence": n["narrative_to_evidence"],
        "instrument_patch_share": n["instrument_patch_share"],
        "forked_share": n["forked_share"],
        "max_patch_share": n["max_patch_share"],
        "flood_share": n["flood_share"],
        "flood_tool": n["flood_tool"],
        "floor_share": floor_share,
        "compactions_per_1k_lines": n["compactions_per_1k_lines"],
        "abort_rate": round(s["turn_aborted"] / max(s["turn_complete"] + s["turn_aborted"], 1), 4),
        # leaking predictors — reported, excluded from correlation
        "pump_share": n["pump_share"],
        "recurring_ask_clusters": n["recurring_ask_clusters"],
    }


CHANNEL_INDEPENDENT = [
    "failure_rate", "timeout_rate", "narrative_to_evidence", "instrument_patch_share",
    "forked_share", "max_patch_share", "flood_share", "floor_share",
    "compactions_per_1k_lines", "abort_rate", "lines", "execs",
]


def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    return round(num / (dx * dy), 3) if dx and dy else 0.0


def percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    v = sorted(values)
    def p(q: float) -> float:
        return v[min(len(v) - 1, int(len(v) * q))]
    return {"p50": p(0.5), "p75": p(0.75), "p90": p(0.90), "p95": p(0.95), "max": v[-1]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("root", type=Path, help="Directory to walk for *.jsonl")
    ap.add_argument("--sample", type=int, default=200, help="Take every k-th file to reach ~N")
    ap.add_argument("--min-lines", type=int, default=1500,
                    help="Skip sessions too small to develop pathology (v1 used 150 and the "
                         "sample came out with p50=614 lines / 40 execs — nothing to diagnose)")
    ap.add_argument("--min-execs", type=int, default=100, help="Skip sessions with little real work")
    ap.add_argument("--min-mb", type=float, default=0.0, help="Pre-filter by file size, cheap")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    files = sorted(f for pattern in ("*.jsonl", "*.jsonl.zstd")
                   for f in args.root.expanduser().rglob(pattern))
    if args.min_mb:
        files = [f for f in files if f.stat().st_size >= args.min_mb * 1e6]
        print(f"{len(files)} files >= {args.min_mb} MB", file=sys.stderr)
    if args.sample and len(files) > args.sample:
        step = len(files) // args.sample
        files = files[::step][: args.sample]
    print(f"scanning {len(files)} sessions from {args.root}", file=sys.stderr)

    rows = []
    for i, f in enumerate(files, 1):
        try:
            r = scan(f)
        except Exception as exc:  # a corrupt session must not kill the batch
            print(f"  !! {f.name}: {exc}", file=sys.stderr)
            continue
        if r["scale"]["lines"] < args.min_lines or r["scale"]["execs"] < args.min_execs:
            continue
        rows.append(row_of(f, r))
        if i % 25 == 0:
            print(f"  {i}/{len(files)}  kept={len(rows)}", file=sys.stderr)

    args.out.expanduser().write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")

    labelled = [r for r in rows if r["user_substantive"] >= 3]
    pos = [r for r in labelled if r["dissat_count"] >= 1]
    print(f"\n{'='*78}")
    print(f"kept {len(rows)} sessions | {len(labelled)} with >=3 substantive user msgs")
    print(f"dissatisfaction expressed in {len(pos)}/{len(labelled)} "
          f"({len(pos)/max(len(labelled),1)*100:.0f}%)")

    print(f"\n-- predictor distributions (n={len(rows)}) --")
    for k in CHANNEL_INDEPENDENT:
        pc = percentiles([r[k] for r in rows])
        print(f"   {k:<26} p50={pc['p50']:<9} p75={pc['p75']:<9} p90={pc['p90']:<9} max={pc['max']}")

    print(f"\n-- correlation with dissatisfaction (n={len(labelled)}, channel-independent only) --")
    ys = [r["dissat_rate"] for r in labelled]
    corrs = sorted(((pearson([r[k] for r in labelled], ys), k) for k in CHANNEL_INDEPENDENT),
                   key=lambda t: -abs(t[0]))
    for c, k in corrs:
        print(f"   {k:<26} r={c:+.3f}")

    print("\n-- leaking predictors (NOT valid evidence, shown for completeness) --")
    for k in ("pump_share", "recurring_ask_clusters"):
        print(f"   {k:<26} r={pearson([r[k] for r in labelled], ys):+.3f}")

    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
