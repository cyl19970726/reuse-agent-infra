#!/usr/bin/env python3
"""Is the agent behind a session finished, running, or stalled? (v2)

Answers the question progress_probe cannot: progress_probe only looks inside a window of
records, so a thread that finished hours ago still reads as INVESTIGATING.

  liveness.py <session.jsonl> [--stale-min 20] [--at <time>]

--at judges the session as it stood at that moment: records after it are ignored, age is measured to it,
and no process check is made. <time> is ISO (2026-09-28T10:30, a zone like +08:00 or Z allowed; without a
zone it is this machine's local time).

Output (one block):
  state: DONE | RUNNING | STALE | UNKNOWN
  last_record: <timestamp>   age_min: <minutes before now, or before --at>
  tail_complete: yes|no      (Codex task_complete/turn end; Claude final assistant turn with end_turn)
  process_alive: yes|no|unknown  (a running codex/claude process mentioning the session id)
Rules: DONE = tail complete and no newer user turn; RUNNING = record within --stale-min or live process;
STALE = not complete, no live process, silent longer than --stale-min.
"""
import argparse, json, os, re, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path


def parse_ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def local(ts) -> str:
    """A record's timestamp in this machine's time zone (the ledger and the report use local time too)."""
    d = parse_ts(ts) if isinstance(ts, str) else ts
    if d is None:
        return str(ts)
    return (d.astimezone() if d.tzinfo else d).strftime("%Y-%m-%d %H:%M:%S %Z").strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session")
    ap.add_argument("--stale-min", type=int, default=20)
    ap.add_argument("--at", help="judge the session as of this time (ISO; without a zone = this machine's local time)")
    a = ap.parse_args()
    path = Path(a.session).expanduser()
    if not path.exists():
        sys.exit(f"no such session: {path}")
    stale_min = a.stale_min
    at = None
    if a.at:
        raw = a.at
        at = parse_ts(raw)
        if at is None:
            sys.exit(f"--at: cannot read time {raw!r} (use ISO, e.g. 2026-09-28T10:30 or 2026-09-28T02:30Z)")
        if at.tzinfo is None:
            at = at.astimezone()  # a plain time is this machine's local time
    elif os.environ.get("REPLAY_CUTOFF"):
        at = parse_ts(os.environ["REPLAY_CUTOFF"])
    last_ts = None; tail_complete = False; last_kind = None; first_ts = None; later = 0
    with open(path, "rb") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = d.get("timestamp")
            if t and first_ts is None:
                first_ts = t
            if at is not None and t:
                tt = parse_ts(t)
                if tt is not None and tt.tzinfo is not None and tt > at:
                    later += 1
                    continue  # written after that moment (records are not always in time order: skip, don't stop)
            if t:
                last_ts = t
            p = d.get("payload") or {}
            typ = d.get("type")
            # Codex
            if typ == "event_msg" and p.get("type") in ("task_complete", "turn_complete"):
                tail_complete = True; last_kind = "complete"
            elif typ == "response_item" and p.get("type") == "message" and p.get("role") == "user":
                tail_complete = False; last_kind = "user"
            elif typ == "event_msg" and p.get("type") in ("task_started", "user_message"):
                tail_complete = False; last_kind = "started"
            # Claude Code
            elif typ == "assistant":
                m = d.get("message") or {}
                tail_complete = m.get("stop_reason") == "end_turn"
                last_kind = "assistant"
            elif typ == "user":
                c = (d.get("message") or {}).get("content")
                is_result = isinstance(c, list) and any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c)
                tail_complete = False; last_kind = "tool_result" if is_result else "user"
    now = at or datetime.now(timezone.utc)
    cut = at is not None
    lt = parse_ts(last_ts) if last_ts else None
    age = (now - lt).total_seconds() / 60 if lt else None
    sid = re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", path.name)
    alive = "unknown"
    if sid and not cut:
        ps = subprocess.run(["ps", "axo", "command"], capture_output=True, text=True).stdout
        procs = [l for l in ps.splitlines() if "liveness.py" not in l and ("codex" in l or "claude" in l)]
        alive = "yes" if any(s in l for l in procs for s in sid) else "no"
    if tail_complete and (age is None or age >= 0):
        state = "DONE"
    elif alive == "yes" or (age is not None and age < stale_min):
        state = "RUNNING"
    elif age is not None:
        state = "STALE"
    else:
        state = "UNKNOWN"
    print(f"state: {state}")
    print(f"last_record: {local(last_ts) if last_ts else None} (local)   raw: {last_ts}   age_min: {None if age is None else round(age, 1)}"
          + (f"  (as of {local(at)})" if cut else ""))
    if cut and last_ts is None and later:
        print(f"note: --at {local(at)} is before the first record ({local(first_ts)}). A time without a zone is read as "
              "local time; the raw timestamps end in Z (UTC) — add Z to use one as is.")
    elif cut and later:
        print(f"note: {later} record(s) after --at were ignored")
    print(f"tail_complete: {'yes' if tail_complete else 'no'}   last_kind: {last_kind}")
    print(f"process_alive: {alive}" + ("  (not checked with --at)" if cut else ""))
    if state == "DONE":
        print("hint: the tail is final — read the last answer; do not re-probe.")


if __name__ == "__main__":
    main()
