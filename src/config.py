"""Konfigurace projektu — cesty, konstanty, vzhled grafu, pomocné formátovače.

Moduly `preprocessing.py`, `hypotheses.py` a `analysis.py` používají výhradně
tuto konfiguraci, aby šly výstupy reprodukovatelně regenerovat.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # generování PNG bez GUI

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# --- cesty ---------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "outputs"
TABULKY_DIR = OUT_DIR / "tabulky"
PREZ_DIR = ROOT / "presentation"
CSV = DATA_DIR / "shots_outcome_Final.csv"

# --- statistické konvence (dle AGENTS.md) --------------------------------
SEED = 42
ALPHA = 0.05

# --- geometrie hřiště ----------------------------------------------------
# Zadání pracuje s hřištěm 100 × 100 jednotek (délka × šířka, zjednodušená
# reprezentace). Pro přepočet na metry uvažujeme standardní rozměry a branku.
DELKA_HRITE_M = 105.0
SIRKA_HRITE_M = 68.0
SIRKA_BRANKY_M = 7.32
POLOVINA_BRANKY_M = SIRKA_BRANKY_M / 2.0

# Výsledkový stav střely -> český popisek a binární indikátory
VYSLEDKY_CS = {
    "Goal": "Gól",
    "SavedShot": "Chycená brankářem",
    "MissedShots": "Vedle branky",
    "BlockedShot": "Zablokovaná",
    "ShotOnPost": "Tyč/břevno",
    "OwnGoal": "Vlastní gól",
}
NA_BRANKU = {"Goal", "SavedShot", "ShotOnPost"}  # střely, které minuly brankáře


def zajisti_slozky() -> None:
    """Vytvoří všechny výstupní adresáře (volání je idempotentní)."""
    for slozka in (OUT_DIR, TABULKY_DIR, PREZ_DIR):
        slozka.mkdir(parents=True, exist_ok=True)


def styl_grafu() -> None:
    """Jednotný vzhled grafů; font DejaVu Sans zvládá češtinu i χ²."""
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.titlesize": 11,
            "axes.titleweight": "bold",
            "axes.labelsize": 10,
            "axes.grid": True,
            "grid.alpha": 0.3,
            "grid.linewidth": 0.6,
            "figure.dpi": 110,
            "savefig.dpi": 150,
            "savefig.bbox": "tight",
            "legend.frameon": False,
            "figure.facecolor": "white",
            "axes.prop_cycle": plt.cycler(
                color=["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b"]
            ),
        }
    )


def uloz_fig(fig, jmeno: str, zavri: bool = True) -> Path:
    """Uloží figure jako PNG do outputs/ a volitelně ji zavře."""
    cesta = Path(OUT_DIR) / f"{jmeno}.png"
    fig.savefig(cesta)
    if zavri:
        plt.close(fig)
    return cesta


# --- formátování výstupů -------------------------------------------------
def fmt_p(p) -> str:
    """Český formát p-hodnoty; drobné hodnoty jako 'p < 0,001'."""
    if p is None:
        return "nelze ověřit"
    p = float(p)
    if np.isnan(p):
        return "nelze ověřit"
    if p < 0.001:
        return "p < 0,001"
    return f"p = {p:.4f}".replace(".", ",")


def cislo(hodnota, zaokrouhleni: int = 3) -> str:
    """České desetinné čárky v textových výstupech."""
    if hodnota is None:
        return "—"
    hodnota = float(hodnota)
    if np.isnan(hodnota):
        return "—"
    return f"{hodnota:.{zaokrouhleni}f}".replace(".", ",")


def markdown_tabulka(df: pd.DataFrame, index: bool = False) -> str:
    """Markdown tabulka s českými desetinnými čárkami."""
    kopie = df.copy()
    for sloupec in kopie.select_dtypes(include=[np.number]).columns:
        kopie[sloupec] = kopie[sloupec].map(
            lambda v: "—" if pd.isna(v) else f"{v:.4f}".replace(".", ",")
        )
    return kopie.to_markdown(index=index)


class Vysledky:
    """Sbírá text, tabulky a odkazy na obrázky do protokolu outputs/vysledky.md."""

    def __init__(self) -> None:
        self.casti: list[str] = []

    def nadpis(self, text: str, uroven: int = 2) -> None:
        self.casti.append(f"{'#' * uroven} {text}\n")

    def radek(self, text: str = "") -> None:
        self.casti.append(text)

    def odstavec(self, text: str) -> None:
        self.casti.append(text + "\n")

    def tabulka(self, df: pd.DataFrame, titulek: str | None = None, index: bool = False) -> None:
        if titulek:
            self.casti.append(f"**{titulek}**\n")
        self.casti.append(markdown_tabulka(df, index=index) + "\n")

    def obrazek(self, jmeno: str, popisek: str | None = None) -> None:
        self.casti.append(f"![{popisek or jmeno}]({jmeno}.png)\n\n*Obrázek: {popisek or jmeno}*\n")

    def uloz(self, nazev: str = "vysledky.md") -> Path:
        cesta = Path(OUT_DIR) / nazev
        hlava = [
            "# Protokol o analýze — Střelecká úspěšnost (VMM)\n",
            f"_Vygenerováno skriptem `src/analysis.py` {datetime.now():%Y-%m-%d %H:%M}, "
            f"α = {ALPHA}, náhodné seed = {SEED}._\n",
        ]
        cesta.write_text("\n".join(hlava + self.casti), encoding="utf-8")
        return cesta
