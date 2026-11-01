import re

import pytest

from emporio_agent.config import get_client_config
from emporio_agent.core.retrieval.ingest import (
    Line,
    build_blocks,
    chunk_document,
    chunk_fixed_size,
    clean_lines,
    read_chunks,
    render_blocks,
    write_chunks,
)

HEADING = re.compile(r"^(\d+(?:\.\d+)?)\.?\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ].{2,80})$")

EXPECTED_SECTIONS = [
    "0", "1", "1.1", "1.2", "2", "3", "3.1", "4", "4.1", "4.2", "4.3", "4.4",
    "5.1", "5.2", "5.3", "6.1", "6.2", "7", "7.1", "7.2", "7.3",
    "8.1", "8.2", "8.3", "9", "10",
]  # fmt: skip


@pytest.fixture(scope="module")
def chunks():
    retrieval = get_client_config().retrieval
    return {
        c.section_id: c for c in chunk_document(retrieval.documents[0], retrieval.heading_pattern)
    }


def test_one_chunk_per_subsection(chunks):
    assert list(chunks) == EXPECTED_SECTIONS


def test_section_path_and_index_text(chunks):
    chunk = chunks["4.2"]
    assert chunk.title == "Trocas por Defeito"
    assert chunk.section_path == "4. Política de Trocas e Devoluções > 4.2 Trocas por Defeito"
    assert chunk.index_text.startswith(chunk.section_path + "\n")
    assert chunk.doc == "politicas_da_loja.pdf"


def test_page_is_where_the_section_starts(chunks):
    assert chunks["4.2"].page == 4
    assert chunks["5.2"].page == 4  # heading on page 4, content on page 5
    assert "Jadlog" in chunks["5.2"].text


def test_headers_and_footers_are_removed(chunks):
    for chunk in chunks.values():
        assert "Manual de Políticas e Procedimentos" not in chunk.text
        assert not re.search(r"Página \d", chunk.text)


def test_tables_become_self_contained_rows(chunks):
    assert (
        "- Forma: PIX; Condições: Pagamento à vista; "
        "Observações: 5% de desconto sobre o preço de tabela"
    ) in chunks["3"].text
    assert "- Dia: Domingo e Feriados; Horário: Fechado" in chunks["2"].text


def test_wrapped_bullets_are_joined(chunks):
    assert (
        "- Parcelamento em até 3x: sem juros, sem valor mínimo de parcela "
        "(exceto abaixo de R$ 50,00)."
    ) in chunks["3.1"].text


def test_numbered_list_survives_page_break(chunks):
    items = [line for line in chunks["7.2"].text.splitlines() if line.startswith("- ")]
    assert [item[2:4] for item in items] == ["1.", "2.", "3.", "4.", "5."]


def test_wrapped_paragraph_after_colon_is_joined(chunks):
    assert "(exemplo: BR4K7M2X9P1BR)" in chunks["5.3"].text


def test_chunks_fit_the_embedding_window(chunks):
    # ~4 chars per token; the embedding model reads 512 tokens.
    assert max(len(c.index_text) for c in chunks.values()) < 1500


def test_repeated_edge_lines_are_noise_regardless_of_digits():
    pages = [f"Header\nBody {n}\nItem {n}\nPágina {n}" for n in range(1, 5)]
    kept = [line.text for line in clean_lines(pages)]
    # "Body n"/"Item n" also repeat (digits ignored) but are not at a page edge → kept
    assert kept == [text for n in range(1, 5) for text in (f"Body {n}", f"Item {n}")]


def test_wrapped_table_cell_joins_last_cell():
    lines = [
        Line(1, 1, "Forma        Observações"),
        Line(1, 1, "PIX          5% de desconto sobre o preço de"),
        Line(1, 40, "tabela"),
    ]
    assert render_blocks(build_blocks(lines, HEADING)) == (
        "- Forma: PIX; Observações: 5% de desconto sobre o preço de tabela"
    )


def test_indented_numbered_items_are_not_headings():
    lines = [Line(1, 0, "7.2 Fluxo"), Line(1, 4, "4. Resposta: apresentar as opções")]
    kinds = [block.kind for block in build_blocks(lines, HEADING)]
    assert kinds == ["heading", "bullet"]


def test_fixed_size_fallback_overlaps():
    text = " ".join(f"palavra{n:03d}" for n in range(300))  # 11 chars per word + space
    parts = chunk_fixed_size(text, "doc.pdf", size=120, overlap=30)
    assert [p.section_id for p in parts[:2]] == ["p1", "p2"]
    assert all(len(p.text) <= 120 for p in parts)
    first, second = parts[0].text.split(), parts[1].text.split()
    assert second[0] in first  # overlap
    assert parts[-1].text.split()[-1] == "palavra299"


def test_chunks_round_trip(tmp_path, chunks):
    path = tmp_path / "index" / "chunks.json"
    write_chunks(list(chunks.values()), path)
    assert read_chunks(path) == list(chunks.values())
