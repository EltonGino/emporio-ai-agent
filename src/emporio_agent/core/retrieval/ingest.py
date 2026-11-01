"""PDF → normalized text → structure-aware chunks (AGENTS.md §6). Client-agnostic.

pypdf's default extraction returns this kind of PDF one word per line, so the layout mode is
used instead: it keeps line breaks and indentation, which is enough to tell headings
(column 0), bullets and list items (indented), table rows (cells separated by wide gaps) and
wrapped lines apart. Running headers/footers are lines repeated on most pages.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pypdf import PdfReader

BULLET = re.compile(r"^(?:[•▪◦‣\-*]|(\d+)[.)])\s+")
CELL_GAP = re.compile(r"\s{3,}")
SENTENCE_END = (".", "!", "?")  # not ":" — "(exemplo:" continues on the next line
EDGE_LINES = 1  # first/last line of a page: the only places for running headers/footers

FALLBACK_CHUNK_CHARS = 800
FALLBACK_OVERLAP_CHARS = 150


@dataclass(frozen=True)
class Chunk:
    doc: str
    section_id: str
    title: str
    section_path: str  # e.g. "4. Política de Trocas e Devoluções > 4.2 Trocas por Defeito"
    page: int
    text: str

    @property
    def index_text(self) -> str:
        """Text used for indexing: the section path gives every chunk its context."""
        return f"{self.section_path}\n{self.text}"


@dataclass
class Line:
    page: int
    indent: int
    text: str


@dataclass
class Block:
    kind: str  # heading | paragraph | bullet | table
    page: int
    text: str = ""
    cells: list[str] = field(default_factory=list)


# --- Extraction and cleaning ---------------------------------------------------------------


def extract_pages(path: Path) -> list[str]:
    return [page.extract_text(extraction_mode="layout") for page in PdfReader(path).pages]


def _noise_key(text: str) -> str:
    return re.sub(r"\d+", "#", text)


def clean_lines(pages: list[str]) -> list[Line]:
    """Non-blank lines with page numbers, minus running headers/footers.

    Only the first and last EDGE_LINES lines of a page can be headers/footers. One is noise
    when it (digits ignored, so "Página 3" == "Página 4") sits at a page edge on at least half
    of the pages. Body lines are never dropped.
    """
    per_page = [
        [
            Line(page=number, indent=len(line) - len(line.lstrip()), text=line.strip())
            for line in page.splitlines()
            if line.strip()
        ]
        for number, page in enumerate(pages, start=1)
    ]
    edges = [lines[:EDGE_LINES] + lines[-EDGE_LINES:] for lines in per_page]
    pages_with = Counter(key for page in edges for key in {_noise_key(e.text) for e in page})
    threshold = max(2, len(pages) / 2)
    return [
        line
        for lines, edge in zip(per_page, edges, strict=True)
        for line in lines
        if not (any(line is e for e in edge) and pages_with[_noise_key(line.text)] >= threshold)
    ]


# --- Block reconstruction ------------------------------------------------------------------


def build_blocks(lines: list[Line], heading: re.Pattern[str]) -> list[Block]:
    blocks: list[Block] = []
    for line in lines:
        previous = blocks[-1] if blocks else None
        if line.indent == 0 and heading.match(line.text):
            blocks.append(Block("heading", line.page, line.text))
        elif (bullet := BULLET.match(line.text)) and line.indent > 0:
            number = f"{bullet.group(1)}. " if bullet.group(1) else ""  # keep list numbering
            blocks.append(Block("bullet", line.page, number + line.text[bullet.end() :]))
        elif CELL_GAP.search(line.text):
            blocks.append(Block("table", line.page, cells=CELL_GAP.split(line.text)))
        elif line.indent > 0 and previous and previous.kind == "bullet":
            previous.text += " " + line.text  # wrapped bullet
        elif line.indent > 0 and previous and previous.kind == "table":
            previous.cells[-1] += " " + line.text  # wrapped last cell
        elif previous and previous.kind == "paragraph" and not previous.text.endswith(SENTENCE_END):
            previous.text += " " + line.text  # wrapped paragraph
        else:
            blocks.append(Block("paragraph", line.page, line.text))
    return blocks


def render_blocks(blocks: list[Block]) -> str:
    """Body text: paragraphs as lines, bullets and table rows as '- ' items.

    The first row of a table is its header; following rows become 'Header: value; ...' so
    each row is self-contained for retrieval and for the LLM.
    """
    out: list[str] = []
    header: list[str] | None = None
    for block in blocks:
        if block.kind != "table":
            header = None
        if block.kind == "paragraph":
            out.append(block.text)
        elif block.kind == "bullet":
            out.append(f"- {block.text}")
        elif block.kind == "table":
            if header is None:
                header = block.cells
            elif len(header) == len(block.cells):
                out.append(
                    "- " + "; ".join(f"{h}: {c}" for h, c in zip(header, block.cells, strict=True))
                )
            else:
                out.append("- " + " | ".join(block.cells))
    return "\n".join(out)


# --- Chunking ------------------------------------------------------------------------------


def chunk_by_headings(blocks: list[Block], doc: str, heading: re.Pattern[str]) -> list[Chunk]:
    """One chunk per (sub)section. Text before a section's first subsection is the section's
    own chunk; sections with no text of their own produce no chunk. Text before the first
    heading (e.g. a cover page) becomes section "0", titled by its first line.
    """
    chunks: list[Chunk] = []
    path: list[tuple[str, str]] = []  # (section_id, heading line)
    current: tuple[str, str, int] = ("0", "", blocks[0].page if blocks else 1)
    body: list[Block] = []

    def flush() -> None:
        if not body:
            return
        section_id, title, page = current
        if section_id == "0":
            title = body[0].text or " ".join(body[0].cells)
            section_path = title
        else:
            section_path = " > ".join(line for _, line in path)
        chunks.append(Chunk(doc, section_id, title, section_path, page, render_blocks(body)))

    for block in blocks:
        if block.kind != "heading":
            body.append(block)
            continue
        flush()
        body = []
        match = heading.match(block.text)
        section_id, title = match.group(1), match.group(2).strip()
        depth = section_id.count(".")
        path = path[:depth] + [(section_id, block.text)]
        current = (section_id, title, block.page)
    flush()
    return chunks


def chunk_fixed_size(
    text: str,
    doc: str,
    size: int = FALLBACK_CHUNK_CHARS,
    overlap: int = FALLBACK_OVERLAP_CHARS,
) -> list[Chunk]:
    """Fallback for documents without recognizable headings: ~size-char windows that
    overlap by ~overlap chars, cut at whitespace."""
    words = text.split()
    chunks: list[Chunk] = []
    start = 0
    while start < len(words):
        end, length = start, 0
        while end < len(words) and length + len(words[end]) + 1 <= size:
            length += len(words[end]) + 1
            end += 1
        end = max(end, start + 1)
        number = len(chunks) + 1
        title = f"{doc} (parte {number})"
        chunks.append(Chunk(doc, f"p{number}", title, title, 1, " ".join(words[start:end])))
        if end >= len(words):
            break
        back, kept = end, 0
        while back > start + 1 and kept + len(words[back - 1]) + 1 <= overlap:
            back -= 1
            kept += len(words[back]) + 1
        start = back
    return chunks


def write_chunks(chunks: list[Chunk], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(chunk) for chunk in chunks]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def read_chunks(path: Path) -> list[Chunk]:
    return [Chunk(**item) for item in json.loads(path.read_text(encoding="utf-8"))]


def chunk_document(path: Path, heading_pattern: re.Pattern[str] | str) -> list[Chunk]:
    heading = re.compile(heading_pattern) if isinstance(heading_pattern, str) else heading_pattern
    blocks = build_blocks(clean_lines(extract_pages(path)), heading)
    if any(block.kind == "heading" for block in blocks):
        return chunk_by_headings(blocks, path.name, heading)
    return chunk_fixed_size(render_blocks(blocks), path.name)
