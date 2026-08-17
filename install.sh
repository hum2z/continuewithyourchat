#!/usr/bin/env bash
# previous — one-line installer
#
#   curl -fsSL https://raw.githubusercontent.com/hum2z/continuewithyourchat/main/install.sh | bash
#
# Downloads the skill into ~/.claude/skills/previous and registers the
# SessionStart/SessionEnd hooks. Safe to re-run: it upgrades in place, and your
# actual memory lives in ~/.claude/previous, which this never touches.

set -euo pipefail

REPO="${PREVIOUS_REPO:-hum2z/continuewithyourchat}"
REF="${PREVIOUS_REF:-main}"
SKILLS_DIR="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
DEST="$SKILLS_DIR/previous"

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

command -v curl >/dev/null 2>&1 || die "curl is required"
command -v tar  >/dev/null 2>&1 || die "tar is required"

PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
      PY="$candidate"
      break
    fi
  fi
done
[ -n "$PY" ] || die "Python 3.9+ is required but was not found on PATH"

TMP="$(mktemp -d)"
# Leaving a temp tree behind on failure is its own small annoyance; clean up
# whichever way this exits.
trap 'rm -rf "$TMP"' EXIT

say "Downloading previous ($REPO@$REF)..."
curl -fsSL "https://codeload.github.com/$REPO/tar.gz/refs/heads/$REF" \
  | tar xz -C "$TMP" \
  || die "download failed — check the repo name and that '$REF' exists"

# The tarball wraps everything in a <repo>-<ref>/ directory, so the skill sits
# four levels down; allow slack in case that wrapper ever changes.
SRC="$(find "$TMP" -maxdepth 6 -type d -path '*/.claude/skills/previous' -print -quit)"
[ -n "$SRC" ] || die "archive did not contain .claude/skills/previous"

# Replace rather than merge, so a rename or deletion upstream does not leave a
# stale file behind that quietly shadows the new one.
mkdir -p "$SKILLS_DIR"
rm -rf "$DEST"
cp -r "$SRC" "$DEST"
say "Installed to $DEST"

"$PY" "$DEST/scripts/pmem.py" install-hook

cat <<'EOF'

Done. Start a new session and type /previous.

Nothing is remembered yet, so the first run will say so — that is expected.
Work as usual, close the session, and the next /previous will know what you did.
EOF
