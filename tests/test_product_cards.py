import json

import pytest
from langchain_core.messages import ToolMessage

from emporio_agent.client.db.engine import make_engine
from emporio_agent.client.db.loader import load_store_data
from emporio_agent.client.product_cards import product_cards
from emporio_agent.client.repository import SqlStoreRepository
from emporio_agent.client.tools import build_tools
from emporio_agent.clock import get_clock
from emporio_agent.config import get_client_config

# The owner's frontend test (gpt-oss:20b on Ollama), verbatim with its U+2011 hyphens.
GF3D_ANSWER = (
    "O Giannini GF‑3D não está em estoque – está **esgotado**.  \n"
    "Mas tem opções parecidas:\n\n"
    "- **Shelby SGD‑195E** – R$ 799,00 (PIX: R$ 759,05)  \n"
    "- **Yamaha C70** – R$ 849,00 (PIX: R$ 806,55)  \n"
    "- **Tagima Dallas Tuner** – R$ 749,00 (PIX: R$ 711,55)\n\n"
    "Qualquer uma dessas pode ser uma boa alternativa."
)


@pytest.fixture(scope="module")
def tools(tmp_path_factory, policy_retriever):
    engine = make_engine(f"sqlite:///{tmp_path_factory.mktemp('db')}/store.db")
    load_store_data(engine)
    repo = SqlStoreRepository(engine)
    built = build_tools(repo, get_client_config(), get_clock(), policy_retriever)
    return {t.name: t for t in built}


def result(tools, name: str, args: dict | None = None) -> ToolMessage:
    return ToolMessage(tools[name].invoke(args or {}), tool_call_id="c", name=name)


def names(cards) -> list[str]:
    return [c["name"] for c in cards]


def test_every_product_cited_gets_a_card_in_order_of_mention(tools):
    search = result(tools, "search_products", {"query": "Giannini GF-3D"})
    alternatives = {p["name"] for p in json.loads(search.text)["alternatives"]}
    assert "Tagima Dallas Tuner Aço Natural" in alternatives  # the tool did return it
    assert names(product_cards(GF3D_ANSWER, [search])) == [
        "Giannini GF-3D Dreadnought Sunburst",
        "Shelby SGD-195E Elétrico Aço Sunburst",
        "Yamaha C70 Nylon Natural",
        "Tagima Dallas Tuner Aço Natural",  # abbreviated name, no model code: was missing
    ]


def test_shortest_unique_leading_words_identify_a_product(tools):
    search = result(tools, "search_products", {"category": "baterias"})
    cards = product_cards("Temos a **Bateria Acústica Pearl Kit 2 Studio**.", [search])
    assert names(cards) == ["Bateria Acústica Pearl Kit 2 Studio"]
    cards = product_cards("A Bateria Acústica Yamaha Kit 3 é ótima.", [search])
    assert names(cards) == ["Bateria Acústica Yamaha Kit 3 Studio"]


@pytest.mark.parametrize(
    "answer",
    [
        "Temos várias baterias acústicas.",  # shared by every kit: ambiguous
        "A Bateria Acústica Yamaha é ótima.",  # Kit 1 and Kit 3 share it
        "O Yamaha é ótimo.",  # brand only
    ],
)
def test_ambiguous_mentions_get_no_card(tools, answer):
    search = result(tools, "search_products", {"category": "baterias"})
    assert product_cards(answer, [search]) == []


def test_typographic_hyphens_and_spaces_still_match(tools):
    search = result(tools, "search_products", {"query": "Takamine GD20"})
    cards = product_cards("O Takamine GD‑20 sai por R$ 2.199,00.", [search])
    assert names(cards) == ["Takamine GD20 Dreadnought Natural"]
