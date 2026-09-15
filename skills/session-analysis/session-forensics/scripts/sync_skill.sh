#!/usr/bin/env bash
# Propagate this skill from the authoritative source to its derived copies.
#
# Why this exists as a harness and not as a note in gates.md:
# the same failure recurred three times, twice on the same day, by the same
# person who had just written the gate.
#
#   n=1  scratchpad/session_metrics.py (307 lines) coexisted with the skill copy
#        (473 lines) — the draft was 166 lines behind
#   n=2  CASE-F: source patched three more times after the last rsync -> 4 files
#        diverged, and TWO ALREADY-FALSIFIED CONCLUSIONS stayed live in the Codex
#        global copy and on GitHub
#   n=3  same session, hours after G0 was written: self-upgrade.md +
#        session_metrics.py diverged again, uncommitted
#   n=4  2026-08-10 audit (CASE-Y): --check reported GREEN while origin/main was
#        15 days / 592 lines behind — verify() compares WORKING TREES only, and
#        the commit/push leg was still discipline. GitHub kept distributing a
#        timeout_rate implementation falsified twice since. Hence publish_verify.
#
# A gate that says "remember to run the comparison" is executed by discipline.
# Discipline failed three times, then a fourth time one leg further down the
# pipe. Every leg has to refuse to proceed instead.
#
# Usage:
#   sync_skill.sh              sync + verify, no git action
#   sync_skill.sh --check      verify only, change nothing (exit 1 if diverged)
#   sync_skill.sh --commit "<msg>"   sync + verify + stage ONLY the skill path + commit
#   sync_skill.sh --commit "<msg>" --push   ... and push, then verify HEAD == origin/main
set -uo pipefail

SRC=${SESSION_FORENSICS_SRC:?Set the authoritative skill directory explicitly}
CODEX=${SESSION_FORENSICS_CODEX:?Set an authorized destination explicitly; prefer project-local scope}
REPO=${SESSION_FORENSICS_REPO:?Set the publication repository explicitly}
SUBPATH=${SESSION_FORENSICS_SUBPATH:-skills/session-analysis/session-forensics}
PUB="$REPO/$SUBPATH"
EXCL=(--exclude 'local/' --exclude '__pycache__/' --exclude '.DS_Store')

die() { printf '\033[31mREFUSED\033[0m %s\n' "$*" >&2; exit 1; }
ok()  { printf '\033[32mOK\033[0m %s\n' "$*"; }

# A per-file digest over the WHOLE tree, not a hand-picked list. A canary grep
# for known-falsified strings only catches the conclusions someone remembered to
# add; byte equality catches every one of them, including the next.
manifest() {
  ( cd "$1" && find . -type f \
      ! -path './local/*' ! -path '*/__pycache__/*' ! -name '.DS_Store' \
      -print0 | sort -z | xargs -0 md5 -q 2>/dev/null | md5 -q )
}

verify() {
  local a b c
  a=$(manifest "$SRC"); b=$(manifest "$CODEX"); c=$(manifest "$PUB")
  if [ "$a" = "$b" ] && [ "$b" = "$c" ]; then
    ok "three copies byte-identical ($a)"
    return 0
  fi
  printf 'source  %s  %s\n' "$a" "$SRC" >&2
  printf 'codex   %s  %s\n' "$b" "$CODEX" >&2
  printf 'github  %s  %s\n' "$c" "$PUB" >&2
  echo "--- differing files ---" >&2
  diff -rq "$SRC" "$CODEX" 2>/dev/null | grep -v 'local\|__pycache__\|DS_Store' >&2
  diff -rq "$SRC" "$PUB" 2>/dev/null | grep -v 'local\|__pycache__\|DS_Store' >&2
  return 1
}

# The publish leg. Byte-identical working trees say nothing about what GitHub is
# serving: content is published only when the repo tree is committed AND pushed.
publish_verify() {
  ( cd "$REPO" || exit 1
    GIT_TERMINAL_PROMPT=0 git fetch -q origin 2>/dev/null || true  # offline: fall back to the local ref
    if [ -n "$(git status --porcelain -- "$SUBPATH")" ]; then
      echo "repo tree has uncommitted changes under $SUBPATH:" >&2
      git status --porcelain -- "$SUBPATH" | head -12 >&2
      exit 1
    fi
    if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
      echo "HEAD $(git rev-parse --short HEAD) != origin/main $(git rev-parse --short origin/main) — committed but not published" >&2
      exit 1
    fi
    exit 0 )
}

MODE=sync; MSG=""; DO_PUSH=0
while [ $# -gt 0 ]; do
  case "$1" in
    --check)  MODE=check ;;
    --commit) MODE=commit; MSG=${2:-}; shift ;;
    --push)   DO_PUSH=1 ;;
    *)        die "unknown argument: $1" ;;
  esac
  shift
done

[ -f "$SRC/SKILL.md" ] || die "source has no SKILL.md: $SRC"

if [ "$MODE" = check ]; then
  verify || die "copies diverged — run without --check to propagate"
  publish_verify || die "working trees match but the PUBLISHED copy is stale — run --commit '<msg>' --push"
  ok "published copy current (repo tree clean, HEAD == origin/main)"
  exit 0
fi

# Compilation gate: never propagate a source that does not import.
for f in "$SRC"/scripts/*.py; do
  python3 -m py_compile "$f" 2>/dev/null || die "does not compile: $f"
done
ok "all scripts compile"

rsync -a --delete "${EXCL[@]}" "$SRC/" "$CODEX/" || die "rsync to codex failed"
rsync -a --delete "${EXCL[@]}" "$SRC/" "$PUB/"   || die "rsync to repo failed"

# local/ is per-user calibration and must never be published.
for d in "$CODEX/local" "$PUB/local"; do
  [ -e "$d" ] && die "local/ leaked into a derived copy: $d"
done
ok "local/ not present in derived copies"

verify || die "propagation ran but copies still differ — do not commit"

if [ "$MODE" != commit ]; then
  publish_verify \
    || die "synced, but NOT PUBLISHED — copies match while the repo is uncommitted/unpushed; rerun with --commit '<msg>' --push"
  ok "published copy current"
  exit 0
fi
[ -n "$MSG" ] || die "--commit needs a message"

cd "$REPO" || die "no repo at $REPO"
# This worktree carries long-standing unrelated deletions; `git add -A` here
# would publish them. Stage the skill path and nothing else, then prove it.
git add -- "$SUBPATH" || die "git add failed"
STRAY=$(git diff --cached --name-only | grep -v "^$SUBPATH/" || true)
[ -z "$STRAY" ] || die "staged files outside $SUBPATH: $STRAY"
if git diff --cached --quiet; then ok "nothing to commit"; exit 0; fi
git diff --cached --stat
git commit -q -m "$MSG" || die "commit failed"
ok "committed: $(git log --oneline -1)"

# Committing without pushing leaves origin/main stale — the exact n=4 state.
# Exit nonzero so nothing downstream mistakes this for "published".
[ "$DO_PUSH" = 1 ] || { printf '\033[33mUNPUBLISHED\033[0m committed but not pushed — pass --push\n'; exit 1; }
git push -q origin HEAD || die "push failed"
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] \
  || die "pushed but HEAD != origin/main"
ok "pushed, HEAD == origin/main"
