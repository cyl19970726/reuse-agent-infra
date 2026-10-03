#!/usr/bin/env python3
"""Check a task map (map.json). v4.2 keeps only the checks that guard a known failure:

  1. structure      required fields, enums, unique ids, every position resolves, the answer carries the
                    items its type needs (one table in SKILL.md); resources were taken stock of, and every
                    unused skill / workflow in inventory.json (next to map.json) was judged relevant or not;
                    a goal that drives the work but is only inferred has a question for the user
  2. goals          every request in the ledger is in the map and belongs to a goal
  3. clarify        a request asking the agent to find out what the user wants is closed only by the USER
                    (a later user line that says more than "继续 / 好 / ok"); --apply turns a false "done" into "partial"
  4. claims         "how far the work got" backed only by agent prose or a compaction summary is a claim;
                    quotes that do not match the raw line are claims too; --apply marks them basis="说法"
  5. first screen   no map ids or "session:line" positions in the answer text (only ids this map defines)
  and the main-line rule: "waiting" only when the CURRENT main-line step is blocked on the user's decision
  and the work has actually stopped.

  map_check.py map.json [--ledger ledger.json] [--verify [--all]] [--apply]

Evidence positions: A:123 / A:10-40 (a session line), file:<path>:<line> (a repository file, relative to the
map's "repo"), site:<n> (item n of "site", or its id). Exit 1 on errors or quote mismatches (after --apply the
mismatches are recorded, exit 0). Warnings never fail. Schema: references/map-schema.md.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from session_events import open_session_text  # noqa: E402

REF = re.compile(r"^([A-Za-z][\w\-]*):L?(\d+)(?:-L?(\d+))?$")
FILE_REF = re.compile(r"^file:(.+):(\d+)(?:-(\d+))?$")
SITE_REF = re.compile(r"^site:([\w\-]+)$")
# the five answers and what each first screen must carry (SKILL.md, one table). An item is met by a point
# with exactly that label, or, for the structural names, by that part of the answer being filled.
MUST = {"progress": ["主线", "第一件事", "现场核对"],
        "handoff": ["主线", "第一件事", "现场核对", "已失效"],
        "distill": ["insights"],
        "audit": ["主因", "主因链", "本该在哪停"],
        "process": ["flows", "table", "watch"]}
STRUCT = {"flows", "table", "watch", "insights", "主线", "现场核对"}
APPROVAL = re.compile(r"^(好|好的|可以|行|是的|对|嗯|继续|开始|ok|OK|yes|go|可以开始了?|按照?这个来?做?|很棒|没问题)[。！!,，\s]*$")
ENUM = {
    "requests.kind": {"request", "correction", "question", "approval", "interjection", "answer", "command", "interrupt"},
    "requests.status": {"done", "partial", "open", "dropped", "superseded", "n/a"},
    "goals.level": {"purpose", "stage", "task"},
    "goals.status": {"done", "partial", "open", "dropped", "superseded"},
    "goals.from": {"said", "inferred", "confirmed"},
    "problems.resolved": {"yes", "no", "partial"},
    "basis": {"物证", "说法"},
    "assets.status": {"current", "stale", "superseded", "missing"},
    "alignment.state": {"on_track", "stuck", "drifted", "waiting"},
    "plan.state": {"done", "doing", "todo", "dropped", "changed", "waiting"},
    "insights.state": {"verified", "candidate", "superseded"},
    "resources.used": {"auto", "loaded", "read", "mentioned", "no"},
    "ext.place": {"top", "process", "problems", "end"},
}
CLAIM_ROLES = {"assistant", "summary"}
MAP_DIR = Path(".")
errors: list[str] = []
warns: list[str] = []


def err(m):
    errors.append(m)


def warn(m):
    warns.append(m)


def iter_nodes(m):
    """(layer, node) for every node that can carry refs / quote / basis."""
    for k in ("requests", "goals", "process", "assets", "problems", "next", "resources", "origin"):
        for n in m.get(k) or []:
            yield k, n
    for g in m.get("goals") or []:
        if isinstance(g.get("ai"), dict):
            yield "goals.ai", g["ai"]
    for side in ("user", "agent"):
        for n in (m.get("collab") or {}).get(side) or []:
            yield f"collab.{side}", n
    for k, n in (m.get("headline") or {}).items():
        if isinstance(n, dict):
            yield f"headline.{k}", n
    for n in (m.get("plan") or {}).get("steps") or []:
        yield "plan", n
    for b in m.get("ext") or []:
        for n in b.get("items") or []:
            yield f"ext.{b.get('by', '?')}", n
    ans = m.get("answer") or {}
    for n in ans.get("points") or []:
        yield "answer", n
    for n in ans.get("insights") or []:
        yield "answer.insights", n


def refs_of(node) -> list[str]:
    out = [node[k] for k in ("at", "span", "closed_at") if isinstance(node.get(k), str)]
    for key in ("refs", "basis_refs", "also", "chain", "confirm_refs"):
        for r in node.get(key) or []:
            out.append(r if isinstance(r, str) else r.get("at", ""))
    return [r for r in out if r]


def parse_ref(r):
    mm = REF.match((r or "").strip())
    return (mm.group(1), int(mm.group(2)), int(mm.group(3) or mm.group(2))) if mm else None


def flat_strings(x, out):
    if isinstance(x, str):
        out.append(x)
    elif isinstance(x, dict):
        for v in x.values():
            flat_strings(v, out)
    elif isinstance(x, list):
        for v in x:
            flat_strings(v, out)
    return out


def squash(s: str) -> str:
    """Compare text, not formatting: drop whitespace, markup tags and escapes of nested JSON."""
    s = (s or "").replace("\\n", " ").replace("\\t", " ").replace('\\"', '"')
    s = re.sub(r"</?[A-Za-z_][\w\-]*(?:\s[^<>]{0,120})?>", "", s)
    return re.sub(r"\s+", "", s).lower()


def line_role(d) -> str:
    """Who wrote a raw record: user / assistant (prose) / action / tool / summary / other.
    Only assistant prose and compaction summaries are claims; the rest is evidence."""
    if not isinstance(d, dict):
        return "other"
    t = d.get("type")
    if t == "assistant":  # Claude Code
        c = (d.get("message") or {}).get("content")
        return "action" if isinstance(c, list) and any(isinstance(x, dict) and x.get("type") == "tool_use" for x in c) else "assistant"
    if t == "user":
        if d.get("isCompactSummary"):
            return "summary"
        c = (d.get("message") or {}).get("content")
        return "tool" if isinstance(c, list) and any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c) else "user"
    p = d.get("payload") if isinstance(d.get("payload"), dict) else {}
    if t == "compacted":  # Codex
        return "summary"
    if t == "response_item":
        pt = p.get("type")
        if pt == "message":
            return {"user": "user", "assistant": "assistant"}.get(p.get("role"), "other")
        if pt == "reasoning":
            return "assistant"
        if pt in ("function_call", "custom_tool_call", "local_shell_call"):
            return "action"
        if pt in ("function_call_output", "custom_tool_call_output"):
            return "tool"
    if t == "event_msg":
        return {"agent_message": "assistant", "agent_reasoning": "assistant", "user_message": "user"}.get(p.get("type"), "tool")
    return "other"


def read_lines(path: Path, wanted: set[int]) -> dict[int, dict]:
    """line -> {"text": squashed text, "role": role, "user": what the user typed (user lines only)}"""
    got = {}
    if not wanted:
        return got
    top = max(wanted)
    with open_session_text(path) as fh:
        for n, raw in enumerate(fh, 1):
            if n > top:
                break
            if n not in wanted:
                continue
            try:
                d = json.loads(raw)
            except Exception:
                got[n] = {"text": squash(raw), "role": "other", "user": None}
                continue
            role, typed = line_role(d), None
            att = d.get("attachment") if isinstance(d.get("attachment"), dict) else {}
            if d.get("type") == "attachment" and att.get("type") == "queued_command":
                typed = str(att.get("prompt") or "").strip()  # a mid-turn interjection is the user too
            elif role == "user":
                c = (d.get("message") or {}).get("content") if d.get("type") == "user" else None
                if c is None:
                    p = d.get("payload") if isinstance(d.get("payload"), dict) else {}
                    c = p.get("content") if p.get("type") == "message" else p.get("message")
                if isinstance(c, list):
                    c = " ".join(str(x.get("text", "")) for x in c if isinstance(x, dict))
                typed = str(c or "").strip()
            got[n] = {"text": squash(" ".join(flat_strings(d, []))), "role": role, "user": typed}
    return got


def found(says: str, body: str) -> bool:
    """A quote matches when its first 40 chars occur in the lines; masked secrets are matched piece by piece."""
    if "[已遮蔽]" in says:
        return all(x in body for x in (squash(p)[:40] for p in says.split("[已遮蔽]")) if len(x) >= 4)
    needle = squash(says)[:40]
    return not needle or needle in body


def expand_ids(items) -> list[str]:
    """Goals may list requests as ranges: ["R1-R40", "R52"]."""
    out = []
    for x in items or []:
        mm = re.match(r"^R(\d+)\s*[-–]\s*R?(\d+)$", str(x))
        out += [f"R{i}" for i in range(int(mm.group(1)), int(mm.group(2)) + 1)] if mm else [str(x)]
    return out


def folded_ids(m) -> dict[str, str]:
    return {x: r.get("id") for r in m.get("requests") or [] for x in r.get("also_ids") or []}


def check_enum(key, v, who):
    if v is not None and v not in ENUM[key]:
        err(f"{who}: {key.split('.')[-1]}={v} 不在 {sorted(ENUM[key])}")


# --------------------------------------------------------------------------- 1. structure

def check_structure(m) -> dict:
    for k in ("schema", "question", "sessions", "headline", "requests", "goals", "next", "answer"):
        if k not in m:
            err(f"缺少顶层字段 {k}")
    hl = m.get("headline") or {}
    for k in ("want", "progress", "problem", "next"):
        if not str((hl.get(k) or {}).get("text", "")).strip():
            err(f"地图四格缺 headline.{k}.text（你要的 / 做到哪 / 最大问题 / 下一步）")
    ids: dict[str, str] = {}
    for layer, n in iter_nodes(m):
        i = n.get("id")
        if i:
            if i in ids:
                err(f"重复 id {i}（{ids[i]} 与 {layer}）")
            ids[i] = layer
        check_enum("basis", n.get("basis"), i or layer)
    for r in m.get("requests") or []:
        if not r.get("at"):
            err(f"{r.get('id')}: 请求缺 at（会话:行）")
        check_enum("requests.kind", r.get("kind"), r.get("id"))
        check_enum("requests.status", r.get("status"), r.get("id"))
    for g in m.get("goals") or []:
        gid = g.get("id")
        check_enum("goals.level", g.get("level"), gid)
        check_enum("goals.status", g.get("status"), gid)
        if g.get("from") is None:
            err(f"{gid}: 目标缺 from（said 你说的 / inferred 推断的 / confirmed 你确认过的）")
        check_enum("goals.from", g.get("from"), gid)
        if g.get("from") == "confirmed" and not g.get("confirm_refs"):
            err(f"{gid}: confirmed 要写 confirm_refs，指向你确认的那一行")
        if g.get("from") == "said" and not (g.get("requests") or g.get("refs")):
            warn(f"{gid}: said 要有出处：请求、发起查看时的话（O:行），或写着这个意图的资源（file:路径:行）")
    for p in m.get("problems") or []:
        check_enum("problems.resolved", p.get("resolved"), p.get("id"))
        if (p.get("spin") or {}).get("steps") and p.get("not_spin"):
            err(f"{p.get('id')}: 同时标了 spin 和 not_spin")
    for x in m.get("assets") or []:
        check_enum("assets.status", x.get("status"), x.get("id"))
    for x in m.get("resources") or []:
        check_enum("resources.used", x.get("used"), x.get("what") or x.get("path"))
    for b in m.get("ext") or []:
        if not (b.get("by") and b.get("title")):
            err("ext 块要有 by（哪个外部 skill 写的）和 title")
        check_enum("ext.place", b.get("place"), f"ext「{b.get('title')}」")
    folded = folded_ids(m)
    known = set(ids) | set(folded)
    for g in m.get("goals") or []:
        for x in g.get("requests") or []:
            if not any(r in known for r in expand_ids([x])):
                err(f"{g.get('id')}: 引用了不存在的请求 {x}")
    for k in (m.get("plan") or {}).get("steps") or []:
        check_enum("plan.state", k.get("state"), k.get("id"))
        for r in ([k["goal"]] if k.get("goal") else []) + list(k.get("via") or []):
            if r not in known:
                err(f"{k.get('id')}: 引用了不存在的 {r}")
    for owner, key in (("problems", "same_root"), ("process", "problems"), ("next", "for")):
        for n in m.get(owner) or []:
            for r in n.get(key) or []:
                if r not in known:
                    err(f"{n.get('id') or str(n.get('do', ''))[:20]}: 引用了不存在的 {r}")
    for p in m.get("problems") or []:
        for r in (p.get("spin") or {}).get("steps") or []:
            if r not in known:
                err(f"{p.get('id')}: spin 引用了不存在的 {r}")
    qs = m.get("questions") or []
    if len(qs) > 3:
        err(f"反问 {len(qs)} 个，最多 3 个")
    for q in qs:
        if not q.get("default"):
            err(f"反问「{q.get('q', '')[:20]}」缺没人答时的默认 default")
        bad = [t for t in q.get("for") or [] if t not in MUST]
        if bad:
            err(f"反问「{q.get('q', '')[:20]}」的 for 有不认识的答案类型 {bad}")
    check_resources(m)
    check_inferred_goals(m)
    check_answer(m, known)
    check_alignment(m)
    return ids


def check_resources(m):
    """The stock of ready-made resources is the observer's call, but every candidate must have been judged:
    each skill or workflow inventory.py found unused (and anything unused that shares words with the user),
    listed in "resources" with relevant true or false."""
    inv_p = MAP_DIR / "inventory.json"
    if "resources" not in m:
        warn("没清点现成资源：跑 inventory.py，把和这次工作直接相关的写进 resources（没有相关的就写 []）")
    for x in m.get("resources") or []:
        if not isinstance(x.get("relevant"), bool):
            warn(f"资源「{x.get('what') or x.get('path')}」没判 relevant（true 相关 / false 看过、无关）")
    if not inv_p.exists():
        if "resources" in m:
            warn(f"地图目录里没有 inventory.json：resources 没法对账（inventory.py --out {MAP_DIR}）")
        return
    inv = json.loads(inv_p.read_text())
    if inv.get("repo_source") == "none":
        warn("inventory.json 说资源清点不完整（没给仓库，会话里也没有工作目录）：给 --repo 重跑")
    have = {str(v) for x in m.get("resources") or [] for v in (x.get("path"), x.get("what"), x.get("name")) if v}

    def listed(it):  # by path (relative or absolute), or the skill's name inside "what" / "path"
        name, path = it.get("name"), it.get("path")
        return any((path and (h == path or h.endswith(path) or path.endswith(h))) or (name and name in h) for h in have)
    cand = []
    for it in inv.get("items") or []:
        if it.get("used") not in ("no", "mentioned"):
            continue
        repo_kind = it.get("scope") == "repo" and it.get("kind") in ("skill", "workflow")
        shares = len(it.get("overlap") or []) >= (1 if it.get("scope") == "repo" else 2)
        if (repo_kind or shares) and not listed(it):
            cand.append(it.get("name") or it.get("path"))
    if cand:
        warn(f"inventory.json 里有 {len(cand)} 份没用上、可能相关的资源没进 resources（{'、'.join(cand[:6])}{'…' if len(cand) > 6 else ''}）："
             "读一眼，逐条写进 resources，relevant 填 true 或 false")


def check_inferred_goals(m):
    """A long-term purpose or a stage goal the work runs on, known only by inference, goes to the user as a
    question (with a default) — else the user never sees that the whole answer rests on a guess."""
    qs = m.get("questions") or []
    for g in m.get("goals") or []:
        if g.get("from") != "inferred" or g.get("level") not in ("purpose", "stage") or g.get("status") in ("dropped", "superseded"):
            continue
        ask = str(g.get("ask") or "")
        if not any(q.get("goal") == g.get("id") or (ask and ask[:12] in str(q.get("q", ""))) for q in qs):
            warn(f"{g.get('id')}: 驱动工作的{'长期目的' if g.get('level') == 'purpose' else '阶段目标'}只是推断的，"
                 "却没有对应的反问：在 questions 里加一条（写 goal: " + str(g.get("id")) + "，带默认）")


def check_answer(m, known):
    a = m.get("answer")
    if not isinstance(a, dict):
        return
    t = a.get("type")
    if t not in MUST:
        err(f"answer.type={t!r} 不在 {sorted(MUST)}（进度 / 接手 / 提炼 / 审计 / 改流程）")
    if not str(a.get("lead", "")).strip():
        err("answer.lead 为空：第一屏第一段，一到三句直接回答问题")
    labels = {str(p.get("label", "")).strip() for p in a.get("points") or []}
    have = {"flows": len(a.get("flows") or []) >= 2, "table": bool((a.get("table") or {}).get("rows")),
            "watch": bool(a.get("watch")), "insights": bool(a.get("insights")),
            "现场核对": bool((m.get("site") or {}).get("items")),
            "主线": bool(((m.get("headline") or {}).get("alignment") or {}).get("state"))}
    for x in list(a.get("must") or []) or MUST.get(t, []):
        if x not in labels and not (x in STRUCT and have.get(x)):
            err(f"answer（{t}）缺必备项「{x}」：写一个 label 为「{x}」的 point"
                + ("，或填 answer 的同名部分" if x in STRUCT else "") + ("（现状、目标两张）" if x == "flows" else ""))
    for n in a.get("insights") or []:
        check_enum("insights.state", n.get("state"), f"insight「{str(n.get('text'))[:16]}」")
        if not n.get("from"):
            err(f"insight「{str(n.get('text'))[:16]}」缺起因 from（问题或请求的编号）")
        for r in n.get("from") or []:
            if r not in known:
                err(f"insight「{str(n.get('text'))[:16]}」的起因 {r} 不存在")
    # 5. first screen: no ids this map defines, no session positions. "Kimi K2.5" is a model name, not step K2.
    aliases = {str(s.get("alias")) for s in m.get("sessions") or []}
    first = [a.get("lead", "")] + [p.get("text", "") for p in a.get("points") or []] + list(a.get("missing") or [])
    first += list(a.get("watch") or []) + [x.get("text", "") for x in a.get("insights") or []]
    first += [c for r in (a.get("table") or {}).get("rows") or [] for c in r]
    first += [x if isinstance(x, str) else x.get("text", "") for f in a.get("flows") or [] for x in f.get("steps") or []]
    hits = set()
    for s in map(str, first):
        for tok in re.findall(r"(?<![A-Za-z0-9_.])([A-Z]\d+)(?![\w.])", s):
            if tok in known:
                hits.add(tok)
        for al, ln in re.findall(r"(?<![A-Za-z0-9_])([A-Za-z][\w\-]*):(\d+)(?![\w.])", s):
            if al in aliases:
                hits.add(f"{al}:{ln}")
    if hits:
        warn(f"首屏文字里有编号或位置（{'、'.join(sorted(hits)[:6])}）：写人话，编号和位置放进 refs（悬停可见）")


def check_alignment(m):
    """waiting = the CURRENT main-line step is blocked on the user's decision and the work has stopped.
    A step elsewhere in the plan waiting for a list while the work goes on is not waiting."""
    al = (m.get("headline") or {}).get("alignment")
    if not isinstance(al, dict) or not al.get("state"):
        err("缺 headline.alignment（on_track 在主线上 / stuck 陷在局部 / drifted 偏离了目标 / waiting 停在等你拍板）")
        return
    check_enum("alignment.state", al["state"], "headline.alignment")
    if not str(al.get("text", "")).strip():
        err("headline.alignment 缺 text：一句话说清为什么这么判断")
    steps = (m.get("plan") or {}).get("steps") or []
    if not steps:
        return
    by = {k.get("id"): k for k in steps}
    now = by.get((m.get("plan") or {}).get("now"))
    doing = [k.get("id") for k in steps if k.get("state") == "doing"]
    waiting = [k.get("id") for k in steps if k.get("state") == "waiting"]
    cur_waits = (now.get("state") == "waiting") if now else (bool(waiting) and not doing)
    if al["state"] == "waiting" and not cur_waits:
        warn("主线判了 waiting，但当前那一步没在等你拍板（" + (f"{'、'.join(doing)} 还在做" if doing else "计划里没有 waiting 的一步")
             + "）：只有当前主线那一步被你的决定卡住、工作停着时才判 waiting")
    if al["state"] == "on_track" and cur_waits:
        warn(f"当前那一步在等你拍板（{now.get('id') if now else '、'.join(waiting)}），计划里也没有别的步骤在做：工作停着，主线判 waiting")


# --------------------------------------------------------------------------- positions, quotes, claims, clarify

def resolve(raw: str) -> Path:
    p = Path(str(raw)).expanduser()
    return p if p.is_absolute() or p.exists() else MAP_DIR / p


def file_path(m, raw: str) -> Path:
    p = Path(raw).expanduser()
    return p if p.is_absolute() else resolve(m.get("repo") or ".") / p


def check_refs(m, verify: bool, check_all: bool):
    sess = {s.get("alias"): resolve(s.get("path", "")) for s in m.get("sessions") or []}
    nlines = {}
    for a, p in sess.items():
        if not p.exists():
            err(f"会话 {a} 路径不存在：{p}")
        else:
            with open_session_text(p) as fh:
                nlines[a] = sum(1 for _ in fh)
    items = (m.get("site") or {}).get("items") or []
    sids = {str(i) for i in range(1, len(items) + 1)} | {str(x.get("id")) for x in items if x.get("id")}
    for layer, n in iter_nodes(m):
        who = n.get("id") or layer
        for r in refs_of(n):
            fm, sm = FILE_REF.match(r), SITE_REF.match(r)
            if fm:
                fp = file_path(m, fm.group(1))
                if not fp.exists():
                    err(f"{who}: {r} 的文件不存在：{fp}")
                elif int(fm.group(3) or fm.group(2)) > len(fp.read_text(errors="ignore").splitlines()):
                    err(f"{who}: {r} 超出文件行数")
            elif sm:
                if sm.group(1) not in sids:
                    err(f"{who}: {r} 不在现场核对 site.items 里")
            elif not (pr := parse_ref(r)):
                err(f"{who}: 看不懂的证据位置 {r!r}（A:123、A:10-40、file:<路径>:<行>、site:<序号>）")
            elif pr[0] not in sess:
                err(f"{who}: {r} 的会话简称 {pr[0]} 不在 sessions 里")
            elif pr[0] in nlines and (pr[2] > nlines[pr[0]] or pr[1] > pr[2]):
                err(f"{who}: {r} 超出会话 {pr[0]} 的行数 {nlines[pr[0]]}")
    if not verify:
        return [], [], []
    quotes = []  # (layer, node, position, excerpt)
    for layer, n in iter_nodes(m):
        if n.get("basis") == "说法":
            continue
        if n.get("quote") and refs_of(n):
            quotes.append((layer, n, refs_of(n)[0], n["quote"]))
        for r in n.get("refs") or []:
            if isinstance(r, dict) and r.get("says") and r.get("at"):
                quotes.append((layer, n, r["at"], r["says"]))
    must = [x for x in quotes if x[0] == "requests"]
    rest = [x for x in quotes if x[0] != "requests"]
    random.seed(len(quotes))
    k = len(rest) if check_all else min(len(rest), max(3, math.ceil(0.2 * len(rest))))
    sample = must + random.sample(rest, k)
    # "how far the work got" (progress box, process steps, artifacts) needs more than the agent saying so
    world = [(layer, n) for layer, n in iter_nodes(m)
             if (layer in ("headline.progress", "process", "assets") or layer.startswith("ext."))
             and n.get("basis") != "说法" and not (n.get("basis") == "物证" and n.get("basis_note"))]
    want: dict[str, set[int]] = {}

    def need(at):
        pr = parse_ref(at)
        if pr and pr[0] in sess:
            want.setdefault(pr[0], set()).update(range(pr[1], min(pr[2], pr[1] + 60) + 1))
    for _, _, at, _ in sample:
        need(at)
    for _, n in world:
        for r in refs_of(n):
            need(r)
    clar = [n for n in m.get("requests") or [] if n.get("clarify") and n.get("status") == "done"]
    for n in clar:
        for r in n.get("basis_refs") or []:
            need(r if isinstance(r, str) else r.get("at", ""))
    text = {a: read_lines(sess[a], w) for a, w in want.items()}

    def lines_of(at):
        a, s, e = parse_ref(at)
        return {i: text.get(a, {}).get(i) for i in range(s, min(e, s + 60) + 1) if text.get(a, {}).get(i)}
    bad = []
    for layer, n, at, says in sample:
        fm = FILE_REF.match(at or "")
        if fm:
            fp = file_path(m, fm.group(1))
            if fp.exists():
                ls = fp.read_text(errors="ignore").splitlines()
                if not found(says, squash(" ".join(ls[int(fm.group(2)) - 1:int(fm.group(3) or fm.group(2))]))):
                    bad.append((layer, n))
                    print(f"  对不上 {n.get('id') or layer} @ {at}：「{says[:40]}」")
            continue
        if not parse_ref(at) or parse_ref(at)[0] not in text:
            continue
        if not found(says, "".join(x["text"] for x in lines_of(at).values())):
            bad.append((layer, n))
            print(f"  对不上 {n.get('id') or layer} @ {at}：「{says[:40]}」")
    print(f"引用核对（{'全部' if check_all else '抽查'}）：{len(sample)}/{len(quotes)} 条，对不上 {len(bad)} 条")
    claims = []
    for layer, n in world:
        rs = refs_of(n)
        if any(FILE_REF.match(r) or SITE_REF.match(r) for r in rs):
            continue  # a repository file or a site check backs it
        roles = {x["role"] for r in rs if parse_ref(r) for x in lines_of(r).values()}
        if roles and roles <= CLAIM_ROLES:
            claims.append((layer, n))
    print(f"做到哪：{len(world)} 个节点，其中 {len(claims)} 个只引用了 agent 自述或压缩摘要")
    unclosed = []
    for n in clar:
        own = parse_ref(n.get("at") or "")
        said, go_on = [], []
        for r in n.get("basis_refs") or []:
            pr = parse_ref(r if isinstance(r, str) else r.get("at", ""))
            if not pr or pr[0] not in text:
                continue
            for ln, x in lines_of(f"{pr[0]}:{pr[1]}-{pr[2]}").items():
                if x["user"] is None or (own and pr[0] == own[0] and ln <= own[1]):
                    continue
                (go_on if len(x["user"]) <= 24 and APPROVAL.match(x["user"]) else said).append(f"{pr[0]}:{ln}")
        if not said:
            n["_why"] = (f"依据里你那几行（{'、'.join(go_on)}）只是让继续，不是确认 agent 的理解" if go_on
                         else "依据里没有你在这条请求之后说的话")
            unclosed.append(n)
    return bad, claims, unclosed


# --------------------------------------------------------------------------- 2. every request belongs to a goal

def check_requests(m, ledger: Path | None):
    listed = {r for g in m.get("goals") or [] for r in expand_ids(g.get("requests"))}
    no_goal = [r.get("id") for r in m.get("requests") or []
               if r.get("status") != "n/a" and r.get("kind") != "command" and not r.get("goal") and r.get("id") not in listed]
    if no_goal:
        err(f"{len(no_goal)} 条请求没归到目标（{'、'.join(no_goal[:8])}{'…' if len(no_goal) > 8 else ''}）："
            "写 goal，或在目标的 requests 里列出（可以写区间 R10-R40）")
    if ledger:
        led = json.loads(ledger.read_text())
        have = {x for r in m.get("requests") or [] for x in [r.get("at")] + list(r.get("also") or [])}
        human = [r for r in led.get("requests") or [] if r.get("source") != "command" and not r.get("folded_into")]
        miss = [r for r in human if r["ref"] not in have]
        print(f"请求覆盖：账本 {len(human)} 条人话，地图收了 {len(human) - len(miss)} 条")
        for r in miss[:10]:
            warn(f"账本 {r['ref']} 没进地图：{r['text'][:50]}")
        if len(miss) > 10:
            warn(f"……还有 {len(miss) - 10} 条没进地图")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("map")
    ap.add_argument("--ledger", help="ledger.json from ledger.py: every human request must be in the map")
    ap.add_argument("--verify", action="store_true", help="read the cited raw lines (quotes, claims, clarify)")
    ap.add_argument("--all", action="store_true", help="check every quote, not a sample")
    ap.add_argument("--apply", action="store_true", help="write the downgrades back into map.json")
    a = ap.parse_args()
    global MAP_DIR
    mp = Path(a.map)
    MAP_DIR = mp.resolve().parent
    m = json.loads(mp.read_text())
    check_structure(m)
    check_requests(m, Path(a.ledger) if a.ledger else None)
    bad, claims, unclosed = check_refs(m, a.verify, a.all)
    for n in unclosed:
        why = n.pop("_why")
        if a.apply:
            n["status"] = "partial"
            n["basis_note"] = f"这条请求要 agent 弄清你的意图，只能由你确认来闭合：{why}"
        else:
            warn(f"{n.get('id')}: 要 agent 弄清意图的请求写了 done，但{why}（--apply 改成 partial）")
    for layer, n in claims:
        if a.apply:
            n["basis"], n["basis_note"] = "说法", "引用的位置全是 agent 自述或压缩摘要，没有工具结果、文件或用户原话佐证"
        else:
            warn(f"{n.get('id') or layer}: 做到哪只引用了 agent 自述/压缩摘要，只是说法（--apply 标 basis=说法）")
    if a.apply and (bad or claims or unclosed):
        for _, n in bad:
            n["basis"], n["basis_note"] = "说法", "引用核对对不上原文，降为说法"
        mp.write_text(json.dumps(m, ensure_ascii=False, indent=1))
        print(f"已标「说法」{len(bad) + len(claims)} 处（原文对不上 {len(bad)}，只有 agent 自述 {len(claims)}），"
              f"弄清意图的请求改 partial {len(unclosed)} 条，写回 {mp}")
        bad = []
    for w in warns:
        print("提醒  " + w)
    for e in errors:
        print("错误  " + e)
    ok = not errors and not bad
    print("map_check: " + ("通过" if ok else f"未通过（错误 {len(errors)}，引用对不上 {len(bad)}）") + f"，提醒 {len(warns)}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
