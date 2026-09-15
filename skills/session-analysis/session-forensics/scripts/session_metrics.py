#!/usr/bin/env python3
"""Normalized structural metrics for an agent session JSONL (Codex / Claude Code).

Design constraints — see references/mental-model.md:
- Streaming only. The file never enters memory; sessions reach 1.5 GB.
- Emits metrics, not verdicts. No thresholds live here (see references/signatures.md
  for why every absolute-count threshold tried so far has been falsified).
- Separates the narrative flow from the evidence flow rather than interleaving them.
- Every count is also reported as a rate, because ~90% of raw signal is session
  scale, not session pathology.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session_events as SE  # noqa: E402
from session_events import CAPABILITIES, detect_format, iter_events  # noqa: E402

# Worktree-root granularity: /Users|/home take <home>/<group>/<repo> (two levels
# below the home dir — matches the calibrated baseline, semantics unchanged);
# /root sandboxes are flat, one level (/root/<workspace>). Before 2026-08-10 the
# pattern was /Users-only, so every /root and /home path collapsed into the one
# pseudo-root "?" and cross-tree fork detection was BLIND there (found in audit
# session CASE-Y — same class as the .codex/worktrees three-segment truncation
# fixed on 2026-08-05). forked_share on sessions patching several /root
# workspaces can now legitimately rise from 0.
WORKTREE_RE = re.compile(r"^(/(?:Users|home)/[^/]+/[^/]+/[^/]+|/root/[^/]+)/")
# Patches are sometimes applied through `exec` as an embedded JS string, where the
# newline is the two characters \ and n rather than a real newline. A greedy (.+)
# then swallows the rest of the patch body and reports it as a file path, which
# corrupts the patch counts, the file count and the whole instrument-forking check.
PATCH_TARGET_RE = re.compile(r"\*\*\* (?:Update|Add|Delete) File: ([^\n\"\\]+)")
FAIL_RE = re.compile(r"exit code: [1-9]|Traceback|\bFAILED\b|\berror:", re.I)
# Any bounded-poll tool saying "timed out" is the poll working, not a failure.
# Kept only to route those hits to `poll_timeouts`, never to `timeouts`.
POLL_TIMEOUT_RE = re.compile(r"timed out|timeout", re.I)

# A command timeout is only measurable from a marker the HARNESS emits, never
# from the word "timeout" appearing in output. Falsified three times, each time
# by a different contamination class:
#
#   1. CASE-G: un-gated word match -> 556/1091 = 0.5096, ABOVE the corpus max
#      (0.3424). 481 were `wait_agent` returning its ordinary bounded-poll
#      result. Fix attempt 1: restrict the numerator to exec-family owners.
#   2. This skill's own audit session (Claude Code, 1026 lines): the owner gate
#      still reported 0.1333, and attributing every hit showed 27/27 were
#      SELF-REFERENTIAL -- the metric name `timeout_rate`, the baseline JSON,
#      the source line of this very regex, a drill rendering of someone else's
#      wait_agent. Zero real timeouts. An exec tool printing the word passes an
#      owner gate trivially, so fix attempt 1 addressed only half the problem
#      it claimed to address.
#   3. Tightening to strong phrases (`command timed out`, `timed out after`,
#      `exit code 124`) still matched a JavaScript source string
#      `throw new Error("Timed out after ...")` inside a read file.
#
# Substring matching over free-form tool output cannot separate "this command
# timed out" from "this output mentions timeouts". So: per-platform harness
# markers only, and `null` where the platform emits none -- the same rule the
# platform table already applies elsewhere. Never 0: absence of a marker is not
# evidence of no timeouts.
#
#   Codex        exec_command whose `yield_time_ms` elapsed returns a session
#                handle instead of an exit code (39 on CASE-G).
#   Claude Code  the harness says it moved the command to the background
#                (2 in the audit session).
#   Kimi         no known marker -> null.
HARNESS_TIMEOUT_RE = {
    "codex": re.compile(r"Process running with session ID", re.I),
    "claude_code": re.compile(r"did not complete within its \S+ timeout", re.I),
    "kimi": None,
}
EXEC_OWNERS = {"exec", "exec_command", "shell", "bash", "local_shell",
               "Bash", "run_command", "container.exec"}
INSTRUMENT_RE = re.compile(r"/(tools|scripts|harness|\.agents|test|tests)/")

# Harness-injected blocks that arrive with role=user but are NOT the user.
# Missing any of these silently corrupts the objective trace.
AMBIENT_MARKERS = (
    "<in-app-browser-context",
    "<recommended_plugins>",
    "<environment_context>",
    "<codex_internal_context",
    "<system-reminder",
    "<notification",
    "<task-notification",
    "<cross-session-message",
    # Claude Desktop also emits peer handoffs as a plain-text wrapper rather
    # than the XML-shaped <cross-session-message> variant.  The body itself
    # contains an <agent-message>, but neither record is an Owner objective.
    "Another Claude session sent a message:",
    "<skill>",
    "<local-command-caveat>",
    "<local-command-stdout>",
    "<INSTRUCTIONS>",
    "AGENTS.md instructions for",
    "Base directory for this skill:",
    "[Image:",
    "[Request interrupted by user]",
    "Continue working toward the active goal.",
)

# Harness blocks that WRAP a real user request instead of replacing it.
# Treating these as ambient silently deletes genuine objectives — and the first
# one at that, which is exactly what "首尾一刀" reads. Peel, do not discard.
# marker -> body delimiter after which the real request starts
WRAPPER_MARKERS = (
    ("# Files mentioned by the user:", "## My request for Codex:"),
)

# Delimiters that start the real request no matter which wrapper preceded them.
# Codex emits a request delimiter after ANY ambient block, not just the
# files-mentioned one. Older builds used `## My request for Codex:`; newer
# builds may shorten it to `## My request:`. Keying the peel off the leading marker discarded 108 of
# 138 wrapped messages in CASE-D (777 MB) — the whole second half of the
# objective trace, including the final instruction, which made a user-requested
# architecture review look like agent self-drift.
STANDALONE_DELIMS = ("## My request for Codex:", "## My request:")

# Claude Code records slash-command plumbing as role=user. A command carrying
# arguments may contain the actual request (for example `/session-forensics
# <request>`), while configuration commands are ambient UI state.
AMBIENT_LOCAL_COMMANDS = ("/model",)

# Messages that carry no instruction — the user acting as a while-loop counter.
PUMP_TOKENS = {
    "继续", "可以", "好", "好的", "嗯", "ok", "OK", "行", "下一步",
    "继续下一步", "可以继续", "继续执行", "可以开始", "开始", "go", "next",
    "继续吧", "可以的", "对", "是",
    # Verb-bearing advance phrases: longer than the old 8-char cap, still zero
    # instruction content. Missing these inflates `substantive` and zeroes
    # `pump_share` (实测 CASE-F L90「然后继续你的工作」).
    "然后继续", "然后继续你的工作", "继续你的工作", "继续工作",
    "继续你的任务", "继续任务", "接着做", "然后呢",
    # Claude Code injects this after an interrupted turn. is_pump() strips
    # whitespace and punctuation before matching.
    "Continuefromwhereyouleftoff",
}
# Exact-whitelist matching already bounds false positives; the length cap is
# just a belt. Derive it from the set instead of hardcoding 8.
_PUMP_MAXLEN = max(len(t) for t in PUMP_TOKENS)


def text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for item in content:
            if isinstance(item, dict):
                v = item.get("text") or item.get("input_text")
                if isinstance(v, str):
                    out.append(v)
            elif isinstance(item, str):
                out.append(item)
        return "\n".join(out)
    return ""


def is_ambient(text: str) -> bool:
    head = text[:400]
    return any(m in head for m in AMBIENT_MARKERS)


def unwrap_user(text: str) -> str:
    """Peel harness wrappers off a user message.

    Returns the real request body. Returns "" when a wrapper is present but
    carries no request (then it IS ambient). Non-wrapped text passes through.
    """
    head = text[:400]
    # This peer wrapper can contain a teammate payload that itself quotes a
    # request delimiter.  Reject the outer control record before the generic
    # delimiter peel, or the quoted inner request is promoted to Owner intent.
    if "Another Claude session sent a message:" in head:
        return ""
    # Kimi records skill activation as a user message containing both the
    # user's slash-command arguments and the full injected skill body. Keep
    # only args=...; otherwise the injected manual becomes the first goal.
    if head.startswith("User activated the skill") and "<skill-loaded" in head:
        match = re.search(r'<skill-loaded\b.*?\bargs="(.*?)">', text, re.S)
        return match.group(1).strip() if match else ""
    if "<command-message>" in head:
        if any(
            f"<command-name>{name}</command-name>" in head
            for name in AMBIENT_LOCAL_COMMANDS
        ):
            return ""
        match = re.search(r"<command-args>(.*?)</command-args>", text, re.S)
        return match.group(1).strip() if match else ""
    for delim in STANDALONE_DELIMS:
        idx = text.find(delim)
        if idx >= 0:
            return text[idx + len(delim):].strip()
    for marker, delim in WRAPPER_MARKERS:
        if marker not in head:
            continue
        idx = text.find(delim)
        if idx < 0:
            return ""
        return text[idx + len(delim):].strip()
    return text


def is_pump(text: str) -> bool:
    t = re.sub(r"[\s。，,.!！?？~、]+", "", text)
    return len(t) <= _PUMP_MAXLEN and t in PUMP_TOKENS


# The handoff contract for §1 is "逐字，未转述". A 400-char preview broke it
# silently: a long instruction gets cut mid-sentence and the receiver inherits a
# truncated goal without any marker that it was truncated. User messages are a
# rounding error against a GB-scale file (65 substantive messages on the 946 MB
# session CASE-A), so the streaming constraint does not justify 400.
# Kept bounded anyway — a pasted log can land in a user message.
USER_MSG_CAP = 8000


# ---------------------------------------------------------------- P2 (harvest)
# `top_commands` buckets raw command prefixes, so the same operation lands in a
# different bucket every time its paths or flags differ — and under Codex the
# JS wrapper `const r = await tools.exec_command({cmd:"...` eats the first 45
# chars, so the leaderboard ranks the wrapper instead of the work. Measured on
# CASE-F: the rsync that propagates the skill to three copies ran 5 times by
# hand, was skipped the 6th, and caused a real divergence — yet it does not
# appear in top_commands at all. Harvest needs the operation, not the string.
CMD_WRAPPER_RE = re.compile(r'["\']?cmd["\']?\s*:\s*"((?:[^"\\]|\\.)*)"', re.S)
SHAPE_NOISE = (
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), " "),
    (re.compile(r"\b[0-9a-f]{7,40}\b"), " "),
    (re.compile(r"\d{4}-\d{2}-\d{2}(?:[T_][\d:.-]+)?"), " "),
    (re.compile(r"\d+"), " "),
)
SHAPE_TOKEN_RE = re.compile(r"[A-Za-z_][\w.+:-]*")
ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_]\w*=")
# Leading words that are shell scaffolding, not the operation being performed.
SHELL_SCAFFOLD = frozenset({
    "do", "then", "else", "elif", "fi", "done", "if", "while", "for", "in",
    "cd", "set", "export", "source", "sudo", "time", "nohup", "exec", "env",
    "echo", "true", "false", "printf", "local", "eval",
})
ABS_PATH_RE = re.compile(r"/(?:[\w.@+-]+/){1,}[\w.@+-]+")
MAX_SEGMENTS = 12          # bound the work on pathological one-liners
MAX_RAW_PER_SHAPE = 16     # we only need the ">=2 implementations" signal


# Codex drives tools through a JS harness. `cmd:"..."` is only ONE of its forms;
# CASE-A (946 MB) uses `const patch = "..."; text await tools.apply_patch(patch)`
# and `tools.write_stdin(...)`, which the cmd-only unwrap left untouched — its
# shape leaderboard came out 60% JS scaffolding. Calibrating a normalizer on a
# single session is how that happens; this one is checked against two.
JS_TOOL_RE = re.compile(r"tools\.(\w+)\s*\(")


def _unwrap_cmd(cmd: str) -> tuple[str, str | None]:
    """Return (shell body, canonical tool name if this is a non-shell tool call).

    For `tools.exec_command({cmd:"..."})` the operation is the shell command, so
    the body is returned. For any other `tools.X(...)` the operation IS X — there
    is no shell command to shape, and pretending otherwise ranks the harness.
    """
    tool = None
    m = JS_TOOL_RE.search(cmd)
    if m:
        tool = m.group(1)
    inner = CMD_WRAPPER_RE.search(cmd)
    if inner:
        return inner.group(1).replace("\\n", "\n").replace('\\"', '"'), None
    if tool and tool not in ("exec_command", "exec", "shell"):
        return "", f"js:{tool}"
    return cmd.replace("\\n", "\n").replace('\\"', '"'), None


# An embedded program body (heredoc, jq filter, python -c) is an ARGUMENT of one
# operation, not a sequence of operations. Segmenting through it makes every
# `import`, `def`, `select`, `return` line look like an executable — measured on
# CASE-F that produced 47 bogus "exes" for a single target file. Blind the
# splitter to these bodies before segmenting.
HEREDOC_RE = re.compile(r"<<-?\s*'?\"?(\w+)'?\"?\n.*?\n\s*\1\b", re.S)
SQUOTE_RE = re.compile(r"'[^']*'", re.S)
DQUOTE_RE = re.compile(r'"[^"]*"', re.S)


def _opaque_bodies(body: str) -> str:
    # Drop them entirely rather than leaving a placeholder: any alphabetic
    # placeholder gets picked up by SHAPE_TOKEN_RE and becomes a fake token.
    body = HEREDOC_RE.sub(" ", body)
    body = SQUOTE_RE.sub(" ", body)
    body = DQUOTE_RE.sub(" ", body)
    return body


def cmd_shapes(cmd: str) -> list[str]:
    """Every operation performed by one command line, as comparable shapes.

    Counts operations, NOT exec calls: `mkdir -p X && rsync ...` contains two.
    That is the right unit for harvest — "how many times did I do this thing".
    """
    raw, tool = _unwrap_cmd(str(cmd))
    if tool:
        return [tool]
    body = _opaque_bodies(raw)
    out: list[str] = []
    for seg in re.split(r"[\n;|]+|&&", body)[:MAX_SEGMENTS]:
        seg = seg.strip()
        if not seg:
            continue
        for rx, sub in SHAPE_NOISE:
            seg = rx.sub(sub, seg)
        # `VAR=value` is a binding, not an operation. Strip leading bindings
        # (this also handles the `ENV=x real-command args` prefix form); a
        # segment that is nothing but bindings has no operation in it at all.
        while True:
            stripped = ENV_ASSIGN_RE.sub("", seg, count=1)
            if stripped == seg:
                break
            seg = stripped.lstrip().split(" ", 1)[1] if " " in stripped.lstrip() else ""
        if not seg.strip():
            continue
        toks = SHAPE_TOKEN_RE.findall(seg)
        while toks and toks[0] in SHELL_SCAFFOLD:
            toks = toks[1:]
        if not toks:
            continue
        exe = os.path.basename(toks[0])
        if exe in SHELL_SCAFFOLD or len(exe) < 2:
            continue
        keep = [exe] + [t for t in toks[1:4] if not t.startswith("<")]
        shape = " ".join(keep)[:60]
        if shape:
            out.append(shape)
    return out


def cmd_targets(cmd: str) -> list[str]:
    """Absolute file paths this command touches, however they were referenced.

    Picked up from the whole command (including `F=/path` assignments and
    heredoc bodies) because that is where the real target usually hides.
    """
    body, _ = _unwrap_cmd(str(cmd))
    return [p for p in ABS_PATH_RE.findall(body) if "." in os.path.basename(p)]


def trigrams(s: str) -> set[str]:
    s = re.sub(r"\s+", "", s)
    return {s[i:i + 3] for i in range(max(0, len(s) - 2))}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def norm(s: str) -> str:
    return re.sub(r"[\s。，,.!！?？~、]+", "", s)


def find_recurring(
    msgs: list[tuple[int, str]],
    assistant_at: list[int],
    call_at: list[int],
    threshold: float = 0.45,
) -> list[dict[str, Any]]:
    """Cluster substantive user messages that restate the same ask, then classify.

    A genuinely repeated ask means the previous statement of it never landed — the
    single highest-value signal that a methodology, not an execution step, failed.

    But a naive text-similarity cluster confuses that with a NETWORK RESEND: the
    message never reached the agent and the user simply sent it again. The two are
    distinguishable in the evidence flow, and only the first kind counts:

        byte-identical  AND  no assistant reply between  AND  no tool call between
            -> resend, discard
        reworded  AND  at least one assistant reply between
            -> restated: the agent answered and still did not resolve it
        otherwise
            -> unclear: surface it, do not count it
    """
    grams = [(ln, m, trigrams(m[:300])) for ln, m in msgs if len(m) > 12]
    used: set[int] = set()
    clusters = []
    for i, (ln_i, m_i, g_i) in enumerate(grams):
        if i in used:
            continue
        group = [(ln_i, m_i)]
        for j in range(i + 1, len(grams)):
            if j in used:
                continue
            ln_j, m_j, g_j = grams[j]
            if jaccard(g_i, g_j) >= threshold:
                group.append((ln_j, m_j))
                used.add(j)
        if len(group) < 2:
            continue
        used.add(i)

        verdicts = []
        for a, b in zip(group, group[1:]):
            lo, hi = a[0], b[0]
            n_assist = sum(1 for x in assistant_at if lo < x < hi)
            n_calls = sum(1 for x in call_at if lo < x < hi)
            identical = norm(a[1]) == norm(b[1])
            if identical and n_assist == 0 and n_calls == 0:
                verdicts.append("resend")
            elif n_assist >= 1:
                verdicts.append("restated")
            else:
                verdicts.append("unclear")
        if all(v == "resend" for v in verdicts):
            verdict = "resend"
        elif "restated" in verdicts:
            verdict = "restated"
        else:
            verdict = "unclear"
        clusters.append({"verdict": verdict, "members": group, "pair_verdicts": verdicts})
    return clusters


def median(values: list[int]) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    mid = len(v) // 2
    return float(v[mid]) if len(v) % 2 else (v[mid - 1] + v[mid]) / 2


def worktree_of(path: str) -> str:
    m = WORKTREE_RE.match(path)
    if not m:
        return "?"
    root = m.group(1)
    # .codex/worktrees/<name>: the meaningful root is one segment deeper;
    # collapsing all worktrees into one root blinds the B-class fork detector.
    if root.endswith("/.codex/worktrees"):
        name = path[m.end():].split("/", 1)[0]
        return f"{root}/{name}" if name else root
    return root


def scan(path: Path, max_lines: int | None = None) -> dict[str, Any]:
    """Compute metrics from the normalized event stream (any supported platform)."""
    fmt = detect_format(path)
    caps = CAPABILITIES.get(fmt, {})

    lines = malformed = session_meta = 0
    compaction_lines: list[int] = []

    patches: Counter[str] = Counter()
    basename_roots: defaultdict[str, set[str]] = defaultdict(set)
    commands: Counter[str] = Counter()
    tools: Counter[str] = Counter()
    # P2 harvest material
    shapes: Counter[str] = Counter()
    shape_raw: defaultdict[str, set[str]] = defaultdict(set)
    shape_fail: Counter[str] = Counter()
    pending_shapes: set[str] = set()
    execs = failures = timeouts = turn_errors = 0
    non_exec_failure_markers: Counter[str] = Counter()
    # None when the platform emits no harness timeout marker: the metric is
    # then unmeasurable and must report null, never 0.
    harness_timeout_re = HARNESS_TIMEOUT_RE.get(fmt)
    poll_timeouts: Counter[str] = Counter()
    instrument_patches = business_patches = 0

    assistant_msgs = reasoning_items = 0
    user_real: list[tuple[int, str]] = []
    user_ambient = 0
    goal_changes: list[tuple[int, Any, str]] = []
    approval_denials: list[tuple[int, str]] = []
    self_pump_rounds: list[tuple[int, Any]] = []
    compaction_open: list[int] = []
    compaction_failed: list[tuple[int, str]] = []
    shadowed_tokens = 0
    shadowed_events = 0

    sub_agents: Counter[str] = Counter()
    ctx_points: list[tuple[int, int]] = []
    window = None

    assistant_at: list[int] = []
    call_at: list[int] = []
    lifecycle: list[tuple[int, str]] = []

    out_volume: Counter[str] = Counter()
    out_calls: Counter[str] = Counter()
    pending_call: str | None = None
    call_names: dict[str, str] = {}

    for e in iter_events(path, max_lines, fmt=fmt):
        if e.kind == "eof":
            lines = e.line
            continue
        if e.kind == "malformed":
            malformed += 1
            continue
        if e.kind == "ui_noise":
            # dsh states two things in the log that other platforms only imply.
            # They ride ui_noise (they are not conversation), so lift them out
            # before the drop or they are lost.
            if e.name == "goal_change":
                goal_changes.append((e.line, e.extra.get("revision"),
                                     " ".join(e.text.split())[:USER_MSG_CAP]))
            elif e.name == "request/context":
                # Route capacity, announced on change. token_count carries the
                # same value; taking it here too means a session with no usage
                # still reports the window its log states.
                w = e.extra.get("window")
                if isinstance(w, int):
                    window = w
            elif e.name.startswith("approval:") and e.extra.get("denied"):
                approval_denials.append((e.line, e.name.split(":", 1)[1]))
            elif e.name == "compaction/start":
                compaction_open.append(e.line)
            elif e.name == "compaction/end":
                if compaction_open:
                    compaction_open.pop()
                if e.extra.get("error"):
                    compaction_failed.append((e.line, str(e.extra["error"])[:200]))
            continue
        if e.kind == "session_meta":
            session_meta += 1
            continue
        if e.kind == "compaction":
            compaction_lines.append(e.line)
            # Only dsh states the price of what a compaction erased. Where it is
            # stated, sum it; elsewhere these stay 0 and are reported as None.
            tok = e.extra.get("shadowed_tokens")
            if isinstance(tok, int):
                shadowed_tokens += tok
            ev = e.extra.get("shadowed_events")
            if isinstance(ev, int):
                shadowed_events += ev
            continue
        if e.kind == "turn_complete":
            lifecycle.append((e.line, "task_complete"))
            continue
        if e.kind == "turn_aborted":
            lifecycle.append((e.line, e.name or "turn_aborted"))
            continue
        if e.kind == "turn_error":
            # Retried request failures. Counted, never folded into aborts.
            turn_errors += 1
            continue
        if e.kind == "subagent":
            sub_agents[e.name] += 1
            continue
        if e.kind == "token_count":
            w = e.extra.get("window")
            if isinstance(w, int):
                window = w
            ctx_points.append((e.line, e.size))
            continue
        if e.kind == "assistant_msg":
            assistant_msgs += 1
            assistant_at.append(e.line)
            continue
        if e.kind == "thinking":
            reasoning_items += 1
            continue
        if e.kind == "user_msg":
            # When the platform records WHY a user-role message exists (dsh's
            # `source.kind`), that fact outranks any text heuristic below: an
            # injected AGENTS.md is ambient because the log says so, not because
            # it happens to match a marker string.
            if e.extra.get("ambient"):
                user_ambient += 1
                # A goal continuation round is the agent pumping ITSELF. It is
                # not an Owner nudge, so it must stay out of pump_share (whose
                # denominator is human messages) — but "re-entered its own goal
                # loop N times" is a spinning signal in its own right.
                if e.extra.get("goal_round"):
                    self_pump_rounds.append((e.line, e.extra["goal_round"]))
                continue
            # Claude Desktop can nest the plain-text peer wrapper inside
            # command plumbing.  Inspect the normalized record before peeling
            # wrappers so an inner request delimiter cannot promote it.
            if "Another Claude session sent a message:" in e.text[:USER_MSG_CAP]:
                user_ambient += 1
                continue
            body = unwrap_user(e.text)
            if not body or is_ambient(body):
                user_ambient += 1
            else:
                user_real.append((e.line, " ".join(body.split())[:USER_MSG_CAP]))
            continue
        if e.kind == "tool_call":
            tools[e.name] += 1
            call_at.append(e.line)
            pending_call = e.name
            call_id = e.extra.get("call_id")
            if isinstance(call_id, str) and call_id:
                call_names[call_id] = e.name
            targets = e.extra.get("patch_targets")
            if not targets:
                targets = [m.strip() for m in PATCH_TARGET_RE.findall(e.text)]
            for target in targets:
                patches[target] += 1
                basename_roots[os.path.basename(target)].add(worktree_of(target))
                if INSTRUMENT_RE.search(target):
                    instrument_patches += 1
                else:
                    business_patches += 1
            is_exec = e.extra.get("is_exec")
            if is_exec is None:
                is_exec = e.name in ("exec", "exec_command", "shell")
            if is_exec:
                execs += 1
                cmd = e.extra.get("command") or e.text
                commands[" ".join(str(cmd).split())[:90]] += 1
                # P2: operation-level view, parallel to the raw `commands`
                # counter above (which stays untouched so max_cmd_share keeps
                # its calibrated baseline).
                seen_here = set()
                for shape in cmd_shapes(cmd):
                    shapes[shape] += 1
                    seen_here.add(shape)
                    raw = " ".join(str(cmd).split())[:120]
                    bucket = shape_raw[shape]
                    if len(bucket) < MAX_RAW_PER_SHAPE:
                        bucket.add(raw)
                pending_shapes = seen_here
            continue
        if e.kind == "tool_output":
            call_id = e.extra.get("call_id")
            correlated = call_names.pop(call_id, None) if isinstance(call_id, str) else None
            owner = e.name or correlated or pending_call or "?"
            out_volume[owner] += e.size
            out_calls[owner] += 1
            # Do NOT clear pending_call here. Codex `function_call_output`
            # records carry no call_id at all (6147/6147 on CASE-A), so
            # attribution is purely positional; clearing on the first output
            # dumped every SUBSEQUENT output of the same call into "?" —
            # measured 172 calls / 8.76M chars unattributed on that session.
            # A late output belongs to the most recent call, not to nobody.
            # pending_call is reset by the next tool_call, which is correct.
            timed_out = bool(
                harness_timeout_re
                and owner in EXEC_OWNERS
                and harness_timeout_re.search(e.text)
            )
            fail_match = bool(FAIL_RE.search(e.text))
            failed_exec = owner in EXEC_OWNERS and fail_match
            bad = failed_exec or timed_out
            if failed_exec:
                failures += 1
            elif fail_match:
                # `failure_rate` is failures / execs, so its numerator must be
                # drawn from the same execution-tool domain as its denominator.
                # Search/read/browser outputs can legitimately quote "error:"
                # or "FAILED" and must not make the ratio exceed 1. Preserve
                # those markers as a diagnostic counter instead of discarding
                # them or pretending they were command failures.
                non_exec_failure_markers[owner] += 1
            if timed_out:
                timeouts += 1
            elif owner not in EXEC_OWNERS and POLL_TIMEOUT_RE.search(e.text):
                # A wait/poll tool returning "timed out" over and over IS the
                # finding (G7 empty-poll loop), it just is not `timeout_rate`.
                poll_timeouts[owner] += 1
            if bad:
                # Which operation is producing the failures — top-level
                # failures/timeouts are session totals and cannot answer this.
                for shape in pending_shapes:
                    shape_fail[shape] += 1
            pending_shapes = set()
            continue

    total_patches = sum(patches.values())
    touched = len(patches)
    forked = {b: sorted(r) for b, r in basename_roots.items() if len(r) >= 2}
    max_patch = patches.most_common(1)[0] if patches else ("-", 0)
    max_cmd = commands.most_common(1)[0] if commands else ("-", 0)

    substantive = [(ln, m) for ln, m in user_real if not is_pump(m)]
    pump_all = [(ln, m) for ln, m in user_real if is_pump(m)]
    pump_genuine: list[tuple[int, str]] = []
    pump_resume: list[tuple[int, str]] = []
    prev_user = 0
    pump_set = {ln for ln, _ in pump_all}
    for ln, m in user_real:
        if ln not in pump_set:
            prev_user = ln
            continue
        kinds = [k for pos, k in lifecycle if prev_user < pos < ln]
        if "task_complete" in kinds and not {"turn_aborted", "thread_rolled_back"} & set(kinds):
            pump_genuine.append((ln, m))
        else:
            pump_resume.append((ln, m))
        prev_user = ln

    pump_gaps = [pump_genuine[i + 1][0] - pump_genuine[i][0] for i in range(len(pump_genuine) - 1)]
    recurring = find_recurring(substantive, assistant_at, call_at)
    recurring_real = [c for c in recurring if c["verdict"] == "restated"]

    bounds = [0] + compaction_lines + [lines]
    segments = []
    for i in range(len(bounds) - 1):
        lo, hi = bounds[i], bounds[i + 1]
        seg = [v for (ln, v) in ctx_points if lo < ln <= hi]
        if seg:
            segments.append({"after_line": lo, "floor": seg[0], "peak": max(seg), "turns": len(seg)})

    def ratio(a: float, b: float) -> float:
        return round(a / b, 4) if b else 0.0

    # A metric whose inputs the platform cannot express must be None, never 0.
    # A silent zero would make a Kimi session look like it never compacted.
    compactions = len(compaction_lines) if caps.get("compaction", True) else None

    return {
        "source": str(path),
        "format": fmt,
        "capabilities": caps,
        "size_bytes": path.stat().st_size,
        # A zstd session log's on-disk size is not its scale: every per-byte
        # intuition (cost, flood share, "is this a big session") must use the
        # decompressed length. Reported separately so neither can be mistaken
        # for the other.
        "size_bytes_decompressed": (SE.uncompressed_size(path)
                                    if SE.is_compressed(path) else None),
        "scale": {
            "lines": lines, "malformed": malformed, "session_meta_records": session_meta,
            "compactions": compactions, "compaction_lines": compaction_lines,
            "execs": execs, "patches": total_patches, "files_touched": touched,
            "assistant_msgs": assistant_msgs, "reasoning_items": reasoning_items,
            "user_substantive": len(substantive), "user_pump": len(pump_genuine),
            "user_resume": len(pump_resume), "user_ambient": user_ambient,
            "turn_complete": sum(1 for _, k in lifecycle if k == "task_complete"),
            "turn_aborted": sum(1 for _, k in lifecycle if k != "task_complete"),
            "turn_errors": turn_errors,
            # An opened compaction with no close: the summarizer died holding the
            # lock. It is a failure, not a compaction, so it is never counted as
            # one. `compactions_failed` are closes that reported an error.
            "compactions_unclosed": len(compaction_open) if caps.get("compaction") else None,
            "compactions_failed": compaction_failed if caps.get("compaction") else None,
            "approval_denials": len(approval_denials),
            # Agent-initiated goal continuation rounds (dsh `goal` source kind).
            # Deliberately NOT folded into user_pump: nobody asked.
            "self_pump_rounds": len(self_pump_rounds),
            "max_goal_round": max((r for _, r in self_pump_rounds
                                   if isinstance(r, int)), default=None),
        },
        "rates": {
            "compactions_per_1k_lines": ratio(len(compaction_lines) * 1000, lines)
                                        if compactions is not None else None,
            "max_patch_share": ratio(max_patch[1], total_patches),
            "max_cmd_share": ratio(max_cmd[1], execs),
            "forked_share": ratio(len(forked), touched),
            "instrument_patch_share": ratio(instrument_patches, total_patches),
            "failure_rate": ratio(failures, execs),
            "timeout_rate": (ratio(timeouts, execs)
                              if harness_timeout_re is not None else None),
            "narrative_to_evidence": ratio(assistant_msgs + reasoning_items, execs + total_patches),
            "pump_share": ratio(len(pump_genuine), len(user_real)),
            "resume_share": ratio(len(pump_resume), len(user_real)),
            "pump_gap_median_lines": median(pump_gaps),
            "recurring_ask_clusters": len(recurring_real),
            "recurring_discarded": len(recurring) - len(recurring_real),
            "flood_share": ratio(out_volume.most_common(1)[0][1] if out_volume else 0,
                                 sum(out_volume.values())),
            "flood_tool": out_volume.most_common(1)[0][0] if out_volume else None,
            "output_megachars": round(sum(out_volume.values()) / 1e6, 2),
        },
        "evidence_flow": {
            "top_patched": patches.most_common(10),
            "top_commands": commands.most_common(8),
            "top_tools": tools.most_common(12),
            # P2 harvest material, at the operation level rather than the raw
            # string level. `calls` counts operations (a compound command line
            # contributes each of its operations), so it does not sum to execs.
            #
            # `distinct_invocations` measures PARAMETER diversity, not
            # implementation forking. Measured on CASE-F: rsync=2 (only the
            # destination changed) but sed=16 (a different file/range每次).
            # A high value therefore means "widely parameterised", NOT "being
            # reinvented". Implementation forking — jq vs sed vs python3 doing
            # the same job, which is the actual harness signal — is a semantic
            # equivalence judgement across DIFFERENT executables and is not
            # decidable at this layer. The reading agent must make that call
            # from the trace; do not add a field that pretends otherwise.
            "top_shapes": [
                {"shape": s, "calls": n,
                 "distinct_invocations": min(len(shape_raw[s]), MAX_RAW_PER_SHAPE),
                 "failing_calls": shape_fail.get(s, 0)}
                for s, n in shapes.most_common(20)
            ],
            "failures": failures, "timeouts": timeouts,
            "non_exec_failure_markers": non_exec_failure_markers.most_common(),
            # G7 empty-poll loop: a wait/poll tool that keeps returning "timed
            # out" burns one model round-trip per call while producing nothing.
            # Deliberately NOT folded into timeout_rate — different denominator,
            # different pathology. CASE-G: wait_agent 481.
            "poll_timeouts": poll_timeouts.most_common(6),
            "instrument_patches": instrument_patches, "business_patches": business_patches,
            "output_volume_by_tool": [
                {"tool": k, "calls": out_calls[k], "megachars": round(v / 1e6, 2),
                 "share": ratio(v, sum(out_volume.values())),
                 "avg_chars": v // max(out_calls[k], 1)}
                for k, v in out_volume.most_common(8)
            ],
        },
        "b_class": {
            "forked_files": forked, "forked_count": len(forked),
            "worktrees": sorted({worktree_of(p) for p in patches}),
        },
        "objective_trace": {
            # P3 normally has to reconstruct goal drift from user messages. dsh
            # records the goal object itself, with a revision number: where this
            # list is non-empty it is the objective's own history, not an
            # inference from it. Empty (not None) elsewhere — no platform can
            # have goal changes it never recorded, but every platform can have
            # zero of them.
            "goal_changes": goal_changes,
            "first_substantive": substantive[0] if substantive else None,
            "substantive": substantive,
            "pump_genuine": pump_genuine,
            "pump_resume": pump_resume,
            "recurring_asks": recurring,
        },
        "sub_agents": sub_agents.most_common(10),
        "context": {
            "window": window, "segments": segments,
            "floor_first": segments[0]["floor"] if segments else None,
            "floor_last": segments[-1]["floor"] if segments else None,
            "peak_max": max((s["peak"] for s in segments), default=None),
            # Measured compaction loss, not estimated. None where the platform
            # does not price what it shadowed — the estimate belongs to the
            # reader, and a 0 here would read as "compaction cost nothing".
            "shadowed_tokens": shadowed_tokens if caps.get("compaction_loss") else None,
            "shadowed_events": shadowed_events if caps.get("compaction_loss") else None,
        },
    }


def report(r: dict[str, Any], users: int) -> None:
    s, n, c = r["scale"], r["rates"], r["context"]
    raw = r.get("size_bytes_decompressed")
    size = (f"{raw/1e6:.0f} MB unpacked / {r['size_bytes']/1e6:.1f} MB on disk"
            if raw else f"{r['size_bytes']/1e6:.0f} MB")
    print(f"\n{'='*76}\n{os.path.basename(r['source'])}  ({size})\n{'='*76}")
    print(f"lines={s['lines']}  compactions={s['compactions']}  execs={s['execs']}  "
          f"patches={s['patches']} over {s['files_touched']} files  session_meta={s['session_meta_records']}")
    print(f"narrative: assistant={s['assistant_msgs']} reasoning={s['reasoning_items']}   "
          f"user: substantive={s['user_substantive']} pump={s['user_pump']} "
          f"resume={s['user_resume']} ambient={s['user_ambient']}")
    print(f"turns: complete={s['turn_complete']} aborted/rolled-back={s['turn_aborted']}"
          + (f" retried-api-errors={s['turn_errors']}" if s.get("turn_errors") else ""))
    print("\n-- rates (compare against references/signatures.md baselines) --")
    for k, v in n.items():
        print(f"   {k:<28} {v}")
    print("\n-- B class: instrument forking --")
    if r["b_class"]["forked_files"]:
        for b, roots in list(r["b_class"]["forked_files"].items())[:8]:
            print(f"   {b}")
            for rt in roots:
                print(f"        {rt}")
    else:
        print("   none")
    print(f"   worktrees touched: {len(r['b_class']['worktrees'])}")
    print("\n-- evidence flow --")
    for f, cnt in r["evidence_flow"]["top_patched"][:6]:
        print(f"   {cnt:4d}x patch  {f}")
    for cmd, cnt in r["evidence_flow"]["top_commands"][:5]:
        print(f"   {cnt:4d}x cmd    {cmd[:82]}")
    print(f"   failures={r['evidence_flow']['failures']} timeouts={r['evidence_flow']['timeouts']} "
          f"instrument/business={r['evidence_flow']['instrument_patches']}/{r['evidence_flow']['business_patches']}")
    if r["evidence_flow"]["poll_timeouts"]:
        pt = r["evidence_flow"]["poll_timeouts"]
        print("   empty-poll returns (G7; NOT counted in timeout_rate): "
              + ", ".join(f"{k}={v}" for k, v in pt))
    print("\n-- context flood: who is burning the context budget --")
    for row in r["evidence_flow"]["output_volume_by_tool"][:6]:
        print(f"   {row['tool']:<18} {row['calls']:>4} calls  {row['megachars']:>7.2f}M chars  "
              f"{row['share']*100:>5.1f}%  avg {row['avg_chars']:,}")
    if r["sub_agents"]:
        print("\n-- sub agents --")
        for a, cnt in r["sub_agents"][:6]:
            print(f"   {cnt:3d}  {a}")
    print(f"\n-- context --\n   window={c['window']}  floor {c['floor_first']} -> {c['floor_last']}  peak={c['peak_max']}")
    rec = r["objective_trace"]["recurring_asks"]
    if rec:
        print("\n-- recurring asks (restated = the first statement never landed; resend = network) --")
        for cl in rec[:5]:
            print(f"   [{cl['verdict']}] x{len(cl['members'])}")
            for ln, m in cl["members"]:
                print(f"      L{ln}: {m[:110]}")
    print("\n-- objective trace (substantive only; ambient and pump filtered) --")
    for ln, m in r["objective_trace"]["substantive"][:users]:
        print(f"   L{ln}: {m[:190]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("sessions", nargs="+", type=Path)
    ap.add_argument("--max-lines", type=int, default=None)
    ap.add_argument("--json-out", type=Path)
    ap.add_argument("--users", type=int, default=8)
    args = ap.parse_args()

    results = []
    for p in args.sessions:
        p = p.expanduser()
        if not p.is_file():
            print(f"!! not found: {p}")
            continue
        r = scan(p, args.max_lines)
        results.append(r)
        report(r, args.users)

    if args.json_out:
        args.json_out.expanduser().write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
