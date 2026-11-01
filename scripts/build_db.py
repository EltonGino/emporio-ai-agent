"""Rebuild the store database from data/raw (idempotent).

Usage: uv run python scripts/build_db.py
"""

from emporio_agent.client.db.engine import make_engine
from emporio_agent.client.db.loader import load_store_data


def main() -> None:
    engine = make_engine()
    counts = load_store_data(engine)
    print(f"Store database rebuilt at {engine.url.render_as_string(hide_password=True)}")
    for table, count in counts.items():
        print(f"  {table:<12} {count:>4} rows")


if __name__ == "__main__":
    main()
