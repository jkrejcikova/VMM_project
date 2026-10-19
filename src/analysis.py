"""Hlavní spouštěč analýzy „Střelecká úspěšnost“ (VMM).

Spustí předzpracování dat, otestuje hypotézy H1–H4 a zapíše protokol
`outputs/vysledky.md` a všechny grafy do `outputs/`.

Použití:
    uv run python src/analysis.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Windows konzole běží v CP-1250 — čeština by se v ní rozbila
for proud in (sys.stdout, sys.stderr):
    if hasattr(proud, "reconfigure"):
        proud.reconfigure(encoding="utf-8", errors="replace")

import config as cfg           # noqa: E402
import hypotheses as hyp       # noqa: E402
import preprocessing as prep   # noqa: E402


def main() -> int:
    """Celé pipeline; vrátí návratový kód 0 při úspěchu."""
    start = time.time()
    cfg.zajisti_slozky()
    cfg.styl_grafu()

    protokol = cfg.Vysledky()
    print("[1/3] předzpracování dat ...")
    priprava = prep.spust(protokol)
    print(f"      {len(priprava['data'])} střel po vyčištění, "
          f"{len(priprava['uplne'])} s úplnými souřadnicemi")

    print("[2/3] testování hypotéz H1–H4 ...")
    souhrn = hyp.spust(protokol, priprava)

    print("[3/3] zápis protokolu ...")
    cesta = protokol.uloz()

    print()
    for zkrácená, vysledek in souhrn.items():
        print(f"  {zkrácená} {vysledek['název']:<28} "
              f"{'H0 zamítáme' if vysledek['zamitnuto'] else 'H0 nezamítáme'}")
    print(f"\nprotokol: {cesta}")
    print(f"grafy:    {cfg.OUT_DIR}")
    print(f"tabulky:  {cfg.TABULKY_DIR}")
    print(f"čas:      {time.time() - start:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
