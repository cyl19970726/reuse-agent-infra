#!/usr/bin/env python3
"""L0 — find the session, across every supported corpus.

The corpora are large (48 GB Codex + 2.2 GB Claude Code + 679 MB Kimi here), so
the one thing this must never do is a full-text grep over everything. The search
order is cheapest-first:

    1. thread id           -> filename match, instant
    2. index thread_name   -> session_index.jsonl, one small file
    3. time window + size  -> stat only, no reads
    4. first user message  -> reads the head of candidate files only
    5. bounded content     -> only within an already-narrowed candidate set

Codex's session_index.jsonl covers only part of the corpus (2980 of 5548 here),
so a miss in the index is not evidence of absence — step 3/4 still apply.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))
from session_events import detect_format, iter_events  # noqa: E402

CORPORA = {
    "codex": ["~/.codex/sessions", "~/.codex/archived_sessions"],
    "claude_code": ["~/.claude/projects"],
    "kimi": ["~/.kimi-code/sessions"],
    # dsh logs are zstd-compressed; the glob must say so or the corpus reads as
    # empty. Layout: <cwd-slug>/<session-id>/session.jsonl.zstd — the id is in
    # the DIRECTORY name, not the file name, so filename matching alone misses.
    "dsh": ["~/.dsh/sessions"],
}
SESSION_GLOBS = ("*.jsonl", "*.jsonl.zstd")
INDEXES = ["~/.codex/session_index.jsonl", "~/.kimi-code/session_index.jsonl"]
DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})")


def walk(corpora: list[str]) -> Iterator[Path]:
    for key in corpora:
        for root in CORPORA.get(key, []):
            base = Path(root).expanduser()
            if base.is_dir():
                for pattern in SESSION_GLOBS:
                    yield from base.rglob(pattern)


def session_id_of(p: Path) -> str:
    """The id a user would quote. dsh puts it on the directory, others on the file."""
    return p.parent.name if p.name.startswith("session.jsonl") else p.name


def file_time(p: Path) -> datetime:
    m = DATE_RE.search(p.name)
    if m:
        try:
            return datetime(*map(int, m.groups()), tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)


def search_indexes(query: str) -> list[dict[str, Any]]:
    hits = []
    for idx in INDEXES:
        p = Path(idx).expanduser()
        if not p.is_file():
            continue
        for line in p.open("r", encoding="utf-8", errors="replace"):
            line = line.strip()
            if not line or query.lower() not in line.lower():
                continue
            try:
                hits.append({"index": str(p), **json.loads(line)})
            except json.JSONDecodeError:
                continue
    return hits


def head_summary(p: Path, max_lines: int = 400) -> dict[str, Any]:
    """Read only the head of a file: format, first real user message, tool hints."""
    first_user = None
    tools: set[str] = set()
    fmt = "unknown"
    try:
        fmt = detect_format(p)
        for e in iter_events(p, max_lines=max_lines, fmt=fmt):
            if e.kind == "user_msg" and first_user is None:
                body = " ".join(e.text.split())
                if body and not body.startswith(("<", "# Files mentioned", "AGENTS.md")):
                    first_user = body[:220]
            elif e.kind == "tool_call":
                tools.add(e.name)
            if first_user and len(tools) > 6:
                break
    except Exception as exc:  # a corrupt or foreign file must not abort the search
        return {"format": fmt, "error": str(exc)[:80]}
    return {"format": fmt, "first_user": first_user, "tools": sorted(tools)[:8]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("query", nargs="?", default="",
                    help="thread id fragment, or keyword for index/first-message search")
    ap.add_argument("--corpora", nargs="+", default=list(CORPORA),
                    choices=list(CORPORA))
    ap.add_argument("--since", help="YYYY-MM-DD")
    ap.add_argument("--until", help="YYYY-MM-DD")
    ap.add_argument("--min-mb", type=float, default=0.0)
    ap.add_argument("--deep", action="store_true",
                    help="read the head of each candidate for its first user message "
                         "(only after the candidate set is already narrow)")
    ap.add_argument("--max-deep", type=int, default=25)
    ap.add_argument("--limit", type=int, default=30)
    args = ap.parse_args()

    if args.query:
        idx_hits = search_indexes(args.query)
        if idx_hits:
            print(f"== index hits ({len(idx_hits)}) ==")
            for h in idx_hits[: args.limit]:
                ident = h.get("id") or h.get("sessionId") or "?"
                label = h.get("thread_name") or h.get("sessionDir") or ""
                updated = h.get("updated_at") or h.get("updatedAt") or ""
                print(f"  {ident}  {label}  {updated}")
            print("  (index coverage is partial — absence here is not absence)\n")

    since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc) if args.since else None
    until = datetime.fromisoformat(args.until).replace(tzinfo=timezone.utc) if args.until else None

    cands: list[tuple[Path, datetime, int]] = []
    for p in walk(args.corpora):
        try:
            size = p.stat().st_size
        except OSError:
            continue
        # A zstd log is ~2x smaller on disk than the thing being measured, so a
        # size floor applied to the compressed bytes silently hides real
        # sessions. Grade compressed candidates on their decompressed length.
        weighted = size * 2 if p.name.endswith((".zstd", ".zst")) else size
        if weighted < args.min_mb * 1e6:
            continue
        if args.query and args.query not in session_id_of(p):
            name_match = False
        else:
            name_match = True
        t = file_time(p)
        if since and t < since:
            continue
        if until and t > until:
            continue
        if args.query and not name_match and not (args.deep or args.since or args.until):
            continue
        cands.append((p, t, size))

    cands.sort(key=lambda r: r[1], reverse=True)
    exact = [c for c in cands if args.query and args.query in session_id_of(c[0])]
    if exact:
        cands = exact

    print(f"== file candidates ({len(cands)}) ==")
    shown = cands[: args.limit]
    deep_budget = args.max_deep if args.deep else 0
    for p, t, size in shown:
        line = f"  {t:%Y-%m-%d %H:%M}  {size/1e6:8.1f}MB  {p}"
        print(line)
        if deep_budget > 0:
            info = head_summary(p)
            if info.get("first_user"):
                print(f"        [{info['format']}] {info['first_user'][:150]}")
            elif info.get("error"):
                print(f"        [{info['format']}] !! {info['error']}")
            deep_budget -= 1
    if len(cands) > args.limit:
        print(f"  ... {len(cands)-args.limit} more (raise --limit or narrow --since/--min-mb)")
    if args.deep and len(cands) > args.max_deep:
        print(f"  note: only the first {args.max_deep} were opened (--max-deep)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
