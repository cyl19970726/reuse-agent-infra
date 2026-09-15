#!/usr/bin/env python3
"""Calibrate this skill against THIS user's own session history.

Why this exists
---------------
Baselines, dissatisfaction phrasing and flood profiles are properties of a
person's environment, not universal constants. A p50 of 10 compactions, or
`view_image` accounting for 87% of tool output, describes one corpus. Someone who
never uses subagents, never views images, or works in a different language will
have a completely different distribution — and reading their sessions against a
foreign baseline is exactly the B-class gap this skill exists to detect:
**referencing something that no longer (or never did) hold in the current context.**

So the skill ships the method, and calibration produces the numbers locally:

    <skill>/local/            # per-user, never distributed
      baseline.json           # metric distributions over this user's corpus
      dissat_candidates.jsonl # mined phrasings awaiting human confirmation
      patterns.json           # confirmed phrasings (written by the confirm step)

Run once to bootstrap, then re-run periodically as the corpus grows.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from session_metrics import scan  # noqa: E402


def default_local_dir() -> Path:
    """Keep calibration in the authoritative skill copy, not a mirror."""
    loaded_root = Path(__file__).resolve().parent.parent
    configured_source = os.environ.get("SESSION_FORENSICS_SRC")
    if configured_source:
        return Path(configured_source).expanduser() / "local"
    canonical = Path.home() / ".claude" / "skills" / loaded_root.name
    if "/.codex/skills/" in str(loaded_root) and (canonical / "SKILL.md").is_file():
        return canonical / "local"
    return loaded_root / "local"

METRICS = [
    "failure_rate", "timeout_rate", "narrative_to_evidence", "instrument_patch_share",
    "forked_share", "max_patch_share", "max_cmd_share", "flood_share",
    "compactions_per_1k_lines", "pump_share", "resume_share",
]

# WIDE seed filter. Optimised for RECALL, not precision — its output is a candidate
# list for a human to confirm, not a label. The narrow hand-written v2 patterns were
# measured at ~80% precision but missed 7 of 7 real complaints in one session,
# because real dissatisfaction arrives as evaluative adjectives ("好烂"), consequence
# descriptions ("别人听到都会跑路了") and surprised interrogatives ("怎么是全英文的")
# rather than as the textbook "为什么你没有..." form.
SEED_PATTERNS = [
    (r"(很|好|太|真)?(烂|差劲|难听|丑|乱|糟|垃圾)", "evaluative-bad"),
    (r"为什么|为啥|怎么(是|会|又|没|还)", "interrogative-challenge"),
    (r"你(没|又|一直|根本|怎么|是不是没)", "second-person-blame"),
    (r"(跑路|看不懂|没读懂|读不懂|理解不了|不知所云)", "consequence"),
    (r"我(说过|讲过|提过|强调过|不是说)", "already-said"),
    (r"(不对|错了|不行|有问题|重来|白做|白干|推翻)", "direct-negative"),
    (r"(傻逼|无语|服了|醉了|？？|\?\?)", "frustration"),
    (r"(太|过于).{0,4}(复杂|慢|久|多)", "too-much"),
    (r"(还是|仍然|依然|又).{0,8}(没|不|失败|错)", "still-broken"),
    (r"(不是我|不是我们).{0,6}(想|要|说)", "not-what-i-asked"),
]

AMBIENT_HINT = re.compile(r"^<|^# Files mentioned|^AGENTS\.md")


def percentiles(values: list[float]) -> dict[str, Any]:
    if not values:
        return {}
    v = sorted(values)

    def p(q: float) -> float:
        return round(v[min(len(v) - 1, int(len(v) * q))], 4)

    return {"n": len(v), "p10": p(0.10), "p25": p(0.25), "p50": p(0.50),
            "p75": p(0.75), "p90": p(0.90), "p95": p(0.95), "max": round(v[-1], 4)}


def mine_candidates(subs: list[tuple[int, str]], session: str) -> list[dict[str, Any]]:
    out = []
    for ln, msg in subs:
        if AMBIENT_HINT.match(msg):
            continue
        tags = [tag for pat, tag in SEED_PATTERNS if re.search(pat, msg)]
        if tags:
            out.append({"session": session, "line": ln, "tags": tags, "text": msg[:220]})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("roots", nargs="+", type=Path,
                    help="Session directories, e.g. ~/.codex/sessions ~/.claude/projects")
    ap.add_argument("--out", type=Path,
                    default=default_local_dir())
    ap.add_argument("--min-lines", type=int, default=1500)
    ap.add_argument("--min-execs", type=int, default=100)
    ap.add_argument("--limit", type=int, default=0, help="Cap files scanned (0 = all)")
    args = ap.parse_args()

    out_dir = args.out.expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    files: list[Path] = []
    for root in args.roots:
        for pattern in ("*.jsonl", "*.jsonl.zstd"):
            files.extend(sorted(root.expanduser().rglob(pattern)))
    if args.limit:
        files = files[: args.limit]
    print(f"calibrating over {len(files)} session files", file=sys.stderr)

    per_metric: dict[str, list[float]] = {m: [] for m in METRICS}
    flood_tools: Counter[str] = Counter()
    flood_volume: Counter[str] = Counter()
    flood_calls: Counter[str] = Counter()
    candidates: list[dict[str, Any]] = []
    per_session: list[tuple[str, dict[str, float]]] = []
    kept = 0

    date_re = re.compile(r"(\d{4}-\d{2}-\d{2})T")

    for i, f in enumerate(files, 1):
        try:
            r = scan(f)
        except Exception as exc:
            print(f"  !! {f.name}: {exc}", file=sys.stderr)
            continue
        s = r["scale"]
        if s["lines"] < args.min_lines or s["execs"] < args.min_execs:
            continue
        kept += 1
        vals: dict[str, float] = {}
        for m in METRICS:
            v = r["rates"].get(m)
            if isinstance(v, (int, float)):
                per_metric[m].append(float(v))
                vals[m] = float(v)
        dm = date_re.search(f.name)
        stamp = dm.group(1) if dm else datetime.fromtimestamp(
            f.stat().st_mtime).strftime("%Y-%m-%d")
        per_session.append((stamp, vals))
        if r["rates"].get("flood_tool"):
            flood_tools[r["rates"]["flood_tool"]] += 1
        for row in r["evidence_flow"]["output_volume_by_tool"]:
            flood_volume[row["tool"]] += int(row["megachars"] * 1e6)
            flood_calls[row["tool"]] += row["calls"]
        candidates.extend(mine_candidates(r["objective_trace"]["substantive"], f.name))
        if i % 250 == 0:
            print(f"  {i}/{len(files)}  kept={kept}", file=sys.stderr)

    # Time-windowed baselines. A corpus spanning many months mixes usage patterns
    # that have since changed; judging this week's session against an 8-month
    # average is itself the B-class gap — referencing a distribution that no longer
    # holds. Callers should prefer the most recent window with enough samples.
    windows: dict[str, dict[str, list[float]]] = {}
    for stamp, vals in per_session:
        key = stamp[:7] if stamp else "unknown"
        bucket = windows.setdefault(key, {m: [] for m in METRICS})
        for m, v in vals.items():
            if isinstance(v, (int, float)):
                bucket[m].append(float(v))

    baseline = {
        "generated_from": [str(p) for p in args.roots],
        "files_seen": len(files),
        "sessions_kept": kept,
        "filters": {"min_lines": args.min_lines, "min_execs": args.min_execs},
        "metrics": {m: percentiles(v) for m, v in per_metric.items()},
        "windows": {
            k: {"n": len(next(iter(v.values()), [])),
                "metrics": {m: percentiles(vals) for m, vals in v.items() if vals}}
            for k, v in sorted(windows.items())
        },
        "window_note": "按月分层。用最近且样本量足够的窗口解读当前会话；"
                       "全局 metrics 仅供跨期对比，不要用来判断本周的会话。",
        "flood": {
            "dominant_tool_counts": flood_tools.most_common(12),
            "avg_output_chars_per_call": {
                t: flood_volume[t] // max(flood_calls[t], 1)
                for t, _ in flood_volume.most_common(15)
            },
        },
        "caveat": "These numbers describe THIS corpus only. Never ship them as constants.",
    }
    (out_dir / "baseline.json").write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")

    seen: set[str] = set()
    uniq = []
    for c in candidates:
        key = c["text"][:60]
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    (out_dir / "dissat_candidates.jsonl").write_text(
        "\n".join(json.dumps(c, ensure_ascii=False) for c in uniq), encoding="utf-8")

    print(f"\nsessions kept: {kept}")
    print(f"baseline    -> {out_dir/'baseline.json'}")
    print(f"candidates  -> {out_dir/'dissat_candidates.jsonl'}  ({len(uniq)} unique)")
    print("\ntop flood tools by avg output size per call (this environment):")
    for t, avg in sorted(baseline["flood"]["avg_output_chars_per_call"].items(),
                         key=lambda kv: -kv[1])[:8]:
        print(f"   {t:<20} {avg:>10,} chars/call")
    print("\nNEXT: a human must confirm which candidates are genuine dissatisfaction.")
    print("The mined list is high-recall / low-precision by design — it is a")
    print("drill-down entry point, not a label.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
