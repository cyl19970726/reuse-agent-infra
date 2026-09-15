#!/usr/bin/env python3
"""What is a silent subagent / Agent Team member actually doing right now?

Why this exists
---------------
A Host waiting on a worker has exactly two questions, and the control plane
answers only the first:

    "has anything happened?"   -> harness `team-run wait` / an event cursor
    "what is it doing NOW?"    -> nothing answers this

The second question is why Hosts poll. Measured on `CASE-G`, the same
pathology recurred across three tool generations, each time because the Host
had no cheap way to see inside a silent worker:

    exec --watch + write_stdin      55.6M chars      (CASE-D)
    wait_agent 30s fixed timeout    486 empty returns, ~4h wall clock
    harness team-run status poll    35 calls, p50 gap 58s, 27 polls / 25min
                                    in a window with zero patches

This probe answers the second question from the worker's own session file,
which is evidence, not narrative. It never replaces the control plane: use
`team-run wait` to learn WHEN something happened, use this to learn WHAT a
silent worker is doing before deciding to steer, interrupt, or leave it alone.

Context boundary
----------------
Worker sessions reach hundreds of MB. Reading one to check on it would import
the very O(N) re-scan problem this skill diagnoses elsewhere. So:

  * first call profiles only the trailing --tail-bytes (default 2 MB)
  * subsequent calls pass --after-line from the previous output, so only the
    increment is retained and classified
  * output is a fixed-size verdict block, never raw records

Both paths stream the file to keep line numbers stable, so wall cost is O(file)
in CPU but O(window) in memory and in context — measured 1.5 s and ~10 lines of
output on a 392 MB session. That asymmetry is the whole point: the expensive
axis is the context, not the CPU.

Probe a RECENT window. Over a long span a worker can both produce and idle, and
the verdict reports the dominant fact; the `repeated` line still surfaces a
concurrent poll loop.

Verdicts are deliberately few and mechanical. `PRODUCING` and `DEAD` are the
only two that justify acting without looking further; everything else means
"still working, leave it alone" or "look with drill.py".
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session_events as SE  # noqa: E402

DEFAULT_TAIL_BYTES = 2 * 1024 * 1024
# A worker that has emitted nothing for this long is not "thinking"; every
# provider writes reasoning records far more often than this.
DEFAULT_STALL_SECS = 300

STATE_CHANGING = {"apply_patch", "Edit", "Write", "NotebookEdit", "MultiEdit"}
# Exec-family names across Codex / Claude Code / Kimi.
EXEC_LIKE = {"exec", "exec_command", "shell", "bash", "Bash", "local_shell",
             "run_command", "container.exec"}
# Waiting on someone else. A worker doing this is not stuck, it is blocked.
WAIT_LIKE = {"wait_agent", "wait", "write_stdin", "list_agents", "sleep"}

FAIL_RE = re.compile(r"exit code: [1-9]|Traceback|\bFAILED\b|\berror:", re.I)
# `timed out` from a bounded poll is the poll working, not a failure. Counting
# it as one is exactly the parse bug this skill hit on its own timeout_rate
# (see local/falsified.md 2026-07-30).
POLL_TIMEOUT_RE = re.compile(r"timed out|\"timed_out\"\s*:\s*true", re.I)
# Both regexes are applied ONLY to outputs whose owning tool is in WAIT_LIKE
# (polls) or EXEC_LIKE (failures) — an owner gate, not a global substring scan.
# Falsified live on 2026-08-10 (this skill's own audit session CASE-Y): a
# healthy read-only auditor was verdicted WAIT_LOOP with "4 empty polls" that
# were all Read outputs of DOCUMENTS DISCUSSING timeouts. Same contamination
# class session_metrics fixed across three generations (HARNESS_TIMEOUT_RE /
# EXEC_OWNERS); the lesson had not propagated to this file — see the
# cross-carrier propagation gate in references/signatures.md.


def tail_offset(path: Path, tail_bytes: int) -> int:
    size = path.stat().st_size
    return max(0, size - tail_bytes)


def scan(path: Path, after_line: int, tail_bytes: int) -> dict:
    """Collect a fixed-size profile of the worker's recent activity.

    Streaming only: the file is never held in memory. When `after_line` is
    given we still stream from the start (line numbers must stay stable) but
    keep nothing before the cursor, so cost is bounded by what we retain, not
    by file size.
    """
    fmt = SE.detect_format(path)
    tools = Counter()
    last_line = after_line
    last_kind = None
    last_tool = None
    last_cmd = None
    n_state_change = 0
    n_exec = 0
    n_wait = 0
    n_fail = 0
    n_poll_timeout = 0
    n_reasoning = 0
    n_assistant = 0
    n_events = 0
    repeated = Counter()

    start_line = 0
    # A compressed log has no usable byte->line correspondence: seeking into the
    # middle of a zstd frame lands in ciphertext, and the on-disk size is not the
    # thing tail_bytes is measured in. Stream it instead. dsh packs its streaming
    # deltas into chunk rows, so its logs are small for their content (13 MB
    # unpacked for a 265-step session) and a full stream stays cheap — but the
    # cold-start window really is unbounded here, so say so rather than pretend.
    if SE.is_compressed(path):
        tail_bytes = 0
    if after_line == 0 and tail_bytes > 0 and path.stat().st_size > tail_bytes:
        # Cold start on a large file: approximate the window by byte offset,
        # then report the first line we actually saw so the caller's cursor is
        # honest about where the picture begins.
        with path.open("rb") as fh:
            fh.seek(tail_offset(path, tail_bytes))
            fh.readline()
            skip_bytes = fh.tell()
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            consumed = 0
            for n, raw in enumerate(fh, start=1):
                consumed += len(raw.encode("utf-8", "replace"))
                if consumed >= skip_bytes:
                    start_line = n
                    break

    for e in SE.iter_events(path, fmt=fmt):
        if e.line <= after_line or e.line < start_line:
            continue
        n_events += 1
        last_line = max(last_line, e.line)
        if e.kind == "tool_call":
            name = e.name or "?"
            tools[name] += 1
            last_kind, last_tool = "tool_call", name
            text = " ".join((e.text or "").split())
            if text:
                last_cmd = text[:160]
                repeated[text[:200]] += 1
            if name in STATE_CHANGING:
                n_state_change += 1
            elif name in EXEC_LIKE:
                n_exec += 1
            elif name in WAIT_LIKE:
                n_wait += 1
        elif e.kind == "tool_output":
            # Attribution is positional (e.name is empty on Codex/Claude Code):
            # an output belongs to the most recent call — the same rule
            # session_metrics uses. last_tool persists until the next tool_call.
            owner = e.name or last_tool
            text = e.text or ""
            if owner in WAIT_LIKE and POLL_TIMEOUT_RE.search(text):
                n_poll_timeout += 1
            elif owner in EXEC_LIKE and FAIL_RE.search(text):
                n_fail += 1
            last_kind = "tool_output"
        elif e.kind == "thinking":
            n_reasoning += 1
            last_kind = "thinking"
        elif e.kind == "assistant_msg":
            n_assistant += 1
            last_kind = "assistant_msg"

    top_repeat = repeated.most_common(1)[0] if repeated else ("", 0)
    return {
        "format": fmt,
        "after_line": after_line,
        "next_after_line": last_line,
        "window_start_line": start_line or (after_line + 1),
        "events": n_events,
        "state_changing": n_state_change,
        "exec": n_exec,
        "wait": n_wait,
        "failures": n_fail,
        "poll_timeouts": n_poll_timeout,
        "reasoning": n_reasoning,
        "assistant": n_assistant,
        "tools": tools.most_common(6),
        "last_kind": last_kind,
        "last_tool": last_tool,
        "last_cmd": last_cmd,
        "top_repeat": {"text": top_repeat[0][:120], "count": top_repeat[1]},
        "mtime_age_secs": int(time.time() - path.stat().st_mtime),
        "size_bytes": path.stat().st_size,
    }


def verdict(p: dict, stall_secs: int) -> tuple[str, str]:
    """Map the profile to one action. Only DEAD and PRODUCING are actionable
    without a second look; the rest mean wait or drill."""
    age = p["mtime_age_secs"]
    if age > stall_secs and p["events"] == 0:
        return ("DEAD", f"no session writes for {age}s and no new records — "
                        "check the runtime/process, not the prompt")
    if p["events"] == 0:
        return ("IDLE", f"no new records since the cursor (file touched {age}s ago) — "
                        "member is between turns or awaiting mail; do not resend work")
    if p["state_changing"] > 0:
        note = ""
        if p["poll_timeouts"] >= 3:
            # Over a long window a worker can both produce and idle. Report the
            # dominant fact but never hide the idling underneath it.
            note = (f"; but {p['poll_timeouts']} empty polls in the same window — "
                    "probe a shorter window to see which is current")
        return ("PRODUCING", f"{p['state_changing']} state-changing edits in this window — "
                             f"leave it alone{note}")
    if p["poll_timeouts"] >= 3 and p["state_changing"] == 0:
        return ("WAIT_LOOP", f"{p['poll_timeouts']} empty poll returns and zero edits — "
                             "it is waiting on someone else, not working (G7)")
    if p["top_repeat"]["count"] >= 5:
        return ("REPEATING", f"same call {p['top_repeat']['count']}x — "
                             "likely a stuck loop; drill before steering")
    # Rate, never absolute count. Falsified live on 2026-07-31: two healthy
    # read-only reviewer members (7 exec / 17 reasoning, and 31 exec) both hit
    # an absolute `failures >= 3` rule and were reported FAILING while they
    # were working correctly — a reviewer that deliberately exercises error
    # paths produces failing outputs BY DESIGN, and never produces edits at
    # all. "Most of what it runs is failing" is the real signal.
    fail_ratio = p["failures"] / p["exec"] if p["exec"] else 0.0
    if p["failures"] >= 3 and fail_ratio >= 0.5:
        return ("FAILING", f"{p['failures']} of {p['exec']} exec calls failing "
                           f"({fail_ratio:.0%}) with zero edits — "
                           "it is fighting the environment; consider steering")
    if p["exec"] > 0 or p["events"] > 0:
        note = f", {p['failures']} expected/handled failures" if p["failures"] else ""
        return ("INVESTIGATING", f"{p['exec']} exec / {p['reasoning']} reasoning, no edits yet"
                                 f"{note} — normal for a reader or an early phase; "
                                 "re-probe later before intervening")
    return ("UNCLEAR", "records present but none classified — use drill.py")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Bounded progress probe for a silent subagent / Agent Team member.")
    ap.add_argument("session", type=Path, help="worker session file (absolute path)")
    ap.add_argument("--after-line", type=int, default=0,
                    help="cursor from a previous run; read only the increment")
    ap.add_argument("--tail-bytes", type=int, default=DEFAULT_TAIL_BYTES,
                    help="cold-start window, default 2MB (ignored when --after-line is set)")
    ap.add_argument("--stall-secs", type=int, default=DEFAULT_STALL_SECS,
                    help="silence beyond this counts as DEAD, default 300")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not args.session.is_absolute():
        print("session path must be absolute (relative paths are why the previous "
              "generation of this skill had a zero execution rate)", file=sys.stderr)
        return 2
    if not args.session.exists():
        print(f"no such session: {args.session}", file=sys.stderr)
        return 2

    tail = 0 if args.after_line else args.tail_bytes
    p = scan(args.session, args.after_line, tail)
    v, why = verdict(p, args.stall_secs)
    p["verdict"], p["reason"] = v, why

    if args.json:
        print(json.dumps(p, ensure_ascii=False, indent=1))
        return 0

    mb = p["size_bytes"] / 1e6
    print(f"{v}  —  {why}")
    print(f"  session      {args.session.name}  ({mb:.1f} MB, {p['format']}, "
          f"touched {p['mtime_age_secs']}s ago)")
    print(f"  window       lines {p['window_start_line']}..{p['next_after_line']}  "
          f"({p['events']} records)")
    print(f"  activity     edits={p['state_changing']} exec={p['exec']} wait={p['wait']} "
          f"fail={p['failures']} empty-polls={p['poll_timeouts']} "
          f"reasoning={p['reasoning']}")
    if p["tools"]:
        print("  tools        " + ", ".join(f"{k}={n}" for k, n in p["tools"]))
    if p["last_cmd"]:
        print(f"  last call    [{p['last_tool']}] {p['last_cmd']}")
    if p["top_repeat"]["count"] >= 3:
        print(f"  repeated     {p['top_repeat']['count']}x  {p['top_repeat']['text']}")
    print(f"  next probe   --after-line {p['next_after_line']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
