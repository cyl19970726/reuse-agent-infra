#!/usr/bin/env python3
"""Bounded line-window drill into a session, with the context gate enforced.

Why this is a harness and not a note in SKILL.md
------------------------------------------------
SKILL.md §5 says "按行号取窗口，每次 ≤50 行，每会话 ≤3 个窗口". That is a rule
executed by discipline, and every operator hand-rolls the reader instead:

  CASE-F used THREE different implementations for the identical operation —
    jq 'select(input_line_number>=A and <=B)'
    sed -n 'A,Bp'
    python3 - <<PY  (open, enumerate, slice)
  and the agent auditing that session then wrote a FOURTH one in its scratchpad.

Four mutually different copies of one operation is the skill's own
「仪器分叉」signature, sitting on its own core step. Worse, none of the four
enforced the ≤50-line cap — the cap only exists if something refuses.

Cross-platform: goes through session_events, so Codex / Claude Code / Kimi all
render the same way. Output is truncated per record; the point is to see the
shape of a window, never to pull raw JSONL across the context boundary.

Usage:
  drill.py <session.jsonl> 660:714
  drill.py <session.jsonl> 660:714 --cap 700          # chars per record
  drill.py <session.jsonl> 100:140 250:290 500:540    # up to 3 windows
  drill.py <session.jsonl> --grep "rsync" --limit 20  # locate lines first
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from session_events import iter_events  # noqa: E402

MAX_WINDOW_LINES = 50
MAX_WINDOWS = 3
DEFAULT_CAP = 400


def parse_window(spec: str) -> tuple[int, int]:
    if ":" not in spec:
        raise SystemExit(f"window must be START:END, got {spec!r}")
    a, b = spec.split(":", 1)
    try:
        start, end = int(a), int(b)
    except ValueError:
        raise SystemExit(f"window bounds must be integers, got {spec!r}")
    if end < start:
        raise SystemExit(f"window END < START: {spec}")
    span = end - start + 1
    if span > MAX_WINDOW_LINES:
        raise SystemExit(
            f"REFUSED: window {spec} spans {span} lines, cap is {MAX_WINDOW_LINES}.\n"
            "This is the context-boundary gate, not a suggestion — a wide window is\n"
            "how raw JSONL ends up crossing into context. Narrow it, or use --grep\n"
            "to find the exact lines worth reading first."
        )
    return start, end


def render(e, cap: int) -> str:
    """One normalized line per event. Never the raw record."""
    if e.kind == "user_msg":
        head = "[user]"
    elif e.kind == "assistant_msg":
        head = "[assistant]"
    elif e.kind == "thinking":
        head = "[reasoning]"
    elif e.kind == "tool_call":
        head = f"[call {e.name}]"
    elif e.kind == "tool_output":
        head = f"[out {e.name or '?'}]"
    elif e.kind == "token_count":
        head = "[token_count]"
    elif e.kind == "session_meta":
        head = "[session_meta]"
    else:
        head = f"[{e.kind}]"
    if e.kind == "token_count":
        extra = e.extra or {}
        body = (
            f"input={extra.get('input_tokens')} "
            f"uncached={extra.get('uncached_input_tokens')} "
            f"cached={extra.get('cached_input_tokens')} "
            f"cache_write={extra.get('cache_write_input_tokens')} "
            f"window={extra.get('window')} "
            f"model={extra.get('model')} "
            f"session={extra.get('session_id')} "
            f"request={extra.get('request_id')} "
            f"timestamp={extra.get('timestamp')}"
        )
    elif e.kind == "session_meta":
        extra = e.extra or {}
        body = (
            f"thread={extra.get('thread_id')} "
            f"session={extra.get('session_id')} "
            f"forked_from={extra.get('forked_from_id')} "
            f"provider={extra.get('model_provider')} "
            f"timestamp={extra.get('timestamp')} "
            f"effort={extra.get('effort')} "
            f"depth={extra.get('delegation_depth')} "
            f"cwd={extra.get('cwd')}"
        )
    else:
        body = " ".join((e.text or "").split())
        # Some events carry their whole payload in `name` (subagent labels,
        # compaction brackets, approval decisions). Rendering text-only printed
        # them as a bare "[subagent]" with nothing after it.
        if not body and e.name:
            body = e.name
    if len(body) > cap:
        body = body[:cap] + f" …(+{len(body) - cap} chars)"
    return f"{e.line}: {head} {body}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session", type=Path)
    ap.add_argument("windows", nargs="*", help="START:END, at most 3")
    ap.add_argument("--cap", type=int, default=DEFAULT_CAP,
                    help=f"chars per record (default {DEFAULT_CAP})")
    ap.add_argument("--grep", help="find lines whose text matches, instead of drilling")
    ap.add_argument("--limit", type=int, default=30, help="max --grep hits")
    args = ap.parse_args()

    if not args.session.exists():
        raise SystemExit(f"no such session: {args.session}")

    if args.grep:
        needle = args.grep.lower()
        hits = 0
        for e in iter_events(args.session):
            if needle in (e.text or "").lower() or needle in (e.name or "").lower():
                print(render(e, 160))
                hits += 1
                if hits >= args.limit:
                    print(f"… stopped at --limit {args.limit}")
                    break
        if not hits:
            print("no match")
        return 0

    if not args.windows:
        raise SystemExit("give at least one START:END window, or use --grep")
    if len(args.windows) > MAX_WINDOWS:
        raise SystemExit(
            f"REFUSED: {len(args.windows)} windows, cap is {MAX_WINDOWS} per session.\n"
            "More than three windows means the anomaly was not localised first —\n"
            "go back to the metrics and pick the lines that actually matter."
        )

    wins = [parse_window(w) for w in args.windows]
    lo, hi = min(a for a, _ in wins), max(b for _, b in wins)
    shown = 0
    for e in iter_events(args.session):
        if e.line > hi:
            break
        if e.line < lo:
            continue
        if any(a <= e.line <= b for a, b in wins):
            print(render(e, args.cap))
            shown += 1
    print(f"\n— {shown} records across {len(wins)} window(s), "
          f"≤{args.cap} chars each —", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
