# QA Code Review — 7-Category Checklist

Every code review MUST evaluate the PR diff against all 7 categories and report PASS or FAIL with evidence for each. This is a hard constraint on the output — no category may be skipped.

## 1. Correctness

Does the code do what it's supposed to do?

- Bugs, logic errors, off-by-one mistakes
- Null/undefined access, wrong return values
- Race conditions in async code
- Misuse of APIs or libraries

## 2. Security

Does the code introduce vulnerabilities?

- Injection: SQL, XSS, command injection
- Hardcoded secrets, API keys, passwords
- Unsafe deserialization
- Path traversal (user input used in file paths)

## 3. Edge Cases

Does the code handle unusual inputs gracefully?

- Empty or null inputs
- Boundary values (0, -1, MAX_INT)
- Error paths and exception handling
- Timeouts and retries

## 4. Missing Tests

Is new code covered by tests?

- New code paths without any test coverage
- Untested error branches
- New public functions/methods without corresponding tests

## 5. Style & Consistency

Does the code follow the project's conventions?

- Naming conventions (snake_case, camelCase, etc.)
- Code duplication — same logic in multiple places
- Dead code (unused imports, unreachable branches)
- Import organization

## 6. Scope Compliance

Does the PR implement what was asked — no more, no less?

- PR matches the hypothesis scope
- No unrelated changes (scope creep)
- No scope shrinkage without justification
- Acceptance criteria from the GitHub issue are all addressed

## 7. Guardrail Compliance

Does the PR respect the project's structural constraints?

- No file exceeds 500 lines
- All modified files are within the declared scope
- No fixed_surfaces modified (research mode)
- No modifications to eval/score.py or .factory/ contents
