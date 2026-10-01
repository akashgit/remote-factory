"""Batch document scorer — grammar and readability scoring for markdown files.

3-node linear pipeline: collect_files → score_documents → generate_report
CEO gate after generate_report with RELOOP back to generate_report on quality failure.

Invoked via: factory batch-doc-scorer --focus <directory_or_file_pattern>
Output: .factory/reviews/doc-scorer-report.md
"""

from __future__ import annotations

from typing import Any

from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    ArtifactCheck,
    Edge,
    GateNode,
    ProjectState,
    VerdictType,
    Workflow,
)

meta = {
    "name": "batch-doc-scorer",
    "description": (
        "Batch document scorer — finds markdown files in a target path, "
        "scores each for grammar (spelling, punctuation, syntax) and "
        "readability (Flesch-Kincaid, sentence complexity), then generates "
        "an aggregated summary report with per-file breakdowns and flagged files. "
        "Outputs: .factory/reviews/doc-scorer-report.md"
    ),
    "argument_hint": "<project_path> --focus <directory_or_file_pattern>",
}


def workflow() -> Workflow:
    """Build the batch-doc-scorer workflow — linear pipeline with CEO review gate."""
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # ── Node 1: Collect and validate markdown files ──────────────
    nodes["collect_files"] = AgentNode(
        id="collect_files",
        role=AgentRole.RESEARCHER,
        model="haiku",
        timeout=120,
        prompt_template=(
            "You are a file collection agent. Your job is to find and validate "
            "all markdown files in the target path and produce a manifest.\n\n"
            "The target directory or file pattern is provided via the --focus "
            "argument in the context below this prompt. Extract it and resolve "
            "it relative to {project_path} if it is not absolute.\n\n"
            "Run these steps:\n\n"
            "1. Create the output directory:\n"
            "   mkdir -p {project_path}/.factory/reviews\n\n"
            "2. Find all markdown files (.md) in the target path. If the focus "
            "is a directory, recursively find all .md files. If it is a glob "
            "pattern, expand it. If it is a single file, use that file.\n\n"
            "3. For each file found, validate:\n"
            "   - The file exists and is readable\n"
            "   - The file is valid UTF-8 text\n"
            "   - The file has non-zero size\n"
            "   - The file has a .md extension\n\n"
            "4. Write the manifest to "
            "{project_path}/.factory/reviews/doc-scorer-manifest.md with this "
            "exact structure:\n\n"
            "   # Document Scorer Manifest\n\n"
            "   ## Collection Summary\n"
            "   - **Target path:** <the resolved path>\n"
            "   - **Files found:** <total count>\n"
            "   - **Files valid:** <valid count>\n"
            "   - **Files skipped:** <skipped count>\n\n"
            "   ## Valid Files\n"
            "   | # | File Path | Size (bytes) |\n"
            "   |---|-----------|-------------|\n"
            "   | 1 | path/to/file.md | 1234 |\n"
            "   ...\n\n"
            "   ## Skipped Files\n"
            "   | File Path | Reason |\n"
            "   |-----------|--------|\n"
            "   | path/to/bad.md | Invalid UTF-8 encoding |\n"
            "   ...\n\n"
            "If no valid markdown files are found, still write the manifest with "
            "zero counts and an empty Valid Files table. Note this in the "
            "Collection Summary.\n\n"
            "IMPORTANT: The manifest MUST contain '## Collection Summary' and "
            "'## Valid Files' sections."
        ),
        writes={".factory/reviews/doc-scorer-manifest.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/reviews/doc-scorer-manifest.md",
                must_exist=True,
                min_size=50,
                must_contain=["## Collection Summary", "## Valid Files"],
            )
        ],
    )

    # ── Node 2: Score each document for grammar and readability ──
    nodes["score_documents"] = AgentNode(
        id="score_documents",
        role=AgentRole.RESEARCHER,
        model="sonnet",
        timeout=900,
        prompt_template=(
            "You are a document quality scorer. Read the manifest at "
            "{project_path}/.factory/reviews/doc-scorer-manifest.md to get the "
            "list of valid markdown files.\n\n"
            "For EACH valid file listed in the manifest, perform the following "
            "analysis:\n\n"
            "### Grammar Scoring (0-100)\n"
            "Read the file and evaluate:\n"
            "- **Spelling errors:** Identify misspelled words (skip code blocks, "
            "inline code, URLs, and technical terms)\n"
            "- **Punctuation errors:** Missing periods, commas, mismatched "
            "quotes/brackets\n"
            "- **Syntax errors:** Subject-verb disagreement, sentence fragments, "
            "run-on sentences, incorrect tense usage\n\n"
            "Grammar scoring rubric:\n"
            "- 0 errors: 100\n"
            "- 1-2 errors: 90\n"
            "- 3-5 errors: 80\n"
            "- 6-10 errors: 70\n"
            "- 11-20 errors: 60\n"
            "- 21-30 errors: 50\n"
            "- 31+ errors: max(0, 50 - (errors - 30) * 2)\n\n"
            "### Readability Scoring (0-100)\n"
            "Read the file (excluding code blocks) and evaluate:\n"
            "- **Flesch-Kincaid Grade Level:** Estimate using the formula "
            "0.39*(words/sentences) + 11.8*(syllables/words) - 15.59. "
            "Count syllables by counting vowel groups in each word.\n"
            "- **Average sentence length:** Total words / total sentences\n"
            "- **Sentence complexity:** Fraction of sentences with 25+ words\n"
            "- **Passive voice ratio:** Fraction of sentences using passive voice\n\n"
            "Readability scoring rubric (for technical documentation):\n"
            "- Grade level 8-12: 100 (ideal range)\n"
            "- Grade level 6-7 or 13-14: 80\n"
            "- Grade level 4-5 or 15-16: 60\n"
            "- Grade level < 4 or > 16: 40\n"
            "- Subtract 5 points if > 30% of sentences are passive voice\n"
            "- Subtract 5 points if > 40% of sentences have 25+ words\n\n"
            "### Composite Score\n"
            "composite = round(grammar * 0.5 + readability * 0.5)\n\n"
            "Write ALL scores to "
            "{project_path}/.factory/reviews/doc-scorer-scores.md with this "
            "exact structure:\n\n"
            "   # Document Scores\n\n"
            "   ## Scoring Summary\n"
            "   - **Files scored:** <count>\n"
            "   - **Scoring method:** Grammar (50%) + Readability (50%)\n\n"
            "   ## Per-File Scores\n"
            "   | File | Grammar | Readability | Composite | Status |\n"
            "   |------|---------|-------------|-----------|--------|\n"
            "   | path/file.md | 85 | 90 | 88 | ✓ Pass |\n"
            "   | path/other.md | 60 | 55 | 58 | ✗ Below Threshold |\n"
            "   ...\n\n"
            "   Status: ✓ Pass if composite >= 70, ✗ Below Threshold otherwise.\n\n"
            "   ## Detailed Analysis\n\n"
            "   ### path/file.md\n"
            "   - **Grammar score:** 85\n"
            "     - Errors found: 3\n"
            "     - Issues: [list each error with line number if possible]\n"
            "   - **Readability score:** 90\n"
            "     - Flesch-Kincaid Grade Level: 9.2\n"
            "     - Average sentence length: 15.3 words\n"
            "     - Complex sentences (25+ words): 12%\n"
            "     - Passive voice ratio: 8%\n"
            "   - **Composite:** 88\n\n"
            "   ... (repeat for each file)\n\n"
            "IMPORTANT: The scores file MUST contain '## Scoring Summary', "
            "'## Per-File Scores', and '## Detailed Analysis' sections."
        ),
        reads={".factory/reviews/doc-scorer-manifest.md"},
        writes={".factory/reviews/doc-scorer-scores.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/reviews/doc-scorer-scores.md",
                must_exist=True,
                min_size=100,
                must_contain=[
                    "## Scoring Summary",
                    "## Per-File Scores",
                    "## Detailed Analysis",
                ],
            )
        ],
    )

    # ── Node 3: Generate aggregated summary report ───────────────
    nodes["generate_report"] = AgentNode(
        id="generate_report",
        role=AgentRole.RESEARCHER,
        model="sonnet",
        timeout=600,
        prompt_template=(
            "You are a report generation agent. Read the scores at "
            "{project_path}/.factory/reviews/doc-scorer-scores.md and the "
            "manifest at {project_path}/.factory/reviews/doc-scorer-manifest.md.\n\n"
            "Generate a comprehensive summary report at "
            "{project_path}/.factory/reviews/doc-scorer-report.md with this "
            "exact structure:\n\n"
            "   # Document Quality Report\n\n"
            "   **Generated:** <current date YYYY-MM-DD>\n"
            "   **Target:** <directory or pattern from manifest>\n\n"
            "   ## Overall Statistics\n"
            "   - **Files processed:** <count>\n"
            "   - **Files passed (≥70):** <count> (<percentage>%)\n"
            "   - **Files below threshold (<70):** <count> (<percentage>%)\n"
            "   - **Average composite score:** <mean across all files>\n"
            "   - **Median composite score:** <median>\n"
            "   - **Score range:** <min> – <max>\n"
            "   - **Average grammar score:** <mean>\n"
            "   - **Average readability score:** <mean>\n\n"
            "   ## Per-File Breakdown\n"
            "   | # | File | Grammar | Readability | Composite | Status |\n"
            "   |---|------|---------|-------------|-----------|--------|\n"
            "   | 1 | path/file.md | 85 | 90 | 88 | ✓ Pass |\n"
            "   ...\n\n"
            "   Sort the table by composite score ascending (worst files first).\n\n"
            "   ## Flagged Files\n"
            "   List every file with composite < 70. For each:\n"
            "   - **File path** and composite score\n"
            "   - **Primary issue:** Grammar or readability (whichever scored lower)\n"
            "   - **Top 3 specific problems** from the detailed analysis\n"
            "   - **Suggested action:** Concrete recommendation to improve the score\n\n"
            "   If no files are flagged, write: 'No files below threshold.'\n\n"
            "   ## Recommendations\n"
            "   Provide 3-5 actionable recommendations based on patterns across "
            "all files. Examples:\n"
            "   - 'Reduce average sentence length in technical guides (currently "
            "22 words, target < 18)'\n"
            "   - 'Run spell-check on the 4 files with grammar scores below 70'\n"
            "   - 'Simplify vocabulary in API reference docs (grade level 14, "
            "target 10-12)'\n\n"
            "   Base recommendations on actual score patterns — do not be generic.\n\n"
            "IMPORTANT: The report MUST contain '## Overall Statistics', "
            "'## Per-File Breakdown', '## Flagged Files', and "
            "'## Recommendations' sections."
        ),
        reads={
            ".factory/reviews/doc-scorer-scores.md",
            ".factory/reviews/doc-scorer-manifest.md",
        },
        writes={".factory/reviews/doc-scorer-report.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/reviews/doc-scorer-report.md",
                must_exist=True,
                min_size=200,
                must_contain=[
                    "## Overall Statistics",
                    "## Per-File Breakdown",
                    "## Flagged Files",
                    "## Recommendations",
                ],
            )
        ],
    )

    # ── Node 4: CEO review gate ──────────────────────────────────
    nodes["gate_report"] = GateNode(
        id="gate_report",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        max_iterations=2,
        gate_prompt=(
            "Evaluate the document scoring report at "
            ".factory/reviews/doc-scorer-report.md against these quality "
            "criteria:\n\n"
            "1. **Overall Statistics present:** The report has an Overall "
            "Statistics section with files processed, pass/fail counts, "
            "averages, median, and score range.\n"
            "2. **Per-File Breakdown present:** The report contains a table "
            "with grammar, readability, and composite scores for every file.\n"
            "3. **Flagged Files addressed:** If any files scored below 70, "
            "they are listed in the Flagged Files section with specific "
            "problems and suggested actions.\n"
            "4. **Recommendations are data-driven:** The recommendations "
            "section references actual score patterns and gives concrete "
            "suggestions (not generic advice).\n"
            "5. **Consistency:** The numbers in Overall Statistics match "
            "the Per-File Breakdown table (counts, averages are plausible).\n\n"
            "PROCEED if all 5 criteria are met.\n"
            "RELOOP to generate_report with specific feedback stating which "
            "criteria failed and what needs to be fixed.\n"
            "HALT only if the report is fundamentally broken (empty, "
            "contains no scoring data, or scores were not actually computed)."
        ),
        reads={
            ".factory/reviews/doc-scorer-report.md",
            ".factory/reviews/doc-scorer-scores.md",
        },
    )

    # ── Edges ────────────────────────────────────────────────────
    edges = [
        # Linear flow: collect → score → report
        Edge(source="collect_files", target="score_documents"),
        Edge(source="score_documents", target="generate_report"),
        Edge(source="generate_report", target="gate_report"),
        # Gate verdicts
        Edge(
            source="gate_report",
            target="generate_report",
            condition=VerdictType.RELOOP,
        ),
        Edge(
            source="gate_report",
            target="generate_report",
            condition=VerdictType.HALT,
        ),
    ]

    # ── Trigger ──────────────────────────────────────────────────
    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        """Trigger when mode is 'batch-doc-scorer'."""
        return ctx.get("mode") == "batch-doc-scorer"

    # ── Return Workflow ──────────────────────────────────────────
    return Workflow(
        name="batch-doc-scorer",
        nodes=nodes,
        edges=edges,
        start_node="collect_files",
        terminal=True,
        trigger=trigger,
    )
