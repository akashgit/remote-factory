"""MkDocs hook: rewrite docs/-prefixed links for index.md.

README.md body content is included into docs/index.md via pymdownx.snippets.
Links in that content use docs/foo.md so GitHub resolves them from the repo
root. This hook expands the snippet include and strips the docs/ prefix BEFORE
MkDocs markdown processing, so link validation sees the corrected paths.

Uses on_page_markdown (pre-render) and manually expands the snippet to run
ahead of both pymdownx.snippets and MkDocs link validation.
"""

import re
from pathlib import Path


def on_page_markdown(markdown: str, page, config, files, **kwargs) -> str:
    if page.file.src_path != "index.md":
        return markdown

    readme = Path(config["docs_dir"]).parent / "README.md"
    if not readme.exists():
        return markdown

    content = readme.read_text()
    start_marker = "<!-- --8<-- [start:body] -->"
    end_marker = "<!-- --8<-- [end:body] -->"
    start = content.find(start_marker)
    end = content.find(end_marker)
    if start == -1 or end == -1:
        return markdown

    body = content[start + len(start_marker) : end].strip()
    body = re.sub(r"\]\(docs/([^)]+)\)", r"](\1)", body)

    markdown = markdown.replace('--8<-- "README.md:body"', body)
    return markdown
