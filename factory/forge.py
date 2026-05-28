"""Unified forge operations — abstraction over GitHub (gh) and GitLab (glab) CLIs."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import structlog

from factory.issue import Forge, infer_remote

log = structlog.get_logger()


class ForgeOps:
    """Dispatch CLI commands to ``gh`` or ``glab`` based on detected forge.

    All methods build the correct CLI command, run it, and normalize the
    JSON output so callers get a consistent schema regardless of forge.
    """

    def __init__(self, project_path: Path, *, forge: Forge | None = None, repo: str | None = None) -> None:
        if forge and repo:
            self.forge: Forge = forge
            self.repo = repo
        elif forge:
            self.forge = forge
            self.repo = ""
        else:
            self.forge, self.repo = infer_remote(project_path)
        self.project_path = project_path
        self._cli = "gh" if self.forge == "github" else "glab"

    def _run(
        self,
        cmd: list[str],
        *,
        timeout: int = 30,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        log.debug("forge_run", cmd=" ".join(cmd), forge=self.forge)
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd or self.project_path,
        )

    def _repo_args(self) -> list[str]:
        if not self.repo:
            return []
        if self.forge == "github":
            return ["-R", self.repo]
        return ["--repo", self.repo]

    # ── Issues ──────────────────────────────────────────────────

    def issue_create(self, title: str, body: str, labels: list[str] | None = None) -> dict | None:
        if self.forge == "github":
            cmd = [self._cli, "issue", "create", "--title", title, "--body", body]
            for lb in labels or []:
                cmd.extend(["--label", lb])
            cmd.extend(["--json", "number,title,url"])
            cmd.extend(self._repo_args())
        else:
            cmd = [self._cli, "issue", "create", "--title", title, "--description", body]
            for lb in labels or []:
                cmd.extend(["--label", lb])
            cmd.extend(["--output", "json"])
            cmd.extend(self._repo_args())

        try:
            result = self._run(cmd)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            log.warning("issue_create_failed", stderr=result.stderr[:200])
            return None
        parsed = self._parse_json(result.stdout)
        return parsed if isinstance(parsed, dict) else None

    def issue_list(
        self,
        *,
        state: str = "open",
        labels: list[str] | None = None,
        limit: int = 20,
        fields: list[str] | None = None,
    ) -> list[dict]:
        gl_state = "opened" if state == "open" else state

        if self.forge == "github":
            cmd = [self._cli, "issue", "list", "--state", state, "--limit", str(limit)]
            for lb in labels or []:
                cmd.extend(["--label", lb])
            json_fields = ",".join(fields) if fields else "number,title,labels,body,author"
            cmd.extend(["--json", json_fields])
            cmd.extend(self._repo_args())
        else:
            cmd = [self._cli, "issue", "list", "--state", gl_state, "--per-page", str(limit)]
            for lb in labels or []:
                cmd.extend(["--label", lb])
            cmd.extend(["--output", "json"])
            cmd.extend(self._repo_args())

        try:
            result = self._run(cmd, cwd=self.project_path)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []
        if result.returncode != 0:
            return []
        raw = self._parse_json(result.stdout)
        if not isinstance(raw, list):
            return []
        return [self._normalize_issue(i) for i in raw]

    # ── Pull / Merge Requests ───────────────────────────────────

    def pr_create(
        self,
        title: str,
        body: str,
        base: str,
        *,
        draft: bool = False,
    ) -> dict | None:
        if self.forge == "github":
            cmd = [self._cli, "pr", "create", "--title", title, "--body", body, "--base", base]
            if draft:
                cmd.append("--draft")
            cmd.extend(["--json", "number,title,url"])
            cmd.extend(self._repo_args())
        else:
            cmd = [
                self._cli, "mr", "create",
                "--title", title,
                "--description", body,
                "--target-branch", base,
            ]
            if draft:
                cmd.append("--draft")
            cmd.extend(self._repo_args())

        try:
            result = self._run(cmd)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            log.warning("pr_create_failed", stderr=result.stderr[:200])
            return None
        parsed = self._parse_json(result.stdout)
        if isinstance(parsed, dict):
            return parsed
        # GitLab: glab mr create may not output JSON; parse the MR URL from stdout
        if self.forge != "github" and result.stdout.strip():
            return {"url": result.stdout.strip().splitlines()[-1]}
        return None

    def pr_list(self, *, state: str = "open", limit: int = 20) -> list[dict]:
        gl_state = "opened" if state == "open" else state

        if self.forge == "github":
            cmd = [
                self._cli, "pr", "list",
                "--state", state, "--limit", str(limit),
                "--json", "number,title,url,state",
            ]
            cmd.extend(self._repo_args())
        else:
            cmd = [
                self._cli, "mr", "list",
                "--state", gl_state, "--per-page", str(limit),
                "--output", "json",
            ]
            cmd.extend(self._repo_args())

        try:
            result = self._run(cmd)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []
        if result.returncode != 0:
            return []
        raw = self._parse_json(result.stdout)
        if not isinstance(raw, list):
            return []
        return [self._normalize_pr(p) for p in raw]

    def pr_diff(self, pr_number: int) -> str:
        kind = "pr" if self.forge == "github" else "mr"
        cmd = [self._cli, kind, "diff", str(pr_number)]
        cmd.extend(self._repo_args())
        try:
            result = self._run(cmd, timeout=60)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return ""
        if result.returncode != 0:
            return ""
        return result.stdout

    def pr_diff_names(self, pr_number: int) -> list[str]:
        if self.forge == "github":
            cmd = [self._cli, "pr", "diff", str(pr_number), "--name-only"]
            cmd.extend(self._repo_args())
            try:
                result = self._run(cmd, timeout=60)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return []
            if result.returncode != 0:
                return []
            return [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]

        # glab has no --name-only: parse diff headers
        raw_diff = self.pr_diff(pr_number)
        return self._parse_diff_filenames(raw_diff)

    def pr_close(self, pr_number: int) -> bool:
        kind = "pr" if self.forge == "github" else "mr"
        cmd = [self._cli, kind, "close", str(pr_number)]
        cmd.extend(self._repo_args())
        try:
            result = self._run(cmd)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    def pr_ready(self, pr_number: int) -> bool:
        if self.forge == "github":
            cmd = [self._cli, "pr", "ready", str(pr_number)]
        else:
            cmd = [self._cli, "mr", "update", str(pr_number), "--ready"]
        cmd.extend(self._repo_args())
        try:
            result = self._run(cmd)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    # ── Reviews ─────────────────────────────────────────────────

    def post_review(
        self,
        pr_number: int,
        body: str,
        verdict: str,
    ) -> bool:
        if self.forge == "github":
            flag = "--approve" if verdict == "KEEP" else "--request-changes"
            cmd = [self._cli, "pr", "review", str(pr_number), flag, "--body", body]
            cmd.extend(self._repo_args())
            try:
                result = self._run(cmd)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return False
            return result.returncode == 0

        # GitLab: no --request-changes equivalent
        if verdict == "KEEP":
            # approve + note
            approve_cmd = [self._cli, "mr", "approve", str(pr_number)]
            approve_cmd.extend(self._repo_args())
            try:
                self._run(approve_cmd)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
        # Always add a note with the review body
        note_cmd = [self._cli, "mr", "note", str(pr_number), "--message", body]
        note_cmd.extend(self._repo_args())
        try:
            result = self._run(note_cmd)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    # ── Search / User ───────────────────────────────────────────

    def search_repos(self, query: str, *, limit: int = 5) -> list[dict]:
        if self.forge == "github":
            cmd = [
                self._cli, "search", "repos", query,
                "--limit", str(limit),
                "--json", "fullName,url,description,stargazersCount",
            ]
        else:
            # glab search is instance-scoped; graceful fallback
            cmd = [
                self._cli, "repo", "search", query,
                "--per-page", str(limit),
                "--output", "json",
            ]

        try:
            result = self._run(cmd, timeout=15)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []
        if result.returncode != 0:
            return []
        raw = self._parse_json(result.stdout)
        if not isinstance(raw, list):
            return []
        return [self._normalize_search_result(r) for r in raw[:limit]]

    def get_user(self) -> str | None:
        if self.forge == "github":
            cmd = [self._cli, "api", "user", "--jq", ".login"]
        else:
            cmd = [self._cli, "api", "user", "--jq", ".username"]

        try:
            result = self._run(cmd, timeout=10)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    # ── Normalization helpers ───────────────────────────────────

    def _normalize_issue(self, raw: dict) -> dict:
        if self.forge == "github":
            return {
                "number": raw.get("number", 0),
                "title": raw.get("title", ""),
                "labels": [lb.get("name", "") for lb in (raw.get("labels") or [])],
                "body": (raw.get("body") or "")[:300],
                "author": (raw.get("author") or {}).get("login", ""),
            }
        return {
            "number": raw.get("iid", 0),
            "title": raw.get("title", ""),
            "labels": raw.get("labels", []),
            "body": (raw.get("description") or "")[:300],
            "author": (raw.get("author") or {}).get("username", ""),
        }

    def _normalize_pr(self, raw: dict) -> dict:
        if self.forge == "github":
            return {
                "number": raw.get("number", 0),
                "title": raw.get("title", ""),
                "url": raw.get("url", ""),
                "state": raw.get("state", ""),
            }
        return {
            "number": raw.get("iid", 0),
            "title": raw.get("title", ""),
            "url": raw.get("web_url", ""),
            "state": raw.get("state", ""),
        }

    def _normalize_search_result(self, raw: dict) -> dict:
        if self.forge == "github":
            return {
                "name": raw.get("fullName", ""),
                "url": raw.get("url", ""),
                "description": (raw.get("description") or "")[:200],
                "stars": raw.get("stargazersCount", 0),
            }
        return {
            "name": raw.get("path_with_namespace", raw.get("name", "")),
            "url": raw.get("web_url", raw.get("http_url_to_repo", "")),
            "description": (raw.get("description") or "")[:200],
            "stars": raw.get("star_count", 0),
        }

    @staticmethod
    def _parse_json(text: str) -> dict | list | None:
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None

    @staticmethod
    def _parse_diff_filenames(diff_text: str) -> list[str]:
        """Extract changed file paths from unified diff output."""
        names: list[str] = []
        seen: set[str] = set()
        for line in diff_text.splitlines():
            if line.startswith("+++ b/"):
                fname = line[6:]
                if fname not in seen:
                    names.append(fname)
                    seen.add(fname)
        return names
