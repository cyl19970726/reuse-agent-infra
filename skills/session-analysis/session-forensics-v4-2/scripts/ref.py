#!/usr/bin/env python3
"""Print one section of a reference file instead of the whole file (v2).

  ref.py                      -> list reference files and their section headings
  ref.py <name>               -> the file (capped at 150 lines; long files list headings instead)
  ref.py <name> <heading>     -> only the section whose heading contains <heading> (capped at 80 lines)
<name> is the file stem under references/, e.g. map-schema, layout, update, orchestration, platforms, codex-jsonl.
"""
import sys
from pathlib import Path

REF = Path(__file__).resolve().parent.parent / "references"


def headings(p):
    return [(i, l.rstrip()) for i, l in enumerate(p.read_text().splitlines(), 1) if l.startswith("#")]


def main():
    a = sys.argv[1:]
    if not a:
        for p in sorted(REF.glob("*.md")):
            n = len(p.read_text().splitlines())
            print(f"{p.stem}  ({n} lines)")
            for _, h in headings(p)[:12]:
                print("    " + h)
        return
    p = REF / (a[0] if a[0].endswith(".md") else a[0] + ".md")
    if not p.exists():
        sys.exit(f"no reference named {a[0]}; run ref.py with no args to list")
    lines = p.read_text().splitlines()
    if len(a) == 1:
        if len(lines) <= 150:
            print("\n".join(lines))
        else:
            print(f"{p.name} has {len(lines)} lines; pick a section:")
            for i, h in headings(p):
                print(f"  L{i} {h}")
        return
    want = a[1]
    hs = headings(p)
    for k, (i, h) in enumerate(hs):
        if want in h:
            level = len(h) - len(h.lstrip("#"))
            end = len(lines)
            for j, h2 in hs[k + 1:]:
                if len(h2) - len(h2.lstrip("#")) <= level:
                    end = j - 1
                    break
            chunk = lines[i - 1:end]
            print("\n".join(chunk[:80]))
            if len(chunk) > 80:
                print(f"... ({len(chunk) - 80} more lines; narrow the heading)")
            return
    sys.exit(f"no heading containing '{want}' in {p.name}")


if __name__ == "__main__":
    main()
