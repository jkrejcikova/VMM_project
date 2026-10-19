"""Konzolový příkaz `vmm-strelecka-uspesnost` — spouští celé pipeline analýzy.

Oprotí přímému `uv run python src/analysis.py` jen šetní práci; oba směry dělají
totéž a ani jeden nezapomíná na `uv` (viz AGENTS.md).
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    """Spustí analýzu H1–H4 a zapíše protokol, grafy a tabulky do outputs/."""
    slozka_src = Path(__file__).resolve().parent.parent
    if str(slozka_src) not in sys.path:
        sys.path.insert(0, str(slozka_src))
    import analysis

    raise SystemExit(analysis.main())
