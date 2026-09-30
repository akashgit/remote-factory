---
name: workflow-batch-document-scorer
description: "Run the batch-document-scorer workflow."
disable-model-invocation: true
argument-hint: "<project_path>"
---

# Batch Document Scorer Workflow

The user wants: **$ARGUMENTS**

## Step: Scan Files

Scan the project directory for all .md files, excluding .git, node_modules, and the workspace itself. Write one absolute path per line to files.txt. The || true ensures the node succeeds even if find returns no results (empty file is handled by validate_scan).

```bash
bash -c 'set -e; mkdir -p $PROJECT_PATH/.factory/workspace/doc-scorer; mkdir -p $PROJECT_PATH/.factory/reports; find $PROJECT_PATH -type f -name "*.md" -not -path "*/.git/*" -not -path "*/node_modules/*" -not -path "*/.factory/workspace/*" > $PROJECT_PATH/.factory/workspace/doc-scorer/files.txt || true'
```

### Gate — Validate Scan (Automated)

**MANDATORY:** Wait for the preceding agent to finish, then run this check BEFORE spawning the next agent. Do NOT run agents in parallel across this gate.

```bash
python3 -c "import sys; from pathlib import Path; p = Path('$PROJECT_PATH/.factory/workspace/doc-scorer/files.txt'); lines = [l for l in p.read_text().splitlines() if l.strip()] if p.exists() else []; count = len(lines); print(f'Found {count} markdown files'); print("PROCEED" if count > 0 else "HALT")"
```

- **PROCEED** (exit 0 / no FAIL in output) → continue to `score_files`
- **HALT** (exit non-zero / FAIL in output) → continue to `review_gate` instead.

## Phase 1: Researcher — Score Files

```bash
factory agent researcher --task "You are a documentation quality evaluator. Your task is to score markdown files for quality.

1. Read the file list at $PROJECT_PATH/.factory/workspace/doc-scorer/files.txt

2. For EACH markdown file listed, read its content and evaluate it on three dimensions, each scored 0-10:

   **Grammar (0-10):**
   - Spelling errors
   - Subject-verb agreement
   - Punctuation issues
   - Sentence fragments or run-on sentences
   - 10 = flawless, 0 = pervasive errors

   **Readability (0-10):**
   - Clarity and flow
   - Active vs passive voice balance
   - Sentence length variance
   - Paragraph structure and coherence
   - Appropriate heading hierarchy
   - 10 = effortlessly clear, 0 = impenetrable

   **Structure (0-10):**
   - Proper markdown syntax (headings, lists, links, code blocks)
   - Internal link validity
   - Code block formatting and language tags
   - Heading order (H1 → H2 → H3, no skips)
   - Consistent formatting conventions
   - 10 = perfectly structured, 0 = broken formatting

3. Write the results as a JSON file to $PROJECT_PATH/.factory/workspace/doc-scorer/scores.json with this EXACT schema:
```json
{
  "files": [
    {
      "path": "relative/path/to/file.md",
      "grammar": 8.5,
      "readability": 7.0,
      "structure": 9.0,
      "issues": ["Issue description 1", "Issue description 2"]
    }
  ]
}
```

IMPORTANT RULES:
- Score EVERY file in the list — do not skip any
- Each score MUST be a number between 0 and 10 (decimals allowed)
- The 'issues' array should list specific problems found (empty if none)
- Use paths relative to the project root in the output
- The output MUST be valid JSON
- If a file cannot be read (binary, empty, etc.), score it 0/0/0 and note the reason in issues

Read: .factory/workspace/doc-scorer/files.txt
Write output to: .factory/workspace/doc-scorer/scores.json" --project "$PROJECT_PATH" --timeout 1200
```

```bash
# Artifact verification: score_files
_vfail=0
_f="$PROJECT_PATH/.factory/workspace/doc-scorer/scores.json"
[ ! -f "$_f" ] && echo "VERIFY FAIL: score_files: .factory/workspace/doc-scorer/scores.json missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: score_files: .factory/workspace/doc-scorer/scores.json is empty" && _vfail=1
[ -f "$_f" ] && [ "$(wc -c < "$_f")" -lt 20 ] && echo "VERIFY FAIL: score_files: .factory/workspace/doc-scorer/scores.json smaller than 20 bytes" && _vfail=1
[ -f "$_f" ] && ! grep -qE '"files"|"grammar"|"readability"|"structure"' "$_f" && echo "VERIFY FAIL: score_files: .factory/workspace/doc-scorer/scores.json missing required sentinel ("files", "grammar", "readability", "structure")" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=score_files" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: score_files artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=score_files" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

## Step: Aggregate Report

Aggregate individual file scores from scores.json into summary statistics and two output reports: a markdown summary with per-file table sorted by composite score, and a machine-readable JSON file with full statistics. Files with composite score < 7.0 are flagged in a 'Needs Review' section.

```bash
python3 -c "import json, sys, statistics;from pathlib import Path;project = '$PROJECT_PATH';scores_path = Path(project) / '.factory/workspace/doc-scorer/scores.json';report_md_path = Path(project) / '.factory/reports/doc-quality-report.md';report_json_path = Path(project) / '.factory/reports/doc-quality-report.json';data = json.loads(scores_path.read_text());files = data.get('files', []);if not files:    report_md_path.write_text('# Documentation Quality Report\n\nNo files scored.\n');    report_json_path.write_text(json.dumps({'files_scored': 0}, indent=2));    sys.exit(0);for f in files:    f['composite'] = round((f.get('grammar',0) + f.get('readability',0) + f.get('structure',0)) / 3, 2);g_scores = [f['grammar'] for f in files];r_scores = [f['readability'] for f in files];s_scores = [f['structure'] for f in files];c_scores = [f['composite'] for f in files];def stats(vals):    return {'mean': round(statistics.mean(vals), 2), 'median': round(statistics.median(vals), 2), 'min': round(min(vals), 2), 'max': round(max(vals), 2)};summary = {    'files_scored': len(files),    'statistics': {        'grammar': stats(g_scores),        'readability': stats(r_scores),        'structure': stats(s_scores),        'composite': stats(c_scores)    },    'files': sorted(files, key=lambda x: x['composite'])};report_json_path.write_text(json.dumps(summary, indent=2));md_lines = [    '# Documentation Quality Report\n',    f'**Files Scored**: {len(files)}  ',    f'**Avg Grammar**: {stats(g_scores)["mean"]}/10  ',    f'**Avg Readability**: {stats(r_scores)["mean"]}/10  ',    f'**Avg Structure**: {stats(s_scores)["mean"]}/10  ',    f'**Avg Composite**: {stats(c_scores)["mean"]}/10\n',    '---\n',    '## Per-File Scores\n',    '| File | Grammar | Readability | Structure | Composite | Issues |',    '|------|---------|-------------|-----------|-----------|--------|',];for f in sorted(files, key=lambda x: x['composite']):    issues_str = '; '.join(f.get('issues', [])) if f.get('issues') else 'None';    md_lines.append(        f'| {f["path"]} | {f["grammar"]} | {f["readability"]} | {f["structure"]} | {f["composite"]} | {issues_str} |'    );md_lines.append('\n---\n');needs_review = [f for f in files if f['composite'] < 7.0];if needs_review:    md_lines.append(f'## Needs Review ({len(needs_review)} files)\n');    for f in needs_review:        md_lines.append(f'- **{f["path"]}** (composite: {f["composite"]}): {"; ".join(f.get("issues", ["No specific issues noted"]))}');    md_lines.append('');report_md_path.write_text('\n'.join(md_lines));print(f'Report written: {len(files)} files scored')"
```

### Steering Point — Review Gate (User Approval)

**This is a USER approval gate, NOT a CEO review gate. Do NOT self-approve.**

Present the strategy/findings to the user by summarizing key points in your output.
Then explicitly ask the user: "Do you approve this plan, or do you have feedback?"

**You MUST wait for the user's response before proceeding.**
- The user says "approve", "yes", "looks good", or similar → proceed to next step
- The user provides feedback or corrections → re-run the previous step incorporating their feedback
- Do NOT write a verdict file and auto-proceed — this gate requires human input
