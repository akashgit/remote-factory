#!/usr/bin/env bash
# Evidence collector for `factory contained`.
#
# Deterministic. No model in the loop. Runs probes, captures argv/stdout/stderr/exit codes,
# and writes evidence.jsonl. It never decides whether a criterion passed — that is the judge's
# job, and keeping the two apart is what makes the judge's verdict mean anything.
#
#   scripts/eval-contained/collect.sh --tiers t0,t1 --phase 1 > evidence.jsonl
#
# Options are documented by `--help`; everything below just locates the interpreter and
# hands off to _collector.py, which holds the logic so it can be unit-tested.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"

# Prefer the repo's own virtualenv so probes exercise the same `factory` the developer runs.
if [[ -n "${VIRTUAL_ENV:-}" && -x "$VIRTUAL_ENV/bin/python3" ]]; then
    PY="$VIRTUAL_ENV/bin/python3"
elif [[ -x "$REPO_ROOT/.venv/bin/python3" ]]; then
    PY="$REPO_ROOT/.venv/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
    PY="$(command -v python3)"
else
    echo "collect.sh: no python3 found" >&2
    exit 2
fi

exec "$PY" "$HERE/_collector.py" --repo-root "$REPO_ROOT" "$@"
