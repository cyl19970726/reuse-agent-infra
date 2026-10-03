#!/usr/bin/env python3
"""Request ledger skeleton: what the USER actually said, plus the work between requests.

  ledger.py A=<session.jsonl> [B=<other.jsonl> ...] --out DIR --skeleton      first build
  ledger.py A=<…> --out DIR/delta --since A:2000 --map DIR/map.json --merge     update mode
  options: --compact (md without approvals, for long threads)  --max N (chars per row)
           --origin O=<session>[:FROM-TO]  the session where the user ASKED for this look: their words
                   there are intent evidence (principles, scope), kept apart from the request ledger

Besides the user's words the ledger also lists, separately and never as user voice:
  origin  what the user said in the session that asked for this look (--origin)
  gaps    OPTIONAL reading: what the agent itself said it had NOT done ("没读/未核/未验证/没测/not read/
          unverified/TODO"), from its messages and subagent reports, with a guess whether a later tool call
          touched the same object. Listed in ledger.md only; nothing needs confirming one by one. A gap reaches
          the answer only when the observer checked it, it bears on this question, and it is still open.
A message re-sent at the top of a continuation file (same text, already in an earlier file) is
dropped and counted, not listed twice.

Sessions may be given as ALIAS=path or bare paths (aliases A, B, C... are assigned).
Writes DIR/ledger.json and DIR/ledger.md when --out is given, else prints the md.
Request ids are numbered over the whole files, so --since never renumbers; --map reuses the ids
of an existing map (matched by position) and numbers new requests after its highest id.
--skeleton writes a starting map.json (no resources block: inventory.py fills that); approvals are folded
into the request before them.
Passwords, tokens and keys are masked in the text ("[已遮蔽]") and counted in the header.

What counts as the user (Claude Code transcript):
  kept    type=user text / string content; slash-command args (<command-args>);
          <pasted_content> (kept, tags removed);
          mid-turn interjections = attachment.queued_command with origin.kind == "human"
          (these are NOT type=user records - older extractors silently lost them)
  dropped isMeta records (skill bodies injected by the harness), isCompactSummary (compaction
          summary), tool_result lists, isSidechain (a subagent's "user" is its parent agent),
          <task-notification>/<system-reminder>/<local-command-stdout> wrappers,
          "Base directory for this skill:" / "This session is being continued" / "Caveat:" texts,
          queue-operation records (UI echo of the same text), uuid re-emissions around compaction
Codex rollout:
  kept    response_item message role=user; text after the last "## My request:" when present;
          <send_user_message_question_reply> answers (kind=answer)
  dropped AGENTS.md / <INSTRUCTIONS> / <environment_context> / <skill> / <recommended_plugins> /
          <in-app-browser-context> / <codex_internal_context> (goal auto-continue) / <system-reminder>
Kimi / dsh: basic support through session_events (user_msg events, same wrapper stripping).

Every dropped record is COUNTED by reason in the header, so a wrong filter is visible, not silent.
kind_guess and segment stats are a skeleton for the map; the agent fills goal and status.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from session_events import detect_format, iter_events, open_session_text  # noqa: E402

WRAPPERS = [r"<system-reminder>.*?</system-reminder>", r"<task-notification>.*?</task-notification>",
            r"<local-command-stdout>.*?</local-command-stdout>", r"<local-command-caveat>.*?</local-command-caveat>",
            r"<skill>.*?</skill>", r"<in-app-browser-context.*?</in-app-browser-context>",
            r"<recommended_plugins>.*?</recommended_plugins>", r"<environment_context>.*?</environment_context>",
            r"<codex_internal_context.*?</codex_internal_context>", r"<user_instructions>.*?</user_instructions>",
            r"<INSTRUCTIONS>.*?</INSTRUCTIONS>", r"<permissions instructions>.*?</permissions instructions>"]
INJECT_PREFIX = {
    "Base directory for this skill": "skill 注入",
    "This session is being continued from a previous conversation": "压缩摘要",
    "Caveat: The messages below were generated": "本地命令说明",
    "# AGENTS.md instructions": "AGENTS.md 注入",
    "Stop hook feedback": "hook 反馈",
    "<task-notification>": "任务通知",
    "<system-reminder>": "系统提醒",
    "<environment_context>": "环境块",
    "<user_instructions>": "AGENTS.md 注入",
    "<INSTRUCTIONS>": "AGENTS.md 注入",
    "<permissions": "权限说明",
    "<skill>": "skill 注入",
    "<recommended_plugins>": "插件清单",
    "<in-app-browser-context": "浏览器说明",
    '<codex_internal_context source="goal">': "goal 自动续跑",
    "User activated the skill": "skill 注入",
    "<notification": "任务通知",
}
HARNESS_CMDS = {"model", "config", "clear", "compact", "cost", "status", "login", "logout", "resume", "memory",
                "permissions", "effort", "fast", "theme", "doctor", "help", "exit", "context", "mcp", "hooks", "plugin"}
APPROVAL = re.compile(r"^(好|好的|可以|行|是的|对|嗯|继续|开始|ok|OK|yes|go|可以开始了?|按照?这个来?做?|很棒|没问题)[。！!,，\s]*")
CORRECTION = re.compile(r"((?<!是)不是[^吗]{0,30}(而是|是)|不要|别再|错了|不对|而不是|你没有|没有理解|很明显|并不是|搞错|不应该|理解有.{0,4}问题"
                        # frustration is a correction signal too: the user is telling the agent it is off track
                        r"|很烦|好烦|烦死|无语|(?:^|[\s，。！!])操(?![作心场练控纵])|说了.{0,4}(遍|次)|怎么又|还是不行|还是不对)")
# Secrets pass straight from a session into a report that may be forwarded: mask the value, keep the label.
MASKS = [
    (re.compile(r"((?:密码|口令)\s*[:：=是为]?\s*)[A-Za-z0-9_@#$%^&*!.+=/\-]{3,}"), r"\1[已遮蔽]"),
    (re.compile(r"((?:passcode|password|passwd)\s*[:：=]\s*)\S{3,}", re.I), r"\1[已遮蔽]"),
    (re.compile(r"((?:token|api[_\- ]?key|secret|access[_\- ]?key)\s*[:：=]\s*)\S{6,}", re.I), r"\1[已遮蔽]"),
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{12,}"), "[已遮蔽]"),
    (re.compile(r"\b(?:ghp|gho|github_pat|xox[abp])_[A-Za-z0-9_]{16,}"), "[已遮蔽]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[已遮蔽]"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{16,}"), r"\1[已遮蔽]"),
]
MASK_TOKEN = "[已遮蔽]"


def mask(txt: str) -> tuple[str, int]:
    n = 0
    for pat, rep in MASKS:
        txt, k = pat.subn(rep, txt)
        n += k
    return txt, n


class Drops(Counter):
    """Dropped records by reason, counted only inside the window being extracted (--since),
    so the header matches the rows below it."""

    def __init__(self, since: int = 0):
        super().__init__()
        self.since = since

    def add(self, line: int, why: str):
        if line >= self.since:
            self[why] += 1
# A request that asks the agent to FIND OUT what the user wants can only be closed by the user confirming;
# the agent's own answer is a claim. The skeleton flags these (clarify: true).
CLARIFY = re.compile(r"(搞清楚?|弄清楚?|理解|想清楚|明白)[^。！？?]{0,14}(想|要|目的|意图|本意|需求|问题)|想解决什么|先问(我|清楚)"
                     r"|figure out what|understand what .{0,20}(want|need|trying)", re.I)
# What the agent says it has not done. Kept apart from the user's words; each is checked later.
GAP = re.compile(r"(没有?(?:来得及)?(?:读|核对?|验证|测试?|跑)|未(?:读|核对?|验证|测试?|跑)|not (?:yet )?(?:read|verified|tested|checked)"
                 r"|(?:haven't|didn't|did not) (?:read|verify|test|check)|unverified|untested|\bTODO\b)", re.I)
# A clause with a gap phrase is NOT a gap when it says the thing was done, that nothing is left, quotes a rule,
# or asks a question. Each skip is counted by reason in ledger.md, so a wrong filter is visible.
GAP_SKIP = [
    (re.compile(r"已(?:经)?[^，。；,;]{0,16}(?:验证|核对?|读|测试?|跑)(?:过|完)|都(?:已经?)?(?:验证|核对?|读|测)过"
                r"|(?:all|already) (?:been )?(?:verified|tested|checked|read)", re.I), "说的是已经做过"),
    (re.compile(r"没有[^，。；,;]{0,24}了\s*[。！!]?$|\bno (?:more |remaining )?TODOs?\b|无\s*TODO|[：:]\s*(?:无|没有|none)\s*[。.]?$"
                r"|清空|清掉|清完|nothing left", re.I), "说的是没有剩下的"),
    (re.compile(r"(?:说|写着|写道|提到|规定|约定|要求)[：:，,]|(?:他|她|用户|对方|你|they|he|she) ?(?:说|讲|said|says)|规则|规范|原则|约定|第\s*[\d一二三四五六七八九十]+\s*条|要标|必须|不得"
                r"|\b(?:rule|must|policy)\b", re.I), "转述规则或别人的话"),
    (re.compile(r"(?:要不要|是否|需不需要|有没有)[^，。；,;]{0,16}[？?]\s*$|[？?]\s*$"), "是提问"),
]
# a mid-turn remark is kept only when it is first-person or clearly "not yet"; the turn's last message
# (what the agent hands to the user) and subagent reports are always kept
OWNED = re.compile(r"我|咱|还没|尚未|没来得及|暂未|暂时没|\bI\b|\bwe\b|not yet|haven't|hasn't|still", re.I)
SENTENCE = re.compile(r"[^。！？!?\n]+[。！？!?]?")
CLAUSE = re.compile(r"[^，,；;。！？!?\n]+[，,；;。！？!?]?")
RULE_REASON = "转述规则或别人的话"  # judged on the whole sentence: "按约定，未读的文件要列出" is a rule
SUBAGENT_TOOLS = {"Agent", "Task", "spawn_agent", "wait_agent", "wait", "create_subagent", "send_input", "SendMessage"}
OBJ = [re.compile(r"`([^`\n]{3,80})`"), re.compile(r"「([^」\n]{2,40})」"), re.compile(r"“([^”\n]{2,40})”"),
       re.compile(r"([\w\-./~]+\.(?:md|py|json|jsonl|ts|tsx|js|html|css|yaml|yml|toml|sh|txt|sql|rs|go))\b")]
TESTISH = re.compile(r"测|test", re.I)
TEST_CMD = re.compile(r"pytest|unittest|\b(?:npm|pnpm|yarn|bun) (?:run )?test|jest|vitest|playwright|cypress|go test|cargo test"
                      r"|make test|mvn test|gradle test|rspec|phpunit", re.I)
GAP_CAP = 40
WORD = re.compile(r"(--[a-z][\w\-]{2,}|[A-Za-z_][\w]*[_.][\w.]+|[a-z]+[A-Z]\w+|[A-Z][a-z]+[A-Z]\w+)")
STOP = {"e.g", "i.e", "etc", "todo", "not_read", "unverified"}
# closed values the SCRIPT writes (guesses); only the agent writes yes / no / n/a after checking
GUESSES = ("maybe", "guess_no", "unknown")


def gap_clauses(txt: str) -> list[tuple[str, str | None]]:
    """(clause, skip reason or None) for every clause holding a gap phrase. One sentence may carry
    several gaps ("没跑端到端测试，也没读模板"), so each clause is its own item."""
    out = []
    for sm in SENTENCE.finditer(txt or ""):
        sent = sm.group(0)
        if not GAP.search(sent):
            continue
        rule = next((pat for pat, w in GAP_SKIP if w == RULE_REASON), None)
        whole = RULE_REASON if rule is not None and rule.search(sent) else None
        for m in CLAUSE.finditer(sent):
            c = norm(m.group(0)).rstrip("，,；;")
            if not GAP.search(c):
                continue
            c = c[:140]
            why = whole or next((w for pat, w in GAP_SKIP if pat.search(c)), None)
            out.append((c, why))
    return out


class Notes:
    """Side channel while reading a session: gap phrases and tool-call arguments (to check closure)."""

    def __init__(self):
        self.gaps: list[dict] = []
        self.calls: list[tuple[int, str]] = []
        self.names: dict[str, str] = {}
        self.says: list[int] = []  # lines with agent prose, to tell a turn's last message
        self.skipped: Counter = Counter()
        self.over = 0

    def scan(self, n: int, txt: str, who: str):
        if who == "agent":
            self.says.append(n)
        for c, why in gap_clauses(txt):
            if why:
                self.skipped[why] += 1
                continue
            if any(g["says"] == c for g in self.gaps):
                continue
            self.gaps.append({"line": n, "says": mask(c)[0], "who": who})

    def call(self, n: int, name: str, args: str):
        self.calls.append((n, f"{name} {args[:4000]}"))

    def keep(self, user_lines: list[int]):
        """Drop mid-turn agent remarks that are not first-person; apply the cap."""
        says, users = sorted(self.says), sorted(user_lines)
        kept = []
        for g in self.gaps:
            if g["who"] == "agent" and not OWNED.search(g["says"]):
                nxt = next((u for u in users if u > g["line"]), 10**12)
                if any(g["line"] < s < nxt for s in says):
                    self.skipped["回合中途的叙述（不是交给你的话，也不是第一人称）"] += 1
                    continue
            if len(kept) >= GAP_CAP:
                self.over += 1
                continue
            kept.append(g)
        self.gaps = kept

    def closure(self, alias: str) -> list[dict]:
        """A GUESS per gap: maybe (a later call touched its object), guess_no (nothing later did), unknown
        (nothing to trace). The agent turns it into yes / no / n/a after looking."""
        out = []
        for g in self.gaps:
            objs = [x.strip() for pat in OBJ for x in pat.findall(g["says"]) if len(x.strip()) >= 2]
            if not objs:  # fall back to code-like words: snake_case, dotted, CamelCase, --flags
                objs = [w for w in WORD.findall(g["says"]) if w.lower() not in STOP]
            item = {"ref": f"{alias}:{g['line']}", "line": g["line"], "says": g["says"], "who": g["who"]}
            later = [(n, body) for n, body in self.calls if n > g["line"]]
            if objs:
                hit = next(((n, o) for n, body in later for o in objs if o in body), None)
                if hit:
                    item.update(closed="maybe", closed_at=f"{alias}:{hit[0]}",
                                why=f"后面有工具调用碰到「{hit[1][:40]}」，是否真补上要看那一行")
                else:
                    item.update(closed="guess_no", why=f"后面没有工具调用碰到「{objs[0][:40]}」（脚本的猜测）")
            elif TESTISH.search(g["says"]):
                hit = next((n for n, body in later if TEST_CMD.search(body)), None)
                if hit:
                    item.update(closed="maybe", closed_at=f"{alias}:{hit}", why="后面跑过测试命令，是不是这一项要看那一行")
                else:
                    item.update(closed="guess_no", why="后面没有跑测试的命令（脚本的猜测）")
            else:
                item.update(closed="unknown", why="句子里没有能追的对象（文件、代码名、引号里的词），自己核")
            out.append(item)
        return out


QUESTION = re.compile(r"([?？]\s*$|吗[\s。]*$|呢[\s。]*$|怎么|为什么|什么|是否|有没有|你觉得)")


def local_time(ts: str) -> str:
    """ISO timestamp (UTC 'Z' or with offset) -> 'MM-DD HH:MM' in this machine's time zone."""
    if not ts:
        return ""
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts[5:16].replace("T", " ")
    if d.tzinfo is not None:
        d = d.astimezone()
    return d.strftime("%m-%d %H:%M")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def classify_prefix(txt: str) -> str | None:
    head = txt.lstrip()[:120]
    for p, why in INJECT_PREFIX.items():
        if head.startswith(p):
            return why
    if "AGENTS.md instructions" in txt[:200]:
        return "AGENTS.md 注入"
    return None


def clean(txt: str) -> str:
    for p in WRAPPERS:
        txt = re.sub(p, "", txt, flags=re.S)
    txt = re.sub(r"</?pasted_content[^>]*>", "", txt)
    return norm(txt)


def guess_kind(txt: str, source: str) -> str:
    if source == "queued":
        return "interjection"
    if source == "answer":
        return "answer"
    if source in ("interrupt", "command"):
        return source
    if len(txt) <= 24 and APPROVAL.match(txt):
        return "approval"
    if CORRECTION.search(txt):
        return "correction"
    if QUESTION.search(txt) and len(txt) < 120:
        return "question"
    return "request"


def human_in_tool_result(x: dict):
    """The user speaks inside tool results too: AskUserQuestion answers and tool-use rejections."""
    body = x.get("content")
    if isinstance(body, list):
        body = " ".join(str(i.get("text", "")) for i in body if isinstance(i, dict))
    if not isinstance(body, str):
        return None
    if body.startswith("The user answered:"):
        pairs = re.findall(r'"(.*?)"="(.*?)"(?:,|\.)', body, re.S)
        if pairs:
            return " ／ ".join(f"[答] {norm(q)[:80]} → {norm(a)}" for q, a in pairs), "answer"
        return "[答] " + norm(body[len("The user answered:"):])[:600], "answer"
    if body.startswith("The user doesn't want to proceed with this tool use"):
        why = body.split("the user said:", 1)[1] if "the user said:" in body else ""
        return ("[拒绝了这次工具调用] " + norm(why)[:600]).strip(), "interrupt"
    return None


def claude_rows(path: Path, dropped: "Drops", seg: "Segments", notes: "Notes"):
    seen_uuid: set[str] = set()
    with open_session_text(path) as fh:
        for n, raw in enumerate(fh, 1):
            try:
                d = json.loads(raw)
            except Exception:
                dropped.add(n, "坏行")
                continue
            if not isinstance(d, dict):
                continue
            uid = d.get("uuid")
            if isinstance(uid, str) and uid:
                if uid in seen_uuid:
                    dropped.add(n, "压缩前重放(同 uuid)")
                    continue
                seen_uuid.add(uid)
            t = d.get("type")
            ts = str(d.get("timestamp") or "")
            seg.stamp(n, ts)
            if d.get("isSidechain"):
                if t == "user":
                    dropped.add(n, "子 agent 记录")
                continue
            if t == "system" and d.get("subtype") == "compact_boundary":
                seg.compaction(n)
                continue
            if t == "attachment":
                att = d.get("attachment") or {}
                if att.get("type") == "queued_command":
                    origin = att.get("origin") if isinstance(att.get("origin"), dict) else {}
                    prompt = att.get("prompt") if isinstance(att.get("prompt"), str) else ""
                    if origin.get("kind") == "human" and prompt.strip():
                        why = classify_prefix(prompt)
                        if why:
                            dropped.add(n, why)
                        else:
                            yield n, ts, clean(prompt), "queued"
                    else:
                        dropped.add(n, "排队的通知/非人消息")
                continue
            if t == "assistant":
                content = (d.get("message") or {}).get("content")
                if isinstance(content, list):
                    for it in content:
                        if isinstance(it, dict) and it.get("type") == "tool_use":
                            inp = it.get("input") if isinstance(it.get("input"), dict) else {}
                            seg.tool(n, str(it.get("name") or "?"), inp.get("file_path") or inp.get("notebook_path"),
                                     it.get("name") in ("Edit", "Write", "MultiEdit", "NotebookEdit"),
                                     it.get("name") in ("Agent", "Task"))
                            notes.names[str(it.get("id"))] = str(it.get("name") or "?")
                            notes.call(n, str(it.get("name") or "?"), json.dumps(inp, ensure_ascii=False))
                        elif isinstance(it, dict) and it.get("type") == "text":
                            seg.say(n)
                            notes.scan(n, str(it.get("text") or ""), "agent")
                continue
            if t != "user":
                continue
            if d.get("isCompactSummary"):
                dropped.add(n, "压缩摘要")
                seg.compaction(n)
                continue
            if d.get("isMeta"):
                dropped.add(n, "skill/命令注入(isMeta)")
                continue
            c = (d.get("message") or {}).get("content")
            if isinstance(c, list):
                results = [x for x in c if isinstance(x, dict) and x.get("type") == "tool_result"]
                if results:
                    for x in results:
                        if notes.names.get(str(x.get("tool_use_id"))) in SUBAGENT_TOOLS:
                            body = x.get("content")
                            body = body if isinstance(body, str) else " ".join(
                                str(i.get("text", "")) for i in body or [] if isinstance(i, dict))
                            notes.scan(n, body, "subagent")
                        got = human_in_tool_result(x)
                        if got:
                            yield n, ts, got[0], got[1]
                    continue
                parts = []
                for x in c:
                    if not isinstance(x, dict):
                        continue
                    if x.get("type") == "text":
                        parts.append(str(x.get("text") or ""))
                    elif x.get("type") == "image":
                        parts.append("[图片]")
                txt = "\n".join(parts)
            elif isinstance(c, str):
                txt = c
            else:
                continue
            if txt.strip().startswith("[Request interrupted by user"):
                yield n, ts, "[用户中断了执行]", "interrupt"
                continue
            if "<command-name>" in txt:
                name = re.search(r"<command-name>(.*?)</command-name>", txt, re.S)
                args = re.search(r"<command-args>(.*?)</command-args>", txt, re.S)
                a = norm(args.group(1)) if args else ""
                nm = norm(name.group(1)) if name else "?"
                if nm.lstrip("/") in HARNESS_CMDS:
                    yield n, ts, f"[命令 {nm}] {a}".strip(), "command"
                elif not a and "<local-command-stdout>" not in txt:
                    yield n, ts, f"[命令 {nm}]", "command"
                elif a:
                    yield n, ts, f"[{nm}] {a}", "user"
                else:
                    dropped.add(n, "本地命令输出")
                continue
            why = classify_prefix(txt)
            if why:
                dropped.add(n, why)
                continue
            body = clean(txt)
            if body:
                yield n, ts, body, "user"
            else:
                dropped.add(n, "只有包装标签")


def codex_rows(path: Path, dropped: "Drops", seg: "Segments", meta: dict, notes: "Notes"):
    with open_session_text(path) as fh:
        for n, raw in enumerate(fh, 1):
            try:
                d = json.loads(raw)
            except Exception:
                dropped.add(n, "坏行")
                continue
            t = d.get("type")
            ts = str(d.get("timestamp") or "")
            seg.stamp(n, ts)
            p = d.get("payload") or {}
            if t == "session_meta" and "source" not in meta:
                s = p.get("source")
                meta["source"] = s if isinstance(s, str) else "subagent"
            if t == "compacted":
                seg.compaction(n)
                continue
            if t != "response_item" or not isinstance(p, dict):
                continue
            pt = p.get("type")
            if pt in ("function_call", "custom_tool_call"):
                name = str(p.get("name") or "?")
                target = None
                args = p.get("arguments") or p.get("input") or ""
                if name == "apply_patch" or "apply_patch" in str(args)[:200]:
                    m = re.search(r"\*\*\* (?:Update|Add) File: (\S+)", str(args))
                    target = m.group(1) if m else "?"
                seg.tool(n, name, target, target is not None, name in ("spawn_agent", "create_subagent"))
                notes.names[str(p.get("call_id"))] = name
                notes.call(n, name, str(args))
                continue
            if pt in ("function_call_output", "custom_tool_call_output"):
                if notes.names.get(str(p.get("call_id"))) in SUBAGENT_TOOLS:
                    out = p.get("output")
                    notes.scan(n, out if isinstance(out, str) else json.dumps(out, ensure_ascii=False), "subagent")
                continue
            if pt != "message":
                continue
            if p.get("role") == "assistant":
                seg.say(n)
                notes.scan(n, " ".join(c.get("text", "") for c in p.get("content") or [] if isinstance(c, dict)), "agent")
                continue
            if p.get("role") != "user":
                continue
            txt = " ".join(c.get("text", "") for c in p.get("content") or [] if isinstance(c, dict))
            if "<send_user_message_question_reply>" in txt:
                yield n, ts, answer_text(txt), "answer"
                continue
            if "## My request:" in txt:
                files = re.findall(r"^## ([^\n]+?): ", txt, re.M)
                body = txt.rsplit("## My request:", 1)[1]
                extra = [f for f in files if f != "My request"]
                if extra:
                    body = f"[附件 {', '.join(extra[:4])}] " + body
                txt = body
            why = classify_prefix(txt)
            if why:
                dropped.add(n, why)
                if why == "goal 自动续跑":
                    seg.auto(n)
                continue
            body = clean(txt)
            if body.startswith("# Files mentioned by the user:"):
                body = "[附件] " + body[len("# Files mentioned by the user:"):]
            if body:
                yield n, ts, body, "user"
            else:
                dropped.add(n, "只有包装标签")


def answer_text(txt: str) -> str:
    """Codex structured answers: keep 'Q → A', drop the call ids."""
    body = txt.replace("<send_user_message_question_reply>", "").replace("</send_user_message_question_reply>", "")
    try:
        items = json.loads(body.strip())
        return " ／ ".join(f"[答] {norm(str(i.get('question', '')))[:80]} → {norm(str(i.get('answer', '')))}"
                           for i in items if isinstance(i, dict))
    except Exception:
        return clean(body)[:600]


def generic_rows(path: Path, dropped: "Drops", seg: "Segments", notes: "Notes"):
    for e in iter_events(path):
        seg.stamp(e.line, str((e.extra or {}).get("timestamp") or ""))
        if e.kind == "user_msg":
            if e.extra.get("ambient"):
                dropped.add(e.line, "环境注入")
                continue
            if e.text.startswith("User activated the skill"):
                # Kimi slash-skill: the skill body is injected, the user's words are in args="…"
                nm = re.search(r'skill "([^"]+)"', e.text)
                ar = re.search(r'args="([^"]*)"', e.text)
                dropped.add(e.line, "skill 注入")
                if ar and ar.group(1).strip():
                    yield e.line, "", f"[/{nm.group(1) if nm else '?'}] {norm(ar.group(1))}", "user"
                continue
            why = classify_prefix(e.text)
            if why:
                dropped.add(e.line, why)
                continue
            body = clean(e.text)
            if body:
                yield e.line, "", body, "queued" if e.extra.get("queued") else "user"
        elif e.kind == "tool_call":
            seg.tool(e.line, e.name, None, bool((e.extra or {}).get("patch_targets")), e.name in ("Agent", "Task"))
            notes.call(e.line, e.name, e.text or "")
        elif e.kind == "compaction":
            seg.compaction(e.line)
        elif e.kind == "assistant_msg":
            seg.say(e.line)
            notes.scan(e.line, e.text or "", "agent")


class Segments:
    """Work between consecutive requests: the skeleton of the process layer."""

    def __init__(self):
        self.events: list[tuple] = []
        self.first_ts: dict[int, str] = {}
        self.last_line = 0

    def stamp(self, n, ts):
        self.last_line = n
        if ts:
            self.first_ts.setdefault(n, ts)

    def tool(self, n, name, target, is_write, is_agent):
        self.events.append((n, "tool", name, target if is_write else None, is_agent))

    def compaction(self, n):
        # a boundary record and the summary record describe ONE compaction
        if any(x[1] == "compaction" and 0 <= n - x[0] <= 5 for x in self.events[-50:]):
            return
        self.events.append((n, "compaction", "", None, False))

    def say(self, n):
        self.events.append((n, "say", "", None, False))

    def auto(self, n):
        # Codex goal mode re-prompts the agent by itself: a long run of these is the agent driving alone
        self.events.append((n, "auto", "", None, False))

    def build(self, alias, starts):
        out = []
        bounds = starts + [self.last_line + 1]
        for i, s in enumerate(starts):
            e = bounds[i + 1] - 1
            evs = [x for x in self.events if s <= x[0] <= e]
            tools = Counter(x[2] for x in evs if x[1] == "tool")
            writes = sorted({Path(x[3]).name for x in evs if x[3]})
            ts0 = next((self.first_ts[k] for k in range(s, e + 1) if k in self.first_ts), "")
            ts1 = next((self.first_ts[k] for k in range(e, s - 1, -1) if k in self.first_ts), "")
            out.append({"span": f"{alias}:{s}-{e}", "from": local_time(ts0), "to": local_time(ts1),
                        "tool_calls": sum(tools.values()), "top_tools": dict(tools.most_common(4)),
                        "subagents": sum(1 for x in evs if x[4]),
                        "compactions": sum(1 for x in evs if x[1] == "compaction"),
                        "auto_continues": sum(1 for x in evs if x[1] == "auto"),
                        "files_written": writes[:8], "files_written_n": len(writes)})
        return out


def load(alias: str, path: Path, since: int):
    """All requests of one session (numbering must not depend on --since); rows before `since`
    are marked in_window=False. Drop counts and masking counts cover the window only."""
    fmt = detect_format(path)
    dropped = Drops(since)
    seg = Segments()
    notes = Notes()
    meta: dict = {}
    if fmt == "claude_code":
        gen = claude_rows(path, dropped, seg, notes)
    elif fmt == "codex":
        gen = codex_rows(path, dropped, seg, meta, notes)
    else:
        gen = generic_rows(path, dropped, seg, notes)
    rows, seen, masked, user_lines = [], {}, 0, []
    for n, ts, txt, source in gen:
        user_lines.append(n)
        key = norm(txt)[:300]
        if key in seen and source in ("user", "queued"):
            # the same sentence again: an echo (queue + delivery) or the user re-sending after an error.
            # Keep the position on the first occurrence so the repeat stays visible and counted as covered.
            first = seen[key]
            if n - first["line"] > 3:
                first.setdefault("repeats", []).append(f"{alias}:{n}")
            dropped.add(n, "重复(同一句已记录)")
            continue
        txt, k = mask(txt)
        if n >= since:
            masked += k
        row = {"ref": f"{alias}:{n}", "line": n, "ts": ts, "when": local_time(ts), "source": source,
               "kind_guess": guess_kind(txt, source), "text": txt, "in_window": n >= since}
        if source in ("user", "queued") and CLARIFY.search(txt):
            row["clarify"] = True
        seen[key] = row
        rows.append(row)
    if rows and rows[0]["kind_guess"] == "correction":
        # the first thing said in a file corrects nothing yet: "不要改表结构" there is a principle, not a correction
        rows[0]["kind_guess"] = "request" if not (QUESTION.search(rows[0]["text"]) and len(rows[0]["text"]) < 120) else "question"
    for r, sg in zip(rows, seg.build(alias, [r["line"] for r in rows]) if rows else []):
        r["segment"] = sg
    interactive = "no" if meta.get("source") in ("exec", "subagent") else "yes"
    if masked:
        dropped["遮蔽的密码/密钥（不是剥掉，是把值换成 [已遮蔽]）"] = masked
    notes.keep(user_lines)
    every = notes.closure(alias)
    gaps = [g for g in every if g["line"] >= since]
    return {"alias": alias, "path": str(path), "format": fmt, "lines": seg.last_line, "since": since,
            "source": meta.get("source"), "interactive": interactive,
            "dropped": dict(dropped.most_common()), "requests": rows, "gaps": gaps, "gaps_over_cap": notes.over,
            "gaps_skipped": dict(notes.skipped.most_common()), "gaps_all": every}


def to_md(led: dict, maxc: int, compact: bool) -> str:
    out = ["# 请求账本骨架（脚本生成；goal / status 由 agent 补）", ""]
    for s in led["sessions"]:
        win = f" · 本次只抽 {s['since']} 行之后" if s.get("since") else ""
        out.append(f"- **{s['alias']}** `{s['path']}` · {s['format']} · {s['lines']} 行 · "
                   f"人话 {s['n_requests']} 条{win} · interactive={s['interactive']}")
        if s["dropped"]:
            out.append("  - 剥掉：" + "，".join(f"{k} {v}" for k, v in s["dropped"].items()))
    for s in led.get("origin_sessions") or []:
        out.append(f"- **{s['alias']}**（发起这次查看的会话）`{s['path']}` · {s['format']} · 收了 "
                   f"{sum(1 for o in led.get('origin') or [] if o['ref'].split(':')[0] == s['alias'])} 条你的话")
    rows = led["requests"]
    folded = [r for r in rows if compact and r["kind_guess"] == "approval"]
    if folded:
        out.append(f"- 紧凑视图：{len(folded)} 条批准（继续/可以…）没列出，见 ledger.json；原话截到 {maxc} 字")
    out += ["", "| # | 位置 | 时间 | 类型(猜) | 原话 | 之后的工作 |", "|---|---|---|---|---|---|"]
    for r in rows:
        if r in folded:
            continue
        t = r["text"].replace("|", "｜")
        t = t[:maxc] + (f"…(+{len(t) - maxc})" if len(t) > maxc else "")
        sg = r.get("segment") or {}
        work = (f"{sg.get('tool_calls', 0)} 次调用" + (f"，写 {sg['files_written_n']} 个文件" if sg.get("files_written_n") else "")
                + (f"，派 {sg['subagents']} 个子 agent" if sg.get("subagents") else "")
                + (f"，自动续跑 {sg['auto_continues']} 次" if sg.get("auto_continues") else "")
                + (f"，压缩 {sg['compactions']} 次" if sg.get("compactions") else ""))
        if r.get("repeats"):
            t += f"（又说了一次：{'、'.join(r['repeats'])}）"
        if r.get("clarify"):
            t = "〔要 agent 弄清意图：只能由你确认来闭合〕" + t
        if r.get("resent"):
            t += f"（续开文件里重发：{'、'.join(r['resent'])}）"
        out.append(f"| {r['id']} | {r['ref']} | {r.get('when', '')} | {r['kind_guess']} | {t} | {work} |")
    if not rows:
        out.append("\n**0 条人话**：先怀疑仪器（格式、范围、过滤），再下结论说用户什么都没说。")
    if led.get("origin"):
        out += ["", "## 发起这次查看的会话里，你说的话（意图证据，不是被观察会话的请求）", "",
                "| # | 位置 | 时间 | 原话 |", "|---|---|---|---|"]
        out += [f"| {o['id']} | {o['ref']} | {o.get('when', '')} | {o['text'][:maxc].replace('|', '｜')} |" for o in led["origin"]]
    gaps = led.get("gaps") or []
    if gaps:
        lab = {"maybe": "可能补了", "guess_no": "像是没补", "unknown": "待核"}
        out += ["", "## agent 自己说没做的（可选参考，不是你的话；只有和这次答案直接相关、核过确实没补上的，才写进答案的「缺什么」）", "",
                "| # | 位置 | 谁说的 | 原句 | 后来 |", "|---|---|---|---|---|"]
        out += [f"| {g['id']} | {g['ref']} | {'子 agent 报告' if g['who'] == 'subagent' else 'agent'} | "
                f"{g['says'].replace('|', '｜')} | {lab.get(g['closed'], g['closed'])}"
                f"{' ' + g['closed_at'] if g.get('closed_at') else ''}：{g.get('why', '')} |" for g in gaps]
        out.append("\n「后来」一栏是脚本的猜测，不用逐条确认。")
        over = sum(s.get("gaps_over_cap") or 0 for s in led["sessions"])
        if over:
            out.append(f"\n另有 {over} 处同类说法超过上限没列出（每个会话最多 {GAP_CAP} 条）。")
    skipped = Counter()
    for s in led["sessions"]:
        skipped.update(s.get("gaps_skipped") or {})
    if skipped:
        out.append("\n含「没读/未验证/TODO」一类字眼、但没当缺口列出的：" + "，".join(f"{k} {v}" for k, v in skipped.most_common())
                   + "。筛错了就用 drill.py --role assistant --grep 找回来。")
    return "\n".join(out) + "\n"


def verbatim(txt: str) -> str:
    """The part of a ledger line that literally occurs in the raw record (labels added here are removed),
    so map_check --verify can check it."""
    if txt.startswith("[答]"):
        return txt.split(" → ", 1)[1].split(" ／ ")[0] if " → " in txt else txt[4:]
    txt = re.sub(r"^\[(附件|命令|图片|拒绝了这次工具调用)[^\]]*\]\s*", "", txt)
    txt = re.sub(r"^\[/?[\w\-:]+\]\s*", "", txt)  # [/slash-command] prefix
    txt = re.sub(r"<image[^>]*>", "", txt).strip()
    return txt or "[无文字]"


# kinds whose status must be judged one by one; the rest may inherit their goal's status
OWN_STATUS = {"correction", "interjection", "interrupt"}


def stub(r: dict) -> dict:
    n = {"id": r["id"], "at": r["ref"], "quote": verbatim(r["text"])[:160], "kind": r["kind_guess"], "goal": None}
    if r.get("clarify"):
        n["clarify"] = True
    if r["kind_guess"] == "command":
        n["status"] = "n/a"
    elif r["kind_guess"] in OWN_STATUS:
        n["status"] = None
    if r.get("repeats"):
        n["also"] = list(r["repeats"])
    return n


def stubs(reqs: list[dict]) -> list[dict]:
    """One stub per request; an approval ('继续', '可以') is folded into the `also` of the request before it,
    so a long thread does not turn into hundreds of rows. The agent can split one back out."""
    out = []
    for r in reqs:
        if r["kind_guess"] == "approval" and out and out[-1]["at"].split(":")[0] == r["ref"].split(":")[0]:
            out[-1].setdefault("also", []).append(r["ref"])
            out[-1].setdefault("also_ids", []).append(r["id"])  # so "R63" can be shown as "已折进 R62"
            continue
        out.append(stub(r))
    return out


def origin_stub(o: dict) -> dict:
    return {"id": o["id"], "at": o["ref"], "quote": verbatim(o["text"])[:200], "use": ""}


def session_stub(s: dict, role: str = "") -> dict:
    out = {"alias": s["alias"], "path": s["path"], "platform": s["format"], "when": "",
           "ledger_through": s["lines"], "read": "", "state": "", "note": ""}
    if role:
        out["role"] = role
    return out


def skeleton(led: dict) -> dict:
    """A starting map: the ledger is copied in so no request can be silently left out."""
    m = {
        "schema": "task-map/1.2", "title": "", "subject": "", "question": "", "updated": "", "tier": "solo",
        "answer": {"type": "", "lead": "", "points": [], "missing": []},
        "sessions": [session_stub(s) for s in led["sessions"]] + [session_stub(s, "origin") for s in led.get("origin_sessions") or []],
        "headline": {**{k: {"text": "", "refs": []} for k in ("want", "progress", "problem", "next")},
                     "alignment": {"state": None, "text": "", "refs": []}},
        "requests": stubs(led["requests"]),
        "goals": [], "plan": {"now": None, "steps": []}, "layers": [], "process": [], "assets": [], "problems": [],
        "collab": {"user": [], "agent": []}, "next": [], "questions": [],
        "method": {"how": "", "checks": "", "cost": "", "unread": []},
    }
    if led.get("origin"):
        m["origin"] = [origin_stub(o) for o in led["origin"]]
    return m


def align_ids(led: dict, old: dict) -> int:
    """Update mode: requests already in the old map keep their id (matched by position; a position found in
    another request's `also` is marked folded_into). A new request keeps its whole-file number when the map
    does not use it yet, else gets the next free number. Returns how many were new."""
    known, folded = {}, {}
    for r in old.get("requests") or []:
        known[r.get("at")] = r.get("id")
        for at in r.get("also") or []:
            folded[at] = r.get("id")
    used = {str(r.get("id")) for r in old.get("requests") or []}
    top = max([int(m.group(1)) for i in used if (m := re.match(r"R(\d+)$", i))] or [0])
    new = 0
    for r in led["requests"]:
        if r["ref"] in known:
            r["id"] = known[r["ref"]]
        elif r["ref"] in folded:
            r["id"] = folded[r["ref"]] + "+"
            r["folded_into"] = folded[r["ref"]]
        else:
            new += 1
            if r["id"] in used:
                top += 1
                while f"R{top}" in used:
                    top += 1
                r["id"] = f"R{top}"
            used.add(r["id"])
    return new


def dedupe_resent(led: dict) -> list[dict]:
    """A continuation file often starts by re-sending the last message of the file before it. The same text
    (12+ chars) already recorded from an EARLIER session is dropped here and counted in that session's header;
    the first occurrence keeps the position in `resent`. Short approvals ("继续") are real new turns."""
    seen: dict[str, dict] = {}
    by_alias = {s["alias"]: s for s in led["sessions"]}
    out = []
    for r in led["requests"]:
        key = norm(r["text"])[:300]
        al = r["ref"].split(":")[0]
        first = seen.get(key)
        if first and first["ref"].split(":")[0] != al and len(key) >= 12 and r["source"] in ("user", "queued"):
            first.setdefault("resent", []).append(r["ref"])
            if r.get("in_window"):
                d = by_alias[al]["dropped"]
                why = "续开文件里重发(同一句已在前一个文件记录)"
                d[why] = d.get(why, 0) + 1
            continue
        seen.setdefault(key, r)
        out.append(r)
    return out


def merge(mp: Path, led: dict) -> int:
    """Append stubs for requests the map does not have yet; record how far the ledger was read."""
    m = json.loads(mp.read_text())
    have = {at for r in m.get("requests") or [] for at in [r.get("at")] + list(r.get("also") or [])}
    fresh = [r for r in led["requests"] if r["ref"] not in have and not r.get("folded_into")]
    new = stubs(fresh)
    m.setdefault("requests", []).extend(new)
    known = {s.get("alias"): s for s in m.get("sessions") or []}
    for s in led["sessions"]:
        if s["alias"] in known:
            known[s["alias"]]["ledger_through"] = s["lines"]
        else:
            m.setdefault("sessions", []).append({"alias": s["alias"], "path": s["path"], "platform": s["format"],
                                                 "when": "", "ledger_through": s["lines"], "read": "", "state": "", "note": ""})
    mp.write_text(json.dumps(m, ensure_ascii=False, indent=1))
    return len(new)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sessions", nargs="+", help="ALIAS=path or path")
    ap.add_argument("--out", help="directory for ledger.json + ledger.md")
    ap.add_argument("--since", default="", help="ALIAS:LINE[,ALIAS:LINE] — only list requests at/after these lines; "
                                                "numbering still counts the whole files. With --map the default is "
                                                "each session's ledger_through + 1")
    ap.add_argument("--max", type=int, default=300, help="chars per request in the md table")
    ap.add_argument("--compact", action="store_true", help="md without approval rows, text cut to 120 chars (long threads)")
    ap.add_argument("--map", help="update mode: an existing map.json — reuse its request ids, number new ones after them")
    ap.add_argument("--merge", action="store_true", help="with --map: append stubs for the new requests into that map")
    ap.add_argument("--origin", action="append", default=[],
                    help="ALIAS=path[:FROM-TO] — the session where the user asked for this look; their words there are "
                         "kept as intent evidence (principles, scope), apart from the request ledger")
    ap.add_argument("--skeleton", action="store_true",
                    help="also write DIR/map.json (only if absent): sessions + requests pre-filled, other layers empty")
    a = ap.parse_args()
    since = {}
    old = json.loads(Path(a.map).read_text()) if a.map else None
    for s in (old or {}).get("sessions") or []:
        if isinstance(s.get("ledger_through"), int):
            since[s.get("alias")] = s["ledger_through"] + 1
    for part in filter(None, a.since.split(",")):
        al, _, ln = part.strip().partition(":")
        since[al] = int(ln)
    led = {"sessions": [], "requests": []}
    for i, spec in enumerate(a.sessions):
        alias, _, p = spec.partition("=") if "=" in spec else (chr(65 + i), "", spec)
        path = Path(p).expanduser()
        if not path.exists():
            sys.exit(f"no such session: {path}")
        s = load(alias, path, since.get(alias, 0))
        led["requests"] += s.pop("requests")
        led["sessions"].append(s)
    # sessions in order of their first request; inside a session, file order (timestamps of
    # local commands and queued records can be earlier than the line they are written on)
    first = {}
    for r in led["requests"]:
        al = r["ref"].split(":")[0]
        first[al] = min(first.get(al, "9"), r["ts"] or "9")
    led["requests"].sort(key=lambda r: (first[r["ref"].split(":")[0]], r["ref"].split(":")[0], r["line"]))
    led["requests"] = dedupe_resent(led)
    for i, r in enumerate(led["requests"], 1):
        r["id"] = f"R{i}"
    gorder = {s["alias"]: i for i, s in enumerate(led["sessions"])}
    for s in led["sessions"]:
        s.pop("gaps_all")
    led["gaps"] = sorted([g for s in led["sessions"] for g in s.pop("gaps")], key=lambda g: (gorder[g["ref"].split(":")[0]], g["line"]))
    for i, g in enumerate(led["gaps"], 1):
        g["id"] = f"U{i}"
    led["origin"], led["origin_sessions"] = [], []
    for spec in a.origin:
        alias, _, rest = spec.partition("=")
        if not rest:
            sys.exit(f"--origin wants ALIAS=path[:FROM-TO], got {spec!r}")
        mm = re.match(r"^(.*?)(?::(\d+)-(\d+))?$", rest)
        path, lo, hi = Path(mm.group(1)).expanduser(), int(mm.group(2) or 0), int(mm.group(3) or 10**12)
        if not path.exists():
            sys.exit(f"no such origin session: {path}")
        s = load(alias, path, 0)
        s.pop("gaps"), s.pop("gaps_all")
        led["origin"] += [r for r in s.pop("requests") if lo <= r["line"] <= hi and r["source"] != "command"]
        led["origin_sessions"].append(s)
    for i, o in enumerate(led["origin"], 1):
        o["id"] = f"I{i}"
    if old is not None:
        new = align_ids(led, old)
        print(f"ids aligned with {a.map}: {new} request(s) not in that map")
    led["requests"] = [r for r in led["requests"] if r.pop("in_window")]
    for s in led["sessions"]:
        s["n_requests"] = sum(1 for r in led["requests"] if r["ref"].split(":")[0] == s["alias"])
    md = to_md(led, 120 if a.compact else a.max, a.compact)
    if a.out:
        o = Path(a.out)
        o.mkdir(parents=True, exist_ok=True)
        (o / "ledger.json").write_text(json.dumps(led, ensure_ascii=False, indent=1))
        (o / "ledger.md").write_text(md)
        print(f"wrote {o/'ledger.json'} and {o/'ledger.md'}: {len(led['requests'])} requests")
        if a.skeleton:
            mp = o / "map.json"
            # a pristine copy: the untouched starting point, for comparing what the builder changed
            (o / "skeleton.json").write_text(json.dumps(skeleton(led), ensure_ascii=False, indent=1))
            if mp.exists():
                print(f"  {mp} exists — skeleton not written (update mode: --map {mp} --merge)")
            else:
                mp.write_text(json.dumps(skeleton(led), ensure_ascii=False, indent=1))
                print(f"  wrote skeleton {mp}: give every request a goal (or list it under a goal), "
                      "judge corrections/interjections/questions one by one, then the other layers")
        for s in led["sessions"]:
            print(f"  {s['alias']}: {s['lines']} lines, dropped {s['dropped']}")
    else:
        print(md)
    if a.merge:
        if not a.map:
            sys.exit("--merge needs --map <existing map.json>")
        print(f"merged {merge(Path(a.map), led)} new request stub(s) into {a.map} (approvals folded into the one before; "
              "save a copy of the map first)")
    if not led["requests"]:
        sys.exit(3)


if __name__ == "__main__":
    main()
