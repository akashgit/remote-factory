"""MkDocs hook: rewrite docs/-prefixed links for index.md.

README.md body content is included into docs/index.md via pymdownx.snippets.
Links in that content use docs/foo.md so GitHub resolves them from the repo
root. This hook strips the docs/ prefix in the rendered HTML so MkDocs
resolves them relative to the docs directory.

Uses on_page_content (post-render) because pymdownx.snippets expands the
include during markdown processing, after on_page_markdown has already fired.
"""

import re


def on_page_content(html: str, page, config, files, **kwargs) -> str:
    if page.file.src_path != "index.md":
        return html

    # Rewrite href="docs/foo.md" → href="foo/" (and with anchors)
    def _rewrite(m):
        path = m.group(1)
        # docs/foo.md#anchor → foo/#anchor
        path = re.sub(r'\.md(#|$)', r'/\1', path)
        return f'href="{path}"'

    html = re.sub(r'href="docs/([^"]+)"', _rewrite, html)

    return html
