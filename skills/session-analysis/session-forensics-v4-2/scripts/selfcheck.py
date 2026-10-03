#!/usr/bin/env python3
"""Pre-publish check for the skill itself (v4.2). Exit non-zero on any failure.

  selfcheck.py             -> dangling references + size limits + local/ layout
  selfcheck.py --siblings  -> also check the external skills next to this one (session-handoff-v4-2, session-audit-v4-2)
  selfcheck.py --privacy   -> also refuse private content in distributed files

Also runs inventory.py on the synthetic repo: the skill the synthetic session never read must come out as
unused, the one it read as read; the same without --repo when the session's cwd points at the repo; and a
session with neither must be reported as an incomplete stock-taking, never as an empty repository.

References checked: references/x.md, scripts/x.py, $SF/x.py, examples/..., and bare *.md names
(a bare name must exist somewhere in the skill, unless it is a well-known outside file or an output name).

Why a script: limits written as prose ("split long files", "never ship local/") drift, and references
keep pointing at files that no longer exist unless something refuses.
"""
import re, sys
from pathlib import Path

sys.dont_write_bytecode = True  # importing ledger for the gap probe must not leave caches in the skill

ROOT = Path(__file__).resolve().parent.parent
LIMITS = {"SKILL.md": 90}
REF_LIMIT = 300          # per reference file; tighten as files get split
fails = []

dist = [p for p in ROOT.rglob("*") if p.is_file() and "local" not in p.relative_to(ROOT).parts
        and "__pycache__" not in p.parts and p.suffix in (".md", ".py", ".yaml", ".sh")]

# 1. dangling references to skill files
BASE = ROOT
OUTSIDE = {"SKILL.md", "AGENTS.md", "CLAUDE.md", "MEMORY.md", "README.md", "AGENT.md", "IMPLEMENTATION_PLAN.md",
           "map.md", "ledger.md", "diff.md", "inventory.md", "brief-challenger.md", "brief-observer.md"}
pat = re.compile(r"(references/[\w\-]+\.md|scripts/[\w\-]+\.py|examples/[\w\-./]+\.(?:jsonl|json|html|md))")
sf = re.compile(r"\$SF/([\w\-]+\.py)")
bare = re.compile(r"(?<![\w/\-.<])([A-Za-z][\w\-]*\.md)\b")
names = {p.name for p in ROOT.rglob("*.md")}


def check_refs(files, root):
    for p in files:
        t = p.read_text(errors="ignore")
        where = p.relative_to(root.parent) if root != ROOT else p.relative_to(ROOT)
        for m in set(pat.findall(t)):
            if not (BASE / m).exists() and not (root / m).exists():
                fails.append(f"dangling: {where} -> {m}")
        for m in set(sf.findall(t)):
            if not (BASE / "scripts" / m).exists():
                fails.append(f"dangling: {where} -> $SF/{m}")
        for m in set(bare.findall(t)):
            if m not in OUTSIDE and m not in names:
                fails.append(f"dangling: {where} -> {m} (no such file in the skill)")


check_refs([p for p in dist if p.name not in ("selfcheck.py", "inventory.py")], ROOT)  # these describe file patterns
if "--siblings" in sys.argv:
    for name in ("session-handoff-v4-2", "session-audit-v4-2"):
        sib = ROOT.parent / name
        if not (sib / "SKILL.md").exists():
            fails.append(f"sibling missing: {sib}")
            continue
        n = len((sib / "SKILL.md").read_text().splitlines())
        if n > 60:
            fails.append(f"too long: {name}/SKILL.md {n} lines > 60")
        check_refs([p for p in sib.rglob("*.md")], sib)

# 2. size limits
for name, lim in LIMITS.items():
    n = len((ROOT / name).read_text().splitlines())
    if n > lim:
        fails.append(f"too long: {name} {n} lines > {lim}")
for p in (ROOT / "references").glob("*.md"):
    n = len(p.read_text().splitlines())
    if n > REF_LIMIT:
        fails.append(f"too long: references/{p.name} {n} lines > {REF_LIMIT} (split by section)")

# 3. local/ may only hold calibration data (and the compatibility symlink)
local = ROOT / "local"
if local.exists():
    for p in local.iterdir():
        if p.name not in ("calibration", "baseline.json"):
            fails.append(f"local/ holds non-calibration item: {p.name} (dossiers belong outside the skill)")

# 4. privacy (only with --privacy): absolute home paths, memory file names, long quoted user speech
if "--privacy" in sys.argv:
    long_quote = re.compile(r"[「“\"]([^」”\"\n]{40,})[」”\"]")
    # a long quote that occurs in the skill's own synthetic sessions is made up, not someone's speech
    synthetic = " ".join(q.read_text(errors="ignore") for q in [*(ROOT / "examples" / "synthetic").glob("*.jsonl"),
                                                                 *(ROOT / "examples").glob("*.json")])
    home = re.compile(r"/Users/(?!<)[\w.\-]+|/home/(?!<)[\w.\-]+|-Users-(?!<)[A-Za-z0-9_]+-")
    for p in dist:
        t = p.read_text(errors="ignore")
        if home.search(t) and p.suffix != ".py":
            fails.append(f"privacy: home path or encoded project dir ({home.search(t).group(0)}) in {p.relative_to(ROOT)}")
        if re.search(r"memory/[\w\-]+\.md", t):
            fails.append(f"privacy: memory file name in {p.relative_to(ROOT)}")
        if p.suffix == ".md":
            real = [q for q in long_quote.findall(t) if q not in synthetic]
            if real:
                fails.append(f"privacy: long quotation (possible user speech) in {p.relative_to(ROOT)}: {real[0][:30]}…")

# 5. the gap filter (ledger.py) against a hand-written probe: a changed filter must not silently get worse
probe = ROOT / "examples" / "synthetic" / "gap_probe.json"
if probe.exists():
    import json
    sys.path.insert(0, str(ROOT / "scripts"))
    from ledger import gap_clauses
    tp = fp = fn = 0
    for c in json.loads(probe.read_text())["cases"]:
        k = sum(1 for _, why in gap_clauses(c["text"]) if not why)
        tp, fp, fn = tp + min(k, c["gaps"]), fp + max(0, k - c["gaps"]), fn + max(0, c["gaps"] - k)
        if k != c["gaps"] and not c.get("known_miss"):
            fails.append(f"gap probe: expected {c['gaps']} gap(s), got {k}: {c['text']}")
    print(f"gap probe: precision {tp / max(1, tp + fp):.2f}, recall {tp / max(1, tp + fn):.2f} (known misses included)")

# 6. inventory.py must name the unread skill of the synthetic repo
syn = ROOT / "examples" / "synthetic"
if (syn / "repo").exists():
    sys.path.insert(0, str(ROOT / "scripts"))
    from inventory import build
    got = {x["name"] or x["path"]: x["used"] for x in build((syn / "repo").resolve(), [("A", syn / "session.jsonl")], {}, None)["items"]}
    for name, want in (("shelf-import", "no"), ("shelf-testing", "read"), ("docs/data-model.md", "no")):
        if got.get(name) != want:
            fails.append(f"inventory probe: {name} should be {want}, got {got.get(name)}")
    print(f"inventory probe: {got}")
    # without --repo: the repository comes from the session's cwd (a copy outside any git work tree)
    import json, shutil, tempfile
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(syn / "repo", Path(tmp) / "repo")
        sess = Path(tmp) / "session.jsonl"
        sess.write_text("".join(json.dumps({**json.loads(ln), "cwd": str(Path(tmp) / "repo")}, ensure_ascii=False) + "\n"
                                for ln in (syn / "session.jsonl").read_text().splitlines() if ln.strip()))
        inv = build(None, [("A", sess)], {}, None)
        got = {x["name"] or x["path"]: x["used"] for x in inv["items"]}
        if inv["repo_source"] != "cwd" or got.get("shelf-import") != "no":
            fails.append(f"inventory cwd probe: repo from {inv['repo_source']}, shelf-import={got.get('shelf-import')}")
        none = build(None, [("A", syn / "session.jsonl")], {}, None)
        if none["repo_source"] != "none" or "不完整" not in none["repo_note"]:
            fails.append("inventory probe: no --repo and no cwd must say the stock-taking is incomplete")
    print(f"inventory cwd probe: repo from {inv['repo_source']}, shelf-import={got.get('shelf-import')}; no cwd -> {none['repo_source']}")

if fails:
    print("SELFCHECK FAILED")
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("selfcheck ok")
