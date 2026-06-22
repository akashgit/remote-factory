---
role: qa
updated: 2026-06-21
item_count: 4
---

## Behavioral Playbook — QA

### DO
- [qa-00001] helpful=0 harmful=0 :: When reviewing browser automation code, explicitly flag that selectors cannot be verified without running against the real site. Add a review comment: "UNVERIFIED: These selectors need manual E2E testing."
- [qa-00002] helpful=0 harmful=0 :: When the project has a .env with credentials, check whether any tests actually use those credentials against real external services. If all tests use mocks, flag that integration correctness is UNTESTED.

### DON'T
- [qa-00003] helpful=0 harmful=0 :: Don't report a high eval score as proof of correctness for integration code. Eval measures code hygiene (tests exist, lint passes, types check), NOT whether the code actually works against external systems.
- [qa-00004] helpful=0 harmful=0 :: Don't count mock-only test suites as evidence of integration correctness. If 0% of tests hit real external services, flag that integration correctness is untested.
