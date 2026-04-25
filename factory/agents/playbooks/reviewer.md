---
role: reviewer
updated: 2026-04-25
item_count: 2
---

## Behavioral Playbook — Reviewer

### DO
- [revw-00001] helpful=0 harmful=0 :: When reviewing browser automation code, explicitly flag that selectors cannot be verified without running against the real site. Add a review comment: "UNVERIFIED: These selectors need manual E2E testing."
- [revw-00002] helpful=0 harmful=0 :: When the project has a .env with credentials, check whether any tests actually use those credentials against real external services. If all tests use mocks, flag that integration correctness is UNTESTED.
