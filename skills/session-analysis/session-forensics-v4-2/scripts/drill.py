#!/usr/bin/env python3
"""Bounded line-window drill into a session, with the context gate enforced.

Why this is a script and not a note: a written rule ("windows of at most 50 lines") is executed by
discipline, and readers hand-roll jq / sed / python slices instead, none of which enforce the cap.
The cap only exists if something refuses.

Cross-platform: goes through session_events, so Codex / Claude Code / Kimi all
render the same way. Output is truncated per record; the point is to see the
shape of a window, never to pull raw JSONL across the context boundary.

Usage:
  drill.py <session.jsonl> 660:714
  drill.py <session.jsonl> 660:714 --cap 700          # chars per record
  drill.py <session.jsonl> 100:140 250:290 500:540    # up to 3 windows
  drill.py <session.jsonl> --grep "rsync" --limit 20  # locate lines first
  drill.py <session.jsonl> --role assistant            # only the agent's replies (whole session, --limit)
  drill.py <session.jsonl> 100:140 --role user         # only the user's lines inside a window
Roles: user (user messages, including mid-turn interjections), assistant (the agent's prose replies),
tool (tool calls and their results).
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
ROLES = {"user": {"user_msg"}, "assistant": {"assistant_msg"}, "tool": {"tool_call", "tool_output"}}


def role_ok(e, role) -> bool:
    return role is None or e.kind in ROLES[role]


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
    ap.add_argument("--limit", type=int, default=30, help="max --grep hits / max records for --role without a window")
    ap.add_argument("--role", choices=sorted(ROLES), help="only records of this role: user | assistant | tool")
    ap.add_argument("--verify", nargs=2, metavar=("LINE", "EXCERPT"),
                    help="check that EXCERPT (literal, case-insensitive) occurs in the record at LINE; exit 1 if not")
    args = ap.parse_args()

    args.session = args.session.expanduser()
    if not args.session.exists():
        raise SystemExit(f"no such session: {args.session}")

    if args.verify:
        want_line, excerpt = int(args.verify[0].split(":")[-1]), " ".join(args.verify[1].split()).lower()
        for e in iter_events(args.session):
            if e.line == want_line:
                body = " ".join(((e.text or "") + " " + (e.name or "")).split()).lower()
                if excerpt in body:
                    print(f"VERIFIED line {want_line}"); return 0
                print(f"MISMATCH line {want_line}: excerpt not found; record starts: {body[:160]}")
                return 1
            if e.line > want_line:
                break
        print(f"MISMATCH line {want_line}: no event record at that line")
        return 1

    if args.grep:
        if any(ch in args.grep for ch in "|*^$[]\\"):
            print("REFUSED: --grep is a literal, case-insensitive substring match; regex characters "
                  "(| * ^ $ [ ] \\) would silently return 'no match'. Split into several --grep calls.",
                  file=sys.stderr)
            return 2
        needle = args.grep.lower()
        hits = 0
        for e in iter_events(args.session):
            if not role_ok(e, args.role):
                continue
            if needle in (e.text or "").lower() or needle in (e.name or "").lower():
                print(render(e, 160))
                hits += 1
                if hits >= args.limit:
                    print(f"… stopped at --limit {args.limit}")
                    break
        if not hits:
            print("no match (literal substring over rendered text; 0 hits can also mean the parser skipped this record type)")
        return 0

    if not args.windows and args.role:
        shown = 0
        for e in iter_events(args.session):
            if role_ok(e, args.role) and (e.text or e.name):
                print(render(e, args.cap))
                shown += 1
                if shown >= args.limit:
                    print(f"… stopped at --limit {args.limit}（加窗口或调大 --limit 看后面的）")
                    break
        print(f"\n— {shown} {args.role} records, ≤{args.cap} chars each —", file=sys.stderr)
        return 0
    if not args.windows:
        raise SystemExit("give at least one START:END window, or use --grep / --role")
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
        if any(a <= e.line <= b for a, b in wins) and role_ok(e, args.role):
            print(render(e, args.cap))
            shown += 1
    print(f"\n— {shown} records across {len(wins)} window(s), "
          f"≤{args.cap} chars each —", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
