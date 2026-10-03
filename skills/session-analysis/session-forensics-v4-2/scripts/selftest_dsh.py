#!/usr/bin/env python3
"""Self-test for the dsh adapter's UNOBSERVED paths.

Why this file exists: the local dsh corpus (19 files, 2026-08-25) contains no
compaction, no failed compaction, and no denied approval — every session ran
under a 1M window and never crossed it. So the compaction accounting, which is
the single most valuable thing dsh offers (it prices exactly what it erased),
is derived from the package's own `.d.ts` contract and cannot be checked
against real data. An untested path that nothing exercises is a path that rots
silently, and this skill's whole thesis is that silent rot is what nobody sees.

So the contract is pinned here as a synthetic fixture instead. It also writes
TWO appended zstd frames, because a single-frame decoder reading only the first
one is the platform's most dangerous failure mode (see references/dsh-jsonl.md).

    python3 selftest_dsh.py          # exits non-zero on any mismatch

Update this file when the dsh contract changes — never to make it pass.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session_metrics as SM  # noqa: E402

RECORDS = [
    {"type": "session", "version": 0, "id": "synth-1", "createdAt": 1,
     "cwd": "/tmp", "delegationDepth": 0},
    {"type": "request/context", "seq": 1, "time": 1,
     "data": {"provider": "deepseek-official", "model": "m", "contextWindow": 1000000}},
    # one human, one harness injection, one self-driven goal round
    {"type": "user/message", "seq": 2, "time": 2,
     "data": {"source": {"kind": "user"},
              "content": [{"type": "text", "text": "please build the thing"}]}},
    {"type": "user/message", "seq": 3, "time": 3,
     "data": {"source": {"kind": "plugin", "plugin": "p"},
              "content": [{"type": "text", "text": "sandbox policy snapshot"}]}},
    {"type": "user/message", "seq": 4, "time": 4,
     "data": {"source": {"kind": "goal", "round": 3, "revision": 1},
              "content": [{"type": "text", "text": "continue goal"}]}},
    # c1: a complete, successful summarizing compaction
    {"type": "compaction/start", "seq": 10, "time": 10,
     "data": {"compactionId": "c1", "turn": 3}},
    {"type": "compaction/summary", "seq": 11, "time": 11,
     "data": {"compactionId": "c1", "summary": [{"type": "text", "text": "s"}],
              "shadowedRange": {"start": 2, "end": 9},
              "shadowedSeqs": list(range(2, 10)), "shadowedTokenCount": 41234,
              "provider": "deepseek-official", "model": "deepseek-v4-flash"}},
    {"type": "compaction/end", "seq": 12, "time": 12,
     "data": {"compactionId": "c1", "turn": 3}},
    # c2: closed, but reported an error -> a failure, not a compaction
    {"type": "compaction/start", "seq": 20, "time": 20,
     "data": {"compactionId": "c2", "turn": 5}},
    {"type": "compaction/end", "seq": 21, "time": 21,
     "data": {"compactionId": "c2", "turn": 5, "error": "summarizer timed out"}},
    # c3: never closed -> the summarizer died holding the lock
    {"type": "compaction/start", "seq": 30, "time": 30,
     "data": {"compactionId": "c3", "turn": 7}},
    # a model-free prune, which is still a real shadowing
    {"type": "compaction/prune", "seq": 31, "time": 31,
     "data": {"shadowedRange": {"start": 22, "end": 29},
              "shadowedSeqs": [22, 23, 24], "shadowedTokenCount": 900}},
    {"type": "approval/decided", "seq": 40, "time": 40,
     "data": {"decision": "deny", "reason": "owner said no"}},
    {"type": "turn/end", "seq": 50, "time": 50,
     "data": {"turn": 1, "reason": {"kind": "completed"}}},
]

# (path in the report, expected value, why it is that value)
EXPECTED = [
    (("scale", "lines"), 14, "both zstd frames were read; 5 means only the first"),
    (("scale", "compactions"), 2, "summary + prune; the two brackets are not compactions"),
    (("scale", "compactions_unclosed"), 1, "c3 opened and never closed"),
    (("scale", "approval_denials"), 1, "one denied approval"),
    (("scale", "user_substantive"), 1, "only source.kind=='user' is a human"),
    (("scale", "user_ambient"), 2, "plugin injection + goal continuation round"),
    (("scale", "self_pump_rounds"), 1, "the goal round is a self-pump, not a user pump"),
    (("scale", "max_goal_round"), 3, "round number is carried through"),
    (("context", "shadowed_tokens"), 42134, "41234 summarized + 900 pruned"),
    (("context", "shadowed_events"), 11, "8 shadowed seqs + 3 pruned"),
    (("context", "window"), 1000000, "announced once by request/context, held as state"),
]


def build(target: Path) -> Path:
    """Write the fixture as two APPENDED zstd frames."""
    lines = [json.dumps(r) for r in RECORDS]
    parts = ["\n".join(lines[:5]) + "\n", "\n".join(lines[5:]) + "\n"]
    path = target / "session.jsonl.zstd"
    with path.open("wb") as fh:
        for part in parts:
            done = subprocess.run(["zstd", "-q", "-c"], input=part.encode(),
                                  capture_output=True)
            if done.returncode != 0:
                raise RuntimeError("zstd CLI unavailable; cannot build the fixture")
            fh.write(done.stdout)
    return path


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        report = SM.scan(build(Path(tmp)))

    if report["format"] != "dsh":
        print(f"FAIL  format detected as {report['format']!r}, expected 'dsh'")
        return 1

    failures = 0
    for (section, key), want, why in EXPECTED:
        got = report[section][key]
        ok = got == want
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'}  {section}.{key:20} = {got!r}"
              f"{'' if ok else f' (expected {want!r})'}    # {why}")

    failed = report["scale"]["compactions_failed"]
    if not (isinstance(failed, list) and len(failed) == 1
            and "summarizer timed out" in failed[0][1]):
        print(f"FAIL  scale.compactions_failed = {failed!r}, expected one timeout entry")
        failures += 1
    else:
        print(f"ok    scale.compactions_failed  = {failed!r}    # c2's error is retained")

    print("PASS" if not failures else f"{failures} FAILURE(S)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
