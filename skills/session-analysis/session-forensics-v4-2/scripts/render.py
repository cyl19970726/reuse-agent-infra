#!/usr/bin/env python3
"""Render a task map (map.json) into report.html (for people) and map.md (for agents).

  render.py map.json [--out DIR]      writes DIR/report.html and DIR/map.md (default: next to map.json)

The first screen is the ANSWER to this map's question (map.json "answer"), shaped by its type (progress,
handoff, distill, audit, process); ready-made resources that bear on the work but went unused are named in it;
what is still missing follows. The map behind the answer comes after in three folded sections. No external libraries, no network: one
self-contained HTML file that reads in light and dark themes and at phone width. Times are shown in
this machine's time zone. Layout and writing rules: references/layout.md.
"""
from __future__ import annotations

import argparse
import html
import json
import re
from datetime import datetime
from pathlib import Path

STATUS = {"current": ("当前有效", "ok"), "stale": ("已过时", "warn"), "missing": ("找不到", "bad"),
          "done": ("完成", "ok"), "partial": ("部分", "warn"), "open": ("未做", "bad"),
          "dropped": ("放下了", "mute"), "superseded": ("被替代", "mute"), "n/a": ("—", "mute"),
          "yes": ("已解决", "ok"), "no": ("未解决", "bad")}
KIND = {"request": "请求", "correction": "纠正", "question": "追问", "approval": "批准", "interjection": "中途插话",
        "answer": "回答", "command": "命令", "interrupt": "打断"}
LEVEL = {"purpose": "长期目的", "stage": "阶段目标", "task": "本轮任务"}
ALIGN = {"on_track": ("在主线上", "ok"), "stuck": ("陷在局部", "warn"), "drifted": ("偏离了目标", "bad"),
         "waiting": ("停在等你拍板", "warn")}
ALIGN_WORDS = ["在主线上", "陷在局部", "偏离了目标", "偏离目标", "停在等你拍板", "主线"]
PLAN = {"done": ("完成", "ok"), "doing": ("正在做", "now"), "todo": ("还没做", "mute"), "dropped": ("放下了", "mute"),
        "changed": ("改了", "warn"), "waiting": ("等你拍板", "warn")}
FROM = {"said": ("你说的", "ok"), "inferred": ("agent 推断的", "warn"), "confirmed": ("你确认过的", "ok")}
INSIGHT = {"verified": ("已验证", "ok"), "candidate": ("候选", "warn"), "superseded": ("被取代", "mute")}
USED = {"auto": ("自动加载", "ok"), "loaded": ("加载了", "ok"), "read": ("读过", "ok"), "mentioned": ("只被提到", "warn"),
        "no": ("没用上", "bad")}
UNUSED = ("mentioned", "no")
# answers about the state of the work: questions without a "for" reach their first screen
WORK_STATE = {"progress", "handoff", "audit"}
ANSWER_NAME = {"progress": "进度", "handoff": "接手", "distill": "提炼", "audit": "定责", "process": "改流程"}
ROOT_KIND = {"input": "用户输入", "judgment": "agent 判断", "norm": "规范", "tool": "工具"}
OWN_STATUS = {"correction", "interjection", "interrupt"}
FOLD_AT = 30  # more requests than this: each goal shows its open / changed ones, the rest folds
REF = re.compile(r"^([A-Za-z][\w\-]*):L?(\d+)(?:-L?(\d+))?$")


def e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def fmt_time(s) -> str:
    """ISO time -> local 'MM-DD HH:MM' (times with a zone are converted; plain ones are taken as local)."""
    s = str(s or "").strip()
    if not s:
        return ""
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s
    if d.tzinfo is not None:
        d = d.astimezone()
    return d.strftime("%m-%d %H:%M")


def folded_ids(m) -> dict:
    return {x: r.get("id") for r in m.get("requests") or [] for x in r.get("also_ids") or []}


def id_label(m, x) -> str:
    """An id as the body shows it; a request folded into another one says so."""
    f = folded_ids(m)
    return f"{x}（已折进 {f[x]}）" if x in f else str(x)


def src(refs) -> str:
    """First-screen evidence: no ids or positions in the text, only a marker with them on hover."""
    refs = [r for r in refs if r]
    return f' <span class="src" title="依据：{e(", ".join(refs))}">依据</span>' if refs else ""


def refs_of(n: dict) -> list[str]:
    out = [n[k] for k in ("at", "span") if isinstance(n.get(k), str)]
    for k in ("refs", "basis_refs"):
        out += [r if isinstance(r, str) else r.get("at", "") for r in n.get(k) or []]
    return [r for r in out if r]


def expand_ids(items) -> list[str]:
    out = []
    for x in items or []:
        mm = re.match(r"^R(\d+)\s*[-–]\s*R?(\d+)$", str(x))
        out += [f"R{i}" for i in range(int(mm.group(1)), int(mm.group(2)) + 1)] if mm else [str(x)]
    return out


def goal_of(m) -> dict:
    """request id -> goal id (the request's own goal, else the goal that lists it)"""
    out = {}
    for g in m.get("goals") or []:
        for r in expand_ids(g.get("requests")):
            out.setdefault(r, g.get("id"))
    for r in m.get("requests") or []:
        if r.get("goal"):
            out[r.get("id")] = r["goal"]
    return out


def alias_of(ref) -> str:
    mm = REF.match(str(ref or ""))
    return mm.group(1) if mm else ""


def assets_table(m) -> str:
    xs = m.get("assets") or []
    if not xs:
        return ""
    rows = "".join(f'<tr><td class="id">{e(x.get("id"))}</td><td><b>{e(x.get("what"))}</b>{claim(x)}'
                   f'<div class="path">{e(x.get("path"))}</div></td><td>{badge(x.get("status"))}'
                   f'<div class="basis">{e(x.get("note"))}</div></td><td class="refs">{chips(refs_of(x))}</td></tr>' for x in xs)
    return ('<h3>关键产物</h3><div class="scroll"><table class="stack"><thead><tr><th>#</th><th>是什么 · 在哪</th><th>状态</th>'
            f'<th>位置</th></tr></thead><tbody>{rows}</tbody></table></div>')


def chips(refs) -> str:
    return "".join(f'<span class="ref">{e(r)}</span>' for r in refs if r)


def badge(key) -> str:
    label, cls = STATUS.get(key, (key or "?", "mute"))
    return f'<span class="b {cls}">{e(label)}</span>'


def claim(n: dict) -> str:
    if n.get("basis") == "说法":
        tip = n.get("basis_note") or "没有对上原文的证据，只是说法"
        return f'<span class="b claim" title="{e(tip)}">说法</span>'
    return ""


def home_short(p) -> str:
    """A report may be forwarded: show a session path from ~, never the account's home directory."""
    p = str(p or "")
    home = str(Path.home())
    if p.startswith(home + "/"):
        return "~" + p[len(home):]
    return re.sub(r"^/(?:Users|home)/[^/]+/", "~/", p)


def para(s) -> str:
    return "".join(f"<p>{e(x)}</p>" for x in str(s or "").split("\n") if x.strip())


# --------------------------------------------------------------------------- html parts

def alignment(m) -> tuple[str, str, str]:
    """(label, css class, text) of the main-line judgement. 'On track' with a big unresolved problem is
    shown in yellow: the process followed the plan, the result is off. The text does not repeat the label."""
    al = (m.get("headline") or {}).get("alignment") or {}
    lab, cls = ALIGN.get(al.get("state"), ("没判断", "mute"))
    if al.get("state") == "on_track" and any(p.get("resolved") in ("no", "partial") for p in m.get("problems") or []):
        lab, cls = "过程在主线，结果有偏差", "warn"
    txt = str(al.get("text") or "").strip()
    for w in sorted(ALIGN_WORDS, key=len, reverse=True):
        if txt.startswith(w):
            txt = txt[len(w):].lstrip("：:，,。 ;；—-")
            break
    return lab, cls, txt


def align_bar(m, first_screen=False) -> str:
    al = (m.get("headline") or {}).get("alignment") or {}
    lab, cls, txt = alignment(m)
    where = src(refs_of(al)) if first_screen else f'<span class="refs">{chips(refs_of(al))}</span>'
    return (f'<div class="align {cls}"><span class="lab">主线</span><b>{e(lab)}</b>'
            f'<span class="txt">{e(txt)}{claim(al)}{where if first_screen else ""}</span>{"" if first_screen else where}</div>')


def head_boxes(m) -> str:
    hl = m.get("headline") or {}
    names = [("want", "你要的"), ("progress", "做到哪"), ("problem", "最大问题"), ("next", "下一步")]
    out = []
    for k, label in names:
        b = hl.get(k) or {}
        out.append(f'<div class="box {k}"><div class="lab">{label}</div><div class="txt">{e(b.get("text"))}{claim(b)}</div>'
                   f'<div class="refs">{chips(refs_of(b))}</div></div>')
    return align_bar(m) + '<section class="four">' + "".join(out) + "</section>"


def cause_label(m, x) -> str:
    """What an id stands for, in words (the first screen shows words; the id goes to the hover)."""
    for k, f in (("problems", "title"), ("requests", "quote"), ("goals", "text"), ("process", "title")):
        for n in m.get(k) or []:
            if n.get("id") == x:
                t = re.sub(r"\s+", " ", str(n.get(f) or "")).strip()
                return t[:40] + ("…" if len(t) > 40 else "")
    fo = folded_ids(m)
    return cause_label(m, fo[x]) if x in fo else str(x)


def causes(m, ids) -> list:
    """[(label, [ids])]: one line per distinct cause; a folded request shares its parent's line."""
    out: dict[str, list] = {}
    for x in ids:
        out.setdefault(cause_label(m, x), []).append(id_label(m, x))
    return list(out.items())


CLOSING = set("）)」』”’》】]，。、；：！？,.;:!?")
OPENING = set("（(「『“‘《【[")


def wrap(t: str, n: int) -> list[str]:
    t = str(t or "")
    out, cur, w = [], "", 0
    for ch in t:
        cw = 1 if ord(ch) < 128 else 2
        if w + cw > n * 2 and ch not in CLOSING:  # a closing mark stays on its line, even one char over
            carry = cur[-1] if cur and cur[-1] in OPENING and len(cur) > 1 else ""  # an opening mark moves down
            out.append(cur[:-1] if carry else cur)
            cur, w = carry, ((1 if ord(carry) < 128 else 2) if carry else 0)
        cur += ch
        w += cw
    return out + ([cur] if cur else [])


def flow_svg(f: dict) -> str:
    """A flow drawn as stacked boxes with arrows (reads at phone width). steps: strings or
    {text, mark: new|changed|cut}. A flow given only as mermaid source is shown as source."""
    steps = f.get("steps") or []
    if not steps:
        if f.get("mermaid"):
            return (f'<figure class="flow"><figcaption>{e(f.get("title"))}</figcaption><pre class="mmd">{e(f["mermaid"])}</pre>'
                    '<p class="note">mermaid 源码：贴进任意 mermaid 查看器可以看图。</p></figure>')
        return ""
    W, pad, lh, gap = 300, 12, 18, 26
    parts, y = [], 8
    for i, st in enumerate(steps):
        st = st if isinstance(st, dict) else {"text": st}
        lines = wrap(st.get("text"), 15)
        h = pad * 2 + lh * len(lines)
        cls = {"new": "fnew", "changed": "fchg", "cut": "fcut"}.get(st.get("mark"), "fbox")
        parts.append(f'<rect x="10" y="{y}" width="{W - 20}" height="{h}" rx="9" class="{cls}"/>')
        for j, ln in enumerate(lines):
            parts.append(f'<text x="{W / 2}" y="{y + pad + lh * j + 13}" text-anchor="middle" class="ft{" cut" if cls == "fcut" else ""}">{e(ln)}</text>')
        tag = {"fnew": "新增", "fchg": "改", "fcut": "删"}.get(cls)
        if tag:
            parts.append(f'<text x="{W - 16}" y="{y + 14}" text-anchor="end" class="ftag">{tag}</text>')
        y += h
        if i < len(steps) - 1:
            parts.append(f'<line x1="{W / 2}" x2="{W / 2}" y1="{y + 3}" y2="{y + gap - 5}" class="farrow"/>'
                         f'<path d="M{W / 2 - 5},{y + gap - 10} L{W / 2},{y + gap - 3} L{W / 2 + 5},{y + gap - 10}" class="farrowhead"/>')
            y += gap
    return (f'<figure class="flow"><figcaption>{e(f.get("title"))}</figcaption>'
            f'<svg viewBox="0 0 {W} {y + 8}" role="img" aria-label="{e(f.get("title"))}">{"".join(parts)}</svg></figure>')


def answer_section(m) -> str:
    a = m.get("answer") or {}
    t = a.get("type") or "other"
    out = [f'<div class="lead1">{e(a.get("lead"))}{claim(a)}</div>']
    labels = [str(x.get("label", "")) for x in a.get("points") or []]
    if t in ("progress", "handoff") and not any("主线" in lb for lb in labels):
        out.append(align_bar(m, first_screen=True))
    if a.get("points"):
        rows = []
        for x in a["points"]:
            if "主线" in str(x.get("label", "")):
                lab, cls, _ = alignment(m)
                rows.append(f'<div class="pt"><dt>{e(x.get("label"))}</dt><dd><span class="b {cls}">{e(lab)}</span> '
                            f'{e(x.get("text"))}{claim(x)}{src(refs_of(x))}</dd></div>')
            else:
                rows.append(f'<div class="pt"><dt>{e(x.get("label"))}</dt><dd>{e(x.get("text"))}{claim(x)}{src(refs_of(x))}</dd></div>')
        out.append(f'<dl class="points">{"".join(rows)}</dl>')
    if a.get("insights"):
        rows = "".join(
            f'<tr><td>{e(x.get("text"))}{src(refs_of(x))}</td><td>'
            + "".join(f'<div class="cause" title="{e("、".join(ids))}"><span class="mute">起因 ← </span>{e(lab)}</div>' for lab, ids in causes(m, x.get("from") or []))
            + f'</td><td>{badge_of(INSIGHT, x.get("state"))}</td></tr>' for x in a["insights"])
        out.append('<div class="scroll"><table class="stack insights"><thead><tr><th>想法</th><th>从哪来</th><th>状态</th></tr></thead>'
                   f'<tbody>{rows}</tbody></table></div>')
    if a.get("flows"):
        out.append('<div class="flows">' + "".join(flow_svg(f) for f in a["flows"]) + "</div>")
    tb = a.get("table") or {}
    if tb.get("rows"):
        out.append(f'{"<h3>" + e(tb.get("title")) + "</h3>" if tb.get("title") else ""}<div class="scroll"><table class="stack"><thead><tr>'
                   + "".join(f"<th>{e(c)}</th>" for c in tb.get("cols") or [])
                   + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{e(c)}</td>" for c in r) + "</tr>" for r in tb["rows"])
                   + "</tbody></table></div>")
    if a.get("watch"):
        out.append('<h3>下一轮复盘看这几个数</h3><ul class="watch">' + "".join(f"<li>{e(x)}</li>" for x in a["watch"]) + "</ul>")
    return (f'<section id="answer" class="answer"><h2>{e(ANSWER_NAME.get(t, "答案"))}</h2>{"".join(out)}'
            f'{ext_blocks(m, "top")}{unused_resources(m)}</section>')


def badge_of(table, key) -> str:
    lab, cls = table.get(key, (key or "?", "mute"))
    return f'<span class="b {cls}">{e(lab)}</span>'


def unused_resources(m) -> str:
    """One sentence per ready-made resource that bears on this work but was not used: "项目里有 X，这次没有用上"."""
    rs = [x for x in m.get("resources") or [] if x.get("relevant") and x.get("used") in UNUSED]
    if not rs:
        return ""
    li = "".join(f'<li>项目里有{e(x.get("what"))}，这次没有用上。{e(x.get("note") or "")}{src(refs_of(x) or [x.get("path")])}</li>'
                 for x in rs)
    return f'<div class="res"><div class="lab">现成资源</div><ul>{li}</ul></div>'


def missing_box(m) -> str:
    """What is still missing: the answer's own list (the observer writes there only gaps it checked, that bear
    on this question and are still open) and what this look did not read."""
    a = m.get("answer") or {}
    items = [f"<li>{e(x)}</li>" for x in a.get("missing") or []]
    for u in (m.get("method") or {}).get("unread") or []:
        items.append(f'<li>这次没读：{e(u.get("what"))}<span class="mute"> — {e(u.get("why"))}</span></li>')
    if not items:
        return ""
    return f'<section class="miss"><h3>缺什么</h3><ul>{"".join(items)}</ul></section>'


def origin_box(m) -> str:
    os_ = m.get("origin") or []
    if not os_:
        return ""
    li = "".join(f'<li>「{e(o.get("quote"))}」{"<span class=mute> — " + e(o.get("use")) + "</span>" if o.get("use") else ""}'
                 f'{chips([o.get("at")])}</li>' for o in os_)
    return ('<section class="orig"><h3>你发起这次查看时说的</h3><p class="lead">不是被观察会话里的请求，'
            f'但写着你的原则和范围，建图时当意图证据用。</p><ul>{li}</ul></section>')


def work_sessions(m) -> list:
    """The sessions being looked at (not the one where the user asked for the look)."""
    return [x for x in m.get("sessions") or [] if x.get("role") != "origin"]


def on_top(m, q) -> bool:
    """A question reaches the first screen when it asks about an inferred goal (q.goal: every answer rests on
    the goal), is marked for this answer's type (q.for), or has no mark and the answer is about the state of the work."""
    t = (m.get("answer") or {}).get("type")
    if q.get("goal"):
        return True
    return t in q["for"] if q.get("for") else t in WORK_STATE


def questions_box(m) -> str:
    qs = [q for q in m.get("questions") or [] if on_top(m, q)]
    if not qs:
        return ""
    li = "".join(f'<li><b>{e(q.get("q"))}</b><div class="why">{e(q.get("why"))}</div>'
                 f'<div class="dflt">没人答时：{e(q.get("default"))}</div></li>' for q in qs)
    return f'<section class="ask"><h3>想先问你</h3><ol>{li}</ol></section>'


def update_box(m) -> str:
    u = m.get("update")
    if not u:
        return ""
    items = "".join(f"<li>{e(x)}</li>" for x in u.get("changes") or [])
    return (f'<section class="upd"><h3>这段时间发生了什么 <small>自 {e(fmt_time(u.get("since")))}</small></h3>'
            f'{para(u.get("story"))}<ul>{items}</ul></section>')


def frontier(r) -> bool:
    """Requests that still shape the work: not finished, set aside, or ones that changed direction."""
    return r.get("status") in ("open", "partial", "dropped") or r.get("kind") in OWN_STATUS


def req_row(r, gstatus) -> str:
    mute = " mute" if r.get("status") in ("dropped", "superseded") else ""
    if r.get("status"):
        st = badge(r.get("status"))
    else:
        st = f'<span class="b mute">随目标{("：" + STATUS.get(gstatus, (gstatus,))[0]) if gstatus else ""}</span>'
    return (f'<tr class="r{mute}"><td class="id">{e(r.get("id"))}</td>'
            f'<td class="q">{e(r.get("quote"))}{claim(r)}</td>'
            f'<td><span class="k">{e(KIND.get(r.get("kind"), r.get("kind")))}</span></td>'
            f'<td>{st}<div class="basis">{e(r.get("basis_text") or "")}</div></td>'
            f'<td class="refs">{chips([r.get("at")] + list(r.get("basis_refs") or []))}'
            f'{"<div class=basis>另见 " + e(" ".join(r.get("also"))) + "</div>" if r.get("also") else ""}</td></tr>')


def ledger_section(m) -> str:
    reqs = m.get("requests") or []
    goals = m.get("goals") or []
    gof = goal_of(m)
    gstat = {g.get("id"): g.get("status") for g in goals}
    fold = len(reqs) > FOLD_AT
    own = sum(1 for r in reqs if r.get("status"))
    live = [r for r in reqs if r.get("status") in ("open", "partial", "dropped")]
    turns = [r for r in reqs if r.get("kind") in OWN_STATUS]
    head = ('<thead><tr><th>#</th><th>原话</th><th>类型</th><th>状态与依据</th><th>位置</th></tr></thead>')
    blocks = []
    gids = {g.get("id") for g in goals}
    loose = [r for r in reqs if gof.get(r.get("id")) not in gids]
    for g, rs in [(g, [r for r in reqs if gof.get(r.get("id")) == g.get("id")]) for g in goals] + \
            ([({"id": "", "text": "没归到目标的", "level": ""}, loose)] if loose else []):
        gl = LEVEL.get(g.get("level"), "")
        src_ = g.get("from") or ("inferred" if g.get("sure") is False else "")
        unsure = badge_of(FROM, src_) if src_ else ""
        title = (f'<span class="gid">{e(g.get("id"))}</span> <span class="lvl">{e(gl)}</span> {e(g.get("text"))} '
                 f'{badge(g.get("status")) if g.get("status") else ""}{unsure} <span class="from">{len(rs)} 条</span>')
        shown = [r for r in rs if frontier(r)] if fold else rs
        rest = [r for r in rs if r not in shown]
        body = "".join(req_row(r, gstat.get(g.get("id"))) for r in shown)
        tbl = f'<div class="scroll"><table class="ledger stack">{head}<tbody>{body}</tbody></table></div>' if shown else ""
        more = ""
        if rest:
            more = (f'<details><summary>其余 {len(rest)} 条（已完成或随目标）</summary><div class="scroll">'
                    f'<table class="ledger stack">{head}<tbody>{"".join(req_row(r, gstat.get(g.get("id"))) for r in rest)}'
                    '</tbody></table></div></details>')
        blocks.append(f'<div class="goal"><div class="gt">{title}</div>{tbl}{more}</div>')
    lead = (f"{len(reqs)} 条。还开着或只做了一部分的 {len(live)} 条，改了方向的（纠正、插话、打断）{len(turns)} 条；"
            f"逐条判断了状态的 {own} 条，其余随所属目标。")
    if fold:
        lead += "每个目标下先列还开着的和改了方向的，其余折起来。"
    return (f'<section id="ledger"><h2><span class="no">02</span>你说过什么</h2>'
            f'<p class="lead">{e(lead)}</p>{"".join(blocks)}</section>')


def goals_block(m) -> str:
    """Goals with who stands behind them, and the AI's reading of each (where the two differ)."""
    gs = m.get("goals") or []
    if not gs:
        return ""
    rows = []
    for g in gs:
        ai = g.get("ai") or {}
        aitxt = (f'<div class="ai"><span class="mute">AI 的理解：</span>{e(ai.get("text"))}{claim(ai)}'
                 + (f'<div class="gap">差在哪：{e(ai.get("gap"))}</div>' if ai.get("gap") else "")
                 + f'<span class="refs">{chips(refs_of(ai))}</span></div>') if ai.get("text") else ""
        rows.append(f'<li><span class="gid">{e(g.get("id"))}</span> <span class="lvl">{e(LEVEL.get(g.get("level"), ""))}</span> '
                    f'{e(g.get("text"))} {badge(g.get("status")) if g.get("status") else ""}{badge_of(FROM, g.get("from"))}'
                    f'<span class="refs">{chips(refs_of(g))}</span>{aitxt}</li>')
    return f'<h3>目标</h3><ul class="goals">{"".join(rows)}</ul>'


def plan_strip(m) -> str:
    plan = m.get("plan") or {}
    ks = plan.get("steps") or []
    if not ks:
        return ""
    via = {sid for k in ks for sid in k.get("via") or []}
    off = [x.get("id") for x in m.get("process") or [] if x.get("id") not in via]
    items = "".join(f'<li class="{PLAN.get(k.get("state"), ("", "mute"))[1]}"><span class="gid">{e(k.get("id"))}</span> {e(k.get("do"))}'
                    f'<div class="basis">{e(PLAN.get(k.get("state"), (k.get("state") or "",))[0])}'
                    f'{" · " + e(k.get("goal")) if k.get("goal") else ""}{claim(k)}</div></li>' for k in ks)
    offtxt = (f'<p class="off">不在计划里的步骤：{e("、".join(off))}。连着出现在最后几步时，多半是陷进了局部。</p>'
              if off else "")
    src = f' <span class="from">计划出自 {chips(refs_of(plan))}</span>' if refs_of(plan) else ""
    return f'<h3>原本的计划，走到哪了{src}</h3><ol class="plan">{items}</ol>{para(plan.get("note"))}{offtxt}'


def timeline_svg(m) -> str:
    steps = m.get("process") or []
    if not steps:
        return ""
    layers = list(m.get("layers") or [])
    for s in steps:
        if s.get("layer") and s["layer"] not in layers:
            layers.append(s["layer"])
    if not layers:
        layers = ["过程"]
    colw, left, top, rowh = 86, 92, 22, 42
    w = left + colw * len(steps) + 20
    multi = len(work_sessions(m)) > 1
    h = top + rowh * len(layers) + 46 + (16 if multi else 0)
    y_of = {ly: top + rowh * i + rowh / 2 for i, ly in enumerate(layers)}
    x_of = [left + colw * i + colw / 2 for i in range(len(steps))]
    parts = []
    for ly, y in y_of.items():
        parts.append(f'<line x1="{left - 6}" x2="{w - 10}" y1="{y}" y2="{y}" class="lane"/>'
                     f'<text x="{left - 12}" y="{y + 4}" class="lyr" text-anchor="end">{e(ly[:6])}</text>')
    probs = {p.get("id"): p for p in m.get("problems") or []}
    idx = {s.get("id"): i for i, s in enumerate(steps)}
    for p in probs.values():
        ss = [idx[s] for s in (p.get("spin") or {}).get("steps") or [] if s in idx]
        if ss:
            x0, x1 = x_of[min(ss)] - colw / 2 + 4, x_of[max(ss)] + colw / 2 - 4
            parts.append(f'<rect x="{x0}" y="{top - 4}" width="{x1 - x0}" height="{rowh * len(layers) + 8}" rx="8" class="spin">'
                         f'<title>{e(p.get("id"))} 打转：{e((p.get("spin") or {}).get("note"))}</title></rect>'
                         f'<text x="{(x0 + x1) / 2}" y="{top + rowh * len(layers) + 18}" class="spinlab" text-anchor="middle">'
                         f'打转 · {e(p.get("id"))}</text>')
    prev = None
    for i, s in enumerate(steps):
        a = alias_of(s.get("span"))
        if a and a != prev and multi:
            x = left + colw * i + 2
            parts.append(f'<line x1="{x}" x2="{x}" y1="{top - 10}" y2="{top + rowh * len(layers)}" class="sessline"/>'
                         f'<text x="{x + 4}" y="{top + rowh * len(layers) + 30}" class="sesslab">会话 {e(a)}</text>')
        prev = a or prev
    pts = [(x_of[i], y_of.get(s.get("layer"), y_of[layers[0]])) for i, s in enumerate(steps)]
    parts.append('<polyline class="path" points="' + " ".join(f"{x},{y}" for x, y in pts) + '"/>')
    for i, s in enumerate(steps):
        x, y = pts[i]
        cls = "dot bad" if any(probs.get(p, {}).get("resolved") in ("no", "partial") for p in s.get("problems") or []) else "dot"
        parts.append(f'<circle cx="{x}" cy="{y}" r="7" class="{cls}"><title>{e(s.get("id"))} {e(s.get("title"))}\n'
                     f'{e(s.get("result"))}</title></circle>'
                     f'<text x="{x}" y="{h - 10}" text-anchor="middle" class="sid">{e(s.get("id"))}</text>')
    return (f'<div class="scroll svgwrap"><svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
            f'aria-label="过程时间线：每个点是一步，落在它主要改动的那一层">{"".join(parts)}</svg></div>'
            '<p class="note">每个点是一步，高度表示这一步主要改动落在哪一层；'
            '红点表示这一步留下了没解决的问题；黄底是围绕同一个问题反复尝试、结果没有稳定变好的几步（打转）。'
            '层只是纵轴，同一层连着几步不等于打转。</p>')


def steps_table(m) -> str:
    steps = m.get("process") or []
    if not steps:
        return ""
    sess = {x.get("alias"): x for x in work_sessions(m)}
    rows, prev = [], None
    for st in steps:
        a = alias_of(st.get("span"))
        if len(sess) > 1 and a and a != prev:
            x = sess.get(a) or {}
            rows.append(f'<tr class="g"><td colspan="4">会话 {e(a)} · {e(x.get("platform"))} {e(x.get("when"))}</td></tr>')
            prev = a
        rows.append(f'<tr><td class="id">{e(st.get("id"))}</td><td><b>{e(st.get("title"))}</b>{claim(st)}<div class="did">{e(st.get("did"))}</div></td>'
                    f'<td>{e(st.get("result"))}{"".join(f"<span class=pchip>{e(x)}</span>" for x in st.get("problems") or [])}</td>'
                    f'<td class="refs">{chips(refs_of(st))}</td></tr>')
    return ('<div class="scroll"><table class="stack"><thead><tr><th>#</th><th>做了什么</th><th>结果</th><th>位置</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def resources_table(m) -> str:
    rs = m.get("resources")
    if rs is None:
        return ""
    if not rs:
        return '<h3>现成资源</h3><p class="lead">清点过，没有和这次工作直接相关的。</p>'
    rows = "".join(f'<tr><td><b>{e(x.get("what"))}</b><div class="path">{e(x.get("path"))}</div></td>'
                   f'<td>{e(x.get("says"))}</td><td>{badge_of(USED, x.get("used"))}'
                   f'{"<div class=basis>和这次工作直接相关</div>" if x.get("relevant") else ""}<div class="basis">{e(x.get("note"))}</div></td>'
                   f'<td class="refs">{chips(refs_of(x))}</td></tr>' for x in rs)
    return ('<h3>现成资源</h3><div class="scroll"><table class="stack"><thead><tr><th>资源</th><th>写着什么</th><th>这次</th><th>位置</th></tr>'
            f'</thead><tbody>{rows}</tbody></table></div>')


def root_groups(problems):
    parent = {p.get("id"): p.get("id") for p in problems}

    def find(x):
        while parent.get(x, x) != x:
            x = parent[x]
        return x
    for p in problems:
        for q in p.get("same_root") or []:
            if q in parent:
                parent[find(q)] = find(p.get("id"))
    groups = {}
    for p in problems:
        groups.setdefault(find(p.get("id")), []).append(p.get("id"))
    return [g for g in groups.values() if len(g) > 1]


def ext_blocks(m, place) -> str:
    """Blocks written by external skills (handoff, audit, ...). The base renders them without knowing them:
    items = list of {text, at|refs, for, basis}, or cols + rows = a table."""
    out = []
    for b in m.get("ext") or []:
        if (b.get("place") or "end") != place:
            continue
        body = ""
        top = place == "top"  # the first screen shows no ids or positions: they go into the hover
        if b.get("items"):
            body += "<ol class=\"ext\">" + "".join(
                f'<li>{"<b>" + e(x.get("label")) + "</b> " if x.get("label") else ""}{e(x.get("text"))}{claim(x)} '
                + (src(refs_of(x) + list(x.get("for") or [])) if top else
                   f'{"<span class=for>" + e(" ".join(id_label(m, i) for i in x.get("for") or [])) + "</span>" if x.get("for") else ""}'
                   f'{chips(refs_of(x))}')
                + '</li>' for x in b["items"]) + "</ol>"
        if b.get("rows"):
            cols = b.get("cols") or []
            body += ('<div class="scroll"><table class="stack"><thead><tr>' + "".join(f"<th>{e(c)}</th>" for c in cols)
                     + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{e(c)}</td>" for c in r) + "</tr>" for r in b["rows"])
                     + "</tbody></table></div>")
        out.append(f'<section class="extb"><h3>{e(b.get("title"))} <small>{e(b.get("by"))}</small></h3>{para(b.get("note"))}{body}</section>')
    return "".join(out)


def site_box(m) -> str:
    """What the work looks like NOW (repo, processes, artifacts), checked against what the agent said."""
    st = m.get("site")
    if not st:
        return ""
    rows = "".join(f'<tr><td>{e(x.get("what"))}</td><td>{e(x.get("state"))}</td><td class="path">{e(x.get("how"))}</td></tr>'
                   for x in st.get("items") or [])
    return (f'<section class="site"><h3>现场核对 <small>核对于 {e(fmt_time(st.get("checked")))}</small></h3><div class="scroll">'
            f'<table class="stack"><thead><tr><th>什么</th><th>现在</th><th>怎么核的</th></tr></thead><tbody>{rows}</tbody>'
            '</table></div></section>')


def root_tags(p) -> str:
    t = []
    if p.get("root_kind"):
        t.append(ROOT_KIND.get(p["root_kind"], p["root_kind"]))
    if p.get("root_basis") != "物证":
        t.append("推断")
    return (" · " + " · ".join(e(x) for x in t)) if t else ""


def problems_part(m) -> str:
    ps = m.get("problems") or []
    if not ps:
        return '<h3>卡在哪</h3><p class="lead">没有发现值得记的问题。</p>'
    gtxt = "".join(f"<li>{'、'.join(e(x) for x in g)} 同根，可以一起解</li>" for g in root_groups(ps))
    cards = []
    for p in ps:
        spin = p.get("spin") or {}
        sp = (f'<div class="spinnote">打转：{e("、".join(spin.get("steps") or []))} — {e(spin.get("note"))}</div>'
              if spin.get("steps") else "")
        if p.get("not_spin"):
            sp += f'<div class="basis">反复试过，但判断不是打转：{e(p.get("not_spin"))}</div>'
        same = f'<div class="same">同根：{e("、".join(p.get("same_root")))}</div>' if p.get("same_root") else ""
        cards.append(f'<div class="card {"open" if p.get("resolved") != "yes" else ""}"><div class="ct">'
                     f'<span class="gid">{e(p.get("id"))}</span> {e(p.get("title"))} {badge(p.get("resolved"))}{claim(p)}</div>'
                     f'<dl><dt>现象</dt><dd>{e(p.get("symptom"))}</dd><dt>根因{root_tags(p)}</dt><dd>{e(p.get("root"))}</dd></dl>'
                     f'{same}{sp}<div class="refs">{chips(refs_of(p))}</div></div>')
    return (f'<h3>卡在哪</h3>{"<ul class=groups>" + gtxt + "</ul>" if gtxt else ""}<div class="cards">{"".join(cards)}</div>'
            f'{ext_blocks(m, "problems")}')


def collab_part(m) -> str:
    c = m.get("collab") or {}
    if not (c.get("user") or c.get("agent")):
        return ""

    def col(items):
        if not items:
            return "<p class='mute'>没有值得提的。</p>"
        return "<ul>" + "".join(f'<li><b>{e(x.get("text"))}</b>{claim(x)}<div class="better">更好的做法：{e(x.get("better"))}</div>'
                                f'<div class="refs">{chips(refs_of(x))}</div></li>' for x in items) + "</ul>"
    return (f'<h3>协作（两边都写，对事不对人）</h3><div class="two"><div><h4>你的消息</h4>{col(c.get("user"))}</div>'
            f'<div><h4>agent 的做法</h4>{col(c.get("agent"))}</div></div>')


def next_part(m) -> str:
    items = "".join(f'<li>{e(x.get("do"))}{claim(x)} <span class="for">{e(" ".join(id_label(m, i) for i in x.get("for") or []))}</span>'
                    f'<div class="refs">{chips(refs_of(x))}</div></li>' for x in m.get("next") or [])
    return f'<h3>下一步</h3><ol class="next">{items}</ol>{ext_blocks(m, "end")}'


def method_part(m) -> str:
    me = m.get("method") or {}
    sess = "".join(f'<li><span class="gid">{e(x.get("alias"))}</span> {e(x.get("platform"))} {e(x.get("when"))}'
                   f'{" · 发起查看" if x.get("role") == "origin" else ""}{" · " + e(x.get("note")) if x.get("note") else ""}'
                   f'<span class="mute"> · 细读 {e(x.get("read") or "只读了账本")}</span>'
                   f'<div class="path">{e(home_short(x.get("path")))}</div></li>' for x in m.get("sessions") or [])
    later = [q for q in m.get("questions") or [] if not on_top(m, q)]
    later_html = ("<dt>其他待问</dt><dd><ul>" + "".join(f"<li>{e(q.get('q'))} — 没人答时：{e(q.get('default'))}</li>" for q in later)
                  + "</ul></dd>") if later else ""
    unread = "".join(f"<li>{e(u.get('what'))} — {e(u.get('why'))}</li>" for u in me.get("unread") or [])
    claims = [n for k in ("requests", "goals", "process", "problems", "assets") for n in m.get(k) or [] if n.get("basis") == "说法"]
    claims += [b for b in (m.get("headline") or {}).values() if isinstance(b, dict) and b.get("basis") == "说法"]
    return (f'<h3>怎么查的</h3><ul class="sesslist">{sess}</ul>'
            f'<dl class="meth"><dt>做法</dt><dd>{e(me.get("how"))}</dd><dt>核对</dt><dd>{e(me.get("checks"))}</dd>'
            f'<dt>成本</dt><dd>{e(me.get("cost"))}</dd><dt>没读的</dt><dd>{"<ul>" + unread + "</ul>" if unread else "都读了"}</dd>'
            f'<dt>只是说法的</dt><dd>{len(claims)} 处，已在正文标「说法」</dd>{later_html}</dl>')


def fold(section_html: str, open_: bool = False) -> str:
    """<section id=x><h2>..</h2>…</section> -> a closed <details> with the h2 as its summary."""
    mm = re.match(r'<section id="([\w\-]+)"><h2>(.*?)</h2>', section_html, re.S)
    if not mm:
        return section_html
    body = section_html[mm.end():section_html.rfind("</section>")]
    return (f'<details class="sec" id="{mm.group(1)}"{" open" if open_ else ""}><summary><h2>{mm.group(2)}</h2></summary>'
            f'{body}</details>')


def map_section(m) -> str:
    """01: the map itself: main line and four boxes, goals (with the AI's reading), plan, problems, next, collaboration."""
    return (f'<section id="map"><h2><span class="no">01</span>任务地图</h2>{head_boxes(m)}{goals_block(m)}{plan_strip(m)}'
            f'{problems_part(m)}{next_part(m)}{collab_part(m)}{site_box(m)}{origin_box(m)}</section>')


def work_section(m) -> str:
    """03: what happened and how this look was made."""
    tl = f'<h3>过程</h3>{timeline_svg(m)}{steps_table(m)}' if m.get("process") else ""
    return (f'<section id="work"><h2><span class="no">03</span>过程和怎么查的</h2>{tl}{assets_table(m)}'
            f'{ext_blocks(m, "process")}{resources_table(m)}{method_part(m)}</section>')


CSS = """
:root{--bg:#fbfaf7;--fg:#1d1f23;--mute:#6b7078;--line:#e3e1dc;--card:#ffffff;--acc:#2f5fd0;--ok:#1f8a4c;--warn:#b7791f;
--bad:#c2410c;--spin:rgba(234,179,8,.18);--chip:#eef1f6;--gap:#fff4e5}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#15171a;--fg:#e7e7e4;--mute:#9aa0a8;--line:#2c3036;
--card:#1c1f23;--acc:#7aa2ff;--ok:#4ade80;--warn:#fbbf24;--bad:#fb923c;--spin:rgba(250,204,21,.14);--chip:#262a30;--gap:#2e2618}}
:root[data-theme="dark"]{--bg:#15171a;--fg:#e7e7e4;--mute:#9aa0a8;--line:#2c3036;--card:#1c1f23;--acc:#7aa2ff;--ok:#4ade80;
--warn:#fbbf24;--bad:#fb923c;--spin:rgba(250,204,21,.14);--chip:#262a30;--gap:#2e2618}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.65 -apple-system,"PingFang SC",
"Microsoft YaHei",system-ui,sans-serif}main{max-width:1040px;margin:0 auto;padding:28px 16px 64px}
header h1{font-size:26px;margin:0 0 4px}header .sub{color:var(--mute);font-size:13px}.asked{margin:14px 0 18px;padding:10px 14px;
border-left:3px solid var(--acc);background:var(--card)}
.four{display:grid;grid-template-columns:1fr 1fr;gap:12px}.box{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:14px 16px}.box .lab{font-size:12px;letter-spacing:.08em;color:var(--mute);margin-bottom:4px}.box .txt{font-size:16px}
.box.problem{border-color:var(--bad)}.box.next{border-color:var(--acc)}
.ask,.upd{margin-top:14px;background:var(--card);border:1px dashed var(--acc);border-radius:12px;padding:10px 16px}
.ask h3,.upd h3{margin:4px 0}.ask .why{color:var(--mute);font-size:13px}.ask .dflt{font-size:13px}
nav.toc{display:flex;flex-wrap:wrap;gap:6px 14px;margin:22px 0 4px;font-size:13px}nav.toc a{color:var(--acc);text-decoration:none}
section{margin-top:34px}h2{font-size:20px;border-bottom:1px solid var(--line);padding-bottom:6px}h2 .no{color:var(--mute);
font-weight:400;margin-right:10px;font-size:14px}h3{font-size:16px;margin:18px 0 8px}.lead{color:var(--mute)}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{border-bottom:1px solid var(--line);padding:7px 8px;text-align:left;vertical-align:top}th{color:var(--mute);font-weight:500;
font-size:12px}tr.g td{background:var(--chip);font-weight:600}tr.mute td{opacity:.55}td.id,.gid{font-family:ui-monospace,Menlo,monospace;
font-size:12px;color:var(--mute);white-space:nowrap}.lvl{font-size:12px;color:var(--acc);font-weight:500}td.q{min-width:220px}
.ledger td.q{max-width:520px}.basis,.did,.better,.why{color:var(--mute);font-size:13px}.k{font-size:12px;white-space:nowrap}
td.gap{background:var(--gap)}.ref{display:inline-block;font:11px ui-monospace,Menlo,monospace;background:var(--chip);color:var(--mute);
border-radius:6px;padding:0 5px;margin:2px 3px 0 0;white-space:nowrap}
.b{display:inline-block;font-size:11px;border-radius:999px;padding:0 8px;margin-left:6px;border:1px solid currentColor;white-space:nowrap}
.b.ok{color:var(--ok)}.b.warn{color:var(--warn)}.b.bad{color:var(--bad)}.b.mute{color:var(--mute)}.b.claim{color:var(--mute);border-style:dashed}
.svgwrap{border:1px solid var(--line);border-radius:12px;background:var(--card);padding:6px}svg{display:block;font:11px -apple-system,
"PingFang SC",sans-serif}svg .lane{stroke:var(--line)}svg .lyr{fill:var(--mute)}svg .path{fill:none;stroke:var(--mute);
stroke-width:1.5;stroke-dasharray:3 3}svg .dot{fill:var(--acc)}svg .dot.bad{fill:var(--bad)}svg .spin{fill:var(--spin)}
svg .spinlab{fill:var(--warn);font-weight:600}svg .sid{fill:var(--mute)}svg .req line{stroke:var(--acc);stroke-width:1}
svg .req text{fill:var(--acc);font-weight:600}.note{font-size:12px;color:var(--mute)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}.card{background:var(--card);
border:1px solid var(--line);border-radius:12px;padding:12px 14px}.card.open{border-left:3px solid var(--bad)}.ct{font-weight:600}
dl{margin:8px 0}dt{font-size:12px;color:var(--mute)}dd{margin:0 0 6px}.spinnote{font-size:13px;color:var(--warn)}.same{font-size:13px}
ul.groups{padding-left:18px;color:var(--warn)}.pchip{font:11px ui-monospace,monospace;color:var(--bad);margin-left:6px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:18px}.two ul{padding-left:18px}.two li{margin-bottom:10px}
ol.next li{margin-bottom:8px}.for{font:11px ui-monospace,monospace;color:var(--acc)}td.path,div.path{font:11px ui-monospace,monospace;color:var(--mute);
word-break:break-all}dl.meth dd{margin-bottom:10px}.mute{color:var(--mute)}footer{margin-top:40px;color:var(--mute);font-size:12px}
.stop{font-size:13px;margin-top:6px;color:var(--bad)}ol.chain{font-size:13px;padding-left:20px;margin:6px 0}
.site{margin-top:14px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:8px 14px}
.site h3{margin:4px 0}.from{font:11px ui-monospace,monospace;color:var(--mute);font-weight:400;margin-left:8px}
.align{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 10px;margin:0 0 12px;padding:10px 14px;border-radius:12px;
background:var(--card);border:1px solid var(--line);border-left:4px solid var(--mute)}.align .lab{font-size:12px;color:var(--mute)}
.align b{font-size:16px}.align.ok{border-left-color:var(--ok)}.align.ok b{color:var(--ok)}.align.warn{border-left-color:var(--warn)}
.align.warn b{color:var(--warn)}.align.bad{border-left-color:var(--bad)}.align.bad b{color:var(--bad)}.align .txt{flex:1 1 320px}
.sess,.extb{margin-top:14px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:8px 16px}
.sess h3,.extb h3{margin:4px 0}.sess ul{padding-left:18px;margin:6px 0}.sess .when{font-size:12px;color:var(--mute)}
.sess .did{color:var(--mute);font-size:13px}.extb small{font-weight:400;color:var(--mute);font-size:12px}ol.ext li{margin-bottom:6px}
.goal{margin:14px 0}.goal .gt{background:var(--chip);border-radius:8px;padding:6px 10px;font-weight:600}
details{margin:4px 0 0}details summary{cursor:pointer;color:var(--acc);font-size:13px;padding:4px 2px}
ol.plan{display:flex;flex-wrap:wrap;gap:8px;list-style:none;padding:0;margin:6px 0}ol.plan li{flex:1 1 160px;background:var(--card);
border:1px solid var(--line);border-radius:10px;padding:6px 10px}ol.plan li.ok{border-color:var(--ok)}ol.plan li.now{border:2px solid var(--acc)}
ol.plan li.warn{border-color:var(--warn)}ol.plan li.mute{opacity:.7}.off{font-size:13px;color:var(--warn)}
svg .sessline{stroke:var(--acc);stroke-dasharray:2 4}svg .sesslab{fill:var(--acc);font-size:10px}
.answer{margin-top:10px}.answer h2{border:0;font-size:13px;letter-spacing:.1em;color:var(--acc);margin:0 0 6px;padding:0}
.lead1{font-size:19px;line-height:1.6;margin:0 0 14px}dl.points{margin:0;display:grid;gap:8px}.pt{display:grid;
grid-template-columns:7.5em 1fr;gap:10px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px}
.pt dt{font-size:13px;color:var(--mute);padding-top:2px}.pt dd{margin:0;font-size:16px}.src{font-size:11px;color:var(--mute);
border-bottom:1px dotted var(--mute);cursor:help;margin-left:6px;white-space:nowrap}.cause{font-size:13px}
.flows{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;margin:12px 0}figure.flow{margin:0;
background:var(--card);border:1px solid var(--line);border-radius:12px;padding:8px 10px}figure.flow figcaption{font-weight:600;
font-size:14px;margin-bottom:4px}figure.flow svg{width:100%;height:auto;max-width:360px;margin:0 auto}
svg .fbox{fill:var(--bg);stroke:var(--line)}svg .fnew{fill:var(--bg);stroke:var(--acc);stroke-width:2}svg .fchg{fill:var(--gap);
stroke:var(--warn);stroke-width:1.5}svg .fcut{fill:none;stroke:var(--mute);stroke-dasharray:4 3}svg .ft{fill:var(--fg);font-size:13px}
svg .ft.cut{fill:var(--mute);text-decoration:line-through}svg .ftag{fill:var(--mute);font-size:10px}svg .farrow{stroke:var(--mute)}
svg .farrowhead{fill:none;stroke:var(--mute)}pre.mmd{font-size:12px;white-space:pre-wrap;background:var(--chip);padding:8px;border-radius:8px}
.miss{margin-top:14px;background:var(--card);border:1px solid var(--warn);border-radius:12px;padding:8px 16px}.miss h3{margin:4px 0}
.miss ul{padding-left:18px;margin:6px 0}.foldnote{margin-top:30px;color:var(--mute);font-size:13px}
details.sec{margin-top:10px;border-top:1px solid var(--line)}details.sec>summary{list-style:none;cursor:pointer}
details.sec>summary::-webkit-details-marker{display:none}details.sec>summary h2{border:0;display:inline-block;margin:10px 0;font-size:18px}
details.sec>summary h2::after{content:" ▸";color:var(--mute);font-size:13px}details.sec[open]>summary h2::after{content:" ▾"}
.orig{margin-top:14px}.orig ul{padding-left:18px}ul.watch{padding-left:18px}
.res{margin-top:10px;background:var(--card);border:1px solid var(--bad);border-radius:10px;padding:8px 14px}.res .lab{font-size:13px;
color:var(--mute)}.res ul{margin:4px 0;padding-left:18px}ul.goals{padding-left:18px}ul.goals li{margin-bottom:8px}
.ai{font-size:14px;margin-top:2px}.ai .gap{color:var(--warn);font-size:13px}h4{font-size:14px;margin:8px 0 4px;color:var(--mute)}
ul.sesslist{padding-left:18px;font-size:14px}
@media (max-width:680px){.four,.two{grid-template-columns:1fr}.pt{grid-template-columns:1fr;gap:2px}.lead1{font-size:17px}header h1{font-size:22px}main{padding-top:18px}
table.stack thead{display:none}table.stack tr{display:block;border-bottom:1px solid var(--line);padding:6px 0}
table.stack td{display:block;border:0;padding:2px 4px}table.stack td.id{display:inline-block}td.q{min-width:0}}
"""


def to_html(m) -> str:
    ss = [x for x in m.get("sessions") or [] if x.get("role") != "origin"]
    sub = " · ".join(x for x in [e(m.get("subject")), f"{len(ss)} 个会话" if len(ss) > 1 else "",
                                 f'更新于 {e(fmt_time(m.get("updated")))}（本地时间）' if m.get("updated") else ""] if x)
    parts = [map_section(m), ledger_section(m), work_section(m)]
    body = (f'<header><h1>{e(m.get("title"))}</h1><div class="sub">{sub}</div></header>'
            f'<div class="asked">这份报告回答：{e(m.get("question"))}</div>'
            f'{answer_section(m)}{missing_box(m)}{questions_box(m)}{update_box(m)}'
            '<p class="foldnote">下面是这份答案背后的任务地图，默认折起，点标题展开。</p>'
            + "".join(fold(x) for x in parts)
            + '<footer>任务地图 · 由 map.json 生成 · 位置写作「会话:行」，file: 是仓库文件，site: 是现场核对的第几项</footer>')
    return (f'<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>{e(m.get("title") or "任务地图")}</title>'
            f'<style>{CSS}</style></head><body><main>{body}</main></body></html>')


# --------------------------------------------------------------------------- map.md

def short(s, n=90) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s[:n] + ("…" if len(s) > n else "")


def r_(n) -> str:
    rs = refs_of(n)
    return f" [{', '.join(rs[:4])}]" if rs else ""


def to_md(m) -> str:
    L = [f"# 任务地图：{m.get('title')}", f"更新 {fmt_time(m.get('updated'))}（本地时间）· 回答：{m.get('question')}", ""]
    L += [f"- 会话 {s.get('alias')}{'（发起查看）' if s.get('role') == 'origin' else ''} = {s.get('path')}（{s.get('platform')} "
          f"{s.get('when') or ''}，你的话抽到 {s.get('ledger_through')} 行，"
          f"细读了 {s.get('read') or '无'}{'，' + s['state'] if s.get('state') else ''}）" + (f"：{s.get('note')}" if s.get("note") else "")
          for s in m.get("sessions") or []]
    a = m.get("answer") or {}
    L += ["", f"## 答案（{ANSWER_NAME.get(a.get('type'), a.get('type'))}）", a.get("lead") or ""]
    L += [f"- {x.get('label')}：{x.get('text')}{r_(x)}" for x in a.get("points") or []]
    L += [f"- 想法［{INSIGHT.get(x.get('state'), (x.get('state'),))[0]}］{x.get('text')} ← {','.join(id_label(m, i) for i in x.get('from') or [])}"
          for x in a.get("insights") or []]
    for f in a.get("flows") or []:
        st = [x if isinstance(x, str) else f"{x.get('text')}" + (f"［{x.get('mark')}］" if x.get("mark") else "") for x in f.get("steps") or []]
        L.append(f"- {f.get('title')}：" + (" → ".join(st) if st else short(f.get("mermaid"), 300)))
    tb = a.get("table") or {}
    if tb.get("rows"):
        L += ["", "| " + " | ".join(tb.get("cols") or []) + " |", "|" + "---|" * len(tb.get("cols") or [])]
        L += ["| " + " | ".join(str(c) for c in r) + " |" for r in tb["rows"]]
    L += [f"- 下一轮看：{x}" for x in a.get("watch") or []]
    miss = [f"项目里有{x.get('what')}，这次没有用上 [{x.get('path')}]" for x in m.get("resources") or []
            if x.get("relevant") and x.get("used") in UNUSED] + list(a.get("missing") or [])
    if miss:
        L += ["", "## 缺什么（含没用上的现成资源）"] + [f"- {x}" for x in miss]
    hl = m.get("headline") or {}
    al = hl.get("alignment") or {}
    lab, _, atxt = alignment(m)
    L += ["", "## 地图四格", f"- 主线：{lab}。{atxt}{r_(al)}"]
    L += [f"- {lab}：{(hl.get(k) or {}).get('text')}{r_(hl.get(k) or {})}"
          + ("（说法）" if (hl.get(k) or {}).get("basis") == "说法" else "")
          for k, lab in [("want", "你要的"), ("progress", "做到哪"), ("problem", "最大问题"), ("next", "下一步")]]
    if m.get("update"):
        L += ["", f"## 最近变化（自 {fmt_time(m['update'].get('since'))}）", short(m["update"].get("story"), 400)]
        L += [f"- {c}" for c in m["update"].get("changes") or []]
    L += ["", "## 目标"]
    for g in m.get("goals") or []:
        src_ = g.get("from") or ("inferred" if g.get("sure") is False else "")
        L.append(f"- {g.get('id')} [{LEVEL.get(g.get('level'), '')}] {g.get('text')}（{STATUS.get(g.get('status'), (g.get('status'),))[0]}"
                 f"{'，' + FROM[src_][0] if src_ in FROM else ''}）"
                 f" ← {','.join(id_label(m, i) for i in g.get('requests') or [])}" + (f"；反问：{g.get('ask')}" if g.get("ask") else ""))
        ai = g.get("ai") or {}
        if ai.get("text"):
            L.append(f"  - AI 的理解：{short(ai.get('text'))}" + (f"；差在哪：{short(ai.get('gap'), 80)}" if ai.get("gap") else "") + r_(ai))
    plan = m.get("plan") or {}
    if plan.get("steps"):
        via = {sid for k in plan["steps"] for sid in k.get("via") or []}
        off = [x.get("id") for x in m.get("process") or [] if x.get("id") not in via]
        L += ["", "## 计划（走到哪）"] + [f"- {k.get('id')} [{PLAN.get(k.get('state'), (k.get('state'),))[0]}] {k.get('do')}"
                                       f" → {k.get('goal') or ''}" + (f"（由 {','.join(k['via'])}）" if k.get("via") else "")
                                       for k in plan["steps"]]
        if off:
            L.append(f"- 计划外的步骤：{','.join(off)}")
    reqs = m.get("requests") or []
    gof, gstat = goal_of(m), {g.get("id"): g.get("status") for g in m.get("goals") or []}
    live = [r for r in reqs if frontier(r)]
    L += ["", f"## 请求（共 {len(reqs)}；只列还开着的、部分完成的、放下的和改了方向的 {len(live)} 条）"]
    L += [f"- {r.get('id')} {r.get('at')} {KIND.get(r.get('kind'), r.get('kind'))} "
          f"{STATUS.get(r.get('status'), ('?',))[0] if r.get('status') else '随目标 ' + str(STATUS.get(gstat.get(gof.get(r.get('id'))), ('?',))[0])}"
          f" → {gof.get(r.get('id'))}：「{short(r.get('quote'), 36)}」" + (f" — {short(r.get('basis_text'), 60)}" if r.get("basis_text") else "")
          for r in live]
    L += ["", "## 问题"]
    for p in m.get("problems") or []:
        tags = [STATUS.get(p.get("resolved"), ("?",))[0]]
        if (p.get("spin") or {}).get("steps"):
            tags.append("打转 " + "-".join([p["spin"]["steps"][0], p["spin"]["steps"][-1]]))
        elif p.get("not_spin"):
            tags.append("反复试过、判断不是打转")
        if p.get("same_root"):
            tags.append("同根 " + ",".join(p["same_root"]))
        if p.get("root_kind"):
            tags.append(ROOT_KIND.get(p["root_kind"], p["root_kind"]))
        L.append(f"- {p.get('id')} [{' / '.join(tags)}] {p.get('title')} — 根因"
                 f"{'' if p.get('root_basis') == '物证' else '（推断）'}：{short(p.get('root'), 100)}{r_(p)}")
    if m.get("process"):
        L += ["", "## 过程"] + [f"- {s.get('id')} {s.get('span')} [{s.get('layer')}] {short(s.get('title'), 40)} → {short(s.get('result'), 70)}"
                               for s in m["process"]]
    if m.get("site"):
        L += ["", f"## 现场核对（{fmt_time(m['site'].get('checked'))}）"] + [f"- {x.get('what')}：{x.get('state')}（{x.get('how')}）"
                                                               for x in m["site"].get("items") or []]
    if m.get("assets"):
        L += ["", "## 关键产物"] + [f"- {x.get('id')} [{STATUS.get(x.get('status'), (x.get('status'),))[0]}] {x.get('what')}：{x.get('path')}"
                                   + (f" — {short(x.get('note'), 60)}" if x.get("note") else "") for x in m["assets"]]
    for b in m.get("ext") or []:
        L += ["", f"## {b.get('title')}（{b.get('by')}）"]
        L += [f"- {x.get('label') + ' ' if x.get('label') else ''}{short(x.get('text'), 120)}{r_(x)}" for x in b.get("items") or []]
        L += ["- " + " | ".join(str(c) for c in r) for r in b.get("rows") or []]
    c = m.get("collab") or {}
    if c.get("user") or c.get("agent"):
        L += ["", "## 协作"] + [f"- 用户侧：{short(x.get('text'), 70)} → {short(x.get('better'), 70)}" for x in c.get("user") or []]
        L += [f"- agent 侧：{short(x.get('text'), 70)} → {short(x.get('better'), 70)}" for x in c.get("agent") or []]
    if m.get("origin"):
        L += ["", "## 发起查看时你说的（意图证据）"] + [f"- {o.get('at')}「{short(o.get('quote'), 80)}」" + (f" — {o.get('use')}" if o.get("use") else "")
                                                     for o in m["origin"]]
    if m.get("resources") is not None:
        L += ["", "## 现成资源"] + [f"- [{USED.get(x.get('used'), (x.get('used'),))[0]}{'，相关' if x.get('relevant') else ''}] "
                                   f"{x.get('what')}：{x.get('path')} — {short(x.get('says'), 80)}{r_(x)}" for x in m["resources"]]
    L += ["", "## 下一步"] + [f"{i}. {x.get('do')}（{','.join(id_label(m, j) for j in x.get('for') or [])}）" for i, x in enumerate(m.get("next") or [], 1)]
    if m.get("questions"):
        L += ["", "## 反问（没人答时按默认继续）"] + [f"- {q.get('q')} → 默认：{q.get('default')}" for q in m["questions"]]
    me = m.get("method") or {}
    L += ["", "## 怎么查的", f"- {short(me.get('how'), 200)}", f"- 核对：{short(me.get('checks'), 160)}"]
    L += [f"- 没读：{u.get('what')}（{u.get('why')}）" for u in me.get("unread") or []]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("map")
    ap.add_argument("--out")
    a = ap.parse_args()
    mp = Path(a.map)
    m = json.loads(mp.read_text())
    out = Path(a.out) if a.out else mp.parent
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.html").write_text(to_html(m))
    (out / "map.md").write_text(to_md(m))
    print(f"wrote {out / 'report.html'} and {out / 'map.md'}")


if __name__ == "__main__":
    main()
