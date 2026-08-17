"""Pre-answering the questions Claude Code asks a *fresh* home directory.

A contained run starts an interactive session inside tmux, on a machine whose `~/.claude` has never
been used. Claude Code quite reasonably asks two things before doing anything:

1. **"Do you trust this folder?"** — the workspace, and again for each new directory the session
   reaches, including the experiment worktrees the CEO creates under it.
2. **"New MCP server found in this project"** — the division's server, from the `.mcp.json` the
   runtime writes next to the project.
3. **"Bypass Permissions mode — do you accept?"** — because the factory runs Claude Code with
   `--dangerously-skip-permissions`, which is what makes an unattended agent loop possible at all.

Both are asked only in interactive mode; `-p` skips them (Claude Code's own `--help` says so). That
is why headless specialist agents never hit this and the interactive CEO does — and why the failure
looks like a hang rather than an error. The run sits at a menu in a terminal nobody is watching,
having already spent the tokens it took to get there.

**None of these has an open answer here.** The workspace is a copy the runtime just made of a
project the user named on the command line; the MCP server is one the runtime just registered
because the user passed `--division`. Answering them at launch is recording a decision the user
already made, not making one on their behalf — which is exactly why this seeds *only* those two
things and touches nothing else in the file.
"""

from __future__ import annotations

import json
import shlex


def render_seed_command(workspace: str, mcp_servers: tuple[str, ...] = ()) -> str:
    """A shell command that merges the trust and MCP answers into `$HOME/.claude.json`.

    Merged rather than written: the file already exists in the image (it carries the
    onboarding marker) and `~/.claude` may be a mount the user opted into with `--mount`, in which
    case it is *their* file and clobbering it would discard real history.

    Seeding the workspace path alone is *not* enough: the CEO works inside an experiment worktree
    whose directory carries a per-run id
    (`.factory-worktrees/run-<id>`), and Claude Code resolves the project from the current
    directory. That path cannot be known at launch. So the two answers are given the only way that
    covers a directory not yet created — `hasTrustDialogAccepted` at the top level of
    `~/.claude.json`, and `enableAllProjectMcpServers` in `~/.claude/settings.json`, which approves
    servers declared by a project's own `.mcp.json` without naming the project.
    """
    payload = json.dumps(
        {"workspace": workspace, "servers": list(mcp_servers)}, sort_keys=True
    )
    script = _SEED_SCRIPT.replace("__PAYLOAD__", payload)
    return f"python3 -c {shlex.quote(script)}"


# Kept as a literal rather than a file so it travels with the run command into either runtime, and
# stdlib-only because it runs before anything the factory installs is guaranteed importable.
_SEED_SCRIPT = '''
import json, os
spec = json.loads("""__PAYLOAD__""")
path = os.path.expanduser("~/.claude.json")
try:
    with open(path) as handle:
        state = json.load(handle)
except (OSError, ValueError):
    state = {}
if not isinstance(state, dict):
    state = {}
state["hasCompletedOnboarding"] = True
projects = state.setdefault("projects", {})
if not isinstance(projects, dict):
    projects = state["projects"] = {}
workspace = spec["workspace"]
for directory in (workspace, os.path.join(workspace, ".factory-worktrees")):
    entry = projects.setdefault(directory, {})
    if not isinstance(entry, dict):
        entry = projects[directory] = {}
    entry["hasTrustDialogAccepted"] = True
    if spec["servers"]:
        enabled = set(entry.get("enabledMcpjsonServers") or [])
        entry["enabledMcpjsonServers"] = sorted(enabled | set(spec["servers"]))
state["hasTrustDialogAccepted"] = True
state["bypassPermissionsModeAccepted"] = True
with open(path, "w") as handle:
    json.dump(state, handle, indent=2)

if spec["servers"]:
    settings_dir = os.path.expanduser("~/.claude")
    os.makedirs(settings_dir, exist_ok=True)
    settings_path = os.path.join(settings_dir, "settings.json")
    try:
        with open(settings_path) as handle:
            settings = json.load(handle)
    except (OSError, ValueError):
        settings = {}
    if not isinstance(settings, dict):
        settings = {}
    settings["enableAllProjectMcpServers"] = True
    with open(settings_path, "w") as handle:
        json.dump(settings, handle, indent=2)
'''.strip()
