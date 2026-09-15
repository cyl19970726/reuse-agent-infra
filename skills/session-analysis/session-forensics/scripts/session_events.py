#!/usr/bin/env python3
"""Normalized event layer over Codex / Claude Code / Kimi Code session logs.

Everything above this file — the two flows, the signatures, the gates — is
platform independent. The only per-platform work is turning one raw record into
zero or more normalized events, which is what this module does.

    Event(line, flow, kind, name, size, text, extra)

    flow  narrative | evidence | lifecycle | meta
    kind  user_msg | assistant_msg | thinking | tool_call | tool_output
          | compaction | turn_complete | turn_aborted | turn_error | token_count
          | session_meta | subagent
          (meta noise kinds: ui_noise | queued | duplicate_record | malformed | eof)

Field notes that matter downstream:
    tool_call    name = tool name, text = raw arguments (patch targets and shell
                 commands are extracted from it by the caller)
    tool_output  name = the tool that produced it, text = output, size = len(output)
    token_count  size = context length for that request
    extra        platform specifics (window size, agent path, subtype, ...)

Verified against real corpora on 2026-07. Every field below was observed, not
assumed — see references/platforms.md for the probe results.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


@dataclass
class Event:
    line: int
    flow: str
    kind: str
    name: str = ""
    size: int = 0
    text: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# transparent decompression
# --------------------------------------------------------------------------- #

# DeepSeek Harness stores `session.jsonl.zstd`: JSONL compressed as a sequence of
# APPENDED zstd frames (one per flush), not one frame. Everything above this file
# still sees plain text lines.
#
# ⚠️ The dangerous failure here is silent, not loud: a decoder without
# `read_across_frames` returns ONLY THE FIRST FRAME and reports success, so a
# 7000-event session parses as ~20 events and reads as a pristine short run. That
# is a manufactured B-class crack — the analysis would cite a file that no longer
# says what it says. So: never fall back to a single-frame read. Prefer a decoder
# proven to span frames, else the `zstd` CLI, else refuse loudly.
COMPRESSED_SUFFIXES = (".zstd", ".zst")


def is_compressed(path: Path) -> bool:
    return path.name.endswith(COMPRESSED_SUFFIXES)


class _ProcTextReader(io.TextIOWrapper):
    """Text lines from a `zstd -dc` pipe; reaps the child on close."""

    def __init__(self, proc: subprocess.Popen):
        self._proc = proc
        super().__init__(proc.stdout, encoding="utf-8", errors="replace")

    def close(self) -> None:
        try:
            super().close()
        finally:
            if self._proc.poll() is None:
                self._proc.terminate()
            self._proc.wait()


def _zstd_text(path: Path):
    try:
        import zstandard  # type: ignore
    except ImportError:
        zstandard = None
    if zstandard is not None:
        try:
            reader = zstandard.ZstdDecompressor().stream_reader(
                path.open("rb"), read_across_frames=True)
        except TypeError:
            reader = None  # too old to span frames -> must not be used
        if reader is not None:
            return io.TextIOWrapper(reader, encoding="utf-8", errors="replace")
    exe = shutil.which("zstd") or shutil.which("zstdcat")
    if exe:
        proc = subprocess.Popen([exe, "-dcq", str(path)],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        return _ProcTextReader(proc)
    raise RuntimeError(
        f"cannot read {path}: needs a multi-frame zstd decoder. Install one of\n"
        "    pip install 'zstandard>=0.18'      (read_across_frames)\n"
        "    brew install zstd                  (CLI fallback)\n"
        "Refusing rather than reading frame 1 only, which would silently truncate "
        "the log to its first flush.")


def open_session_text(path: Path):
    """Line-iterable text handle for a session log, compressed or not."""
    if is_compressed(path):
        return _zstd_text(path)
    return path.open("r", encoding="utf-8", errors="replace")


def uncompressed_size(path: Path) -> int:
    """Decompressed byte length. Streams; never materializes the file."""
    if not is_compressed(path):
        return path.stat().st_size
    total = 0
    with open_session_text(path) as handle:
        while True:
            block = handle.read(1 << 20)
            if not block:
                break
            total += len(block.encode("utf-8", "replace"))
    return total


# --------------------------------------------------------------------------- #
# format detection
# --------------------------------------------------------------------------- #

def detect_format(path: Path, probe_lines: int = 60) -> str:
    """Sniff the log format. Cheap: reads at most `probe_lines` lines."""
    votes = {"codex": 0, "claude_code": 0, "kimi": 0, "codex_legacy": 0, "dsh": 0}
    with open_session_text(path) as handle:
        for i, line in enumerate(handle):
            if i >= probe_lines:
                break
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            t = rec.get("type") or ""
            if t in _DSH_CHUNK_ROWS or (t == "session" and "delegationDepth" in rec) or (
                    "/" in t and t.split("/", 1)[0] in _DSH_NAMESPACES and "seq" in rec):
                # dsh types are namespaced with "/" and every row carries `seq`;
                # chunk rows use deliberately slash-less tags (see chunk-rows.d.ts).
                votes["dsh"] += 1
            elif t in ("response_item", "event_msg", "session_meta", "turn_context", "compacted"):
                votes["codex"] += 1
            elif t in ("user", "assistant", "system", "attachment", "ai-title", "last-prompt"):
                votes["claude_code"] += 1
            elif t.startswith(("context.", "usage.", "turn.", "tools.", "permission.",
                               "config.", "llm.", "interaction.", "profile.")):
                votes["kimi"] += 1
            elif t == "metadata" and "protocol_version" in rec:
                # Every Kimi wire opens with this header (672/672 locally, always
                # exactly {type, protocol_version, created_at}). Without it, a
                # session that has only just started — header plus profile.bind,
                # nothing else yet — casts ZERO votes and is refused as
                # `unknown`. That is precisely the freshly-spawned worker
                # progress_probe exists to look at, so the probe was blind at the
                # one moment it is most wanted. `protocol_version` is what makes
                # this safe: bare `metadata` is too generic a tag to claim.
                votes["kimi"] += 1
            elif "record_type" in rec or {"id", "timestamp", "instructions"} <= rec.keys():
                # 2025-08~09-era Codex rollout: a header dict + record_type rows,
                # pre-response_item schema. ~247 files (~4%) of the local corpus.
                # Named so the refusal is specific, not a shrug (see
                # references/codex-jsonl.md §格式代际边界).
                votes["codex_legacy"] += 1
    best = max(votes, key=lambda k: votes[k])
    return best if votes[best] else "unknown"


# --------------------------------------------------------------------------- #
# Codex
# --------------------------------------------------------------------------- #

def _text_of(content: Any) -> str:
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


def _codex_events(rec: dict[str, Any], line: int) -> Iterator[Event]:
    rtype = rec.get("type")
    if rtype == "compacted":
        yield Event(line, "lifecycle", "compaction")
        return
    if rtype == "session_meta":
        payload = rec.get("payload") or {}
        yield Event(
            line,
            "meta",
            "session_meta",
            extra={
                "thread_id": payload.get("id"),
                "session_id": payload.get("session_id"),
                "forked_from_id": payload.get("forked_from_id"),
                "model_provider": payload.get("model_provider"),
                "timestamp": rec.get("timestamp"),
            },
        )
        return
    if rtype == "turn_context":
        payload = rec.get("payload") or {}
        yield Event(
            line,
            "meta",
            "session_meta",
            extra={
                "thread_id": payload.get("thread_id"),
                "session_id": payload.get("session_id"),
                "forked_from_id": None,
                "model_provider": payload.get("model"),
                "timestamp": rec.get("timestamp"),
                "effort": payload.get("effort") or payload.get("reasoning_effort"),
                "cwd": payload.get("cwd"),
                "approval_policy": payload.get("approval_policy"),
            },
        )
        return

    payload = rec.get("payload")
    if not isinstance(payload, dict):
        return
    ptype = payload.get("type")

    if rtype == "response_item":
        if ptype == "message":
            role = payload.get("role")
            body = _text_of(payload.get("content"))
            if role == "assistant":
                yield Event(line, "narrative", "assistant_msg", text=body)
            elif role == "user":
                yield Event(line, "evidence", "user_msg", text=body)
        elif ptype == "reasoning":
            yield Event(line, "narrative", "thinking")
        elif ptype in ("function_call", "custom_tool_call"):
            raw = payload.get("arguments") or payload.get("input") or ""
            if not isinstance(raw, str):
                raw = json.dumps(raw, ensure_ascii=False)
            yield Event(line, "evidence", "tool_call", name=payload.get("name") or "?", text=raw)
        elif ptype in ("function_call_output", "custom_tool_call_output"):
            out = str(payload.get("output") or "")
            yield Event(line, "evidence", "tool_output", size=len(out), text=out)

    elif rtype == "event_msg":
        if ptype == "sub_agent_activity":
            yield Event(line, "meta", "subagent", name=str(payload.get("agent_path") or "?"),
                        extra={"thread": payload.get("agent_thread_id")})
        elif ptype == "token_count":
            info = payload.get("info") or {}
            last_usage = info.get("last_token_usage") or {}
            val = last_usage.get("input_tokens")
            if isinstance(val, int) and val > 0:
                yield Event(line, "meta", "token_count", size=val,
                            extra={
                                "window": info.get("model_context_window"),
                                "input_tokens": val,
                                "cached_input_tokens": last_usage.get("cached_input_tokens"),
                                "cache_write_input_tokens": last_usage.get(
                                    "cache_write_input_tokens"
                                ),
                                "timestamp": rec.get("timestamp"),
                            })
        elif ptype == "task_complete":
            yield Event(line, "lifecycle", "turn_complete")
        elif ptype in ("turn_aborted", "thread_rolled_back"):
            yield Event(line, "lifecycle", "turn_aborted", name=ptype)


# --------------------------------------------------------------------------- #
# Claude Code
# --------------------------------------------------------------------------- #

# Observed: compaction is a system record with subtype=compact_boundary, and the
# summary record carries isCompactSummary=True + compactMetadata.trigger.
# There is no task_started/task_complete pair like Codex has; stop_hook_summary is
# the closest turn-end marker and api_error the closest abort marker.
_CC_PATCH_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}
_CC_EXEC_TOOLS = {"Bash", "BashOutput"}


def _claude_events(rec: dict[str, Any], line: int) -> Iterator[Event]:
    rtype = rec.get("type")

    if rec.get("isCompactSummary") is True:
        yield Event(line, "lifecycle", "compaction",
                    extra={"trigger": (rec.get("compactMetadata") or {}).get("trigger")})
        return

    if rtype == "system":
        sub = rec.get("subtype")
        if sub == "compact_boundary":
            yield Event(line, "lifecycle", "compaction", extra={"trigger": "boundary"})
        elif sub == "stop_hook_summary":
            yield Event(line, "lifecycle", "turn_complete")
        elif sub in ("api_error", "model_refusal_fallback"):
            # NOT a turn abort. These are transient request failures the harness
            # retries; the turn usually continues and completes. Folding them
            # into `turn_aborted` made this skill's own audit session report
            # "complete=10 aborted/rolled-back=11" — a headline that reads as a
            # session which abandoned more turns than it finished, when in fact
            # all 11 were retried API errors and every turn completed.
            yield Event(line, "lifecycle", "turn_error", name=str(sub))
        return

    if rtype in ("ai-title", "last-prompt", "queue-operation", "mode", "custom-title"):
        # UI bookkeeping, NOT an embedded session. Codex's session_meta means
        # "another thread was inlined here"; emitting these under the same kind
        # would make a Claude Code session look like it contained 573 subthreads.
        # Same name, different meaning across platforms = definition drift.
        title = next((rec.get(k) for k in ("title", "customTitle", "text", "summary", "prompt")
                      if isinstance(rec.get(k), str)), "")
        yield Event(line, "meta", "ui_noise", name=str(rtype), text=title,
                    extra={"session_id": rec.get("sessionId")})
        return

    if rtype == "attachment":
        # A queued_command is a message the user typed while the agent was busy.
        # It lands with message.content null, so every consumer that only reads
        # message.* dropped all of them — measured on CASE-AB: 20 human
        # questions invisible to the objective trace, including the one the
        # archaeology task was named after (L4303). The machine test is
        # attachment.origin.kind (human/peer), which matched manual reading
        # exactly (78 records: human 20 / peer 7 / no-origin 51 — the no-origin
        # ones are task-notification plumbing, not people).
        att = rec.get("attachment")
        if isinstance(att, dict) and att.get("type") == "queued_command":
            origin = att.get("origin") if isinstance(att.get("origin"), dict) else {}
            prompt = att.get("prompt") if isinstance(att.get("prompt"), str) else ""
            if origin.get("kind") == "human" and prompt.strip():
                yield Event(line, "evidence", "user_msg", text=prompt,
                            extra={"queued": True})
            else:
                # Visible to drill (kind + text), excluded from the objective
                # trace: peer messages and notification plumbing are not the user.
                yield Event(line, "meta", "queued",
                            name=str(att.get("commandMode") or origin.get("kind") or "?"),
                            text=prompt)
        return

    if rec.get("isSidechain"):
        yield Event(line, "meta", "subagent", name=str(rec.get("sessionId") or "?"))

    msg = rec.get("message")
    if not isinstance(msg, dict):
        return

    usage = msg.get("usage")
    if isinstance(usage, dict):
        uncached = int(usage.get("input_tokens") or 0)
        cached = int(usage.get("cache_read_input_tokens") or 0)
        created = int(usage.get("cache_creation_input_tokens") or 0)
        ctx = uncached + cached + created
        if ctx > 0:
            yield Event(line, "meta", "token_count", size=ctx,
                        extra={
                            "window": None,
                            "input_tokens": ctx,
                            "uncached_input_tokens": uncached,
                            "cached_input_tokens": cached,
                            "cache_write_input_tokens": created,
                            "model": msg.get("model"),
                            "session_id": rec.get("sessionId"),
                            "request_id": rec.get("requestId") or rec.get("promptId"),
                            "timestamp": rec.get("timestamp"),
                        })

    role = msg.get("role")
    content = msg.get("content")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return

    for item in content:
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        if itype == "text":
            body = str(item.get("text") or "")
            if role == "assistant":
                yield Event(line, "narrative", "assistant_msg", text=body)
            else:
                yield Event(line, "evidence", "user_msg", text=body)
        elif itype == "thinking":
            yield Event(line, "narrative", "thinking")
        elif itype == "tool_use":
            name = str(item.get("name") or "?")
            args = item.get("input")
            raw = json.dumps(args, ensure_ascii=False) if not isinstance(args, str) else args
            targets = []
            if name in _CC_PATCH_TOOLS and isinstance(args, dict):
                fp = args.get("file_path") or args.get("notebook_path")
                if isinstance(fp, str):
                    targets.append(fp)
            cmd = args.get("command") if (name in _CC_EXEC_TOOLS and isinstance(args, dict)) else None
            yield Event(line, "evidence", "tool_call", name=name, text=raw,
                        extra={"patch_targets": targets, "command": cmd,
                               "is_exec": name in _CC_EXEC_TOOLS})
        elif itype == "tool_result":
            body = item.get("content")
            out = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
            yield Event(line, "evidence", "tool_output", size=len(out), text=out)
        elif itype == "image":
            src = item.get("source") or {}
            data = src.get("data") if isinstance(src, dict) else None
            n = len(data) if isinstance(data, str) else 0
            yield Event(line, "evidence", "tool_output", name="image", size=n, text="")


# --------------------------------------------------------------------------- #
# Kimi Code
# --------------------------------------------------------------------------- #

# Kimi writes one wire.jsonl per agent under
#   sessions/<workspace>/session_<uuid>/agents/<agent>/wire.jsonl
# so subagents live in SEPARATE FILES, unlike Codex (inlined). Claude Code moved
# to the same separate-files layout around 2026-08 (<session>/subagents/*.jsonl;
# the isSidechain-inline form survives only in older files — see platforms.md).
# Callers wanting the full picture must walk the agents/ directory; a single
# wire.jsonl is one agent's view.
#
# ⚠️ No compaction marker was observed in the probed corpus. Until one is found,
# every compression-derived conclusion must be reported as UNAVAILABLE for Kimi
# rather than silently computed as zero.
_KIMI_PATCH_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch"}
_KIMI_EXEC_TOOLS = {"Bash", "Shell", "Exec", "run_command"}


def _kimi_events(rec: dict[str, Any], line: int) -> Iterator[Event]:
    rtype = rec.get("type") or ""

    if rtype == "metadata":
        yield Event(line, "meta", "session_meta")
        return

    if rtype == "turn.prompt":
        body = rec.get("prompt") or rec.get("text") or ""
        if not isinstance(body, str):
            body = json.dumps(body, ensure_ascii=False)
        yield Event(line, "evidence", "user_msg", text=body)
        return

    if rtype == "context.append_message":
        msg = rec.get("message") if isinstance(rec.get("message"), dict) else rec
        role = msg.get("role")
        body = _text_of(msg.get("content"))
        if role == "user":
            yield Event(line, "evidence", "user_msg", text=body)
        elif role == "assistant":
            yield Event(line, "narrative", "assistant_msg", text=body)
        return

    if rtype != "context.append_loop_event":
        return

    ev = rec.get("event")
    if not isinstance(ev, dict):
        return
    etype = ev.get("type")

    if etype == "tool.call":
        name = str(ev.get("name") or "?")
        call_id = ev.get("toolCallId")
        args = ev.get("args")
        raw = json.dumps(args, ensure_ascii=False) if not isinstance(args, str) else args
        targets = []
        if name in _KIMI_PATCH_TOOLS and isinstance(args, dict):
            fp = args.get("path") or args.get("file_path")
            if isinstance(fp, str):
                targets.append(fp)
        cmd = args.get("command") if (name in _KIMI_EXEC_TOOLS and isinstance(args, dict)) else None
        yield Event(line, "evidence", "tool_call", name=name, text=raw,
                    extra={"patch_targets": targets, "command": cmd,
                           "is_exec": name in _KIMI_EXEC_TOOLS,
                           "call_id": call_id})
    elif etype == "tool.result":
        call_id = ev.get("toolCallId")
        res = ev.get("result")
        out = res.get("output") if isinstance(res, dict) else res
        out = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
        yield Event(line, "evidence", "tool_output", size=len(out), text=out,
                    extra={"call_id": call_id})
    elif etype == "content.part":
        part = ev.get("part") or {}
        ptype = part.get("type")
        if ptype == "think":
            yield Event(line, "narrative", "thinking")
        elif ptype == "text":
            yield Event(line, "narrative", "assistant_msg", text=str(part.get("text") or ""))
    elif etype == "step.end":
        usage = ev.get("usage") or {}
        ctx = sum(int(usage.get(k) or 0) for k in
                  ("inputOther", "inputCacheRead", "inputCacheCreation"))
        if ctx > 0:
            yield Event(line, "meta", "token_count", size=ctx, extra={"window": None})
        if ev.get("finishReason") in ("stop", "end_turn"):
            yield Event(line, "lifecycle", "turn_complete")
        elif ev.get("finishReason") in ("aborted", "cancelled", "error"):
            yield Event(line, "lifecycle", "turn_aborted", name=str(ev.get("finishReason")))


# --------------------------------------------------------------------------- #
# DeepSeek Harness (dsh)
# --------------------------------------------------------------------------- #

# Storage: ~/.dsh/sessions/<cwd-slug>/<session-id>/session.jsonl.zstd
# A header line, then one row per event. Two vocabularies share the file:
#
#   session events   44 namespaced types ("tool/call", "compaction/summary", ...)
#   chunk rows       3 slash-less tags packing runs of streaming deltas
#
# Four things here are load-bearing and unlike every other supported platform:
#
# 1. CHUNK ROWS ARE NOT EVIDENCE. `text-chunks` / `reasoning-chunks` /
#    `tool-call-chunks` pack a run of token deltas whose content is ALSO carried,
#    assembled, by the `assistant/message` that closes the step (~56x envelope
#    amplification is why they are packed at all). Emitting them as narrative
#    would double-count assistant output — the same defect already documented for
#    Claude Code's re-emitted compaction block. They are counted as noise, never
#    silently dropped.
#
# 2. AMBIENT IS STRUCTURAL, NOT HEURISTIC. `user/message` carries a `source`
#    discriminator: only `kind == "user"` is a human prompt. `agent-instructions`
#    (AGENTS.md/CLAUDE.md injection) and `plugin` (sandbox-policy snapshots) are
#    harness injection wearing the user role. dsh states this in the log, so the
#    ambient marker is passed through as fact instead of being guessed from text
#    — 18 of 34 `user/message` rows in the local corpus are injection.
#
# 3. COMPACTION LOSS IS MEASURABLE. `compaction/summary` carries `shadowedSeqs`,
#    `shadowedRange` and `shadowedTokenCount`: the exact events and token price
#    the summary replaced. This is the only platform where the skill's核心 claim
#    (compression blindness) can be measured rather than estimated.
#    `compaction/prune` is the model-free variant. An unmatched `compaction/start`
#    is a compaction that never closed — a failure, not a compaction.
#
# 4. NOT ALL HISTORY IS THIS RUN'S. Events before the LAST `session/end-seed`
#    came from a seed (resume, fork, or replay). Counting them as this
#    lifecycle's work inflates every effort metric. The marker is surfaced as a
#    session_meta record so the boundary is visible to the caller.
#
# Subagents get their OWN session log (header `delegationDepth > 0`), like Kimi
# and modern Claude Code — a single file is one agent's view. Children sit in the
# SAME cwd directory as their parent; only the header distinguishes them (their
# directory name lacks the `session-` prefix, but that is a convention, not a
# contract — read the header).
#
# ⚠️ `~/.dsh/storages/{workspace,session_projcache}.json` list ROOT sessions
# only. Measured locally: 9 listed, 19 on disk — every one of the 10 missing is a
# subagent. Taking the store's list as the session list drops a quarter of the
# work (puzhi-fan: 9040 child lines under a 27692-line parent) while looking
# complete. Walk the directory; the stores are for locating, never for counting.
_DSH_NAMESPACES = {
    "turn", "step", "user", "assistant", "tool", "tool-workflow", "request",
    "compaction", "session", "agent", "agent-preset", "llm", "todo", "goal",
    "approval", "permission", "plan", "sandbox", "schedule", "command", "hook",
    "feedback", "subagent", "web", "internal",
}
_DSH_CHUNK_ROWS = {"text-chunks", "reasoning-chunks", "tool-call-chunks"}
_DSH_PATCH_TOOLS = {"edit", "write", "str_replace_editor"}
_DSH_EXEC_TOOLS = {"bash", "pwsh"}
_DSH_END_KINDS = {"completed": "turn_complete", "error": "turn_error"}


def _dsh_text(blocks: Any) -> str:
    if isinstance(blocks, str):
        return blocks
    if not isinstance(blocks, list):
        return ""
    out = []
    for b in blocks:
        if isinstance(b, dict) and isinstance(b.get("text"), str):
            out.append(b["text"])
    return "\n".join(out)


def _dsh_result_text(message: Any) -> tuple[str, bool]:
    """Flatten a ToolResultMessage to (text, is_error)."""
    if not isinstance(message, dict):
        return "", False
    err = False
    out = []
    for block in message.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("isError"):
            err = True
        inner = block.get("content")
        if isinstance(inner, list):
            out.append(_dsh_text(inner))
        elif isinstance(inner, str):
            out.append(inner)
        elif isinstance(block.get("text"), str):
            out.append(block["text"])
    return "\n".join(out), err


def _make_dsh_adapter():
    """dsh needs state: the context window arrives in its own record.

    `request/context` is logged only when the route or capacity CHANGES, so the
    window for a given step is the last one announced, not one carried by the
    step. A stateless per-record adapter would report window=None for every
    request after the first — a capability the platform has, silently lost.
    """
    state: dict[str, Any] = {"window": None, "provider": None, "model": None}

    def adapter(rec: dict[str, Any], line: int) -> Iterator[Event]:
        rtype = rec.get("type") or ""
        data = rec.get("data") if isinstance(rec.get("data"), dict) else {}

        # ---- header ----------------------------------------------------- #
        if rtype == "session":
            yield Event(line, "meta", "session_meta", name="header",
                        extra={"session_id": rec.get("id"), "cwd": rec.get("cwd"),
                               "delegation_depth": rec.get("delegationDepth"),
                               "schema_version": rec.get("version")})
            return

        # ---- packed streaming deltas: noise, but counted ------------------ #
        if rtype in _DSH_CHUNK_ROWS:
            gaps = data.get("dt") or []
            members = data.get("texts") or data.get("args") or []
            yield Event(line, "meta", "ui_noise", name=rtype,
                        extra={"n_deltas": len(members),
                               "span_ms": sum(g for g in gaps if isinstance(g, (int, float)))})
            return
        if rtype == "assistant/chunk":
            yield Event(line, "meta", "ui_noise", name="assistant/chunk")
            return

        # ---- route / capacity -------------------------------------------- #
        if rtype == "request/context":
            win = data.get("contextWindow")
            if isinstance(win, int):
                state["window"] = win
            state["provider"] = data.get("provider")
            state["model"] = data.get("model")
            # Also surface it: the window is a property of the ROUTE, announced
            # independently of any assistant message. Carrying it only on
            # token_count would silently drop it from a session that announced a
            # window but never produced a usage-bearing message.
            yield Event(line, "meta", "ui_noise", name="request/context",
                        extra={"window": state["window"], "provider": state["provider"],
                               "model": state["model"]})
            return
        if rtype == "request/header":
            # Carries the assembled system prompt — the config layer (SKILL §1.5).
            # Kept as a named drill target; the body is not lifted into memory.
            yield Event(line, "meta", "ui_noise", name="request/header",
                        extra={"reason": data.get("reason")})
            return

        # ---- the surface -------------------------------------------------- #
        if rtype == "user/message":
            source = data.get("source") if isinstance(data.get("source"), dict) else {}
            kind = str(source.get("kind") or "")
            # `MessageSourceMap` is declared merge-extensible ("plugins add their
            # own kinds"), so this set is OPEN and cannot be enumerated here.
            # Everything that is not literally `user` is therefore treated as
            # injection. That is the safe direction: a future kind mistaken for
            # injection loses a signal, while one mistaken for the human promotes
            # harness text to Owner intent — the exact defect this skill exists
            # to catch. Kinds seen locally: user, plugin, agent-instructions,
            # skill-catalog, skill-invocation, goal, subagent-report,
            # subagent-settled, coordinator.
            #
            # Note dsh does NOT need Kimi's skill-body unwrap: `skill-invocation`
            # carries only the rendered skill body, while "the user's own words
            # ride a plain user message" (dsh-skill/types). The two never fuse.
            extra: dict[str, Any] = {"ambient": kind != "user", "source_kind": kind}
            if kind == "goal":
                # A goal continuation round: the agent re-prompting ITSELF from
                # its own goal object. Never Owner intent — but the round number
                # is a first-class spinning signal, so keep it.
                extra["goal_round"] = source.get("round")
                extra["goal_revision"] = source.get("revision")
            yield Event(line, "evidence", "user_msg", name=kind,
                        text=_dsh_text(data.get("content")), extra=extra)
            # A child's report, or the manager's account of a child settling, is
            # also evidence that a subagent ran — the only such evidence when the
            # child's own log is not in hand. The two kinds stay distinct: a
            # report is what the child chose to say, `settled` is the runtime
            # stating what became of it.
            if kind in ("subagent-report", "subagent-settled", "coordinator"):
                yield Event(line, "meta", "subagent",
                            name=str(source.get("senderSessionId") or kind),
                            extra={"via": kind, "summary": source.get("summary")})
            return

        if rtype == "assistant/message":
            message = data.get("message") if isinstance(data.get("message"), dict) else {}
            for block in message.get("content") or []:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "reasoning":
                    yield Event(line, "narrative", "thinking",
                                text=str(block.get("text") or ""))
                elif btype == "text":
                    yield Event(line, "narrative", "assistant_msg",
                                text=str(block.get("text") or ""))
            usage = data.get("usage")
            if isinstance(usage, dict):
                # Context length for this request = what the model was fed:
                # fresh input plus what it read from cache. Output and reasoning
                # tokens are generation, not occupancy — folding them in would
                # overstate pressure.
                ctx = sum(int(usage.get(k) or 0)
                          for k in ("inputTokens", "cacheReadTokens"))
                if ctx > 0:
                    yield Event(line, "meta", "token_count", size=ctx,
                                extra={"window": state["window"],
                                       "model": state["model"],
                                       "output_tokens": usage.get("outputTokens"),
                                       "reasoning_tokens": usage.get("reasoningTokens")})
            return

        if rtype == "tool/call":
            name = str(data.get("name") or "?")
            raw = data.get("arguments")
            raw = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
            args: Any = None
            try:
                args = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                args = None  # the model's raw string is logged unparsed by design
            targets = []
            command = None
            if isinstance(args, dict):
                if name in _DSH_PATCH_TOOLS:
                    fp = args.get("file_path") or args.get("path")
                    if isinstance(fp, str):
                        targets.append(fp)
                if name in _DSH_EXEC_TOOLS:
                    cmd = args.get("command")
                    command = cmd if isinstance(cmd, str) else None
            yield Event(line, "evidence", "tool_call", name=name, text=raw,
                        extra={"patch_targets": targets, "command": command,
                               "is_exec": name in _DSH_EXEC_TOOLS,
                               "call_id": data.get("callId")})
            return

        if rtype == "tool/result":
            text, block_err = _dsh_result_text(data.get("message"))
            failure = data.get("error") if isinstance(data.get("error"), dict) else None
            message = data.get("message") if isinstance(data.get("message"), dict) else {}
            source = message.get("source") if isinstance(message.get("source"), dict) else {}
            yield Event(line, "evidence", "tool_output", size=len(text), text=text,
                        extra={"call_id": source.get("callId"),
                               "is_error": bool(block_err or failure),
                               "error_code": (failure or {}).get("code")})
            return

        # ---- lifecycle ----------------------------------------------------- #
        if rtype == "turn/end":
            reason = data.get("reason") if isinstance(data.get("reason"), dict) else {}
            kind = str(reason.get("kind") or "")
            mapped = _DSH_END_KINDS.get(kind, "turn_aborted")
            extra = {}
            if kind == "error":
                extra = {"code": ((reason.get("error") or {}) if isinstance(
                    reason.get("error"), dict) else {}).get("code")}
            yield Event(line, "lifecycle", mapped, name=kind or "unknown", extra=extra)
            return

        # A retried provider failure: the request died and was reissued. Counted,
        # never folded into aborts — the turn may still have completed.
        if rtype == "llm/retry":
            failure = data.get("failure") if isinstance(data.get("failure"), dict) else {}
            yield Event(line, "lifecycle", "turn_error",
                        name=str(failure.get("code") or "retry"),
                        text=str(failure.get("message") or ""),
                        extra={"attempt": data.get("retry"),
                               "max_retries": data.get("maxRetries"),
                               "delay_ms": data.get("delayMs")})
            return

        # ---- compaction ----------------------------------------------------- #
        if rtype == "compaction/summary":
            shadowed = data.get("shadowedSeqs") or []
            yield Event(line, "lifecycle", "compaction", name="summary",
                        text=_dsh_text(data.get("summary")),
                        extra={"shadowed_events": len(shadowed),
                               "shadowed_tokens": data.get("shadowedTokenCount"),
                               "shadowed_range": data.get("shadowedRange"),
                               "summarizer_model": data.get("model"),
                               "compaction_id": data.get("compactionId")})
            return
        if rtype == "compaction/prune":
            shadowed = data.get("shadowedSeqs") or []
            yield Event(line, "lifecycle", "compaction", name="prune",
                        extra={"shadowed_events": len(shadowed),
                               "shadowed_tokens": data.get("shadowedTokenCount"),
                               "shadowed_range": data.get("shadowedRange")})
            return
        if rtype in ("compaction/start", "compaction/end"):
            # Bracket markers, not compactions. An unmatched start is a
            # compaction that never closed; `end.error` is one that failed.
            yield Event(line, "meta", "ui_noise", name=rtype,
                        extra={"compaction_id": data.get("compactionId"),
                               "error": data.get("error")})
            return

        # ---- seed boundary --------------------------------------------------- #
        if rtype == "session/end-seed":
            # Everything BEFORE the last one of these was inherited, not done here.
            yield Event(line, "meta", "session_meta", name="end_seed")
            return

        # ---- goal (P3 gets a native source on this platform) ------------------ #
        if rtype == "goal/change":
            goal = data.get("goal") if isinstance(data.get("goal"), dict) else data
            yield Event(line, "meta", "ui_noise", name="goal_change",
                        text=str(goal.get("objective") or ""),
                        extra={"revision": goal.get("revision"),
                               "phase": goal.get("phase")})
            return

        # ---- user gates ------------------------------------------------------- #
        if rtype == "approval/decided":
            decision = str(data.get("decision") or data.get("outcome") or "")
            yield Event(line, "meta", "ui_noise", name=f"approval:{decision or '?'}",
                        text=str(data.get("reason") or ""),
                        extra={"denied": decision not in ("allow", "allow-always",
                                                          "approved", "accept")})
            return

        if rtype == "subagent/descriptor":
            yield Event(line, "meta", "subagent",
                        name=str(data.get("label") or data.get("agentModel") or "subagent"),
                        # `provider` here is the SPAWN mechanism ("spawn"/"fork"),
                        # not the model vendor — those are agentProvider/agentModel.
                        # Reading `provider` as the model route mislabels every child.
                        extra={"mode": data.get("mode"),
                               "spawn_kind": data.get("provider"),
                               "provider": data.get("agentProvider"),
                               "model": data.get("agentModel")})
            return

        # Everything else in the vocabulary is log-only bookkeeping. Named so a
        # future signature can find it, dropped so it cannot inflate a count.
        yield Event(line, "meta", "ui_noise", name=rtype)

    return adapter


_ADAPTERS = {"codex": _codex_events, "claude_code": _claude_events, "kimi": _kimi_events}

# Adapters that must carry state across records get a factory instead: one fresh
# instance per iter_events() call, so two walks of the same file cannot bleed.
_ADAPTER_FACTORIES = {"dsh": _make_dsh_adapter}


def _adapter_for(fmt: str):
    factory = _ADAPTER_FACTORIES.get(fmt)
    return factory() if factory else _ADAPTERS.get(fmt)

# Capabilities each platform can actually support. A metric whose inputs are not
# observable on a platform must be reported as unavailable, never as zero — a
# silent zero for "compactions" would make a Kimi session look pristine.
CAPABILITIES = {
    "codex":       {"compaction": True,  "turn_lifecycle": True,  "context_window": True,
                    "subagents": "inline"},
    # 2026-08 layout: subagents live in <session-dir>/subagents/agent-*.jsonl —
    # the main file carries ZERO inline sidechain records (top-40 largest main
    # files probed, 2026-08-10). Auditing the main file alone misses all
    # subagent evidence; walk subagents/. Older files may still be inline.
    "claude_code": {"compaction": True,  "turn_lifecycle": "approx", "context_window": False,
                    "subagents": "separate-files (pre-2026-08: inline sidechain)"},
    "kimi":        {"compaction": False, "turn_lifecycle": "approx", "context_window": False,
                    "subagents": "separate-files"},
    # dsh is the most instrumented of the four: real turn/step brackets, an
    # announced context window, and compaction events that state exactly which
    # events and how many tokens were shadowed. `compaction_loss` is true only
    # here — elsewhere the loss can be inferred, never read.
    "dsh":         {"compaction": True,  "turn_lifecycle": True,  "context_window": True,
                    "compaction_loss": True, "structural_ambient": True,
                    "seed_boundary": True, "compressed": True,
                    "subagents": "separate-sessions in the SAME cwd dir; "
                                 "header delegationDepth>0 (dir name lacks the "
                                 "session- prefix). NOT listed in projcache/workspace"},
}


def iter_events(path: Path, max_lines: int | None = None,
                fmt: str | None = None) -> Iterator[Event]:
    fmt = fmt or detect_format(path)
    adapter = _adapter_for(fmt)
    if adapter is None:
        hint = (" — 2025-08~09-era Codex rollout, pre-response_item schema, "
                "known-unsupported (loud refuse, not silent zeros); "
                "see references/codex-jsonl.md §格式代际边界"
                if fmt == "codex_legacy" else "")
        raise ValueError(f"unsupported session format: {fmt}{hint} ({path})")
    seen = 0
    # Claude Code re-emits, verbatim, the whole block of records it fed into a
    # compaction — same `uuid`, same timestamp, immediately before the
    # compact_boundary line. Measured on CASE-Q: one contiguous block
    # L2185-2458, 274 lines = 10.1% of the file, inflating Bash 309 -> 354
    # (+14.6%) and Read 83 -> 92, and manufacturing 45 phantom "byte-identical
    # re-runs" that read as a re-orientation loop but never happened.
    # Dedupe by uuid, and emit a marker so the drop is counted, never silent.
    seen_uuids: set[str] = set()
    with open_session_text(path) as handle:
        for line_no, raw in enumerate(handle, start=1):
            if max_lines and line_no > max_lines:
                break
            seen = line_no
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                yield Event(line_no, "meta", "malformed")
                continue
            if not isinstance(rec, dict):
                yield Event(line_no, "meta", "malformed")
                continue
            uid = rec.get("uuid")
            if isinstance(uid, str) and uid:
                if uid in seen_uuids:
                    yield Event(line_no, "meta", "duplicate_record", name=uid)
                    continue
                seen_uuids.add(uid)
            yield from adapter(rec, line_no)
    # Terminal marker: lines that produce no events still count toward file length,
    # so the caller cannot derive the true line count from events alone.
    yield Event(seen, "meta", "eof", extra={"format": fmt})


if __name__ == "__main__":
    import sys
    from collections import Counter

    for arg in sys.argv[1:]:
        p = Path(arg).expanduser()
        fmt = detect_format(p)
        kinds: Counter[str] = Counter()
        tools: Counter[str] = Counter()
        last = 0
        for e in iter_events(p):
            kinds[f"{e.flow}/{e.kind}"] += 1
            if e.kind == "tool_call":
                tools[e.name] += 1
            last = e.line
        print(f"\n{p.name}  format={fmt}  lines={last}")
        print(f"  capabilities: {CAPABILITIES.get(fmt)}")
        for k, v in kinds.most_common(12):
            print(f"    {k:<26} {v}")
        print(f"    tools: {dict(tools.most_common(6))}")
