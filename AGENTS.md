# AGENTS.md / CLAUDE.md — pravidla pro práci v tomto repozitáři

## Prostředí

* Python spravujte přes **`uv`** (`uv add <balicek>`, `uv run <skript>`). Žádné `pip install`,
  žádný systémový Python (na stroji není).
* Analýza se spouští z kořene projektu:
  ```bash
  uv run python src/analysis.py            # celé pipeline (předzpracování + H1–H4)
  uv run python src/make_presentation.py   # vygeneruje .pptx z outputs/
  ```
* `outputs/` je **generovaný** adresář – maže se a regeneruje, ruční zásahy do něj
  se nepřenášejí do kódu. Zdroj pravdy pro čísla je `outputs/vysledky.md`.

## Jazyk a styl

* Komentáře, názvy grafů, texty výstupů a prezentace **česky**, s diacritikou.
* Názvy proměnných a souborů anglicky/bez diacritiky (kvůli přenositelnosti cest).
* Terminologie podle přednášek VMM: *krabicový graf*, *graf úpatí (scree plot)*,
  *biplot*, *rozptylový diagram komponentního skóre*, *χ² diagram*, *komunalita*,
  *Bartlettův test sférickosti*, *Wardova metoda*, *Tukey HSD*, *standardizace*
  (≠ normalizace). Nepoužívat „spolehlivost" místo komunity.
* Všechny hypotézy se vypisují jako H0 / H1 s explicitním závěrem
  **„H0 zamítáme" / „H0 nezamítáme"** na α = 0,05.

## Statistické konvence

* α = 0,05, p zaokrouhlovat na 3–4 platné číslice, `p < 0,001` místo 0,0000.
* Větší data testy normality zamítají H0 plošně – vždy doplnit vizuální kontrolu
  a reportovat šikmost/špičatost, ne jen p-hodnotu.
* Odlehlé hodnoty se **neodstraňují mlčky**: vždy počet, kritérium (χ² kvantil) a
  citlivostní analýza se i bez nich.
* U K-means a jakéhokoli stochastického algoritmu fixní `random_state` (SEED = 42).
* Reprodukčnost: jedna cesta ke všem grafům, žádný ručně vložený obrázek.

## Data

* `data/shots_outcome_Final.csv` je **input only** – nikdy se nepřepisuje.
  Čištěná data se vrací jako DataFrame, případně se uloží do `outputs/tabulky/`.
* Pozor na měřítko: X, Y jsou v souboru 0–1, zadání definuje 0–100 → ×100.
* `result` se v datech jmenuje malými písmeny (v zadání „Result").

## Git

* Výchozí větev `main`, remote `origin` → `https://github.com/jkrejcikova/VMM_project.git`.
* Přednáškové PDF (`docs/VMM-*.pdf`) se **necommitují** (autorská práva, ~45 MB);
  jsou v `.gitignore`.
* Nikdy necommitovat `.venv/`, `__pycache__/`, `.claude/`.
