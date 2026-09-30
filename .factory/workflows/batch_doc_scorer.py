"""Batch document scorer workflow — grammar, readability, and structure scoring for markdown files.

5-node linear pipeline: scan_files → validate_scan → score_files → aggregate_report → review_gate
HALT from validate_scan if no files found.
PROCEED/HALT from review_gate (optional interactive checkpoint).

Invoked via: factory workflow run batch-document-scorer <project_path>
Output: .factory/reports/doc-quality-report.md, .factory/reports/doc-quality-report.json
"""

from __future__ import annotations

from typing import Any

from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    ArtifactCheck,
    Edge,
    FnNode,
    GateNode,
    ProjectState,
    VerdictType,
    Workflow,
)

meta = {
    "name": "batch-document-scorer",
    "description": (
        "Batch document scorer — scans a directory for .md files, scores each "
        "for grammar (0-10), readability (0-10), and structure (0-10) using an "
        "LLM-based evaluator, and produces a summary report with per-file "
        "metrics and aggregate statistics."
    ),
    "argument_hint": "<project_path>",
}


def workflow() -> Workflow:
    """Build the batch-document-scorer workflow — linear pipeline with validation and review gates."""
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # ── Node 1: Scan files ───────────────────────────────────────
    #
    # Uses bash `find` to locate all .md files under the project path.
    # Writes a plain-text file list (one absolute path per line) to the
    # workspace directory.  The command:
    #   1. Creates the workspace and reports directories (mkdir -p)
    #   2. Finds all *.md files recursively, excluding hidden dirs and
    #      node_modules
    #   3. Writes the list to .factory/workspace/doc-scorer/files.txt
    #
    nodes["scan_files"] = FnNode(
        id="scan_files",
        command=(
            "bash -c '"
            'set -e; '
            'mkdir -p {project_path}/.factory/workspace/doc-scorer; '
            'mkdir -p {project_path}/.factory/reports; '
            'find {project_path} '
            '-type f -name "*.md" '
            '-not -path "*/.git/*" '
            '-not -path "*/node_modules/*" '
            '-not -path "*/.factory/workspace/*" '
            '> {project_path}/.factory/workspace/doc-scorer/files.txt '
            "|| true"
            "'"
        ),
        notes=(
            "Scan the project directory for all .md files, excluding .git, "
            "node_modules, and the workspace itself. Write one absolute path "
            "per line to files.txt. The || true ensures the node succeeds even "
            "if find returns no results (empty file is handled by validate_scan)."
        ),
        writes={".factory/workspace/doc-scorer/files.txt"},
    )

    # ── Node 2: Validate scan ────────────────────────────────────
    #
    # GateNode with fn evaluator that checks whether at least one .md
    # file was found.  Prints PROCEED if the file list is non-empty,
    # HALT if zero files were found.
    #
    nodes["validate_scan"] = GateNode(
        id="validate_scan",
        evaluator_type="fn",
        evaluator_command=(
            'python3 -c "'
            "import sys; "
            "from pathlib import Path; "
            "p = Path('{project_path}/.factory/workspace/doc-scorer/files.txt'); "
            "lines = [l for l in p.read_text().splitlines() if l.strip()] if p.exists() else []; "
            "count = len(lines); "
            "print(f'Found {count} markdown files'); "  # noqa: RUF027
            'print("PROCEED" if count > 0 else "HALT")'
            '"'
        ),
        reads={".factory/workspace/doc-scorer/files.txt"},
    )

    # ── Node 3: Score files ──────────────────────────────────────
    #
    # AgentNode with RESEARCHER role that reads each markdown file from
    # the file list and scores it on three dimensions.  The agent writes
    # structured JSON output to scores.json.
    #
    nodes["score_files"] = AgentNode(
        id="score_files",
        role=AgentRole.RESEARCHER,
        model="sonnet",
        timeout=1200,
        prompt_template=(
            "You are a documentation quality evaluator. Your task is to score "
            "markdown files for quality.\n\n"
            "1. Read the file list at "
            "{project_path}/.factory/workspace/doc-scorer/files.txt\n\n"
            "2. For EACH markdown file listed, read its content and evaluate it "
            "on three dimensions, each scored 0-10:\n\n"
            "   **Grammar (0-10):**\n"
            "   - Spelling errors\n"
            "   - Subject-verb agreement\n"
            "   - Punctuation issues\n"
            "   - Sentence fragments or run-on sentences\n"
            "   - 10 = flawless, 0 = pervasive errors\n\n"
            "   **Readability (0-10):**\n"
            "   - Clarity and flow\n"
            "   - Active vs passive voice balance\n"
            "   - Sentence length variance\n"
            "   - Paragraph structure and coherence\n"
            "   - Appropriate heading hierarchy\n"
            "   - 10 = effortlessly clear, 0 = impenetrable\n\n"
            "   **Structure (0-10):**\n"
            "   - Proper markdown syntax (headings, lists, links, code blocks)\n"
            "   - Internal link validity\n"
            "   - Code block formatting and language tags\n"
            "   - Heading order (H1 → H2 → H3, no skips)\n"
            "   - Consistent formatting conventions\n"
            "   - 10 = perfectly structured, 0 = broken formatting\n\n"
            "3. Write the results as a JSON file to "
            "{project_path}/.factory/workspace/doc-scorer/scores.json with this "
            "EXACT schema:\n"
            "```json\n"
            "{\n"
            '  "files": [\n'
            "    {\n"
            '      "path": "relative/path/to/file.md",\n'
            '      "grammar": 8.5,\n'
            '      "readability": 7.0,\n'
            '      "structure": 9.0,\n'
            '      "issues": ["Issue description 1", "Issue description 2"]\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "```\n\n"
            "IMPORTANT RULES:\n"
            "- Score EVERY file in the list — do not skip any\n"
            "- Each score MUST be a number between 0 and 10 (decimals allowed)\n"
            "- The 'issues' array should list specific problems found (empty if none)\n"
            "- Use paths relative to the project root in the output\n"
            "- The output MUST be valid JSON\n"
            "- If a file cannot be read (binary, empty, etc.), score it 0/0/0 and "
            "note the reason in issues\n"
        ),
        reads={".factory/workspace/doc-scorer/files.txt"},
        writes={".factory/workspace/doc-scorer/scores.json"},
        post_checks=[
            ArtifactCheck(
                path=".factory/workspace/doc-scorer/scores.json",
                must_exist=True,
                min_size=20,
                must_contain=['"files"', '"grammar"', '"readability"', '"structure"'],
            )
        ],
    )

    # ── Node 4: Aggregate report ─────────────────────────────────
    #
    # FnNode that reads scores.json and produces both the markdown
    # summary report and the machine-readable JSON report.
    #
    nodes["aggregate_report"] = FnNode(
        id="aggregate_report",
        command=(
            "python3 -c \""
            "import json, sys, statistics\n"
            "from pathlib import Path\n"
            "\n"
            "project = '{project_path}'\n"
            "scores_path = Path(project) / '.factory/workspace/doc-scorer/scores.json'\n"
            "report_md_path = Path(project) / '.factory/reports/doc-quality-report.md'\n"
            "report_json_path = Path(project) / '.factory/reports/doc-quality-report.json'\n"
            "\n"
            "data = json.loads(scores_path.read_text())\n"
            "files = data.get('files', [])\n"
            "\n"
            "if not files:\n"
            "    report_md_path.write_text('# Documentation Quality Report\\n\\nNo files scored.\\n')\n"
            "    report_json_path.write_text(json.dumps({'files_scored': 0}, indent=2))\n"
            "    sys.exit(0)\n"
            "\n"
            "for f in files:\n"
            "    f['composite'] = round((f.get('grammar',0) + f.get('readability',0) + f.get('structure',0)) / 3, 2)\n"
            "\n"
            "g_scores = [f['grammar'] for f in files]\n"
            "r_scores = [f['readability'] for f in files]\n"
            "s_scores = [f['structure'] for f in files]\n"
            "c_scores = [f['composite'] for f in files]\n"
            "\n"
            "def stats(vals):\n"
            "    return {'mean': round(statistics.mean(vals), 2), 'median': round(statistics.median(vals), 2), "
            "'min': round(min(vals), 2), 'max': round(max(vals), 2)}\n"
            "\n"
            "summary = {\n"
            "    'files_scored': len(files),\n"
            "    'statistics': {\n"
            "        'grammar': stats(g_scores),\n"
            "        'readability': stats(r_scores),\n"
            "        'structure': stats(s_scores),\n"
            "        'composite': stats(c_scores)\n"
            "    },\n"
            "    'files': sorted(files, key=lambda x: x['composite'])\n"
            "}\n"
            "\n"
            "report_json_path.write_text(json.dumps(summary, indent=2))\n"
            "\n"
            "md_lines = [\n"
            "    '# Documentation Quality Report\\n',\n"
            "    f'**Files Scored**: {len(files)}  ',\n"
            "    f'**Avg Grammar**: {stats(g_scores)[\"mean\"]}/10  ',\n"
            "    f'**Avg Readability**: {stats(r_scores)[\"mean\"]}/10  ',\n"
            "    f'**Avg Structure**: {stats(s_scores)[\"mean\"]}/10  ',\n"
            "    f'**Avg Composite**: {stats(c_scores)[\"mean\"]}/10\\n',\n"
            "    '---\\n',\n"
            "    '## Per-File Scores\\n',\n"
            "    '| File | Grammar | Readability | Structure | Composite | Issues |',\n"
            "    '|------|---------|-------------|-----------|-----------|--------|',\n"
            "]\n"
            "\n"
            "for f in sorted(files, key=lambda x: x['composite']):\n"
            "    issues_str = '; '.join(f.get('issues', [])) if f.get('issues') else 'None'\n"
            "    md_lines.append(f'| {f[\"path\"]} | {f[\"grammar\"]} | {f[\"readability\"]} | {f[\"structure\"]} | {f[\"composite\"]} | {issues_str} |')\n"
            "\n"
            "md_lines.append('\\n---\\n')\n"
            "\n"
            "needs_review = [f for f in files if f['composite'] < 7.0]\n"
            "if needs_review:\n"
            "    md_lines.append(f'## Needs Review ({len(needs_review)} files)\\n')\n"
            "    for f in needs_review:\n"
            "        md_lines.append(f'- **{f[\"path\"]}** (composite: {f[\"composite\"]}): {\"; \".join(f.get(\"issues\", [\"No specific issues noted\"]))}')\n"
            "    md_lines.append('')\n"
            "\n"
            "report_md_path.write_text('\\n'.join(md_lines))\n"
            "print(f'Report written: {len(files)} files scored')"
            "\""
        ),
        notes=(
            "Aggregate individual file scores from scores.json into summary "
            "statistics and two output reports: a markdown summary with per-file "
            "table sorted by composite score, and a machine-readable JSON file "
            "with full statistics. Files with composite score < 7.0 are flagged "
            "in a 'Needs Review' section."
        ),
        reads={".factory/workspace/doc-scorer/scores.json"},
        writes={
            ".factory/reports/doc-quality-report.md",
            ".factory/reports/doc-quality-report.json",
        },
    )

    # ── Node 5: Review gate (optional interactive checkpoint) ────
    #
    # User gate that pauses execution so a human can inspect the report
    # before the workflow terminates.  In headless mode, this gate is
    # auto-approved (PROCEED).  When running interactively, the user
    # sees the report paths and can approve or halt.
    #
    nodes["review_gate"] = GateNode(
        id="review_gate",
        evaluator_type="user",
        reads={
            ".factory/reports/doc-quality-report.md",
            ".factory/reports/doc-quality-report.json",
        },
    )

    # ── Edges ────────────────────────────────────────────────────
    edges = [
        # Linear flow: scan → validate → score → aggregate → review
        Edge(source="scan_files", target="validate_scan"),
        Edge(source="validate_scan", target="score_files", condition=VerdictType.PROCEED),
        Edge(source="validate_scan", target="review_gate", condition=VerdictType.HALT),
        Edge(source="score_files", target="aggregate_report"),
        Edge(source="aggregate_report", target="review_gate"),
        # review_gate is the terminal node — both verdicts end the workflow
    ]

    # ── Trigger ──────────────────────────────────────────────────
    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        """Trigger when mode is 'batch-document-scorer'."""
        return ctx.get("mode") == "batch-document-scorer"

    return Workflow(
        name="batch-document-scorer",
        nodes=nodes,
        edges=edges,
        start_node="scan_files",
        terminal=True,
        trigger=trigger,
    )
