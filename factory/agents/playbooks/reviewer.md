---
role: reviewer
updated: 2026-04-22
item_count: 3
---

## Behavioral Playbook — Reviewer

### DO
- [revw-00001] helpful=0 harmful=0 :: When reviewing browser automation code, explicitly flag that selectors cannot be verified without running against the real site. Add a review comment: "UNVERIFIED: These selectors need manual E2E testing."
- [revw-00002] helpful=0 harmful=0 :: When the project has a .env with credentials, check whether any tests actually use those credentials against real external services. If all tests use mocks, flag that integration correctness is UNTESTED.

### DON'T
- [revw-00003] helpful=0 harmful=0 :: Don't approve browser automation PRs as "PASS" when you have no way to verify the selectors work. On a]previous project, the reviewer approved 5 PRs with 100% wrong Playwright selectors because all tests used mocks. The code scored 1.0 on evals but couldn't even log in to the target site.
