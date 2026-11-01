"""Build the policy index: documents → structure-aware chunks (data/runtime/index/).

Usage: uv run python scripts/build_index.py
"""

from emporio_agent.config import INDEX_DIR, get_client_config
from emporio_agent.core.retrieval.ingest import chunk_document, write_chunks

CHUNKS_FILE = INDEX_DIR / "chunks.json"


def main() -> None:
    retrieval = get_client_config().retrieval
    chunks = [
        chunk
        for document in retrieval.documents
        for chunk in chunk_document(document, retrieval.heading_pattern)
    ]
    write_chunks(chunks, CHUNKS_FILE)
    print(f"{len(chunks)} chunks written to {CHUNKS_FILE}")
    for chunk in chunks:
        print(f"  {chunk.section_id:<5} p{chunk.page}  {len(chunk.text):>4} chars  {chunk.title}")


if __name__ == "__main__":
    main()
