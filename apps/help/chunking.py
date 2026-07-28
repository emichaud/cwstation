"""Split help-doc markdown into heading-scoped passages for lexical RAG.

Pure-stdlib chunking — no numpy, no embedding model, no optional deps. This
is the *lexical* (dependency-free) half of RAG: it produces one passage per
markdown section, tagged with the heading trail it came from, so a tool or
LLM gets a focused, citable answer instead of a whole document.

The *semantic* half (embeddings + vector search) is deliberately absent here.
A future Embedder can consume these same passages without touching this file,
because everything downstream binds to the passage dict / SearchHit shape,
not to how the passage was retrieved.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

# ATX headings only (#, ##, ###). Setext headings are not used in help docs.
_HEADING = re.compile(r"^(#{1,3})\s+(.*)$", re.MULTILINE)


def chunk_markdown(
    text: str,
    *,
    source: str,
    max_chars: int = 1800,
    overlap: int = 200,
) -> Iterator[dict]:
    """Yield ``{source, heading_path, text}`` passages from one markdown string.

    Splits on ATX headings so each passage keeps the context of the section it
    came from, then windows any section longer than ``max_chars`` with a small
    ``overlap`` so no passage is cut mid-thought. ``heading_path`` is the trail
    of enclosing headings joined with ``›`` (e.g. ``"Theme › Anti-patterns"``),
    which becomes the citation shown to the caller.
    """
    text = text or ""
    matches = list(_HEADING.finditer(text))

    if not matches:
        # No headings — window the whole document under an empty trail.
        yield from _window(text.strip(), source, "", max_chars, overlap)
        return

    # Any body before the first heading (intro paragraph, etc.).
    preamble = text[: matches[0].start()].strip()
    if preamble:
        yield from _window(preamble, source, "", max_chars, overlap)

    stack: list[tuple[int, str]] = []  # breadcrumb of (level, title)
    for i, m in enumerate(matches):
        level, title = len(m.group(1)), m.group(2).strip()
        # Pop headings at this level or deeper, then push the current one.
        stack = [(lv, t) for (lv, t) in stack if lv < level]
        stack.append((level, title))
        heading_path = " › ".join(t for _, t in stack)

        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            yield from _window(body, source, heading_path, max_chars, overlap)


def _window(
    body: str, source: str, heading_path: str, max_chars: int, overlap: int
) -> Iterator[dict]:
    """Emit ``body`` as one passage, or several overlapping windows if long."""
    if not body:
        return
    if len(body) <= max_chars:
        yield {"source": source, "heading_path": heading_path, "text": body}
        return
    step = max(1, max_chars - overlap)
    for pos in range(0, len(body), step):
        window = body[pos : pos + max_chars]
        if window.strip():
            yield {"source": source, "heading_path": heading_path, "text": window}


def iter_help_markdown() -> Iterator[dict]:
    """Discover every help page and yield its RAW markdown for chunking.

    Reuses the existing help discovery (``get_all_sections``) but reads the raw
    ``.md`` files directly rather than ``build_search_index()`` — the latter
    strips HTML and truncates to 2000 chars, which would erase the ``#``/``##``
    headings the chunker splits on. Frontmatter is stripped and ``{{ vars }}``
    substituted so the indexed text matches what a reader actually sees.

    Yields ``{source, section, title, text}`` where ``text`` is intact markdown.
    """
    from apps.help.utils import (
        _get_section_dir,
        get_all_sections,
        substitute_variables,
    )

    for section in get_all_sections():
        section_slug = section.get("slug", "")
        section_dir = _get_section_dir(section_slug)
        if section_dir is None:
            continue
        for page in section.get("pages", []):
            slug = page.get("slug")
            if not slug:
                continue
            file_path = section_dir / f"{slug}.md"
            if not file_path.exists():
                continue

            raw = file_path.read_text(encoding="utf-8")

            # Strip YAML frontmatter (mirrors get_help_page).
            content = raw
            if raw.startswith("---"):
                parts = raw.split("---", 2)
                if len(parts) >= 3:
                    content = parts[2].strip()

            content = substitute_variables(content, section_slug)
            yield {
                "source": slug,
                "section": section_slug,
                "title": page.get("title", slug),
                "text": content,
            }
