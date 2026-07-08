"""Knowledge-base parsing + chunking.

The 7 KB markdown docs under ``assets/kb`` are the grounding data for triage.
This module parses them into structured records used for BOTH:
  * building the Azure AI Search index (``setup.build_search_index``), and
  * the local mock search client (so triage can be exercised without Azure).

Each KB doc has a "## Recommended Assignment Group" section whose value is the
ServiceNow assignment group used when triage escalates to a ticket.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# assets/kb relative to repo root (this file is src/helpdesk/agents/kb.py).
REPO_ROOT = Path(__file__).resolve().parents[3]
KB_DIR = REPO_ROOT / "assets" / "kb"


@dataclass
class KbDoc:
    """A parsed knowledge-base article."""

    doc_id: str
    title: str
    source: str
    assignment_group: str
    keywords: list[str]
    content: str
    sections: dict[str, str] = field(default_factory=dict)

    @property
    def resolution_steps(self) -> str:
        return self.sections.get("resolution steps", "")


def _split_sections(markdown: str) -> tuple[str, dict[str, str]]:
    """Return (H1 title, {lowercased H2 heading: body})."""
    title = ""
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []

    def flush() -> None:
        if current is not None:
            sections[current] = "\n".join(buf).strip()

    for line in markdown.splitlines():
        h1 = re.match(r"^#\s+(.*)$", line)
        h2 = re.match(r"^##\s+(.*)$", line)
        if h1:
            title = h1.group(1).strip()
            continue
        if h2:
            flush()
            current = h2.group(1).strip().lower()
            buf = []
            continue
        buf.append(line)
    flush()
    return title, sections


def parse_markdown(doc_id: str, source: str, markdown: str) -> KbDoc:
    title, sections = _split_sections(markdown)
    assignment_group = sections.get("recommended assignment group", "").strip()
    # Keep only the first non-empty line of the assignment-group section.
    if assignment_group:
        assignment_group = next(
            (ln.strip() for ln in assignment_group.splitlines() if ln.strip()), ""
        )
    keywords_raw = sections.get("keywords", "")
    keywords = [k.strip() for k in re.split(r"[,\n]", keywords_raw) if k.strip()]
    return KbDoc(
        doc_id=doc_id,
        title=title or doc_id,
        source=source,
        assignment_group=assignment_group,
        keywords=keywords,
        content=markdown.strip(),
        sections=sections,
    )


def load_local_kb(kb_dir: Path | None = None) -> list[KbDoc]:
    """Parse every ``*.md`` under the local KB directory."""
    directory = kb_dir or KB_DIR
    docs: list[KbDoc] = []
    for path in sorted(directory.glob("*.md")):
        docs.append(parse_markdown(path.stem, path.name, path.read_text(encoding="utf-8")))
    return docs


def chunk_doc(doc: KbDoc, max_chars: int = 1200) -> list[str]:
    """Chunk a doc for embedding.

    KB docs are small; we chunk by section, splitting only when a section exceeds
    ``max_chars`` so resolution steps stay together with their heading.
    """
    chunks: list[str] = []
    ordered = [k for k in doc.sections if k != "keywords"]
    for heading in ordered:
        body = doc.sections[heading].strip()
        if not body:
            continue
        block = f"## {heading.title()}\n{body}"
        if len(block) <= max_chars:
            chunks.append(block)
        else:
            for i in range(0, len(body), max_chars):
                chunks.append(f"## {heading.title()}\n{body[i:i + max_chars]}")
    if not chunks:
        chunks.append(doc.content[:max_chars])
    return chunks
