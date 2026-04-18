---
role: builder
updated: 2026-04-17
item_count: 4
---

## Behavioral Playbook — Builder

### DO
- [bldr-00001] helpful=0 harmful=0 :: When writing browser automation (Playwright, Selenium, Puppeteer), add a comment flagging that selectors are UNVERIFIED and need manual E2E testing against the real site

### DON'T
- [bldr-00002] helpful=0 harmful=32 :: Don't write CSS selectors by guessing element IDs/names (e.g. `#user_name`, `#btnSearch`, `input[type="submit"]`). On erica-agent, every single guessed selector was wrong — the real site used role-based elements like `get_by_role("textbox", name="Enter Your Agent ID")`. 100% failure rate on guessed selectors.
- [bldr-00003] helpful=0 harmful=32 :: Don't hardcode hostnames for load-balanced sites. On erica-agent, Pinergy load-balances across h3d/h3h/h3z/h3e/h3v/etc subdomains. Hardcoding `h3d.mlspin.com` caused immediate failure. Capture the host dynamically after redirect.
- [bldr-00004] helpful=0 harmful=16 :: Don't use `page.wait_for_load_state("networkidle")` after iframe operations — iframes with persistent connections prevent networkidle from ever resolving, causing 30s timeouts. Use frame-level waits or `domcontentloaded` instead.
