import pytest

from emporio_agent.config import get_client_config
from emporio_agent.core.retrieval.bm25 import BM25Retriever
from emporio_agent.core.retrieval.ingest import chunk_document


@pytest.fixture(scope="session")
def policy_retriever():
    """BM25 over the real manual: tool tests need retrieval but not the embedding model."""
    retrieval = get_client_config().retrieval
    return BM25Retriever(chunk_document(retrieval.documents[0], retrieval.heading_pattern))
