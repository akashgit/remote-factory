"""Ground truth leakage detection for research mode.

Provides programmatic guards against ground truth content leaking into
hypotheses, strategies, or code changes — either directly (file access)
or indirectly (content hints like "do NOT use subtraction").
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from factory.models import FactoryConfig

import structlog

log = structlog.get_logger()

# Tokens that appear in almost every codebase — not distinctive enough to flag
_STOPWORDS: frozenset[str] = frozenset({
    # Python keywords / builtins
    "def", "class", "return", "import", "from", "if", "else", "elif",
    "for", "while", "try", "except", "with", "as", "in", "not", "and",
    "or", "is", "none", "true", "false", "self", "cls", "pass", "break",
    "continue", "raise", "yield", "lambda", "assert", "global", "nonlocal",
    "finally", "del", "async", "await",
    # JS/TS keywords
    "var", "let", "const", "function", "new", "this", "typeof", "instanceof",
    "null", "undefined", "void", "throw", "catch", "export", "default",
    # Common programming terms
    "test", "tests", "error", "errors", "data", "result", "results",
    "value", "values", "name", "type", "types", "path", "file", "files",
    "list", "dict", "set", "map", "get", "put", "post", "delete",
    "init", "main", "run", "start", "stop", "open", "close", "read",
    "write", "print", "log", "debug", "info", "warn", "config",
    "input", "output", "args", "kwargs", "key", "item", "items",
    "index", "count", "size", "length", "string", "number", "int",
    "float", "bool", "byte", "bytes", "char", "array", "object",
    "node", "text", "content", "body", "header", "status", "code",
    "message", "response", "request", "url", "port", "host",
    "the", "and", "for", "with", "that", "this", "from", "have",
    "are", "was", "were", "been", "has", "had", "will", "would",
    "should", "could", "can", "may", "must", "shall", "might",
    "use", "using", "used", "make", "made", "add", "added",
    "fix", "fixed", "update", "updated", "change", "changed",
    "create", "created", "remove", "removed", "check", "checked",
})

# Minimum token length to consider
_MIN_TOKEN_LEN = 3

# Regex for negation patterns: "do NOT <word>", "avoid <word>", etc.
_NEGATION_PATTERNS = [
    re.compile(r"\bdo\s+not\s+(\w+)", re.IGNORECASE),
    re.compile(r"\bshould\s+not\s+(\w+)", re.IGNORECASE),
    re.compile(r"\bmust\s+not\s+(\w+)", re.IGNORECASE),
    re.compile(r"\bavoid\s+(\w+)", re.IGNORECASE),
    re.compile(r"\bnever\s+(\w+)", re.IGNORECASE),
    re.compile(r"\bdon'?t\s+(\w+)", re.IGNORECASE),
]

# Regex to extract numeric literals (integers and decimals)
_NUMERIC_RE = re.compile(r"\b(\d+\.\d+|\d{2,})\b")

# Regex to extract quoted strings
_QUOTED_RE = re.compile(r"""(?:"([^"]{3,}?)"|'([^']{3,}?)')""")

# Regex to split camelCase and snake_case identifiers
_IDENT_SPLIT_RE = re.compile(r"[A-Z][a-z]+|[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+")

# Sensitivity thresholds for token overlap (Jaccard)
_OVERLAP_THRESHOLDS = {
    "low": 0.25,
    "medium": 0.15,
    "high": 0.08,
}


@dataclass
class LeakageFinding:
    """A single instance of potential ground truth leakage."""

    source_file: str
    leaked_token: str
    context: str
    leak_type: str  # "token_overlap" | "negation_hint" | "specific_value"


@dataclass
class LeakageReport:
    """Result of a content leakage check."""

    flagged: bool
    risk_level: str  # "none" | "low" | "medium" | "high"
    findings: list[LeakageFinding] = field(default_factory=list)


def _tokenize_text(text: str) -> set[str]:
    """Extract tokens from text, splitting identifiers and filtering stopwords."""
    raw_words = re.findall(r"[A-Za-z_]\w*", text)
    tokens: set[str] = set()
    for word in raw_words:
        # Split camelCase/PascalCase
        parts = _IDENT_SPLIT_RE.findall(word)
        for part in parts:
            lower = part.lower()
            if len(lower) >= _MIN_TOKEN_LEN and lower not in _STOPWORDS:
                tokens.add(lower)
        # Also keep the full word if long enough
        lower_word = word.lower()
        if len(lower_word) >= _MIN_TOKEN_LEN and lower_word not in _STOPWORDS:
            tokens.add(lower_word)
    return tokens


def _extract_specific_values(text: str) -> set[str]:
    """Extract numeric literals and quoted strings from text."""
    values: set[str] = set()
    for m in _NUMERIC_RE.finditer(text):
        val = m.group(1)
        # Skip very common numbers
        if val not in ("10", "100", "0.0", "1.0", "0.5"):
            values.add(val)
    for m in _QUOTED_RE.finditer(text):
        val = m.group(1) or m.group(2)
        if val:
            values.add(val)
    return values


def fingerprint_fixed_surfaces(
    project_path: Path, fixed_surfaces: list[str]
) -> dict[str, set[str]]:
    """Build a fingerprint (set of distinctive tokens) for each fixed surface file.

    Glob-expands patterns, reads file content, extracts identifiers and
    specific values. Common stopwords are filtered out.
    """
    fingerprints: dict[str, set[str]] = {}
    if not fixed_surfaces:
        return fingerprints

    for pattern in fixed_surfaces:
        # Glob-expand the pattern relative to project_path
        matched_files = list(project_path.glob(pattern))
        if not matched_files:
            # Try as a direct path
            direct = project_path / pattern
            if direct.is_file():
                matched_files = [direct]

        for filepath in matched_files:
            if not filepath.is_file():
                continue
            try:
                content = filepath.read_text(errors="replace")
            except (OSError, PermissionError):
                continue

            rel_path = str(filepath.relative_to(project_path))
            tokens = _tokenize_text(content)
            values = _extract_specific_values(content)
            tokens |= values

            if tokens:
                fingerprints[rel_path] = tokens

    log.debug(
        "fingerprinted_fixed_surfaces",
        file_count=len(fingerprints),
        total_tokens=sum(len(t) for t in fingerprints.values()),
    )
    return fingerprints


def _check_token_overlap(
    text_tokens: set[str],
    fingerprints: dict[str, set[str]],
    threshold: float,
) -> list[LeakageFinding]:
    """Check Jaccard-like overlap between text tokens and fingerprint sets."""
    findings: list[LeakageFinding] = []
    for source_file, fp_tokens in fingerprints.items():
        if not fp_tokens or not text_tokens:
            continue
        overlap = text_tokens & fp_tokens
        jaccard = len(overlap) / len(text_tokens | fp_tokens)
        if jaccard >= threshold:
            top_tokens = sorted(overlap)[:5]
            findings.append(LeakageFinding(
                source_file=source_file,
                leaked_token=", ".join(top_tokens),
                context=f"Jaccard overlap={jaccard:.2f} ({len(overlap)} shared tokens)",
                leak_type="token_overlap",
            ))
    return findings


def _check_negation_hints(
    text: str,
    fingerprints: dict[str, set[str]],
) -> list[LeakageFinding]:
    """Check for negation patterns that encode ground truth by exclusion."""
    all_fp_tokens: set[str] = set()
    token_sources: dict[str, str] = {}
    for source_file, tokens in fingerprints.items():
        for t in tokens:
            all_fp_tokens.add(t)
            token_sources[t] = source_file

    findings: list[LeakageFinding] = []
    for pattern in _NEGATION_PATTERNS:
        for match in pattern.finditer(text):
            negated_word = match.group(1).lower()
            if negated_word in all_fp_tokens:
                source = token_sources[negated_word]
                start = max(0, match.start() - 20)
                end = min(len(text), match.end() + 20)
                findings.append(LeakageFinding(
                    source_file=source,
                    leaked_token=negated_word,
                    context=text[start:end].strip(),
                    leak_type="negation_hint",
                ))
    return findings


def _check_specific_values(
    text: str,
    fingerprints: dict[str, set[str]],
) -> list[LeakageFinding]:
    """Check for specific numeric/string values from ground truth appearing in text."""
    text_values = _extract_specific_values(text)
    if not text_values:
        return []

    findings: list[LeakageFinding] = []
    for source_file, fp_tokens in fingerprints.items():
        fp_values = {t for t in fp_tokens if re.match(r"^\d", t) or len(t) > 5}
        overlap = text_values & fp_values
        for val in overlap:
            idx = text.find(val)
            start = max(0, idx - 20)
            end = min(len(text), idx + len(val) + 20)
            findings.append(LeakageFinding(
                source_file=source_file,
                leaked_token=val,
                context=text[start:end].strip() if idx >= 0 else val,
                leak_type="specific_value",
            ))
    return findings


def scan_for_leakage(
    text: str,
    fingerprints: dict[str, set[str]],
    sensitivity: str = "medium",
) -> LeakageReport:
    """Scan text for potential ground truth leakage against fixed surface fingerprints.

    Three sub-checks:
    1. Token overlap: Jaccard similarity between text and fingerprint tokens
    2. Negation hints: "do NOT <token>", "avoid <token>" where token is from ground truth
    3. Specific values: numeric/string literals from ground truth appearing in text

    Args:
        text: The text to scan (hypothesis, strategy, diff, etc.)
        fingerprints: Output of fingerprint_fixed_surfaces()
        sensitivity: "low", "medium", or "high" (lower threshold = more sensitive)

    Returns:
        LeakageReport with flagged status, risk level, and findings.
    """
    if not text or not fingerprints:
        return LeakageReport(flagged=False, risk_level="none")

    threshold = _OVERLAP_THRESHOLDS.get(sensitivity, _OVERLAP_THRESHOLDS["medium"])
    text_tokens = _tokenize_text(text)

    all_findings: list[LeakageFinding] = []

    # Sub-check 1: token overlap
    all_findings.extend(_check_token_overlap(text_tokens, fingerprints, threshold))

    # Sub-check 2: negation hints (always run regardless of sensitivity)
    all_findings.extend(_check_negation_hints(text, fingerprints))

    # Sub-check 3: specific values
    all_findings.extend(_check_specific_values(text, fingerprints))

    if not all_findings:
        return LeakageReport(flagged=False, risk_level="none")

    # Determine risk level from findings
    has_negation = any(f.leak_type == "negation_hint" for f in all_findings)
    has_value = any(f.leak_type == "specific_value" for f in all_findings)
    has_overlap = any(f.leak_type == "token_overlap" for f in all_findings)

    if has_negation or (has_value and has_overlap):
        risk_level = "high"
    elif has_value or has_overlap:
        risk_level = "medium"
    else:
        risk_level = "low"

    log.info(
        "leakage_scan_complete",
        flagged=True,
        risk_level=risk_level,
        finding_count=len(all_findings),
    )
    return LeakageReport(flagged=True, risk_level=risk_level, findings=all_findings)


def scan_diff_for_leakage(
    diff_text: str,
    fingerprints: dict[str, set[str]],
    sensitivity: str = "medium",
) -> LeakageReport:
    """Scan a PR diff for ground truth leakage.

    Extracts only added lines (+ prefix) from the diff to avoid false positives
    from unchanged context lines, then runs the standard leakage scanner.
    """
    if not diff_text or not fingerprints:
        return LeakageReport(flagged=False, risk_level="none")

    # Extract only added lines (strip the + prefix)
    added_lines: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added_lines.append(line[1:])

    if not added_lines:
        return LeakageReport(flagged=False, risk_level="none")

    added_text = "\n".join(added_lines)
    return scan_for_leakage(added_text, fingerprints, sensitivity)


def validate_research_config(
    config: FactoryConfig,
    project_path: Path,
) -> list[str]:
    """Validate research mode configuration for ground truth isolation.

    Returns list of error strings (empty = valid).
    """
    from factory.eval.guards import _glob_match

    errors: list[str] = []

    if config.research_target is None:
        errors.append("research_target is not configured — research mode requires a target")

    if not config.fixed_surfaces:
        errors.append(
            "fixed_surfaces is empty — ground truth files must be listed to enable "
            "leakage guards. Without this, all leakage checks are vacuous."
        )

    if not config.mutable_surfaces:
        errors.append(
            "mutable_surfaces is empty — the Builder needs at least one file it can modify"
        )

    # Check that fixed_surfaces patterns match at least one existing file
    for pattern in config.fixed_surfaces:
        matched = list(project_path.glob(pattern))
        direct = project_path / pattern
        if not matched and not direct.exists():
            errors.append(
                f"fixed_surfaces pattern '{pattern}' matches no files — "
                f"verify the pattern is correct"
            )

    # Check for overlap between mutable and fixed surfaces
    for mutable_pattern in config.mutable_surfaces:
        mutable_files = list(project_path.glob(mutable_pattern))
        for mf in mutable_files:
            rel_path = str(mf.relative_to(project_path))
            for fixed_pattern in config.fixed_surfaces:
                if _glob_match(rel_path, fixed_pattern):
                    errors.append(
                        f"overlap: '{rel_path}' matches both mutable_surfaces "
                        f"('{mutable_pattern}') and fixed_surfaces ('{fixed_pattern}')"
                    )

    return errors


def get_diff_text(project_path: Path, baseline_sha: str) -> str:
    """Get the diff between baseline and HEAD."""
    try:
        result = subprocess.run(
            ["git", "diff", f"{baseline_sha}..HEAD"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""
