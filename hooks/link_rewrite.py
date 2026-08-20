"""MkDocs hook: rewrite docs/-prefixed links for index.md.

README.md is a symlink to docs/index.md. Links in that file use
docs/foo.md so GitHub resolves them correctly from the repo root.
This hook strips the docs/ prefix at build time so MkDocs resolves
them relative to the docs directory.
"""

import re


def on_page_markdown(markdown: str, page, config, files, **kwargs) -> str:
    if page.file.src_path != "index.md":
        return markdown

    # Inline links: [text](docs/foo.md) or [text](docs/foo.md#anchor)
    markdown = re.sub(
        r'\]\(docs/([^)]+)\)',
        r'](\1)',
        markdown,
    )

    # Reference-style links: [text]: docs/foo.md
    markdown = re.sub(
        r'^(\[[^\]]+\]:\s+)docs/(.+)$',
        r'\1\2',
        markdown,
        flags=re.MULTILINE,
    )

    return markdown
