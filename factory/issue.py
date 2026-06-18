"""Fetch and format GitHub/GitLab issues as build specs."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import structlog

log = structlog.get_logger()

Forge = Literal["github", "gitlab"]


@dataclass
class IssueSpec:
    number: int
    title: str
    body: str
    labels: list[str] = field(default_factory=list)
    url: str = ""
    forge: Forge = "github"


def parse_issue_ref(ref: str, project_path: Path) -> tuple[Forge, str, int]:
    """Parse an issue reference into (forge, owner/repo, number).

    Handles:
    - ``42`` — bare number, infer remote from git
    - ``https://github.com/owner/repo/issues/42``
    - ``https://gitlab.com/owner/repo/-/issues/42``
    - ``owner/repo#42`` — GitHub shorthand
    """
    ref = ref.strip()

    gh_url = re.match(
        r"https?://([^/]+)/([^/]+/[^/]+)/issues/(\d+)", ref,
    )
    if gh_url:
        host = gh_url.group(1)
        owner_repo = gh_url.group(2)
        number = int(gh_url.group(3))
        forge: Forge = "gitlab" if "gitlab" in host else "github"
        return forge, owner_repo, number

    gl_url = re.match(
        r"https?://([^/]+)/(.+?)/-/issues/(\d+)", ref,
    )
    if gl_url:
        owner_repo = gl_url.group(2)
        number = int(gl_url.group(3))
        return "gitlab", owner_repo, number

    shorthand = re.match(r"^([^/]+/[^#]+)#(\d+)$", ref)
    if shorthand:
        owner_repo = shorthand.group(1)
        number = int(shorthand.group(2))
        return "github", owner_repo, number

    if ref.isdigit():
        forge, owner_repo = infer_remote(project_path)
        return forge, owner_repo, int(ref)

    raise ValueError(
        f"Cannot parse issue reference: {ref!r}. "
        "Expected a number, URL, or owner/repo#number."
    )


def infer_remote(project_path: Path) -> tuple[Forge, str]:
    """Infer forge and owner/repo from ``git remote get-url origin``."""
    try:
        result = subprocess.run(
            ["git", "-C", str(project_path), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Cannot infer remote: git remote get-url origin failed in {project_path}"
        ) from exc

    url = result.stdout.strip()
    log.debug("inferred_git_remote", url=url, project=str(project_path))

    ssh_match = re.match(r"git@([^:]+):(.+?)(?:\.git)?$", url)
    if ssh_match:
        host = ssh_match.group(1)
        owner_repo = ssh_match.group(2)
        forge: Forge = "gitlab" if "gitlab" in host else "github"
        return forge, owner_repo

    parsed = urlparse(url)
    if parsed.hostname:
        path = parsed.path.lstrip("/").removesuffix(".git")
        forge = "gitlab" if "gitlab" in parsed.hostname else "github"
        return forge, path

    raise RuntimeError(f"Cannot parse git remote URL: {url!r}")


def fetch_issue(issue_ref: str, project_path: Path) -> IssueSpec:
    """Fetch an issue from GitHub or GitLab and return an ``IssueSpec``."""
    forge, owner_repo, number = parse_issue_ref(issue_ref, project_path)
    log.info("fetching_issue", forge=forge, repo=owner_repo, number=number)

    if forge == "github":
        cmd = [
            "gh", "issue", "view", str(number),
            "-R", owner_repo,
            "--json", "title,body,labels,number,url",
        ]
    else:
        cmd = [
            "glab", "issue", "view", str(number),
            "--repo", owner_repo,
            "--output", "json",
        ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError:
        cli = "gh" if forge == "github" else "glab"
        raise RuntimeError(
            f"{cli} CLI not found. Install it to fetch {forge} issues."
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Failed to fetch {forge} issue #{number} from {owner_repo}: "
            f"{exc.stderr.strip()}"
        ) from exc

    data = json.loads(result.stdout)

    if forge == "github":
        labels = [lb["name"] for lb in data.get("labels", [])]
        return IssueSpec(
            number=data["number"],
            title=data["title"],
            body=data.get("body", ""),
            labels=labels,
            url=data.get("url", ""),
            forge=forge,
        )

    return IssueSpec(
        number=data.get("iid", number),
        title=data.get("title", ""),
        body=data.get("description", ""),
        labels=data.get("labels", []),
        url=data.get("web_url", ""),
        forge=forge,
    )


def is_issue_ref(ref: str) -> bool:
    """Check if *ref* looks like an issue reference without needing a project path.

    Detects URLs, ``owner/repo#N`` shorthand, and bare integers.
    Does NOT validate that the issue exists — just pattern-matches.
    """
    ref = ref.strip()
    if ref.isdigit():
        return True
    if re.match(r"https?://[^/]+/.+/issues/\d+", ref):
        return True
    if re.match(r"^[^#]+/[^#]+#\d+$", ref):
        return True
    return False


def format_issue_as_spec(spec: IssueSpec) -> str:
    """Format an ``IssueSpec`` as a markdown build specification."""
    lines = [f"# {spec.title}", ""]
    if spec.url:
        lines.append(f"Issue: {spec.url}")
        lines.append("")
    if spec.labels:
        lines.append(f"Labels: {', '.join(spec.labels)}")
        lines.append("")
    lines.append(spec.body)
    return "\n".join(lines)
