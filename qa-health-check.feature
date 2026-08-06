# Track A of issue #915: human-authored BDD spec for the QA agent.
# Section 1: Health Check — the mechanical eval-and-compare phase.

Feature: QA Health Check
  The first step of QA agent and it always runs. It runs the
  project eval, compares scores against the baseline, and gates
  on whether to proceed or revert if and only if key evals fail.
  It should not stop/revert if eval score is simply static.

  Background:
    Given the QA agent has a link to a PR
    And this is the first stage of the QA process

  Scenario: Unit tests break but composite score improves
    Given a codebase from the PR
    And it has existing unit tests
    And the baseline composite score is 0.70
    When the QA agent runs factory eval
    And the eval returns a composite score of 0.82
    But unit tests are failing
    Then the health check should report FAIL
    # Unit test failure overrides composite score improvement —
    # passing tests is a prerequisite, not a dimension to trade off

  Scenario: Eval score regresses below baseline
    Given a codebase from the PR
    And the baseline composite score is 0.85
    And the configured threshold is 0.75
    When the QA agent runs factory eval
    And the eval returns a composite score of 0.60
    And unit tests are passing
    Then the health check should report FAIL
    # Score dropped significantly — the Builder's changes made things worse

  Scenario: Eval fails completely (no valid score)
    Given a codebase from the PR
    And the baseline composite score is 0.80
    When the QA agent runs factory eval
    And the eval command crashes or returns no valid JSON
    Then the health check should report REVERT
    And the QA agent should NOT proceed to code review
    # If we can't even run eval, the changes broke something fundamental

  Scenario: Eval score dips slightly within noise range
    Given a codebase from the PR
    And the baseline composite score is 0.85
    When the QA agent runs factory eval
    And the eval returns a composite score of 0.83
    And unit tests are passing
    Then the health check should report PASS
    # Small regressions can be eval variance, not real damage —
    # health check should not block on noise
