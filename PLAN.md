# PLAN.md — Semestrální projekt VMM

**Téma:** 2. STŘELECKÁ ÚSPĚŠNOST
**Data:** `data/shots_outcome_Final.csv` — 1 000 střel, 12 hráčů (Aston Villa), 301 zápasů, sezóny 2014–2020
**Repo:** https://github.com/jkrejcikova/VMM_project.git
**Hladina významnosti:** α = 0,05

---

## 0. Postup a stav

| # | Úkol | Stav |
|---|------|------|
| 1 | Struktura složek + přesun dat a PDF | hotovo |
| 2 | `uv` projekt + balíčky (pandas, scipy, sklearn, statsmodels, pingouin, matplotlib, python-pptx) | hotovo |
| 3 | Studia zadání + přednášek VMM-1..11 (terminologie) | hotovo |
| 4 | EDA a audit dat | hotovo |
| 5 | `src/preprocessing.py` — čištění, odlehlé hodnoty, standardizace | hotovo |
| 6 | `src/hypotheses.py` — H1–H4 | hotovo |
| 7 | `src/analysis.py` — orchestrator, `outputs/vysledky.md` | hotovo |
| 8 | `src/make_presentation.py` — .pptx na 10 min obhajoby | hotovo |
| 9 | `README.md` + commit + push | hotovo |

## 1. Zjištění z auditu dat (řídí předzpracování)

* 1 000 řádků × 20 sloupců, žádná duplicita, `id` unikátní.
* **Chybějící hodnoty:** `X` 98 (9,8 %), `Y` 178 (17,8 %), `shotType` 178 (17,8 %),
  `lastAction` 82 (8,2 %), `player_assisted` 234 (23,4 %). V datech nejsou řetězcové
  markery typu `-` (pouze reálné NaN) — kontrola provedena.
* **Měřítko souřadnic:** zadání definuje hřiště 100×100, data jsou normalizovaná na
  **0–1** (max X = 0,987). → přeškálování ×100. Po něm žádná hodnota mimo rozsah.
* **Vlastní góly** (2 řádky, `result = OwnGoal`, X = 0,088 / 0,034, xG = 0): rána na
  vlastní branku je pro model úspěšnosti na branku soupeře **neplatná hodnota** → vyloučena.
* **Penalty** (6 řádků): degenerované — X, Y i xG konstantní (0,885 / 0,5 / 0,7612),
  úspěšnost 50 %. Ponechány + označeny, ve citlivostní analýze vynechány.
* **Vzdálenost a úhel** v datech nejsou → derivují se z X, Y (přepočet na metry):
  `vzdálenost` ke středu branky a `úhel` (úhel odkrytý brankou, standardní metrika
  kvality šance).

## 2. Předzpracování

1. Načtení + typy, překodování `result` → cíl `gol` (1/0).
2. Validace rozsahů X, Y (0–100 po přeškálování), vyloučení OwnGoal.
3. Chybějící hodnoty:
   * kategorické (`shotType`, `lastAction`) → vlastní kategorie `Neznámý`;
   * numerické X, Y → **imputace se neprovádí**, analýza běží na kompletním
     souboru (736 střel s X i Y); odůvodnění: geometrické veličiny se nedají
     rozumně imputovat bez zkreslení ordinačních metod;
   * srovnání rozdělení xG mezi řádky s a bez chybějících souřadnic (test MCAR).
4. Odlehlé hodnoty: **Mahalanobisova vzdálenost** na standardizovaných
   (xG, vzdálenost, úhel, X, Y) + **χ² diagram** (výběrové vs. modelové kvantily),
   kritérium = 99,7 % kvantil χ²₂ (5 stupňů volnosti).
5. Škálování/standardizace: `StandardScaler` (centrování + směrodatná odchylka),
   transformace uchovány pro interpretaci.

## 3. Hypotézy (α = 0,05)

* **H1 — ANOVA / MANOVA.** Vliv typu zakončení (`shotType`) a herní situace
  (`situation`) na vektor parametrů střely (xG, vzdálenost, úhel, X, Y).
  MANOVA (Wilksův lambda) + univariační ANOVA + Tukey HSD + Leveného test.
* **H2 — PCA / faktorová analýza.** Existuje latentní struktura (redukce dimenze)?
  Bartlettův test sférickosti, eigenvalues, Kaiserovo kritérium, graf úpatí (scree plot),
  kumulativní rozptyl, paralelní analýza, biplot; FA s varimax rotací, komunality.
* **H3 — Shluková analýza.** Lze střely rozdělit do takticky odlišných skupin?
  Wardova metoda + dendrogram, k-means s volbou k (WSS/elbow, CH, silhouette, DB),
  profil shluků včetně úspěšnosti.
* **H4 — Diskriminační analýza / logistická regrese.** Vliv vzdálenosti, úhlu a xG
  na úspěšnost (gól). Logistická regrese (LR test, pseudo-R², ROC/AUC, OR),
  LDA + klasifikační matice, Hotellingovo T² (gól vs. nezgólovaná střela).

## 4. Výstupy

* `outputs/*.png` — všechny grafy (krabicové grafy, korelační matice, χ² diagram,
  scree plot, biplot, dendrogram, heatmapy, mapa střel, ROC).
* `outputs/vysledky.md` — všechny testové statistiky, p-hodnoty, závěry H0/neH0.
* `presentation/VMM_strelecka_uspesnost.pptx` — 18 snímků z generovaných výstupů
  (`src/make_presentation.py` čísla ani grafy nepřepisuje ručně).

## 5. Odevzdání

Aktuální stav: analýza i prezentace se vygenerují na jeden příkaz,
`outputs/vysledky.md` je zdroj pravdy pro všechna čísla (35 grafů, 25 tabulek).

README + `git add .` + commit + push do `origin main`.
Přednáškové PDF se záměrně necommitují (autorská práva, ~45 MB) — viz `.gitignore`.
