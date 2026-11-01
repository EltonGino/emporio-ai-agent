from pathlib import Path

import emporio_agent

RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"
RAW_FILES = [
    "categories.csv",
    "customers.csv",
    "order_items.csv",
    "orders.csv",
    "products.csv",
    "promotions.csv",
    "politicas_da_loja.pdf",
]


def test_package_imports():
    assert emporio_agent.__version__


def test_raw_inputs_present():
    missing = [name for name in RAW_FILES if not (RAW_DIR / name).is_file()]
    assert not missing, f"missing raw inputs in data/raw: {missing}"
