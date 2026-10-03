#!/usr/bin/env python3
"""Compare two task maps.

  map_diff.py old.json new.json            what changed over time (update mode)
  map_diff.py a.json b.json --compare      where two maps of the same work disagree (e.g. two versions, or two people)
  add --json out.json to also write the diff as data (render.py shows map.json "update" if you copy it in)

Requests are matched by position (at). Problems are paired by CONTENT, not by id or order: text
similarity of title + symptom + root, plus a bonus when their evidence overlaps; the best pairs are
taken first. So a problem renumbered or reordered between versions is still recognised as the same one.
Goals and steps are matched by id.
The script lists facts; the agent writes the story ("这段时间发生了什么") from them.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REF = re.compile(r"^([A-Za-z][\w\-]*):L?(\d+)(?:-L?(\d+))?$")


def refs(n):
    out = [n[k] for k in ("at", "span") if isinstance(n.get(k), str)]
    for k in ("refs", "basis_refs"):
        out += [r if isinstance(r, str) else r.get("at", "") for r in n.get(k) or []]
    return out


def spans(n, slack=5):
    out = []
    for r in refs(n):
        m = REF.match(r or "")
        if m:
            s, e = int(m.group(2)), int(m.group(3) or m.group(2))
            out.append((m.group(1), s - slack, e + slack))
    return out


def overlap(a, b):
    return any(x[0] == y[0] and x[1] <= y[2] and y[1] <= x[2] for x in spans(a) for y in spans(b))


def short(s, n=60):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s[:n] + ("…" if len(s) > n else "")


def tail_spin(m):
    """The latest >=3 steps all attempt the same problem, and it is not marked 'not a spin':
    the live 'stuck in a local problem' signal. Layers are only the timeline's axis."""
    steps = m.get("process") or []
    if not steps:
        return None
    probs = {p.get("id"): p for p in m.get("problems") or []}
    for pid in steps[-1].get("problems") or []:
        k = 0
        for s in reversed(steps):
            if pid not in (s.get("problems") or []):
                break
            k += 1
        p = probs.get(pid) or {}
        if k >= 3 and not p.get("not_spin") and p.get("resolved") != "yes":
            return (pid, k, [s.get("id") for s in steps[-k:]])
    return None


def grams(t: str) -> set:
    t = re.sub(r"\s+", "", str(t or "")).lower()
    return {t[i:i + 2] for i in range(len(t) - 1)} or ({t} if t else set())


def similarity(p, q) -> float:
    a = grams(" ".join(str(p.get(k) or "") for k in ("title", "symptom", "root")))
    b = grams(" ".join(str(q.get(k) or "") for k in ("title", "symptom", "root")))
    sim = len(a & b) / len(a | b) if a and b else 0.0
    return sim + (0.2 if overlap(p, q) else 0.0)


def pair_problems(pa, pb, floor=0.18):
    """Greedy best-first pairing by similarity. Returns (pairs [(p, q, score)], only_a, only_b)."""
    cand = sorted(((similarity(p, q), i, j) for i, p in enumerate(pa) for j, q in enumerate(pb)), reverse=True)
    ua, ub, pairs = set(), set(), []
    for sc, i, j in cand:
        if sc < floor:
            break
        if i in ua or j in ub:
            continue
        ua.add(i)
        ub.add(j)
        pairs.append((pa[i], pb[j], sc))
    return pairs, [p for i, p in enumerate(pa) if i not in ua], [q for j, q in enumerate(pb) if j not in ub]


ALIGN = {"on_track": "在主线上", "stuck": "陷在局部", "drifted": "偏离了目标", "waiting": "停在等你拍板", None: "没判断"}


def plan_pos(m):
    ks = (m.get("plan") or {}).get("steps") or []
    return next((k.get("id") for k in ks if k.get("state") == "doing"), None), {s for k in ks for s in k.get("via") or []}


def over_time(old, new):
    out = {"headline": [], "requests": [], "goals": [], "plan": [], "problems": [], "process": [], "next": [], "questions": []}
    oa = ((old.get("headline") or {}).get("alignment") or {}).get("state")
    na = ((new.get("headline") or {}).get("alignment") or {}).get("state")
    oat = ((old.get("answer") or {}).get("lead"))
    nat = ((new.get("answer") or {}).get("lead"))
    if oat != nat:
        out["headline"].append(f"答案：{short(oat)} → {short(nat)}")
    if oa != na:
        out["headline"].append(f"⚠ 主线：{ALIGN.get(oa, oa)} → {ALIGN.get(na, na)}" if na in ("stuck", "drifted", "waiting")
                               else f"主线：{ALIGN.get(oa, oa)} → {ALIGN.get(na, na)}")
    (op, _), (np_, via) = plan_pos(old), plan_pos(new)
    if op != np_:
        out["plan"].append(f"计划走到：{op or '—'} → {np_ or '—'}")
    osteps_ids = {s.get("id") for s in old.get("process") or []}
    off = [s.get("id") for s in new.get("process") or [] if s.get("id") not in osteps_ids and s.get("id") not in via]
    if (new.get("plan") or {}).get("steps") and off:
        out["plan"].append(f"新步骤里不在计划中的：{'、'.join(off)}" + ("（连续 ≥3 步，检查是否陷在局部）" if len(off) >= 3 else ""))
    for k, lab in [("want", "你要的"), ("progress", "做到哪"), ("problem", "最大问题"), ("next", "下一步")]:
        a = ((old.get("headline") or {}).get(k) or {}).get("text")
        b = ((new.get("headline") or {}).get(k) or {}).get("text")
        if a != b:
            out["headline"].append(f"{lab}：{short(a)} → {short(b)}")
    oreq = {r.get("at"): r for r in old.get("requests") or []}
    for r in new.get("requests") or []:
        o = oreq.get(r.get("at"))
        if not o:
            out["requests"].append(f"新请求 {r.get('id')} {r.get('at')}（{r.get('kind')}）：「{short(r.get('quote'), 30)}」→ {r.get('goal') or '未归属'}")
        elif o.get("status") != r.get("status"):
            out["requests"].append(f"{r.get('id')} {o.get('status')} → {r.get('status')}：「{short(r.get('quote'), 40)}」")
        elif o.get("goal") != r.get("goal"):
            out["requests"].append(f"{r.get('id')} 改归属 {o.get('goal')} → {r.get('goal')}")
    onodes = {n.get("id"): n for n in old.get("goals") or []}
    for n in new.get("goals") or []:
        o = onodes.get(n.get("id"))
        if not o:
            out["goals"].append(f"新目标 {n.get('id')}：{short(n.get('text'))}")
            continue
        if o.get("status") != n.get("status"):
            out["goals"].append(f"{n.get('id')} {o.get('status')} → {n.get('status')}：{short(n.get('text'), 40)}")
        if o.get("text") != n.get("text"):
            out["goals"].append(f"{n.get('id')} 目标改写：「{short(o.get('text'), 40)}」→「{short(n.get('text'), 40)}」")
        if o.get("from") != n.get("from"):
            out["goals"].append(f"{n.get('id')} 确认状态 {o.get('from')} → {n.get('from')}")
    for g in sorted(set(onodes) - {n.get("id") for n in new.get("goals") or []}):
        out["goals"].append(f"{g} 从地图里移除了（确认是合并还是遗漏）")
    pairs, gone, fresh_p = pair_problems(list(old.get("problems") or []), list(new.get("problems") or []))
    for o, n, _ in pairs:
        tag = n.get("id") if o.get("id") == n.get("id") else f"{o.get('id')}→{n.get('id')}（重新编号，按内容认出是同一个）"
        if o.get("resolved") != n.get("resolved"):
            out["problems"].append(f"{tag} {o.get('resolved')} → {n.get('resolved')}：{short(n.get('title'), 40)}")
        elif o.get("id") != n.get("id"):
            out["problems"].append(f"{tag}：{short(n.get('title'), 40)}")
        if not (o.get("spin") or {}).get("steps") and (n.get("spin") or {}).get("steps"):
            out["problems"].append(f"{n.get('id')} 新标打转：{'、'.join(n['spin']['steps'])}")
    for n in fresh_p:
        out["problems"].append(f"新问题 {n.get('id')}：{short(n.get('title'))}")
    for o in gone:
        out["problems"].append(f"{o.get('id')}「{short(o.get('title'), 30)}」在新地图里找不到内容相近的问题（确认是解决后删了、合并了还是遗漏）")
    osteps = {s.get("id") for s in old.get("process") or []}
    fresh = [s for s in new.get("process") or [] if s.get("id") not in osteps]
    for s in fresh:
        out["process"].append(f"新步骤 {s.get('id')} {s.get('span')} [{s.get('layer')}] {short(s.get('title'), 30)} → {short(s.get('result'), 40)}")
    ts = tail_spin(new)
    if ts:
        out["process"].append(f"⚠ 最近 {ts[1]} 步（{'、'.join(ts[2])}）都在尝试同一个问题 {ts[0]}：结果没有稳定变好就是打转，"
                              "检查是否陷在局部、丢了全局")
    on = [x.get("do") for x in old.get("next") or []]
    nn = [x.get("do") for x in new.get("next") or []]
    if on != nn:
        out["next"] += [f"新增：{short(x)}" for x in nn if x not in on] + [f"移除或完成：{short(x)}" for x in on if x not in nn]
        if on and nn and on[0] != nn[0]:
            out["next"].append(f"第一件事变了：「{short(on[0], 40)}」→「{short(nn[0], 40)}」")
    oq = {q.get("q") for q in old.get("questions") or []}
    out["questions"] = [f"新反问：{q.get('q')}（默认 {q.get('default')}）" for q in new.get("questions") or [] if q.get("q") not in oq]
    return out


def compare(a, b, la, lb):
    out = {"requests": [], "goals": [], "problems": [], "process": [], "headline": []}
    ar = {r.get("at"): r for r in a.get("requests") or []}
    br = {r.get("at"): r for r in b.get("requests") or []}
    for at in sorted(set(ar) | set(br), key=lambda x: (x or "")):
        x, y = ar.get(at), br.get(at)
        if not x or not y:
            out["requests"].append(f"{at} 只在{la if x else lb}里：「{short((x or y).get('quote'), 40)}」")
        elif x.get("status") != y.get("status"):
            out["requests"].append(f"{at} 状态分歧 {la}={x.get('status')} / {lb}={y.get('status')}：「{short(x.get('quote'), 40)}」")
    out["headline"].append(f"主线\n    {la}：{ALIGN.get(((a.get('headline') or {}).get('alignment') or {}).get('state'))}"
                           f"\n    {lb}：{ALIGN.get(((b.get('headline') or {}).get('alignment') or {}).get('state'))}")
    for k, lab in [("want", "你要的"), ("progress", "做到哪"), ("problem", "最大问题"), ("next", "下一步")]:
        out["headline"].append(f"{lab}\n    {la}：{short(((a.get('headline') or {}).get(k) or {}).get('text'), 90)}"
                               f"\n    {lb}：{short(((b.get('headline') or {}).get(k) or {}).get('text'), 90)}")
    ag = [g.get("text") for g in a.get("goals") or [] if g.get("level") == "purpose"]
    bg = [g.get("text") for g in b.get("goals") or [] if g.get("level") == "purpose"]
    out["goals"].append(f"长期目的 {la}：{'；'.join(short(x, 50) for x in ag) or '—'}")
    out["goals"].append(f"长期目的 {lb}：{'；'.join(short(x, 50) for x in bg) or '—'}")
    pairs, only_a, only_b = pair_problems(list(a.get("problems") or []), list(b.get("problems") or []))
    for p, q, sc in pairs:
        same = "一致" if p.get("resolved") == q.get("resolved") else f"是否解决分歧 {p.get('resolved')}/{q.get('resolved')}"
        out["problems"].append(f"两边都看到（相似度 {sc:.2f}）：{la}.{p.get('id')}「{short(p.get('title'), 30)}」≈ "
                               f"{lb}.{q.get('id')}「{short(q.get('title'), 30)}」（{same}）")
    for p in only_a:
        out["problems"].append(f"只有{la}看到：{p.get('id')}「{short(p.get('title'), 40)}」— 回原文核实")
    for q in only_b:
        out["problems"].append(f"只有{lb}看到：{q.get('id')}「{short(q.get('title'), 40)}」— 回原文核实")
    for m, lab in ((a, la), (b, lb)):
        ts = tail_spin(m)
        spins = [p.get("id") for p in m.get("problems") or [] if (p.get("spin") or {}).get("steps")]
        out["process"].append(f"{lab}：{len(m.get('process') or [])} 步，标打转 {spins or '无'}"
                              + (f"，最近 {ts[1]} 步都在试 {ts[0]}" if ts else ""))
    return out


def to_md(d, title):
    names = {"headline": "首屏", "requests": "请求", "goals": "目标", "plan": "计划", "problems": "问题", "process": "过程",
             "next": "下一步", "questions": "反问"}
    lines = [f"# {title}"]
    for k, v in d.items():
        if v:
            lines += ["", f"## {names.get(k, k)}"] + [f"- {x}" for x in v]
    if len(lines) == 1:
        lines.append("\n没有变化。")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--labels", default="")
    ap.add_argument("--json")
    x = ap.parse_args()
    a, b = json.loads(Path(x.a).read_text()), json.loads(Path(x.b).read_text())
    if x.compare:
        la, lb = (x.labels.split(",") + ["甲", "乙"])[:2] if x.labels else ("甲", "乙")
        d = compare(a, b, la, lb)
        md = to_md(d, f"两份地图的分歧：{la} vs {lb}")
    else:
        d = over_time(a, b)
        md = to_md(d, f"地图变化：{a.get('updated')} → {b.get('updated')}")
    print(md)
    if x.json:
        Path(x.json).write_text(json.dumps(d, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
