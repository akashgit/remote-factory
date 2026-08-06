# Track A of issue #915: human-authored BDD spec for the QA agent.
# Section 3: Adversarial QA — testing the feature as a skeptical user.

Feature: QA Adversarial Testing
  The final step of the QA agent. It switches identity to a
  skeptical user who does NOT trust the Builder and tests the
  feature by actually running the project. No re-running
  pytest/lint — that was the health check's job. This is about
  "does the thing actually work when I use it?"

  The output is feedback for the next build-QA iteration, not
  a verdict. The CEO decides keep/revert — the adversarial
  step just reports what works and what doesn't.

  Background:
    Given the health check has passed
    And code review found no critical issues
    And the QA agent has acceptance criteria (from the GitHub issue or the CEO agent)

  @cli
  Scenario: CLI happy path works
    Given the project is a CLI tool
    And the hypothesis added a new "--format json" flag
    When the QA agent runs the CLI with "--format json"
    Then the command should exit 0
    And the output should be valid JSON
    And the feedback should report the JSON output criterion as VERIFIED

  @cli
  Scenario: CLI gives helpful error on bad input
    Given the project is a CLI tool
    And the hypothesis added a new "--port" flag expecting a number
    When the QA agent runs the CLI with "--port abc"
    Then the command should exit non-zero
    And the output should contain a human-readable error message
    But the command should NOT crash with a traceback
    # A user-facing error message is fine; a raw Python traceback is not

  @cli
  Scenario: CLI handles missing required arguments
    Given the project is a CLI tool
    And the new feature requires a "--config" argument
    When the QA agent runs the CLI without "--config"
    Then the command should show usage help or a clear error
    And the command should NOT silently do nothing

  @server
  Scenario: API endpoint returns correct response
    Given the project is an API server
    And the hypothesis added a new "/api/stats" endpoint
    When the QA agent starts the server
    And sends a GET request to "/api/stats"
    Then the response should be HTTP 200
    And the response body should match the expected schema
    And the server process should be killed after testing
    # Always clean up — orphaned server processes break the next run

  @server
  Scenario: API endpoint handles bad request gracefully
    Given the project is an API server
    And the hypothesis added a new POST endpoint
    When the QA agent sends a POST with invalid JSON
    Then the response should be HTTP 400 or 422
    But the server should NOT crash
    And the server process should be killed after testing

  @tui
  Scenario: TUI launches and responds to input
    Given the project is an interactive TUI
    When the QA agent launches it in a tmux session
    And captures the initial screen
    Then the TUI should render without errors
    When the QA agent sends navigation keystrokes
    And captures the screen after each keystroke
    Then the screen should update in response
    And the tmux session should be cleaned up
    # tmux is mandatory for TUI testing — there's no other way
    # to interact with a curses/textual app non-interactively

  @library
  Scenario: Library can be imported and called
    Given the project is a Python library
    And the hypothesis added a new "parse" function
    When the QA agent runs "python -c" to import and call the function
    Then the function should return the expected result
    And no import errors should occur

  Scenario: Smoke test fails
    Given the project has a smoke test defined in factory.md
    When the QA agent runs the smoke test
    And the smoke test fails
    Then the feedback should flag the smoke test failure
    And the QA agent should NOT continue with feature testing
    # If the smoke test fails, nothing else matters —
    # report it and let the Builder fix the basics first

  Scenario: All acceptance criteria verified with evidence
    Given there are 3 acceptance criteria
    When the QA agent tests each criterion
    And each test produces command output as evidence
    And all criteria are met
    Then the feedback should report all criteria as VERIFIED
    # Every criterion needs evidence: command + output.
    # A test without evidence is NOT_VERIFIED.

  Scenario: Feature mostly works but one criterion fails
    Given there are 3 acceptance criteria
    When the QA agent tests each criterion
    And criteria 1 and 2 pass
    But criterion 3 is not verified
    Then the feedback should report criterion 3 as NOT_VERIFIED with details
    And the feedback should describe what went wrong so the Builder can fix it
    # The QA agent's job is to give the Builder actionable information,
    # not to make the keep/revert call

  Scenario: Builder claimed a blocker that is not real
    Given the Builder noted "cannot test — requires external API key"
    When the QA agent checks whether the API key is actually needed
    And the feature can be tested with a mock or local fallback
    Then the blocker should be flagged as invalid in the feedback
    And the feature should be tested anyway
    # Don't take the Builder's word for it — verify claimed limitations

  Scenario: Builder claimed a blocker that is real
    Given the Builder noted "cannot test — requires paid third-party service"
    When the QA agent checks whether the service is actually needed
    And there is no way to test without the external dependency
    Then the feedback should accept the blocker with justification
    And the criterion should be marked SKIPPED
    # Real blockers are fine — just verify they're real
