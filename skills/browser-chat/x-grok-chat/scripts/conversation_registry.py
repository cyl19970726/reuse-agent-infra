#!/usr/bin/env python3
"""Private routing registry for X Grok conversations."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


DEFAULT_REGISTRY = Path.home() / ".codex/state/x-grok-chat/conversations.json"
KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
CONVERSATION_ID_PATTERN = re.compile(r"^[0-9]{8,32}$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def empty_state() -> dict:
    return {"schema_version": 1, "current_key": None, "conversations": {}}


def canonical_url(conversation_id: str) -> str:
    return "https://x.com/i/grok?conversation=" + quote(conversation_id, safe="")


def validate_key(value: str) -> str:
    if not KEY_PATTERN.fullmatch(value):
        raise SystemExit("invalid key: use 1-128 letters, digits, '_' or '-'")
    return value


def validate_conversation_id(value: str) -> str:
    if not CONVERSATION_ID_PATTERN.fullmatch(value):
        raise SystemExit("invalid conversation-id: expected 8-32 digits")
    return value


def prepare_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)


def read_state(path: Path) -> dict:
    if not path.exists():
        return empty_state()
    with path.open("r", encoding="utf-8") as handle:
        state = json.load(handle)
    if state.get("schema_version") != 1 or not isinstance(
        state.get("conversations"), dict
    ):
        raise SystemExit(f"unsupported or invalid registry: {path}")
    state.setdefault("current_key", None)
    return state


def write_state(path: Path, state: dict) -> None:
    prepare_parent(path)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=".conversations-", suffix=".tmp", dir=path.parent
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


@contextmanager
def locked_state(path: Path, write: bool = False):
    prepare_parent(path)
    lock_path = path.parent / "conversations.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX if write else fcntl.LOCK_SH)
        state = read_state(path)
        yield state
        if write:
            write_state(path, state)


def emit(payload: dict | list) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def command_list(path: Path) -> None:
    with locked_state(path) as state:
        records = list(state["conversations"].values())
        records.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        emit({"current_key": state["current_key"], "conversations": records})


def command_get(path: Path, key: str | None, current: bool = False) -> None:
    with locked_state(path) as state:
        resolved_key = state["current_key"] if current else key
        record = state["conversations"].get(resolved_key) if resolved_key else None
        emit({"found": record is not None, "key": resolved_key, "conversation": record})


def command_upsert(path: Path, args: argparse.Namespace) -> None:
    key = validate_key(args.key)
    conversation_id = validate_conversation_id(args.conversation_id)
    with locked_state(path, write=True) as state:
        existing = state["conversations"].get(key, {})
        timestamp = now_iso()
        record = {
            "key": key,
            "label": args.label,
            "conversation_id": conversation_id,
            "url": args.url or canonical_url(conversation_id),
            "task_space_id": (
                args.task_space_id
                if args.task_space_id is not None
                else existing.get("task_space_id")
            ),
            "status": "active",
            "created_at": existing.get("created_at", timestamp),
            "updated_at": timestamp,
        }
        state["conversations"][key] = record
        if args.make_current or state["current_key"] is None:
            state["current_key"] = key
        emit({"saved": True, "current_key": state["current_key"], "conversation": record})


def command_set_current(path: Path, key: str) -> None:
    key = validate_key(key)
    with locked_state(path, write=True) as state:
        record = state["conversations"].get(key)
        if record is None:
            raise SystemExit(f"unknown conversation key: {key}")
        if record.get("status") != "active":
            raise SystemExit(f"conversation is not active: {key}")
        state["current_key"] = key
        emit({"saved": True, "current_key": key, "conversation": record})


def command_close(path: Path, key: str) -> None:
    key = validate_key(key)
    with locked_state(path, write=True) as state:
        record = state["conversations"].get(key)
        if record is None:
            raise SystemExit(f"unknown conversation key: {key}")
        record["status"] = "closed"
        record["updated_at"] = now_iso()
        if state["current_key"] == key:
            state["current_key"] = None
        emit({"saved": True, "current_key": state["current_key"], "conversation": record})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    commands.add_parser("current")
    get_parser = commands.add_parser("get")
    get_parser.add_argument("--key", required=True)
    upsert_parser = commands.add_parser("upsert")
    upsert_parser.add_argument("--key", required=True)
    upsert_parser.add_argument("--label", required=True)
    upsert_parser.add_argument("--conversation-id", required=True)
    upsert_parser.add_argument("--url")
    upsert_parser.add_argument("--task-space-id", type=int)
    upsert_parser.add_argument("--make-current", action="store_true")
    current_parser = commands.add_parser("set-current")
    current_parser.add_argument("--key", required=True)
    close_parser = commands.add_parser("close")
    close_parser.add_argument("--key", required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    path = args.registry.expanduser().resolve()
    if args.command == "list":
        command_list(path)
    elif args.command == "current":
        command_get(path, None, current=True)
    elif args.command == "get":
        command_get(path, args.key)
    elif args.command == "upsert":
        command_upsert(path, args)
    elif args.command == "set-current":
        command_set_current(path, args.key)
    elif args.command == "close":
        command_close(path, args.key)


if __name__ == "__main__":
    main()
