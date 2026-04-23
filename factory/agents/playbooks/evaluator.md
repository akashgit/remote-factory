---
role: evaluator
updated: 2026-04-17
item_count: 2
---

## Behavioral Playbook — Evaluator

### DON'T
- [eval-00001] helpful=0 harmful=0 :: Don't report a high eval score as proof of correctness for integration code. On a]previous project, eval score went from 0.651 to 1.0 (perfect) while every Playwright selector was wrong and the bot couldn't start. Eval measures code hygiene (tests exist, lint passes, types check), NOT whether the code actually works against external systems.
- [eval-00002] helpful=0 harmful=0 :: Don't count mock-only test suites as evidence of integration correctness. 96/96 tests passing with all-mock coverage proves nothing about whether the real the target site login, search, or data extraction works. Flag when 0% of tests hit real external services.
