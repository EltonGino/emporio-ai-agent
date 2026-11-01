"""Print the data-quality findings (AGENTS.md §5.3) with their evidence and decisions.

Usage: uv run python scripts/profile_data.py
"""

from emporio_agent.client.profiling import run_checks


def main() -> None:
    for finding in run_checks():
        print(f"{finding.id:<4} {finding.title}")
        for line in finding.evidence or ["(no evidence found)"]:
            print(f"     - {line}")
        print(f"     Decision: {finding.decision}\n")


if __name__ == "__main__":
    main()
