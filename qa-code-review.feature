# Track A of issue #915: human-authored BDD spec for the QA agent.
# Section 2: Code Review — reading the PR diff and evaluating against a checklist.
# See SYNTAX.md for Gherkin reference.

Feature: QA Code Review
  The second step of the QA agent. It reads every changed file
  in the PR diff and evaluates quality. The key judgment: only
  critical issues block the PR — minor style nits and small
  imperfections should not stop progress.

  Background:
    Given the health check has passed
    And the QA agent has the hypothesis and acceptance criteria (from the GitHub issue or the CEO agent)

  # --- Hard constraint: output structure ---
  # The code review MUST evaluate and report on ALL 7 categories.
  # See qa-code-review-checklist.md for full definitions.
  #   1. Correctness
  #   2. Security
  #   3. Edge cases
  #   4. Missing tests
  #   5. Style & consistency
  #   6. Scope compliance
  #   7. Guardrail compliance
  # No category may be skipped. Each must report PASS or FAIL with evidence.

  Scenario: Clean PR with no issues
    Given the diff contains 3 changed files
    And all changes match the hypothesis scope
    And all acceptance criteria from the GitHub issue are implemented
    When the QA agent reviews the diff
    Then all checklist categories should PASS
    And spec fidelity should be "3/3 criteria met"
    And the code review should report zero issues

  Scenario: PR introduces a critical bug
    Given the diff adds a function that accesses a variable before checking for None
    And this will cause a runtime crash on the happy path
    When the QA agent reviews the diff
    Then the correctness category should FAIL
    And the issue should be severity "critical"
    And the QA agent should NOT proceed to adversarial testing
    # Critical bugs are a hard stop — no point testing a feature that will crash

  Scenario: PR has minor style issues but works correctly
    Given the diff has inconsistent naming in one file
    And a small block of duplicated code
    But the logic is correct and all acceptance criteria are met
    When the QA agent reviews the diff
    Then the style category should FAIL
    But the overall code review should report ISSUES_FOUND
    And the QA agent should proceed to adversarial testing
    # Style nits don't block — the Builder can clean up later

  Scenario: PR has scope creep
    Given the hypothesis asked for "add --format flag to the CLI"
    But the diff also refactors the logging module
    And the logging changes are unrelated to the hypothesis
    When the QA agent reviews the diff
    Then the scope compliance category should FAIL
    And the issue should be severity "important"
    # Scope creep isn't critical but should be flagged —
    # unrelated changes are risk without reward

  Scenario: Builder stubbed out a deliverable
    Given the hypothesis requires implementing a cache layer
    And the diff contains a CacheManager class
    But the class methods are all "pass" or "raise NotImplementedError"
    When the QA agent checks plan completion
    Then the deliverable should be flagged as "stubbed"
    And plan completion should report unsatisfied items
    # A stub is not an implementation — don't give credit for empty shells

  Scenario: Acceptance criteria partially met
    Given the GitHub issue has 4 acceptance criteria
    And the diff implements 3 of them
    But the 4th criterion is missing with no justification
    When the QA agent reviews the diff
    Then spec fidelity should be "3/4 criteria met"
    And scope shrinkage should be flagged
    # Missing criteria without a valid reason (needs API keys, needs
    # human decision) is unjustified scope shrinkage

  Scenario: PR modifies a fixed surface in research mode
    Given the project is in research mode
    And fixed_surfaces includes "eval/score.py"
    And the diff modifies eval/score.py
    When the QA agent reviews the diff
    Then the guardrail compliance category should FAIL
    And the issue should be severity "critical"
    And the QA agent should NOT proceed to adversarial testing
    # Fixed surfaces are off-limits — modifying them invalidates the experiment

  Scenario: New code paths added without tests
    Given the diff adds a new module with 3 public functions
    But no test file is added or modified
    When the QA agent reviews the diff
    Then the missing tests category should FAIL
    And the issue should be severity "important"
    But the QA agent should still proceed to adversarial testing
    # Missing tests is bad practice but not a blocker —
    # the adversarial step will catch if the code actually works
