#!/usr/bin/env bash
# pre-compact: fires before /compact (manual or auto). Runs a `claude -p` sidecar
# that reads the just-finished conversation, promotes principled learnings to the
# repo's durable targets, and writes an ephemeral handoff snapshot the SessionStart
# hook will point the post-compact session at.
#
# PreCompact is awaited (blocking) — CC waits for this hook (and its sidecar) before
# it starts summarizing. Exit 2 hard-blocks the compaction and surfaces stderr to the
# user; we use that so a failed snapshot aborts /compact rather than silently losing
# the in-flight context. Exit 0 lets compaction proceed.
#
# Sidecar invocation pattern (claude -p with tool use + no session pollution) is
# derived from an upstream session-management tool's `sop` subcommand; this plugin
# ships its own prompt rather than shelling out to it.
#
# TRUST ASSUMPTION (security): the sidecar runs with --dangerously-skip-permissions and
# reads the just-finished transcript, which contains verbatim session content (user
# messages, tool output, external data). A crafted message in that transcript is therefore
# untrusted input reaching an agent with broad tool access. We accept this deliberately:
#   - The transcript was already read, in full, by the main session that produced it — the
#     sidecar gains no privilege the original session didn't already have over this repo.
#   - Promotion targets are not confinable to a fixed subtree (CLAUDE.md lives at the repo
#     root; per-project memory dirs can live OUTSIDE the repo under ~/.claude/projects/...),
#     and the prompt samples large transcripts via head/grep — so a narrow --allowed-tools
#     allowlist would break promotion. Broad access is required for the feature to work.
#   - A `timeout` wrapper (below) bounds runaway/looping behavior so a hijacked sidecar
#     can't block /compact forever.
# Treat the transcript as a controlled artifact from the CC runtime. If you need a stronger
# boundary, run the sidecar in a sandbox or scope promotion to in-repo paths only.

set -uo pipefail

INPUT="$(cat 2>/dev/null || true)"

jq_field() {
  # $1 = jq path expression (e.g. .transcript_path). Prints value or empty string.
  printf '%s' "$INPUT" | jq -r "${1} // empty" 2>/dev/null || printf ''
}

TRANSCRIPT="$(jq_field .transcript_path)"
CWD="$(jq_field .cwd)"
TRIGGER="$(jq_field .trigger)"
SESSION_ID="$(jq_field .session_id)"

# Repo root: prefer the cwd from stdin, then $CLAUDE_PROJECT_DIR, then $PWD.
REPO_DIR="${CWD:-${CLAUDE_PROJECT_DIR:-$PWD}}"

SOP_FILE="${REPO_DIR}/.claude/sop-compact.md"
SNAP_DIR="${REPO_DIR}/.claude/sop-compact"
# Seconds-resolution UTC + a PID suffix: two concurrent compacts (e.g. auto-compact in
# two long-running sessions on the same repo) can hit the same wall-second; the -$$
# disambiguates so the second writer's mv -f doesn't clobber the first's handoff. The
# timestamp prefix still dominates lexical order, so SessionStart's latest-glob and
# prune_handoffs' sort are unaffected. (machine#118)
TS="$(date -u +%Y%m%dT%H%M%SZ)-$$"
HANDOFF="${SNAP_DIR}/handoff-${TS}.md"

mkdir -p "$SNAP_DIR"

# prune_handoffs: after a new handoff is written, keep only the most recent N matching
# handoff-*.md and remove the rest. N is SOP_COMPACT_HANDOFF_RETENTION (default 10).
# Glob expansion is lexically sorted and the zero-padded UTC timestamp prefix dominates
# (the -$$ suffix only disambiguates within a wall-second), so lexical == chronological;
# the oldest files sort first and are the ones removed. Called AFTER the write so the
# just-written handoff is always among the kept N. A keep < 1 (or non-numeric) value is a
# no-op so we never delete the file SessionStart needs. *.error.log and .handoff-*.XXXXXX
# temp files don't match handoff-*.md, so they're untouched.
prune_handoffs() {
  local keep="${SOP_COMPACT_HANDOFF_RETENTION:-10}"
  [[ "$keep" =~ ^[0-9]+$ ]] && (( keep >= 1 )) || return 0
  local files=()
  shopt -s nullglob
  files=( "${SNAP_DIR}"/handoff-*.md )
  shopt -u nullglob
  local count=${#files[@]}
  (( count > keep )) || return 0
  local i
  for (( i = 0; i < count - keep; i++ )); do
    rm -f "${files[i]}"
  done
}

# --- No SOP yet: write a minimal stub handoff and let compaction proceed. ----------
if [[ ! -f "$SOP_FILE" ]]; then
  TMP="$(mktemp "${SNAP_DIR}/.handoff-${TS}.XXXXXX")"
  {
    printf '# Pre-compact handoff (stub — no SOP)\n\n'
    printf '_Generated %s by sop-compact PreCompact hook (trigger: %s)._\n\n' "$TS" "${TRIGGER:-unknown}"
    printf 'This repo has **no `.claude/sop-compact.md`** — run `/init-sop-compact` to generate one '
    printf 'so future compactions get a real Promote+Snapshot pass.\n\n'
    printf 'For now there is no repo-tailored procedure. After this compaction:\n\n'
    printf '1. Treat the conversation history above as a lossy compaction summary, not the live session.\n'
    printf '2. Re-check live state (git status, gh, files) before acting.\n'
    printf '3. Prior transcript (for archaeology if needed): `%s`\n' "${TRANSCRIPT:-unknown}"
  } >"$TMP"
  mv -f "$TMP" "$HANDOFF"
  prune_handoffs
  exit 0
fi

# --- SOP present: run the sidecar to promote + snapshot. ---------------------------
MODEL="${SOP_COMPACT_MODEL:-opus[1m]}"

PROMPT_FILE="$(mktemp)"
STDERR_FILE="$(mktemp)"
cleanup() { rm -f "$PROMPT_FILE" "$STDERR_FILE"; }
trap cleanup EXIT

# extract_handoff: pull the handoff body out from between the ===HANDOFF=== and ===END===
# sentinels the sidecar is asked to emit. The sidecar tends to narrate its promotion
# decisions before the markdown; the sentinels let us drop that preamble so the saved file
# starts at the `# Pre-compact handoff` heading. Reads raw output on stdin, prints the
# extracted body on stdout. Exit 0 if both sentinels were found and the body is non-empty;
# exit 1 otherwise (caller falls back to writing the raw output).
extract_handoff() {
  awk '
    /^===HANDOFF===[[:space:]]*$/ { capture=1; started=1; next }
    /^===END===[[:space:]]*$/     { if (capture) { capture=0; ended=1 } next }
    capture                       { lines[n++] = $0 }
    END {
      if (!started || !ended) exit 1
      # Strip a single leading blank line so the H1 lands at the top of the file.
      first = 0
      if (n > 0 && lines[0] == "") first = 1
      empty = 1
      for (i = first; i < n; i++) {
        print lines[i]
        if (lines[i] != "") empty = 0
      }
      if (empty) exit 1
    }
  '
}

cat >"$PROMPT_FILE" <<EOF
You are the pre-compact sidecar for the sop-compact Claude Code plugin. A \`/compact\`
is about to lossily rewrite a conversation into a summary. Your job is to preserve the
high-value, non-reconstructable context BEFORE that happens.

Working directory: ${REPO_DIR}
Compaction trigger: ${TRIGGER:-unknown}
Just-finished conversation transcript (JSONL): ${TRANSCRIPT}

Do this, in order:

1. Read \`${SOP_FILE}\` — the repo-specific procedure. It tells you this repo's
   promotion targets (memory dir, CLAUDE.md, etc.), snapshot conventions, live-state
   checks, and what counts as in-flight state here. Follow it.

2. Read the transcript at \`${TRANSCRIPT}\` to understand the just-finished session.
   It may be large — sample (head/tail/grep) enough to characterize what happened, the
   in-flight work, decisions made, and any principled learnings. You do not need every line.

3. PROMOTE (direct file edits): for each non-reconstructable learning that is *principled*
   (will recur, not a one-off), write it to the durable target named in the SOP — memory
   files, CLAUDE.md, etc. Use your Write/Edit tools to make these edits now. Promote
   validated non-obvious decisions, not only corrections. Do NOT promote anything already
   on disk or reconstructable from git/gh/files.

4. SNAPSHOT (your stdout): after promoting, output a dense handoff document as Markdown.
   It will be saved as the post-compact handoff file. Capture the non-reconstructable
   in-flight state the summary will lose first:
   - Active framings, analogies, shared language coined this session
   - In-flight design decisions WITH their reasoning (the why decays first)
   - What you and the user were mid-discussing (open questions, half-formed directions)
   - Rejected approaches and WHY they were rejected
   - Relationship / tonal context if it shifted
   - A short "resume here" pointer: what the next session should do first
   Do NOT restate anything reconstructable from gh/git/files or anything you just promoted.
   The handoff body must start with a top-level heading like \`# Pre-compact handoff\`.

Output format: emit the handoff markdown between EXACTLY these two sentinel lines, each
on its own line, with NOTHING outside them — no preamble, no explanation of what you
promoted, no trailing commentary. Any reasoning about your promotion decisions belongs
INSIDE the handoff body (e.g. under a "Notes" section), not before the opening sentinel.

===HANDOFF===
<handoff markdown content here, starting with the \`# Pre-compact handoff\` heading>
===END===
EOF

# Run the sidecar from the repo root so relative paths in the SOP resolve. Capture
# stdout (the handoff) and stderr (debug on failure) separately.
#
# PreCompact is awaited and blocking, so an unbounded sidecar would hang /compact forever
# (the user can't interrupt it). Wrap in `timeout` (default 600s, override via
# SOP_COMPACT_TIMEOUT); on expiry `timeout` exits 124, which the RC check below catches and
# converts to an exit-2 block — a clear failure rather than an infinite hang.
# Default is 600s (not 300s) because the sidecar defaults to opus[1m] (v0.3.3) and ingests
# the whole just-finished transcript — the long sessions this targets can need >5min to
# read + promote + snapshot, and a 300s wall would exit-2-block compaction (rc=124) on
# exactly those sessions (machine#120 review).
SIDECAR_OUT="$(
  cd "$REPO_DIR" && timeout "${SOP_COMPACT_TIMEOUT:-600}" claude -p "$(cat "$PROMPT_FILE")" \
    --model "$MODEL" \
    --setting-sources "" \
    --disable-slash-commands \
    --strict-mcp-config \
    --no-chrome \
    --no-session-persistence \
    --dangerously-skip-permissions \
    2>"$STDERR_FILE"
)"
RC=$?

if [[ $RC -ne 0 || -z "${SIDECAR_OUT// /}" ]]; then
  DEBUG="${SNAP_DIR}/handoff-${TS}.error.log"
  {
    printf 'sop-compact PreCompact sidecar failed (rc=%s) at %s\n' "$RC" "$TS"
    printf 'model=%s session=%s\n\n--- stderr ---\n' "$MODEL" "${SESSION_ID:-unknown}"
    cat "$STDERR_FILE" 2>/dev/null
  } >"$DEBUG"
  # Exit 2 hard-blocks compaction so the user keeps the live context and knows the
  # snapshot failed (rather than silently compacting into a lossy summary).
  echo "sop-compact: pre-compact sidecar failed (rc=$RC). Compaction blocked to preserve context. See $DEBUG" >&2
  exit 2
fi

# The sidecar wraps its handoff in ===HANDOFF===/===END=== sentinels so any promotion-
# decision narration it emits stays out of the saved file. Extract the body; if the
# sentinels are missing/malformed, fall back to the raw output (a degraded snapshot beats
# losing the in-flight context) and warn so a maintainer can spot the extraction failure.
if HANDOFF_BODY="$(printf '%s\n' "$SIDECAR_OUT" | extract_handoff)"; then
  HANDOFF_CONTENT="$HANDOFF_BODY"
else
  HANDOFF_CONTENT="$SIDECAR_OUT"
  echo "sop-compact: sidecar output missing sentinels; wrote raw output as fallback (see handoff for inspection)" >&2
fi

# Write the handoff atomically so SessionStart never reads a partial file.
TMP="$(mktemp "${SNAP_DIR}/.handoff-${TS}.XXXXXX")"
printf '%s\n' "$HANDOFF_CONTENT" >"$TMP"
mv -f "$TMP" "$HANDOFF"
prune_handoffs

# --- Optional repo-local extension: run after a successful snapshot. ---------------
# Failures here must not take down the pre-hook, so guard with controlled error handling.
EXT="${REPO_DIR}/.claude/sop-compact/pre.sh"
if [[ -f "$EXT" ]]; then
  ( set +e; SOP_COMPACT_HANDOFF="$HANDOFF" SOP_COMPACT_TRANSCRIPT="$TRANSCRIPT" bash "$EXT" ) || true
fi

exit 0
