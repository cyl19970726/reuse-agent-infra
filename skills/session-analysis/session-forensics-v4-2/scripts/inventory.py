#!/usr/bin/env python3
"""What ready-made resources existed at the cut, and did the session use them?

  inventory.py [--repo DIR] A=<session> [B=<session> ...] [--until A:LINE] [--ledger ledger.json] [--out DIR]

Without --repo the repository is taken from the sessions' working directory (cwd; its git top level when it is
inside a git repository), and inventory.md says so on its first line. With neither, it says loudly that the
stock-taking is incomplete instead of reporting an empty repository.

Resources in the repository (or in an export of it as it was at the cut):
  skill      .claude/skills/*/SKILL.md, .agents/skills/*/SKILL.md, .codex/skills/*/SKILL.md (any depth)
  guidance   AGENTS.md, CLAUDE.md
  workflow   workflows/**, .claude/commands/*.md, .claude/agents/*.md
  docs       docs/**/*.md
  readme     README*.md
plus every skill the session itself names (a Skill call, a slash command, "Base directory for this skill",
a skill listing, a path .../skills/<name>/SKILL.md), including global ones outside the repo.

For each one: what it says (a skill's description, a document's title and first line, with the line to cite),
and what the session did with it up to the cut:
  loaded     a skill was loaded, or the harness injected the file (Claude Code nested memory, Codex AGENTS.md)
  auto       CLAUDE.md at the session's working directory: Claude Code puts it into the system prompt,
             which the transcript does not show
  read       a tool call names the file
  mentioned  its path or name occurs somewhere (a listing, a message, a tool output), never read
  no         nowhere
"overlap" lists words the resource shares with the user's own words (from --ledger, else the session's user
messages): a sorting hint for the observer, not a verdict. Writes DIR/inventory.json and DIR/inventory.md.
Relevance is the observer's call: it reads the unused ones that might matter and writes them into the map.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from session_events import detect_format, iter_events, open_session_text  # noqa: E402

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".next", "target", ".cache"}
SKILL_RE = re.compile(r"(?:^|/)\.(?:claude|agents|codex)/skills/([^/]+)/SKILL\.md$")
WORKFLOW_EXT = {".md", ".yml", ".yaml", ".json", ".js", ".ts", ".py", ".toml"}
GENERIC = {"readme.md", "index.md", "skill.md", "agents.md", "claude.md", "notes.md", "todo.md"}
RANK = {"loaded": 4, "auto": 4, "read": 3, "mentioned": 2, "no": 0}
USED_WORD = {"loaded": "加载了", "auto": "自动加载（记录里看不到）", "read": "读过", "mentioned": "只被提到", "no": "没出现"}
CAP = 400  # repo files listed at most; the rest are counted


def classify(rel: str) -> str | None:
    low = rel.lower()
    base = low.rsplit("/", 1)[-1]
    if SKILL_RE.search(rel):
        return "skill"
    if base in ("agents.md", "claude.md", "agent.md"):
        return "guidance"
    if ("/workflows/" in "/" + low or re.search(r"(^|/)\.claude/(commands|agents)/[^/]+\.md$", low)) \
            and Path(low).suffix in WORKFLOW_EXT:
        return "workflow"
    if re.match(r"^(docs?|documentation)/", low) and low.endswith(".md"):
        return "docs"
    if base.startswith("readme") and base.endswith(".md"):
        return "readme"
    return None


def walk(repo: Path) -> list[Path]:
    out = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not (d.startswith(".") and d not in (".claude", ".agents", ".codex", ".github"))]
        for f in files:
            out.append(Path(root) / f)
    return out


def describe(p: Path) -> tuple[str, int]:
    """(what it says, the line to cite). A skill: its front-matter description; else the title + first line."""
    try:
        lines = p.read_text(errors="ignore").splitlines()
    except OSError:
        return "", 1
    if lines and lines[0].strip() == "---":
        for i, ln in enumerate(lines[1:60], 2):
            if ln.strip() == "---":
                break
            mm = re.match(r"^description:\s*(.*)$", ln)
            if mm:
                val = mm.group(1).strip().strip("'\"")
                if val in ("|", ">", "|-", ">-", ""):
                    more = []
                    for nxt in lines[i:i + 8]:
                        if not nxt.startswith((" ", "\t")):
                            break
                        more.append(nxt.strip())
                    val = " ".join(more)
                return val[:220], i
    title, first, at = "", "", 1
    for i, ln in enumerate(lines[:80], 1):
        s = ln.strip()
        if not s or s == "---":
            continue
        if s.startswith("#") and not title:
            title, at = s.lstrip("# ").strip(), i
            continue
        if not first:
            first = s
            at = at if title else i
        if title and first:
            break
    return (f"{title} — {first}" if title and first else title or first)[:220], at


def git_first_added(repo: Path) -> dict[str, str] | None:
    """rel path -> ISO time the file was first committed; None when the repo is not a git repository."""
    if not (repo / ".git").exists():
        return None
    try:
        out = subprocess.run(["git", "-C", str(repo), "log", "--diff-filter=A", "--name-only", "--format=@%cI"],
                             capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    first, when = {}, ""
    for ln in out.splitlines():
        if ln.startswith("@"):
            when = ln[1:]
        elif ln.strip():
            first[ln.strip()] = when  # newest first: the last write is the earliest add
    return first


def tokens(s: str) -> set[str]:
    s = str(s or "").lower()
    out = {w for w in re.findall(r"[a-z][a-z0-9_\-]{3,}", s)}
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", s):
        out |= {run[i:i + 2] for i in range(len(run) - 1)}
    return out - {"this", "that", "with", "from", "when", "what", "skill", "用于", "使用", "一个", "这个", "什么", "可以"}


def iso(ts) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return d if d.tzinfo else d.astimezone()
    except (TypeError, ValueError):
        return None


class Seen:
    """Everything the session did with names and paths, up to the cut."""

    def __init__(self):
        self.tool_args: list[tuple[str, str]] = []   # (ref, raw arguments of a tool call)
        self.texts: list[tuple[str, str]] = []       # (ref, any text on the line)
        self.loaded: dict[str, str] = {}             # skill name or absolute file -> first ref
        self.listed: dict[str, str] = {}             # skill name -> first ref (a listing or a path)
        self.listing_desc: dict[str, str] = {}       # skill name -> description from a listing
        self.skill_paths: dict[str, str] = {}        # skill name -> SKILL.md path seen in the session
        self.user_words: list[str] = []
        self.cwd: Counter = Counter()                # working directory -> number of lines that carry it
        self.cut_time: datetime | None = None
        self.formats: set[str] = set()

    def first(self, d: dict, key: str, ref: str):
        d.setdefault(key, ref)


def flat(x, out):
    if isinstance(x, str):
        out.append(x)
    elif isinstance(x, dict):
        for v in x.values():
            flat(v, out)
    elif isinstance(x, list):
        for v in x:
            flat(v, out)
    return out


def scan(alias: str, path: Path, until: int | None, seen: Seen):
    fmt = detect_format(path)
    seen.formats.add(fmt)
    for ev in iter_events(path, max_lines=until):
        ref = f"{alias}:{ev.line}"
        if ev.kind == "tool_call":
            seen.tool_args.append((ref, ev.text or ""))
            if ev.name == "Skill":
                try:
                    name = str((json.loads(ev.text) or {}).get("skill") or "")
                except (ValueError, AttributeError):
                    name = ""
                if name:
                    seen.first(seen.loaded, name.split(":")[-1], ref)
        elif ev.kind == "user_msg" and ev.text and len(ev.text) < 4000 and not ev.text.lstrip().startswith("<") \
                and "AGENTS.md instructions" not in ev.text and "Base directory for this skill" not in ev.text:
            seen.user_words.append(ev.text)
    with open_session_text(path) as fh:
        for n, raw in enumerate(fh, 1):
            if until and n > until:
                break
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            ref = f"{alias}:{n}"
            ts = d.get("timestamp")
            if ts is None and isinstance(d.get("payload"), dict):
                ts = d["payload"].get("timestamp")
            t = iso(ts) if ts else None
            if t and (seen.cut_time is None or t > seen.cut_time):
                seen.cut_time = t
            cw = d.get("cwd")
            if cw is None and isinstance(d.get("payload"), dict):
                cw = d["payload"].get("cwd")  # Codex turn_context / session_meta
            if isinstance(cw, str) and cw:
                seen.cwd[cw] += 1
            att = d.get("attachment") if isinstance(d.get("attachment"), dict) else {}
            at = att.get("type")
            if at == "skill_listing":
                for name in att.get("names") or []:
                    seen.first(seen.listed, str(name).split(":")[-1], ref)
                for mm in re.finditer(r"^- ([\w:\-\.]+): (.+)$", str(att.get("content") or ""), re.M):
                    seen.listing_desc.setdefault(mm.group(1).split(":")[-1], mm.group(2)[:220])
            elif at == "dynamic_skill":
                for name in att.get("skillNames") or []:
                    nm = str(name).split(":")[-1]
                    seen.first(seen.listed, nm, ref)
                    seen.skill_paths.setdefault(nm, str(Path(str(att.get("skillDir") or "")) / nm / "SKILL.md"))
            elif at == "nested_memory" and att.get("path"):
                seen.first(seen.loaded, str(Path(att["path"]).resolve()), ref)
            text = " ".join(flat(d, []))
            seen.texts.append((ref, text))
            for mm in re.finditer(r"Base directory for this skill:\s*(\S+)", text):
                seen.first(seen.loaded, Path(mm.group(1)).name, ref)
                seen.skill_paths.setdefault(Path(mm.group(1)).name, str(Path(mm.group(1)) / "SKILL.md"))
            for mm in re.finditer(r"<command-name>/?([\w:\-\.]+)</command-name>", text):
                seen.first(seen.loaded, mm.group(1).split(":")[-1], ref)
            for mm in re.finditer(r"<skill>\s*<name>([^<]+)</name>", text):  # Codex injects a loaded skill like this
                seen.first(seen.loaded, mm.group(1).strip(), ref)
            for mm in re.finditer(r"AGENTS\.md instructions for (\S+)", text):  # Codex injects AGENTS.md
                seen.first(seen.loaded, str((Path(mm.group(1).rstrip(":")) / "AGENTS.md").resolve()), ref)
            for mm in re.finditer(r"([~\w./\-]*?/skills/([\w\-\.]+)/SKILL\.md)", text):
                seen.first(seen.listed, mm.group(2), ref)
                seen.skill_paths.setdefault(mm.group(2), mm.group(1))


def use_of(item: dict, seen: Seen) -> dict:
    """Best evidence of use for one resource: loaded / auto / read / mentioned / no, with first positions."""
    keys = [k for k in item["keys"] if k]
    got = {"used": "no"}
    name = item.get("name")
    for k in [name, item.get("abs")]:
        if k and k in seen.loaded:
            got.update(used="loaded", at=seen.loaded[k])
            break
    if got["used"] == "no" and item.get("auto"):
        got["used"] = "auto"
    if got["used"] == "no":
        for ref, args in seen.tool_args:
            if any(k in args for k in keys):
                got.update(used="read", at=ref)
                break
    ment = None
    words = [k for k in keys] + ([name] if name and ("-" in name or len(name) >= 6) else [])
    for ref, text in seen.texts:
        if any((re.search(rf"(?<![\w\-]){re.escape(w)}(?![\w\-])", text) if w == name else w in text) for w in words):
            ment = ref
            break
    if ment is None and name in seen.listed:
        ment = seen.listed[name]
    if ment:
        got["mentioned_at"] = ment
        if got["used"] == "no":
            got["used"] = "mentioned"
    return got


def git_top(d: Path) -> Path | None:
    try:
        out = subprocess.run(["git", "-C", str(d), "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(out.stdout.strip()).resolve() if out.returncode == 0 and out.stdout.strip() else None


def repo_from_cwd(seen: Seen) -> tuple[Path | None, str]:
    """The repository the sessions worked in: their most frequent cwd, lifted to its git top level.
    Returns (path or None, a sentence for the first line of inventory.md)."""
    if not seen.cwd:
        return None, "⚠ 没给仓库（--repo），会话记录里也没有工作目录（cwd）：资源清点不完整，下面只有会话里点名的 skill。给 --repo 再跑一次。"
    cw, _ = seen.cwd.most_common(1)[0]
    d = Path(cw).expanduser()
    if not d.is_dir():
        return None, (f"⚠ 没给仓库（--repo）；会话的工作目录 `{cw}` 在这台机器上不存在：资源清点不完整。"
                      "给 --repo（仓库，或切点时的导出）再跑一次。")
    top = git_top(d)
    root = top or d.resolve()
    more = f"（会话里另有 {len(seen.cwd) - 1} 个工作目录，没清点）" if len(seen.cwd) > 1 else ""
    return root, f"仓库取自会话的工作目录（cwd）：`{root}`{'，git 顶层' if top else ''}{more}。不对的话给 --repo 再跑。"


def build(repo: Path | None, sessions: list[tuple[str, Path]], until: dict[str, int], ledger: Path | None) -> dict:
    seen = Seen()
    for alias, p in sessions:
        scan(alias, p, until.get(alias), seen)
    source, note = ("arg", "") if repo else ("cwd", "")
    if not repo:
        repo, note = repo_from_cwd(seen)
        source = "cwd" if repo else "none"
    if ledger and ledger.exists():
        seen.user_words = [r.get("text", "") for r in json.loads(ledger.read_text()).get("requests") or []]
    words = tokens(" ".join(seen.user_words))
    items, over = [], 0
    cwd = {str(Path(c).resolve()) for c in seen.cwd}
    added = None
    if repo:
        added = git_first_added(repo)
        files = sorted(walk(repo))
        for f in files:
            rel = f.relative_to(repo).as_posix()
            kind = classify(rel)
            if not kind:
                continue
            if len([x for x in items if x["scope"] == "repo"]) >= CAP:
                over += 1
                continue
            says, line = describe(f)
            mm = SKILL_RE.search(rel)
            name = mm.group(1) if mm else None
            keys = [str(f.resolve()), rel]
            if name:
                keys.append(f"skills/{name}/SKILL.md")
            elif f.name.lower() not in GENERIC and len(f.name) >= 8:
                keys.append(f.name)
            auto = (kind == "guidance" and f.name == "CLAUDE.md" and "claude_code" in seen.formats
                    and any(c == str(f.parent.resolve()) or c.startswith(str(f.parent.resolve()) + os.sep) for c in cwd))
            it = {"scope": "repo", "kind": kind, "name": name, "path": rel, "abs": str(f.resolve()), "says": says,
                  "cite": f"file:{rel}:{line}", "keys": keys, "auto": auto}
            if added is not None and seen.cut_time:
                t = iso(added.get(rel))
                it["after_cut"] = None if t is None else t > seen.cut_time  # None: never committed
            items.append(it)
    repo_skills = {x["name"] for x in items if x["kind"] == "skill"}
    names = (set(seen.loaded) | set(seen.listed)) - repo_skills
    for nm in sorted(n for n in names if "/" not in n and not n.endswith(".md")):
        sp = seen.skill_paths.get(nm)
        says = seen.listing_desc.get(nm, "")
        cite = ""
        if sp and Path(sp).expanduser().exists():
            d, ln = describe(Path(sp).expanduser())
            says, cite = says or d, f"file:{Path(sp).expanduser()}:{ln}"
        items.append({"scope": "session", "kind": "skill", "name": nm, "path": sp or "", "abs": "", "says": says, "cite": cite,
                      "keys": [f"skills/{nm}/SKILL.md"] + ([sp] if sp else []), "auto": False,
                      "listed_only": nm not in seen.loaded})
    for it in items:
        it.update(use_of(it, seen))
        it["overlap"] = sorted(tokens(it["says"] + " " + (it["name"] or "")) & words)[:6]
        it.pop("keys")
    return {"repo": str(repo) if repo else "", "repo_source": source, "repo_note": note, "git": added is not None,
            "cut": {"until": until, "time": seen.cut_time.astimezone().isoformat() if seen.cut_time else ""},
            "sessions": [{"alias": a, "path": str(p)} for a, p in sessions], "over_cap": over, "items": items}


def to_md(inv: dict) -> str:
    cut = inv["cut"]
    where = "、".join(f"{a}:{n}" for a, n in cut["until"].items()) or "会话末尾"
    out = [f"# 现成资源清点（切点 {where}{' · ' + cut['time'][:16].replace('T', ' ') + '（本地时间）' if cut['time'] else ''}）", ""]
    if inv.get("repo_note"):
        out += [inv["repo_note"], ""]
    if inv["repo"]:
        out.append(f"仓库：`{inv['repo']}`" + ("" if inv["git"] else
                   "（不是 git 仓库：确认不了这些文件在切点时已存在；仓库在会话之后变过的话，给切点时的导出）"))
    repo = [x for x in inv["items"] if x["scope"] == "repo"]
    unused = [x for x in repo if RANK[x["used"]] < 3]
    if inv["repo"]:
        out.append(f"仓库里 {len(repo)} 份资源，会话没读也没加载的 {len(unused)} 份" + (f"（另有 {inv['over_cap']} 份超过上限没列）" if inv["over_cap"] else "") + "。")
    order = {"skill": 0, "workflow": 1, "guidance": 2, "docs": 3, "readme": 4}
    rows = sorted(repo, key=lambda x: (RANK[x["used"]] >= 3, order[x["kind"]], -len(x["overlap"]), x["path"]))
    out += ["", "| 类别 | 资源 | 写着什么（出处） | 会话里 | 和你的话重合 |", "|---|---|---|---|---|"] if rows else []
    for x in rows:
        late = " ⚠切点后才提交" if x.get("after_cut") else (" ⚠未提交" if x.get("after_cut") is None and inv["git"] and "after_cut" in x else "")
        pos = x.get("at") or x.get("mentioned_at") or ""
        out.append(f"| {x['kind']} | `{x['path']}`{late} | {x['says'].replace('|', '｜')[:120]}（{x['cite']}） | "
                   f"{USED_WORD[x['used']]}{' ' + pos if pos else ''} | {'、'.join(x['overlap'])} |")
    sess = [x for x in inv["items"] if x["scope"] == "session"]
    used = [x for x in sess if not x.get("listed_only") or RANK[x["used"]] >= 3]
    listed = sorted([x for x in sess if x not in used], key=lambda x: -len(x["overlap"]))
    if used:
        out += ["", "## 会话里用到或点名的 skill（仓库外的也算）", "", "| skill | 写着什么 | 会话里 |", "|---|---|---|"]
        out += [f"| {x['name']} | {x['says'].replace('|', '｜')[:120]} | {USED_WORD[x['used']]} {x.get('at') or x.get('mentioned_at') or ''} |"
                for x in used]
    if listed:
        top = [x for x in listed if x["overlap"]][:10]
        out += ["", f"## 只在技能清单里出现、没被加载的 skill：{len(listed)} 个"]
        out += [f"- {x['name']}：{x['says'][:100]}（重合：{'、'.join(x['overlap'])}）" for x in top]
        rest = [x["name"] for x in listed if x not in top]
        if rest:
            out.append(f"- 其余：{', '.join(rest[:60])}{' …' if len(rest) > 60 else ''}")
    out += ["", "下一步（观察者）：读「没出现 / 只被提到」里和这次工作可能相关的，尤其 skill 和工作流里写的流程、阶段、原则。",
            "相关的写进地图 `resources`（relevant: true）；里面写着的长期目的当「你说的」证据建目标，出处写 file:路径:行。"]
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sessions", nargs="+", help="ALIAS=path or path")
    ap.add_argument("--repo", help="the repository, or an export of it as it was at the cut")
    ap.add_argument("--until", default="", help="ALIAS:LINE[,ALIAS:LINE]: the cut; later lines are ignored")
    ap.add_argument("--ledger", help="ledger.json: the user's own words, for the overlap hint")
    ap.add_argument("--out", help="directory for inventory.json + inventory.md (else print the md)")
    a = ap.parse_args()
    sessions = []
    for i, spec in enumerate(a.sessions):
        alias, _, p = spec.partition("=") if "=" in spec else (chr(65 + i), "", spec)
        path = Path(p).expanduser()
        if not path.exists():
            sys.exit(f"no such session: {path}")
        sessions.append((alias, path))
    until = {}
    for part in filter(None, a.until.split(",")):
        al, _, ln = part.strip().rpartition(":")
        until[al or sessions[0][0]] = int(ln)
    repo = Path(a.repo).expanduser().resolve() if a.repo else None
    if repo and not repo.is_dir():
        sys.exit(f"no such repo: {repo}")
    inv = build(repo, sessions, until, Path(a.ledger) if a.ledger else None)
    md = to_md(inv)
    if a.out:
        o = Path(a.out)
        o.mkdir(parents=True, exist_ok=True)
        (o / "inventory.json").write_text(json.dumps(inv, ensure_ascii=False, indent=1))
        (o / "inventory.md").write_text(md)
        n = sum(1 for x in inv["items"] if x["scope"] == "repo" and RANK[x["used"]] < 3)
        if inv["repo_note"]:
            print(inv["repo_note"], file=sys.stderr if inv["repo_source"] == "none" else sys.stdout)
        print(f"wrote {o / 'inventory.md'}: {len(inv['items'])} resources, {n} in the repo never read or loaded")
    else:
        print(md)


if __name__ == "__main__":
    main()
