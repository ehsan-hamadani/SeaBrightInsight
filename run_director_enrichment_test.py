"""Run director enrichment logic for a small sample of companies.

This script is intended for smoke testing the notebook-based company director import flow.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

NB = json.loads((ROOT / "director_enrichment.ipynb").read_text(encoding="utf-8"))


def cell_source(idx: int) -> str:
    """Return the source code for a notebook cell by index."""
    return "".join(NB["cells"][idx]["source"])


def main() -> None:
    """Execute notebook cells to generate a sample director export CSV."""
    g: dict[str, object] = {"__name__": "__main__", "__file__": str(Path(__file__))}
    for idx in (1, 2, 3):
        exec(cell_source(idx), g)
    g["MAX_COMPANIES"] = 5
    g["OUTPUT_CSV"] = "market_research_directors_test5.csv"
    exec(cell_source(4), g)
    print("\nSaved:", g.get("OUTPUT_CSV"), "| rows:", len(g.get("result", [])))


if __name__ == "__main__":
    main()
