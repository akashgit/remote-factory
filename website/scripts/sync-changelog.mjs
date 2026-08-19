import { readFileSync, writeFileSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, "..", "..");
const changelog = readFileSync(join(root, "CHANGELOG.md"), "utf-8");

const mdx = `---
title: "Changelog"
full: true
---

${changelog}`;

writeFileSync(
  join(__dirname, "..", "content", "docs", "changelog.mdx"),
  mdx,
);

console.log("Synced CHANGELOG.md → content/docs/changelog.mdx");
