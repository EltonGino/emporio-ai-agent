"""Build the policy index: documents → structure-aware chunks → embeddings (data/runtime/index/).

Usage: uv run python scripts/build_index.py
The first run downloads the embedding model to data/runtime/models/.
"""

import time

from emporio_agent.config import INDEX_DIR, get_client_config
from emporio_agent.core.retrieval.index import build_index, default_embedder


def main() -> None:
    retrieval = get_client_config().retrieval
    started = time.perf_counter()
    embedder = default_embedder()
    loaded = time.perf_counter()
    chunks = build_index(retrieval.documents, retrieval.heading_pattern, embedder)
    done = time.perf_counter()
    print(f"{len(chunks)} chunks indexed in {INDEX_DIR}")
    print(f"  embedding model: {embedder.model_name}")
    print(f"  model load {loaded - started:.1f}s, chunk + embed {done - loaded:.1f}s")
    for chunk in chunks:
        print(f"  {chunk.section_id:<5} p{chunk.page}  {len(chunk.text):>4} chars  {chunk.title}")


if __name__ == "__main__":
    main()
