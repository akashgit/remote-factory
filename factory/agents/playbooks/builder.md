---
role: builder
updated: 2026-04-25
item_count: 2
---

## Behavioral Playbook — Builder

### DO
- [bldr-00001] helpful=0 harmful=0 :: When writing browser automation (Playwright, Selenium, Puppeteer), add a comment flagging that selectors are UNVERIFIED and need manual E2E testing against the real site

### DON'T
- [bldr-00002] helpful=0 harmful=0 :: Don't use `page.wait_for_load_state("networkidle")` after iframe operations — iframes with persistent connections prevent networkidle from ever resolving, causing 30s timeouts. Use frame-level waits or `domcontentloaded` instead.
