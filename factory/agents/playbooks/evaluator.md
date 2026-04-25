---
role: evaluator
updated: 2026-04-25
item_count: 2
---

## Behavioral Playbook — Evaluator

### DON'T
- [eval-00001] helpful=0 harmful=0 :: Don't report a high eval score as proof of correctness for integration code. Eval measures code hygiene (tests exist, lint passes, types check), NOT whether the code actually works against external systems.
- [eval-00002] helpful=0 harmful=0 :: Don't count mock-only test suites as evidence of integration correctness. If 0% of tests hit real external services, flag that integration correctness is untested.
