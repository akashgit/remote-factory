#!/usr/bin/env bash
# session-start: post-compact orientation. Fires on every SessionStart but only acts
# when source == "compact"; for every other source (startup, resume, clear) it exits
# silently so normal session starts are untouched.
#
# It emits a pointer-only directive (not the full handoff content) via
# hookSpecificOutput.additionalContext, telling the new session to read the latest
# handoff file the PreCompact sidecar wrote.

set -uo pipefail

INPUT="$(cat 2>/dev/null || true)"

jq_field() {
  printf '%s' "$INPUT" | jq -r "${1} // empty" 2>/dev/null || printf ''
}

SOURCE="$(jq_field .source)"

# Only orient on the way back in from a /compact. No-op otherwise.
if [[ "$SOURCE" != "compact" ]]; then
  exit 0
fi

CWD="$(jq_field .cwd)"
REPO_DIR="${CWD:-${CLAUDE_PROJECT_DIR:-$PWD}}"
REPO="$(basename "$REPO_DIR")"

# Latest handoff (timestamps are zero-padded + UTC, so lexical == chronological;
# ls -t by mtime is equivalent and robust to clock format).
LATEST="$(ls -t "${REPO_DIR}/.claude/sop-compact/"handoff-*.md 2>/dev/null | head -1 || true)"
LEGACY=""

if [[ -z "$LATEST" ]]; then
  # Back-compat: pick up v0.2.x snapshots written by the old compact-sop plugin.
  SNAP_DIR="${COMPACT_SOP_SNAPSHOT_DIR:-$HOME/.claude/compact-sop/snapshots}"
  LATEST="$(ls -t "${SNAP_DIR}/pre-compact-${REPO}-"*.md /tmp/pre-compact-"${REPO}"-*.md 2>/dev/null | head -1 || true)"
  [[ -n "$LATEST" ]] && LEGACY=" (legacy compact-sop snapshot — consider running /init-sop-compact to migrate)"
fi

if [[ -n "$LATEST" ]]; then
  POINTER="You were just compacted (SessionStart source=compact). Before doing anything else, read \`${LATEST}\`${LEGACY} immediately for orientation — it is the pre-compact handoff with the non-reconstructable in-flight context. Trust hierarchy: live state (git/gh/files) > handoff > compaction summary. Do not start new work until you have read it; then re-check live state and wait for the user."
else
  POINTER="You were just compacted (SessionStart source=compact), but no pre-compact handoff was found under \`${REPO_DIR}/.claude/sop-compact/\`. Treat the history above as a lossy summary: re-check live state (git status, gh, files) before acting, and consider running \`/init-sop-compact\` so future compactions produce a handoff. Do not start new work until you have re-oriented."
fi

jq -nc --arg ctx "$POINTER" \
  '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}'

# --- Optional repo-local extension. ------------------------------------------------
EXT="${REPO_DIR}/.claude/sop-compact/post.sh"
if [[ -f "$EXT" ]]; then
  ( set +e; SOP_COMPACT_HANDOFF="$LATEST" bash "$EXT" >/dev/null 2>&1 ) || true
fi

exit 0
