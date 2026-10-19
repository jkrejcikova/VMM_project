# Střelecká úspěšnost — semestrální projekt (VMM)

Multivariační analýza střel z anglické Premier League (Aston Villa, sezóny 2014–2020):
jak poloha a situace ovlivňují parametry střely, kolik dimenzí střelu skutečně
potřebuje, jaké přirozené typy střel v datech jsou a co z toho lze odvodit o úspěšnosti.

Vše běží v Pythonu spravovaném přes **`uv`**, bez ručního zásahu do výstupů:
`outputs/` je generovaný adresář (maže se a regeneruje) a zdrojem pravdy pro všechna
čísla je protokol `outputs/vysledky.md`.

## Jak spustit

```bash
uv sync                                  # nainstaluje závislosti z pyproject.toml
uv run python src/analysis.py            # předzpracování + H1–H4 → outputs/
uv run python src/make_presentation.py   # prezentace → presentation/
```

Stejnou analýzu spustí i příkaz `uv run vmm-strelecka-uspesnost`
(nastavený v `pyproject.toml`, uvnitř volá `src/analysis.py`).

Analýza trvá řádově 30 s a je plně deterministická (`SEED = 42` u všech stochastických
metod, tj. K-means, permutačních testů i t-SNE). Prezentace nevzniká z opsaných čísel —
`src/make_presentation.py` čte texty a čísla přímo z `outputs/vysledky.md` a grafy z
`outputs/*.png`, takže po změně analýzy stačí spustit oba skripty za sebou.

## Struktura

```
data/shots_outcome_Final.csv   vstupní data (input only — nikdy se nepřepisují)
src/config.py                  cesty, SEED, α = 0,05, rozměry hřiště, pomocníci pro protokol
src/preprocessing.py           čištění, odvození geometrie, MCAR, Mahalanobis, standardizace
src/hypotheses.py              H1–H4 (testy, grafy, tabulky, závěry)
src/analysis.py                řídicí skript celého pipeline
src/make_presentation.py       18 snímků .pptx z vygenerovaných výstupů
src/vmm_strelecka_uspesnost/   konzolový příkaz (jen předá práci analysis.py)
outputs/vysledky.md            protokol se všemi testy, grafy a tabulkami
outputs/*.png                  35 grafů
outputs/tabulky/*.csv          25 tabulek (včetně vyčištěných dat)
presentation/*.pptx            prezentace na obhajobu
docs/                          přednášková PDF (ven z repozitáře — autorská práva)
```

## Data a příprava

`data/shots_outcome_Final.csv` = 1 000 záznamů o střelách, 12 střelců, 301 zápasů,
sezóny 2014–2020, 20 sloupců. Při přípravě jsme

1. **přeškálovali souřadnice** X, Y z rozsahu 0–1 na 100 × 100 jednotek hřiště
   (měřítko podle zadání),
2. **vyloučili 2 vlastní góly** (rána na vlastní branku nedává smysl u střely na
   branku soupeře) → **998** střel,
3. **odvodili geometrii**, kterou data neobsahují: vzdálenost ke středu branky
   (`dist_m`, hřiště 105 × 68 m, branka 7,32 m) a **úhel odkrytý brankou**
   (`angle_deg`) — standardní metrika kvality šance,
4. **neimputovali chybějící souřadnice** (X 9,8 %, Y 17,7 %): geometrické veličiny by
   imputace nahnala k průměru a zkreslila ordinační i shlukové metody. Ordinační a
   shlukové analýzy a model tedy běží na **729** střelách z pole s úplnými souřadnicemi
   (735 s úplnými souřadnicemi minus 6 penalt). Předpoklad MCAR jsme ověřili
   Mann–Whitneyovým U pro xG (p = 0,353) a χ² pro chybějící souřadnice × výsledek
   (p = 0,921) — řádky bez souřadnic se v xG ani v úspěšnosti neliší,
5. chybějící kategorie (`shotType`, `lastAction`, `player_assisted`) doplnili vlastní
   kategorií **„Neznámý“** — chybějící údaj sám o sobě nese informaci,
6. odlehlé hodnoty detekovali **Mahalanobisovou vzdáleností** na pěti standardizovaných
   příznacích (5 stupňů volnosti) s kritériem 99,7 % kvantilu χ² a vykreslili
   **χ² diagram**.
   **Neodstraňujeme je mlčky** — všechny analytické závěry opakujeme bez nich
   (citlivostní analýza je u H2 i H4),
7. **penalty (6 řádků) vyčlenili**: X, Y i xG mají konstantní hodnotu, nulová variabilita
   polohy by zkreslila korelační matici i model. Úspěšnost penalt (50 %) reportujeme
   zvlášť, do modelu nevstupují.

## Výsledky (α = 0,05)

| hypotéza | co zkoumá | výsledek |
|---|---|---|
| **H1** | vliv typu zakončení a herní situace na parametry střely (ANOVA, Welch, Kruskal–Wallis, MANOVA, Tukey HSD, χ²) | **H0 zamítáme** — nejsilnější efekt na úhel odkrytý brankou podle typu zakončení (η² = 0,169, ω² = 0,166, velký efekt); xG se podle zakončení nelíší (p = 0,4427) |
| **H2** | redukce dimenze 5 příznaků (Bartlett, KMO, PCA, paralelní analýza, varimax) | **H0 zamítáme** — stačí **1 komponenta** (60,7 % rozptylu, 2 komponenty 80,7 %); „osa obtížnosti šance“. KMO jen 0,584 → redukce je orientační zkrácení dat |
| **H3** | přirozená shluková struktura střel (Ward, K-means, permutační test silhouette, ARI) | **H0 zamítáme** — **2 shluky**, silhouette 0,503, permutační p = 0,0050; úspěšnější shluk 30,0 % gólů proti průměru 8,2 % |
| **H4** | model pravděpodobnosti gólu (logistická regrese, Wald, LR testy, VIF, Hosmer–Lemeshow, LDA, cross-validace) | **H0 zamítáme** — AUC 0,745 (95% IS 0,656–0,824), McFaddenovo R² 0,162; +0,1 xG zvyšuje šanci 1,66× (IS 1,32–2,08) |

Nejzajímavější na H4 je rozklad celkového efektu: **samotná poloha** (vzdálenost + úhel)
sice vysvětlí gól lépe než průměr (χ²(2) = 46,64, p < 0,001), ale **nad rámec xG už
nic nepřidává** (χ²(2) = 2,15, p = 0,3418) — informace o poloze je v xG už obsažena a
příznaky se o ni dělí (VIF 46,5). Waldovy testy u vzdálenosti a úhlu proto samy o sobě
průkazné nejsou; v tom je též důvod, proč mapu pravděpodobnosti gólu počítáme z modelu
pouze s geometrií — je to model, který jde aplikovat na libovolnou pozici bez xG.

Klasifikace modelu (60 gólů z 729 střel, tedy nevyvážená odezva 8,2 %):

| práh | senzitivita | specificita | správně |
|---|---|---|---|
| 0,5 (výchozí) | 0,17 | 0,99 | 92,2 % (IS 90,0–93,9 %) |
| 0,08 (Youdenův) | 0,65 | 0,81 | 80,1 % (IS 77,1–82,8 %) |

Výchozí práh u nevyvážené odezvy prakticky nic nepředpovídá (model označí za gól
jen 17 střel); Youdenův práh obě chyby vyvažuje. Proto reportujeme AUC a kalibraci,
ne „úspěšnost klasifikace 92 %“.

## Statistické konvence

* α = 0,05, každý test zapsán jako **H0 / H1** s explicitním závěrem
  „H0 zamítáme“ / „H0 nezamítáme“.
* p-hodnoty na 3–4 platné číslice, `p < 0,001` místo 0,0000.
* U velkého souboru testy normality zamítají H0 plošně — proto vždy doplňujeme
  vizuální kontrolu a reportujeme šikmost a špičatost, ne jen p-hodnotu.
* Normalita není podmínkou ANOVY při velkých skupinách (Centrální limitová věta);
  jistotu přidává Kruskal–Wallisův test se stejným závěrem.
* Efekty reportujeme spolu s p-hodnotou (η², ω², Cramérovo V, OR, AUC) — při
  n ≈ 1 000 by samotné p bylo bezcenné.
* Opakovatelnost: `SEED = 42`, jedna cesta ke všem grafům, žádný ručně vložený obrázek.
* Terminologie podle přednášek VMM: *krabicový graf*, *graf úpatí (scree plot)*,
  *biplot*, *rozptýlový diagram komponentního skóre*, *χ² diagram*, *komunalita*,
  *Bartlettův test sférickosti*, *Wardova metoda*, *Tukey HSD*, *standardizace*
  (≠ normalizace).

## Limitace

* **Pozorování, ne experiment** — model ukazuje asociaci, ne příčinu: střelec
  z lepší pozice „nemá větší dovednost“, jen měl lepší šanci.
* **Chybějící souřadnice** (9,8 % u X, 17,7 % u Y) se v ordinačních metodách nezastupují;
  ověřili jsme MCAR, ale neříkáme, že vynechání je zadarmo.
* **KMO = 0,584** je pod obvyklou hranicí 0,6 — redukci dimenze bereme jako
  orientační zkrácení dat, nikoli jako důkaz, že ostatní rozměry jsou zbytečné.
* **Shluky nejsou ostré entity** — u fotbalových dat jde o popisné „typy“
  s pozvolnými hranicemi; t-SNE ukázalo část nelinearity (ARI = 0,260),
  lineární metrika tedy část struktury nerozliší.
* **Zbývající nejistota u H4 není chyba modelu** — střelba je ze své podstaty
  náhodný děj (ani penalty se nepromění vždy), správnou interpretací je odhad
  pravděpodobnosti, ne předpověď výsledku.

## Podklady

Předmětové zadání „2. STŘELECKÁ ÚSPĚŠNOST“ a přednášky VMM-1 až VMM-11
(adresář `docs/`, v repozitáři nejsou kvůli autorským právům); postup a stav
práce jsou v [PLAN.md](PLAN.md), pravidla pro agenty i lidi v [AGENTS.md](AGENTS.md).
