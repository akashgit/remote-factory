# Gherkin Syntax Cheat Sheet

## Structure

```
Feature: <name>                  # one per file, describes a capability
  <free text description>        # optional, for humans

  Background:                    # optional, runs before EVERY scenario
    Given <shared setup step>

  Scenario: <name>               # one concrete example of behavior
    Given <starting state>
    When <action happens>
    Then <expected outcome>

  Scenario Outline: <name>       # same scenario, multiple data rows
    Given <step with "<variable>">
    When <step with "<variable>">
    Then <step with "<variable>">

    Examples:
      | variable1 | variable2 |
      | value_a   | value_b   |
      | value_c   | value_d   |
```

## Keywords

| Keyword            | Purpose                                           |
|--------------------|---------------------------------------------------|
| `Feature:`         | Top-level grouping, one per file                  |
| `Scenario:`        | A single concrete example                         |
| `Given`            | The starting state / preconditions                |
| `When`             | The action or event that happens                  |
| `Then`             | The expected outcome / what you check             |
| `And`              | Continues the previous Given/When/Then            |
| `But`              | Like And, but reads better for negatives          |
| `Background:`      | Shared setup before every scenario                |
| `Scenario Outline:`| Parameterized scenario (used with Examples table) |
| `Examples:`        | Data table for Scenario Outline                   |
| `@tag`             | Label on a Feature or Scenario for filtering      |
| `#`                | Comment                                           |

## Tips

- **Given** = what's true before anything happens (setup)
- **When** = the single action being tested (trigger)
- **Then** = what should be true after (assertion)
- **And/But** = continuation; inherits the type of the line above
- Keep scenarios **declarative** ("the user logs in") not imperative ("clicks field, types email...")
- One behavior per scenario
- Free text after Feature: is for humans — tools ignore it

## Example

```gherkin
Feature: Shopping cart checkout
  Users should be able to check out items in their cart.

  Background:
    Given the store has items in stock

  Scenario: Successful checkout with valid payment
    Given a user with 3 items in their cart
    And a valid credit card on file
    When they click checkout
    Then the order should be confirmed
    And the cart should be empty

  Scenario: Checkout fails with expired card
    Given a user with 1 item in their cart
    And an expired credit card on file
    When they click checkout
    Then they should see an error message "Payment declined"
    But the cart should still have 1 item

  @edge-case
  Scenario Outline: Checkout with various cart states
    Given a user with <count> items in their cart
    When they click checkout
    Then they should see "<result>"

    Examples:
      | count | result              |
      | 0     | Cart is empty       |
      | 1     | Order confirmed     |
      | 999   | Order confirmed     |
```
