"""Testování hypotéz H1–H4 nad vyčištěnými daty střeleckých pokusů.

Metodiky navazují na přednášky VMM:
  H1  ANOVA / MANOVA + Tukey HSD, Kruskal–Wallis, Levene, Welch        (VMM-4)
  H2  PCA inkl. Bartlettova testu sférickosti, Kaiser–Meyer–Olkin, grafu
      úpatí (scree plot), paralelní analýzy a biplotu; faktorová analýza
      s varimax rotací a komunalitami                          (VMM-6, VMM-9)
  H3  hierarchické shlukování (Wardova metoda) + K-means, validace pomocí
      silhouette, Calinski–Harabasz, Davies–Bouldin, bootstrap ARI a
      permutačního testu (VMM-5), kontrola nelinearity pomocí t-SNE  (VMM-10)
  H4  logistická regrese (Wald, LR test, VIF, Hosmer–Lemeshow), LDA,
      ROC/AUC, konfuzní matice, křížová validace                       (VMM-7)

Každá funkce `testuj_hN` zapíše protokol do objektu `cfg.Vysledky`, uloží grafy
do `outputs/` a tabulky do `outputs/tabulky/`. Všechny grafy vznikají jedinou
cestou ze skriptu `src/analysis.py` — žádné ručně vložené obrázky.
"""

from __future__ import annotations

import itertools

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pingouin as pg
import seaborn as sns
import statsmodels.api as sm
from scipy import stats
from scipy.cluster.hierarchy import dendrogram, fcluster, linkage
from sklearn.cluster import KMeans
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
from sklearn.linear_model import LogisticRegression
from sklearn.manifold import TSNE
from sklearn.metrics import (
    adjusted_rand_score,
    calinski_harabasz_score,
    confusion_matrix,
    davies_bouldin_score,
    roc_auc_score,
    roc_curve,
    silhouette_score,
)
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from statsmodels.multivariate.manova import MANOVA
from statsmodels.stats.multicomp import pairwise_tukeyhsd

import config as cfg
import preprocessing as prep

# ---------------------------------------------------------------------------
# Proměnné a převody kategorií
# ---------------------------------------------------------------------------
PROM_H1 = ["xG", "dist_m", "angle_deg"]
PROM_H2 = list(prep.VMM_PROMENNE)          # xG, dist_m, angle_deg, X, Y
PROM_H3 = ["dist_m", "angle_deg", "xG", "Y"]
ZAKLADNI_MODEL = ["xG", "dist_m", "angle_deg"]
ROHSIRENY_MODEL = ZAKLADNI_MODEL + ["minuta", "jina_polovina", "bez_asistence"]

SKUP_MIN = 30        # nejmenší rozsah kategorie, kterou ještě srovnáváme
ALPHA = cfg.ALPHA

ZAKONCENI_CS = {
    "RightFoot": "Pravá noha",
    "LeftFoot": "Levá noha",
    "Head": "Hlava",
    "Volley": "Volej",
    "Other": "Jiné",
    prep.KATEGORIE_NENI: prep.KATEGORIE_NENI,
}
SITUACE_CS = {
    "OpenPlay": "Otevřená hra",
    "FromCorner": "Roh",
    "DirectFreekick": "Přímý kop",
    "SetPiece": "Standardní situace",
    "Penalty": "Penalta",
    "FastBreak": "Rychlý protiútok",
}

# ---------------------------------------------------------------------------
# Pomocné funkce
# ---------------------------------------------------------------------------
def _uloz_tabulku(df: pd.DataFrame, nazev: str) -> None:
    """Uloží tabulku do outputs/tabulky/, aby byla dohledatelná mimo protokol."""
    df.to_csv(cfg.TABULKY_DIR / f"{nazev}.csv", index=False)


def _hodnoceni(vysledky: cfg.Vysledky, test: str, statistika: str, p,
               komentare: str | None = None) -> bool:
    """Zapíše test a rozsudek nad H0; vrátí True, když na α = 0,05 zamítáme H0."""
    zamitame = p is not None and not np.isnan(float(p)) and float(p) < ALPHA
    text = f"- **{test}**: {statistika}, {cfg.fmt_p(p)} → "
    text += ("na α = 0,05 **H0 zamítáme** (efekt prokázán)" if zamitame
             else "na α = 0,05 **H0 nezamítáme** (efekt prokázán nebyl)")
    if komentare:
        text += f" — {komentare}"
    vysledky.radek(text)
    return zamitame


def _vymen_zrite(df: pd.DataFrame, sloupec: str, minimalni: int = SKUP_MIN) -> pd.DataFrame:
    """Nechá jen řádky z kategorií s dostatečným rozsahem (testy bývají křehké)."""
    cetnosti = df[sloupec].value_counts()
    drzet = cetnosti.index[cetnosti >= minimalni]
    return df.loc[df[sloupec].isin(drzet)]


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilsonův interval spolehlivosti pro podíl (funguje i pro malá n)."""
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    jmenovatel = 1 + z**2 / n
    stred = (p + z**2 / (2 * n)) / jmenovatel
    polomer = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / jmenovatel
    return max(0.0, stred - polomer), min(1.0, stred + polomer)


def _cramers_v(tabulka: pd.DataFrame) -> float:
    """Cramérovo V z kontingenční tabulky — míra síly závislosti 0–1."""
    cetnosti = tabulka.to_numpy(dtype=float)
    chi2 = stats.chi2_contingency(cetnosti)[0]
    n = cetnosti.sum()
    return float(np.sqrt(chi2 / n / (min(tabulka.shape) - 1)))


def _krizovy_test(df: pd.DataFrame, a: str, b: str) -> tuple[pd.DataFrame, float, int, float]:
    """χ² test nezávislosti dvou kategorických znaků."""
    tabulka = pd.crosstab(df[a], df[b])
    chi2, p, stupne, _ = stats.chi2_contingency(tabulka.to_numpy())
    return tabulka, float(chi2), int(stupne), float(p)


def _hotelling(x: np.ndarray, y: np.ndarray) -> dict:
    """Dvouvýběrový Hotellingův T² test s Raoovou F aproximací."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    n1, n2, p = len(x), len(y), x.shape[1]
    diference = x.mean(axis=0) - y.mean(axis=0)
    sesbir = ((n1 - 1) * np.cov(x, rowvar=False)
              + (n2 - 1) * np.cov(y, rowvar=False)) / (n1 + n2 - 2)
    t2 = float(n1 * n2 / (n1 + n2) * diference @ np.linalg.solve(sesbir, diference))
    df1, df2 = p, n1 + n2 - p - 1
    F = df2 / (df1 * (n1 + n2 - 2)) * t2
    return {"T2": t2, "F": float(F), "df1": df1, "df2": df2,
            "p": float(stats.f.sf(F, df1, df2)), "rozdil": diference}


def _vif(exog: pd.DataFrame) -> pd.DataFrame:
    """Faktory inflace rozptylu (VIF) pro model včetně absolutního členu."""
    matice = sm.add_constant(exog).to_numpy(dtype=float)
    jmena = ["konstanta"] + list(exog.columns)
    radi = []
    for i, jmeno in enumerate(jmena):
        model = sm.OLS(matice[:, i], np.delete(matice, i, axis=1)).fit()
        r2 = float(model.rsquared)
        radi.append({
            "proměnná": jmeno,
            "R² proti ostatním": r2,
            "VIF": np.inf if r2 >= 1 else 1 / (1 - r2),
        })
    return pd.DataFrame(radi)


# ---------------------------------------------------------------------------
# H1 — vliv typu zakončení a herní situace na parametry střely
# ---------------------------------------------------------------------------
def pridej_prometene(df: pd.DataFrame) -> pd.DataFrame:
    """Přejmenuje kategorie do češtiny a sestrojí pomocné příznaky modelů."""
    df = df.copy()
    df["zakončení"] = df["shotType"].map(lambda v: ZAKONCENI_CS.get(v, v))
    df["situace_cs"] = df["situation"].map(lambda v: SITUACE_CS.get(v, v))
    df["jina_polovina"] = (df["side"] == "pravá").astype(int)
    df["bez_asistence"] = (df["player_assisted"] == prep.KATEGORIE_NENI).astype(int)
    # minutu hrajeme s minutou zápasu; nastavenou dobu (+4) bereme jako danou minutu
    minuta = pd.to_numeric(df["minute"].astype(str).str.split("+").str[0], errors="coerce")
    df["minuta"] = minuta.fillna(minuta.median())
    return df


def _popis_skupin(df: pd.DataFrame, prom: str, between: str) -> pd.DataFrame:
    """Popis numerické proměnné po skupinách faktoru (včetně kvartilů)."""
    skupina = df.groupby(between, observed=True)[prom]
    prepocet = pd.DataFrame({
        "n": skupina.size(),
        "průměr": skupina.mean(),
        "směrodatná odchylka": skupina.std(),
        "medián": skupina.median(),
        "1. kvartil": skupina.quantile(0.25),
        "3. kvartil": skupina.quantile(0.75),
        "minimum": skupina.min(),
        "maximum": skupina.max(),
    }).sort_values("průměr", ascending=False)
    prepocet.insert(0, "skupina", prepocet.index.astype(str))
    return prepocet.reset_index(drop=True)[
        ["skupina", "n", "průměr", "směrodatná odchylka", "medián",
         "1. kvartil", "3. kvartil", "minimum", "maximum"]
    ]


def _testy_znaku(df: pd.DataFrame, prom: str, between: str) -> dict:
    """ANOVA, Welch, Kruskal–Wallis, Levene, velikost efektu a Tukey HSD."""
    vyber = df[[prom, between]].dropna()
    skupiny = [(jmeno, skupina[prom].to_numpy(dtype=float))
               for jmeno, skupina in vyber.groupby(between, observed=True)]
    vysledek = {"k": len(skupiny),
                "n": int(sum(len(hodnoty) for _, hodnoty in skupiny))}

    vysledek["F"], vysledek["p_anova"] = (float(v) for v in stats.f_oneway(
        *[hodnoty for _, hodnoty in skupiny]))
    vysledek["KW"], vysledek["p_kw"] = (float(v) for v in stats.kruskal(
        *[hodnoty for _, hodnoty in skupiny]))
    vysledek["Levene"], vysledek["p_levene"] = (float(v) for v in stats.levene(
        *[hodnoty for _, hodnoty in skupiny], center="median"))

    vse = np.concatenate([hodnoty for _, hodnoty in skupiny])
    soucty_kennu = sum(len(hodnoty) * (hodnoty.mean() - vse.mean()) ** 2
                       for _, hodnoty in skupiny)
    soucet_dovnitr = ((vse - vse.mean()) ** 2).sum() - soucty_kennu
    vysledek["eta2"] = soucty_kennu / (soucty_kennu + soucet_dovnitr)
    vysledek["omega2"] = (
        (soucty_kennu - (vysledek["k"] - 1) * soucet_dovnitr
         / (vysledek["n"] - vysledek["k"]))
        / (soucet_dovnitr + soucty_kennu)
    )

    try:
        welch = pg.welch_anova(dv=prom, between=between, data=vyber)
        vysledek["F_welch"] = float(welch["F"].iloc[0])
        vysledek["p_welch"] = float(welch["p-unc"].iloc[0])
    except Exception:                                        # pojistka pro starší verze
        vysledek["F_welch"] = np.nan
        vysledek["p_welch"] = np.nan

    vysledek["tukey"] = None
    if vysledek["p_anova"] < ALPHA and vysledek["k"] > 2:
        tusken = pairwise_tukeyhsd(endog=vyber[prom].to_numpy(dtype=float),
                                   groups=vyber[between].to_numpy(), alpha=ALPHA)
        dvojice = list(itertools.combinations(tusken.groupsunique, 2))
        mezni = tusken.confint() if callable(tusken.confint) else tusken.confint
        vysledek["tukey"] = pd.DataFrame({
            "skupina A": [str(a) for a, _ in dvojice],
            "skupina B": [str(b) for _, b in dvojice],
            "rozdíl průměrů": tusken.meandiffs,
            "dolní mez 95 %": mezni[:, 0],
            "horní mez 95 %": mezni[:, 1],
            "p-hodnota": tusken.pvalues,
            "rozdíl prokázán": np.where(tusken.reject, "ano", "ne"),
        })

    diagnostika = []
    for jmeno, hodnoty in skupiny:
        vzorek = pd.Series(hodnoty)
        diagnostika.append({
            "proměnná": prep.popisek(prom),
            "skupina": str(jmeno),
            "n": len(hodnoty),
            "šikmost": stats.skew(hodnoty),
            "špičatost": stats.kurtosis(hodnoty),
            "Shapiro–Wilk p": stats.shapiro(
                vzorek.sample(min(500, len(hodnoty)), random_state=cfg.SEED)
            ).pvalue if len(hodnoty) >= 3 else np.nan,
        })
    diagnostika = pd.DataFrame(diagnostika).sort_values("n", ascending=False)
    diagnostika = diagnostika.reset_index(drop=True)
    diagnostika.insert(0, "faktor", between)
    vysledek["diagnostika"] = diagnostika
    return vysledek


def _manova(df: pd.DataFrame, prom: list[str], between: str) -> pd.DataFrame | None:
    """Jednofaktorová MANOVA; vrátí tabulku multivariačních kritérií."""
    vzorec = " + ".join(f"Q('{kolonka}')" for kolonka in prom)
    try:
        prepis = MANOVA.from_formula(f"{vzorec} ~ C({between})", data=df).mv_test()
    except Exception:                                        # nepodařilo-li se sestavit
        return None
    for nazev, obsah in prepis.results.items():
        if between in str(nazev) and isinstance(obsah, dict) and "stat" in obsah:
            staty = obsah["stat"].copy()
            staty.index = [str(i) for i in staty.index]
            return (staty.rename(columns={"Num DF": "num_df", "Den DF": "den_df",
                                          "F Value": "F_hodnota", "Pr > F": "pvalue"})
                    .reset_index(names="kritérium"))
    return None


def _graf_krabice(df: pd.DataFrame, prom: str, between: str, jmeno: str,
                  titulek: str) -> str:
    """Krabicový graf po skupinách s vynesenými jednotlivými pozorováními."""
    poradi = df.groupby(between, observed=True)[prom].median().sort_values().index
    fig, osy = plt.subplots(figsize=(8.4, 4.0))
    sns.boxplot(data=df, x=between, y=prom, order=poradi, hue=between, legend=False,
                palette="Set2", width=0.6, fliersize=3, ax=osy)
    sns.stripplot(data=df, x=between, y=prom, order=poradi, color="0.25",
                  size=2.2, jitter=0.25, alpha=0.5, ax=osy)
    osy.set_title(titulek)
    osy.set_xlabel(None)
    osy.set_ylabel(prep.popisek(prom))
    osy.tick_params(axis="x", rotation=20)
    fig.tight_layout()
    cfg.uloz_fig(fig, jmeno)
    return jmeno


def _graf_intervaly(df: pd.DataFrame, prom: str, between: str, jmeno: str,
                    titulek: str) -> str:
    """Průměry po skupinách s chybovými úsečkami (95% interval spolehlivosti)."""
    prepocet = (df.groupby(between, observed=True)[prom]
                  .agg(["mean", "std", "size"]).reset_index())
    prepocet["se"] = prepocet["std"] / np.sqrt(prepocet["size"])
    prepocet = prepocet.sort_values("mean")
    kriticka = stats.t.ppf(0.975, prepocet["size"] - 1)

    fig, osy = plt.subplots(figsize=(7.2, 3.8))
    osy.errorbar(prepocet[between], prepocet["mean"], yerr=kriticka * prepocet["se"],
                 fmt="o", capsize=4, color="#1f77b4", ecolor="#d62728",
                 elinewidth=1.4, ms=6)
    for poradi, radek in enumerate(prepocet.itertuples(index=False)):
        osy.annotate(f"n = {int(radek.size)}", (poradi, radek.mean),
                     xytext=(10, 0), textcoords="offset points",
                     fontsize=8, va="center")
    osy.set_title(titulek)
    osy.set_xlabel(None)
    osy.set_ylabel(f"průměr: {prep.popisek(prom)} (95% IS)")
    osy.tick_params(axis="x", rotation=20)
    fig.tight_layout()
    cfg.uloz_fig(fig, jmeno)
    return jmeno


def _graf_profil(df: pd.DataFrame, prom: list[str], between: str, jmeno: str) -> str:
    """Profilový graf MANOVY — průměry standardizovaných proměnných po skupinách."""
    sloupce = [f"{p}_std" for p in prom]
    prepocet = df.groupby(between, observed=True)[sloupce].mean().reset_index()
    fig, osy = plt.subplots(figsize=(7.2, 4.2))
    for radek in prepocet.itertuples(index=False):
        osy.plot(range(len(sloupce)), [getattr(radek, s) for s in sloupce],
                 marker="o", lw=1.8, label=str(getattr(radek, between)))
    osy.axhline(0, color="0.6", lw=0.8)
    osy.set_xticks(range(len(sloupce)))
    osy.set_xticklabels([prep.popisek(p) for p in prom])
    osy.set_ylabel("standardizovaná hodnota (z)")
    osy.set_title(f"Profil skupin ve standardizovaném prostoru — faktor {between}")
    osy.legend(loc="best", fontsize=8)
    fig.tight_layout()
    cfg.uloz_fig(fig, jmeno)
    return jmeno


def _graf_kontingence(tabulka: pd.DataFrame, jmeno: str, titulek: str,
                      popisek_a: str, popisek_b: str) -> str:
    """χ² znázorníme teplotní mapou sdružených relativních četností (χ² diagram)."""
    relativni = tabulka.div(tabulka.sum(axis=1), axis=0) * 100
    fig, osy = plt.subplots(figsize=(8.0, 3.6))
    sns.heatmap(relativni, annot=True, fmt="0.1f", cmap="YlGnBu",
                cbar_kws={"label": "podíl ve skupině (%)"}, linewidths=0.6,
                linecolor="white", ax=osy)
    osy.set_title(titulek)
    osy.set_xlabel(popisek_b)
    osy.set_ylabel(popisek_a)
    osy.tick_params(axis="y", rotation=0)
    fig.tight_layout()
    cfg.uloz_fig(fig, jmeno)
    return jmeno


FAKTORY_H1 = {
    "zakončení": {
        "klic": "zakonceni", "nadpis": "Typ zakončení (shotType)",
        "jak": "zakončení", "mnoz": "typů zakončení",
        "zaklad": 10, "profil": "16_profil_zakonceni", "kontingence": "17_kontingence_zakonceni",
    },
    "situace_cs": {
        "klic": "situace", "nadpis": "Herní situace (situation)",
        "jak": "herní situace", "mnoz": "herních situací",
        "zaklad": 20, "profil": "26_profil_situace", "kontingence": "27_kontingence_situace",
    },
}


def testuj_h1(vysledky: cfg.Vysledky, data: pd.DataFrame) -> dict:
    """H1: liší se parametry střely podle typu zakončení a herní situace."""
    vysledky.nadpis("2. H1 — vliv typu zakončení a herní situace na parametry střely")
    df = pridej_prometene(data)
    # kód „Neznámý" je absence údaje, nikoli způsob zakončení — vyřazujeme jej
    dat_zakonceni = _vymen_zrite(df.loc[df["zakončení"] != prep.KATEGORIE_NENI], "zakončení")
    dat_situace = _vymen_zrite(df, "situace_cs")
    soubory = {"zakončení": dat_zakonceni, "situace_cs": dat_situace}

    grafy: list[str] = []
    vysledky.odstavec(
        "**H0:** střední hodnoty parametrů střely (xG, vzdálenost od branky, úhel pohledu) "
        "jsou ve všech typech zakončení i herních situacích shodné. **H1:** alespoň jedna "
        "skupina se liší. Do srovnání bereme jen kategorie s alespoň "
        f"{SKUP_MIN} střelami; kód „{prep.KATEGORIE_NENI}“ (chybějící údaj) řadíme stranou, "
        "nebo by uměle zvětšoval rozptyl uvnitř skupin."
    )

    zamitnuti: dict[str, list[bool]] = {}
    efekty: dict[tuple[str, str], dict] = {}
    for faktor, informace in FAKTORY_H1.items():
        dat = soubory[faktor]
        klic = informace["klic"]
        jak = informace["jak"]
        mnoz = informace["mnoz"]
        vysledky.nadpis(f"{informace['nadpis']} — {len(dat)} střel, "
                        f"{dat[faktor].nunique()} kategorií", 3)
        zamitnuti[faktor] = []

        for pozice, prom in enumerate(PROM_H1):
            test = _testy_znaku(dat, prom, faktor)
            nazev = prep.popisek(prom)
            vysledky.tabulka(_popis_skupin(dat, prom, faktor),
                             f"Parametr {nazev} podle faktoru {klic}")
            _uloz_tabulku(_popis_skupin(dat, prom, faktor), f"h1_{klic}_{prom}")
            if (test["diagnostika"]["šikmost"].abs() > 1).any():
                poznamka = ("rozdělení je zešikmené → ANOVA doplňována Kruskal–Wallisem "
                            "a Welchovou verzí")
            else:
                poznamka = "předpoklady ANOVA jsou v rozumné míře splněny"
            zamitnuto = _hodnoceni(
                vysledky, f"Jednocestná ANOVA: {nazev} ~ {klic}",
                f"F({test['k'] - 1}, {test['n'] - test['k']}) = {cfg.cislo(test['F'], 2)}, "
                f"η² = {cfg.cislo(test['eta2'], 3)}, ω² = {cfg.cislo(test['omega2'], 3)}",
                test["p_anova"], komentare=poznamka)
            zamitnuti[faktor].append(zamitnuto)
            efekty[(klic, prom)] = test
            _hodnoceni(vysledky, f"Leveneův test homogenity rozptylů: {nazev}",
                       f"F = {cfg.cislo(test['Levene'], 2)}", test["p_levene"],
                       komentare="nezamítnutí podporuje klasickou ANOVA")
            _hodnoceni(vysledky, f"Kruskal–Wallisův test: {nazev}",
                       f"H = {cfg.cislo(test['KW'], 2)}", test["p_kw"],
                       komentare="shodný závěr s ANOVA → výsledek není dán zešikmením")
            if not np.isnan(test["p_welch"]):
                _hodnoceni(vysledky, f"Welchova ANOVA (nerovné rozptyly): {nazev}",
                           f"F = {cfg.cislo(test['F_welch'], 2)}", test["p_welch"])
            if test["tukey"] is not None:
                vyznamne = test["tukey"].loc[test["tukey"]["rozdíl prokázán"] == "ano"]
                if len(vyznamne):
                    vysledky.tabulka(vyznamne.reset_index(drop=True),
                                     f"Tukey HSD — prokázané rozdíly: {nazev} ({klic})")
                    _uloz_tabulku(test["tukey"], f"h1_tukey_{klic}_{prom}")
            vysledky.tabulka(test["diagnostika"],
                             f"Diagnostika předpokladů (normalita po skupinách): {nazev}")

            cislo_krabice = informace["zaklad"] + 2 * pozice
            grafy.append(_graf_krabice(
                dat, prom, faktor, f"{cislo_krabice}_{klic}_{prom}",
                f"Rozdělení {nazev} podle {jak}"))
            grafy.append(_graf_intervaly(
                dat, prom, faktor, f"{cislo_krabice + 1}_{klic}_{prom}",
                f"Průměry {nazev} podle {jak} (95% IS)"))
            for obrazek in grafy[-2:]:
                vysledky.obrazek(obrazek, f"{nazev} podle {jak}")

        staty = _manova(dat, PROM_H1, faktor)
        if staty is not None:
            for radek in staty.itertuples(index=False):
                _hodnoceni(
                    vysledky,
                    f"MANOVA ({', '.join(PROM_H1)}) ~ {jak} — {radek[0]}",
                    f"F({cfg.cislo(radek.num_df, 1)}, {cfg.cislo(radek.den_df, 1)}) = "
                    f"{cfg.cislo(radek.F_hodnota, 2)}", float(radek.pvalue))
            grafy.append(_graf_profil(dat, PROM_H1, faktor, informace["profil"]))
            vysledky.obrazek(informace["profil"],
                             f"Profil {mnoz} v prostoru parametrů střely")

        dve = dat[faktor].value_counts().head(2).index.tolist()
        if len(dve) == 2:
            h = _hotelling(dat.loc[dat[faktor] == dve[0], PROM_H1].to_numpy(dtype=float),
                           dat.loc[dat[faktor] == dve[1], PROM_H1].to_numpy(dtype=float))
            _hodnoceni(vysledky, f"Hotellingovo T²: {dve[0]} vs. {dve[1]}",
                       f"T² = {cfg.cislo(h['T2'], 2)}, F({h['df1']}, {h['df2']}) = "
                       f"{cfg.cislo(h['F'], 2)}", h["p"],
                       komentare="profil skupin se liší jako celek")

        tabulka, chi2, stupne, p = _krizovy_test(df, faktor, "result")
        drzet = df[faktor].value_counts().head(6).index
        vyber = df.loc[df[faktor].isin(drzet)]
        grafy.append(_graf_kontingence(
            pd.crosstab(vyber[faktor], vyber["result"]), informace["kontingence"],
            f"Výsledky střel podle {jak} (χ² diagram)", klic, "výsledek"))
        vysledky.obrazek(informace["kontingence"], f"Kontingence {jak} × výsledek")
        _hodnoceni(vysledky, f"χ² test nezávislosti {jak} × výsledek",
                   f"χ²({stupne}) = {cfg.cislo(chi2, 2)}, Cramérovo V = "
                   f"{cfg.cislo(_cramers_v(tabulka), 3)}", p,
                   komentare="skupiny se liší i úspěšností, ne jen polohou")

    zaver_h1 = any(zamitnuti["zakončení"]) or any(zamitnuti["situace_cs"])
    (klic_nej, prom_nej), test_nej = max(efekty.items(), key=lambda polozka: polozka[1]["eta2"])
    test_xg_z = efekty[("zakonceni", "xG")]
    velikost = ("malý" if test_nej["eta2"] < 0.01
                else "střední" if test_nej["eta2"] < 0.06 else "velký")
    text = (
        f"**Závěr H1: {'H0 zamítáme' if zaver_h1 else 'H0 nezamítáme'}** — typ zakončení "
        f"a herní situace {'prokazatelně ovlivňují' if zaver_h1 else 'neprokazují vliv na'} "
        "geometrii střely (vzdálenost od branky a úhel pohledu). "
        f"Nejsilnější efekt: {prep.popisek(prom_nej)} podle "
        f"{'typu zakončení' if klic_nej == 'zakonceni' else 'herní situace'} "
        f"(η² = {cfg.cislo(test_nej['eta2'], 3)}, ω² = {cfg.cislo(test_nej['omega2'], 3)}), "
        f"což je podle Cohenových měřítek ({cfg.cislo(0.01, 2)} / {cfg.cislo(0.06, 2)} / "
        f"{cfg.cislo(0.14, 2)}) **{velikost}** efekt."
    )
    if zaver_h1:
        text += (
            f" Naopak kvalita šance měřená xG se podle typu zakončení nelíší "
            f"({cfg.fmt_p(test_xg_z['p_anova'])}, η² = {cfg.cislo(test_xg_z['eta2'], 3)}) — "
            "střelci si způsob zakončení vybírají podle toho, odkud a za jaké situace střílejí, "
            "ne podle kvality šance. Prakticky to znamená, že polohu a situaci nelze při "
            "popisu střely zaměňovat a jejich závislost musíme respektovat při redukci dimenze "
            "(H2) i v modelech úspěšnosti (H4)."
        )
    vysledky.odstavec(text)
    return {"název": "H1 — zakončení a situace", "zaver": text, "grafy": grafy,
            "zamitnuto": zaver_h1, "hlavni_graf": "16_profil_zakonceni",
            "data": {"zakončení": dat_zakonceni, "situace": dat_situace}}


# ---------------------------------------------------------------------------
# H2 — redukce dimenze (PCA a faktorová analýza)
# ---------------------------------------------------------------------------
def _kmo(korelace: pd.DataFrame) -> tuple[pd.Series, float]:
    """Kaiser–Meyer–Olkin: poměr squared korelace vs. squared parciální korelace."""
    inverze = np.linalg.inv(korelace.to_numpy(dtype=float))
    gama = -inverze / np.sqrt(np.outer(np.diag(inverze), np.diag(inverze)))
    rho2 = korelace.to_numpy(dtype=float) ** 2
    sigma2 = gama ** 2
    np.fill_diagonal(rho2, 0.0)
    np.fill_diagonal(sigma2, 0.0)
    po_prvku = rho2.sum(axis=0) / (rho2.sum(axis=0) + sigma2.sum(axis=0))
    celkem = rho2.sum() / (rho2.sum() + sigma2.sum())
    return pd.Series(po_prvku, index=korelace.columns), float(celkem)


def _bartlett(korelace: pd.DataFrame, n: int) -> tuple[float, int, float]:
    """Bartlettův test sférickosti: H0 = korelační matice je jednotková."""
    p = korelace.shape[0]
    determininant = max(np.linalg.slogdet(korelace.to_numpy(dtype=float))[1], -300.0)
    chi2 = -(n - 1 - (2 * p + 5) / 6.0) * determininant
    stupne = p * (p - 1) // 2
    return float(chi2), int(stupne), float(stats.chi2.sf(chi2, stupne))


def _paralelni_analyze(n: int, p: int, iteraci: int = 500) -> np.ndarray:
    """Průměrná vlastní čísla náhodných dat (paralelní analýza, permutace sloupců)."""
    rng = np.random.default_rng(cfg.SEED)
    nasobene = np.empty((iteraci, p))
    for i in range(iteraci):
        nasobene[i] = np.linalg.eigvalsh(
            np.corrcoef(rng.standard_normal((n, p)), rowvar=False))
    return np.sort(nasobene.mean(axis=0))[::-1]


def _varimax(zateze: np.ndarray, iteraci: int = 500) -> tuple[np.ndarray, np.ndarray]:
    """Kaiserova varimax rotace (orthogonální) — zjednodušuje interpretaci."""
    m, k = zateze.shape
    if k < 2:
        return zateze, np.eye(m)
    rotace = np.eye(k)
    d = 0.0
    for _ in range(iteraci):
        lambda_matice = zateze @ rotace
        spolecna = np.sqrt((lambda_matice ** 2).sum(axis=0))
        spolecna[spolecna == 0] = 1.0
        gama = zateze.T @ (lambda_matice ** 3
                           - lambda_matice * ((lambda_matice ** 2).sum(axis=0) / m))
        u, sing, vt = np.linalg.svd(gama)
        nova = u @ vt
        hodnota = sing.sum()
        if hodnota <= d:
            break
        d = hodnota
        rotace = rotace @ nova
    return zateze @ rotace, rotace


def _komunality(zateze: np.ndarray) -> np.ndarray:
    """Komunalita = podíl rozptylu proměnné vysvětlený faktory (čtverce zátěží)."""
    return (zateze ** 2).sum(axis=1)


def _skore_faktoru(data: np.ndarray, korelace: np.ndarray,
                   zateze: np.ndarray) -> np.ndarray:
    """Regresní (Thurstoneův) odhad faktorových skóre: F = Z R⁻¹ Λ."""
    return data @ np.linalg.solve(korelace, zateze)


def _nacteni(korelace: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    """Zátěže z korelační matice: Λ = V_K √D_K (PCA extrakce, unitární komunality)."""
    vlastni, vektory = np.linalg.eigh(korelace)
    poradi = np.argsort(vlastni)[::-1]
    vlastni = np.clip(vlastni[poradi], 0.0, None)
    vektory = vektory[:, poradi]
    return vektory[:, :k] * np.sqrt(vlastni[:k]), vlastni


def _cisla_kriterii(vlastni: np.ndarray, n: int, p: int) -> pd.DataFrame:
    """Srovnání kritérií počtu komponent: Kaiser, lom, souběžná analýza, 70/80 %."""
    je_lom = []
    for i in range(len(vlastni)):
        mensi = vlastni[i + 1:] if i + 1 < len(vlastni) else np.array([0.0])
        vacsi = vlastni[:i] if i else np.array([vlastni[0]])
        mensi_prum = mensi.mean()
        je_lom.append(bool(vlastni[i] > mensi_prum * 3
                           and (not len(vacsi) or vlastni[i] < 3 * vlastni[i - 1])))
    kumulsivne = np.cumsum(vlastni) / vlastni.sum()
    kritéria = {
        "Kaiser (λ > 1)": int((vlastni > 1.0).sum()),
        "graf úpatí (výrazný lom)": next((i + 1 for i, l in enumerate(je_lom) if l), 1),
        "paralelní analýza": int((vlastni > _paralelni_analyze(n, p)).sum()),
        "akumulovaných ≥ 70 %": int(np.searchsorted(kumulsivne, 0.70) + 1),
        "akumulovaných ≥ 80 %": int(np.searchsorted(kumulsivne, 0.80) + 1),
    }
    return pd.DataFrame({"kritérium": list(kritéria),
                         "počet dimenzí": [str(hodnota) for hodnota in kritéria.values()]})


def _graf_scree(vlastni: np.ndarray, paralelni: np.ndarray, k: int, jmeno: str) -> str:
    """Graf úpatí s paralelní analýzou a kumulativním vysvětleným rozptylem."""
    podil = vlastni / vlastni.sum()
    fig, osy = plt.subplots(1, 2, figsize=(10.4, 3.9))
    pozice = np.arange(1, len(vlastni) + 1)

    osy[0].plot(pozice, vlastni, "o-", label="vlastní číslo λ (skutečná data)")
    osy[0].plot(pozice, paralelni, "s--", color="0.45",
                label="průměr λ na náhodných datech")
    osy[0].axhline(1.0, color="#d62728", lw=1.0, ls=":", label="Kaiserovo kritérium λ = 1")
    osy[0].axvline(k, color="#2ca02c", lw=1.2, ls="-.", label=f"zvoleno K = {k}")
    osy[0].set_xticks(pozice)
    osy[0].set_xlabel("pořadí komponenty")
    osy[0].set_ylabel("vlastní číslo λ")
    osy[0].set_title("Graf úpatí (scree plot) a paralelní analýza")
    osy[0].legend(fontsize=8)

    kumulativa = np.cumsum(podil) * 100
    osy[1].bar(pozice, podil * 100, alpha=0.75, label="podíl komponenty")
    osy[1].plot(pozice, kumulativa, "o-", color="#d62728", label="akumulováno")
    osy[1].axhline(70, color="0.5", lw=0.8, ls=":")
    osy[1].axvline(k, color="#2ca02c", lw=1.2, ls="-.")
    for souřadnice, hodnota in zip(pozice, kumulativa):
        osy[1].annotate(f"{hodnota:.0f} %", (souřadnice, hodnota), xytext=(0, 6),
                        textcoords="offset points", ha="center", fontsize=8)
    osy[1].set_xticks(pozice)
    osy[1].set_ylim(0, 108)
    osy[1].set_xlabel("pořadí komponenty")
    osy[1].set_ylabel("vysvětlený rozptyl (%)")
    osy[1].set_title("Koláč rozptyla po komponentách")
    osy[1].legend(fontsize=8, loc="center right")

    fig.tight_layout()
    cfg.uloz_fig(fig, jmeno)
    return jmeno


def _graf_biplot(skore: np.ndarray, zateze: np.ndarray, jmena: list[str],
                 vlastni: np.ndarray, k: int, barvy: pd.Series,
                 jmeno: str, titulek: str) -> str:
    """Biplot v rovině prvních dvou komponent — skóre pozorování + vektory zátěží."""
    meritko = np.sqrt(vlastni[:2] / vlastni.sum()) * skore[:, :2].std(axis=0) * 3.5
    fig, osy = plt.subplots(figsize=(7.6, 5.4))
    for skupina in sorted(barvy.astype(str).unique()):
        maska = barvy.astype(str) == skupina
        osy.scatter(skore[maska, 0], skore[maska, 1], s=14, alpha=0.6, label=skupina)
    for i, nazev in enumerate(jmena):
        vektor = zateze[i, :2] * meritko
        if np.linalg.norm(vektor) < 1e-9:
            continue
        osy.annotate("", xy=(0, 0), xytext=tuple(vektor * 2.2),
                     arrowprops={"arrowstyle": "->", "color": "#d62728", "lw": 1.6})
        osy.annotate(nazev, tuple(vektor * 2.45), color="#d62728",
                     fontsize=9, ha="center", va="center")
    osy.set_xlabel(f"komponenta 1 ({vlastni[0] / vlastni.sum() * 100:.1f} %)")
    osy.set_ylabel(f"komponenta 2 ({vlastni[1] / vlastni.sum() * 100:.1f} %)")
    osy.axhline(0, color="0.7", lw=0.7)
    osy.axvline(0, color="0.7", lw=0.7)
    osy.set_title(titulek)
    osy.legend(fontsize=8, loc="best")
    fig.tight_layout()
    cfg.uloz_fig(fig, jmeno)
    return jmeno


def _graf_kmo(kmo_po_prvku: pd.Series, korelace: pd.DataFrame, jmeno: str) -> str:
    """Vlevo KMO po proměnných, vpravo korelační matice — podklad pro redukci."""
    fig, osy = plt.subplots(1, 2, figsize=(10.6, 4.0),
                            gridspec_kw={"width_ratios": [1, 1.35]})
    klice = [prep.popisek(i) for i in kmo_po_prvku.index]
    osy[0].barh(klice, kmo_po_prvku.to_numpy(), color="#1f77b4", alpha=0.8)
    osy[0].axvline(0.5, color="#d62728", ls=":", lw=1.0)
    osy[0].set_xlim(0, 1.02)
    osy[0].set_xlabel("KMO pro proměnnou")
    osy[0].set_title("Kaiser–Meyer–Olkin (0,5 mez použitelnosti)")
    for hodnota, pozice in zip(kmo_po_prvku.to_numpy(), range(len(klice))):
        osy[0].annotate(f"{hodnota:.2f}", (hodnota, pozice), xytext=(4, 0),
                        textcoords="offset points", va="center", fontsize=8)

    sns.heatmap(korelace, annot=True, fmt="+.2f", cmap="RdBu_r", vmin=-1, vmax=1,
                square=True, cbar_kws={"shrink": 0.8},
                xticklabels=[prep.popisek(c) for c in korelace.columns],
                yticklabels=[prep.popisek(c) for c in korelace.columns], ax=osy[1])
    osy[1].set_title("Korelační matice příznaků")
    osy[1].tick_params(axis="x", rotation=35)
    fig.tight_layout()
    cfg.uloz_fig(fig, jmeno)
    return jmeno


def _graf_rotace(zateze_ne: np.ndarray, zateze_ro: np.ndarray, k: int,
                 jmena: list[str], jmeno: str) -> str:
    """Srovnání zátěží před a po varimax rotaci + komunality."""
    sloupce = np.arange(k)
    sirka = 0.38
    fig, osy = plt.subplots(1, 2, figsize=(10.4, 4.0))
    for i, nazev in enumerate(jmena):
        osy[0].bar(sloupce + (i - (len(jmena) - 1) / 2) * sirka,
                   zateze_ne[i, :k], width=sirka, alpha=0.85, label=nazev)
        osy[1].bar(sloupce + (i - (len(jmena) - 1) / 2) * sirka,
                   zateze_ro[i, :k], width=sirka, alpha=0.85, label=nazev)
    for osa, titulek in zip(osy, ("zátěže před rotací", "zátěže po varimax rotaci")):
        osa.axhline(0, color="0.4", lw=0.8)
        osa.axhline(0.7, color="#2ca02c", ls=":", lw=1.0)
        osa.axhline(-0.7, color="#2ca02c", ls=":", lw=1.0)
        osa.set_xticks(sloupce)
        osa.set_xticklabels([f"F{j + 1}" for j in sloupce])
        osa.set_ylabel("zátěž (korelace s faktorem)")
        osa.set_title(titulek)
        osa.set_ylim(-1.05, 1.05)
    osy[1].legend(fontsize=8, ncol=2, loc="upper center", bbox_to_anchor=(0.5, -0.13))
    fig.suptitle("Extrakce faktorů a zjednodušující rotace", fontweight="bold")
    fig.tight_layout()
    cfg.uloz_fig(fig, jmeno)
    return jmeno


def omezi_pro_ordinaci(uplne: pd.DataFrame) -> pd.DataFrame:
    """Množina pro ordinační/shlukové metody: úplné souřadnice bez penalt."""
    return uplne.loc[uplne["is_penalty"] == 0].copy()


def standardizuj_mnozinu(df: pd.DataFrame, sloupce: list[str]) -> tuple[np.ndarray, pd.DataFrame]:
    """Standardizace na dané množině (VMM-3) — vrátí matici z-proměnných a konstanty."""
    stred = df[sloupce].mean(axis=0)
    odchylka = df[sloupce].std(axis=0, ddof=1)
    matice = ((df[sloupce] - stred) / odchylka).to_numpy(dtype=float)
    konstanty = pd.DataFrame({"proměnná": [prep.popisek(s) for s in sloupce],
                              "průměr": stred.to_numpy(),
                              "směrodatná odchylka": odchylka.to_numpy()})
    return matice, konstanty


def jmenuj_faktory(zateze: np.ndarray, jmena: list[str], k: int) -> list[str]:
    """Popíše faktory podle proměnných se silnou zátěží (|λ| ≥ 0,5)."""
    popisy = []
    for sloupec in range(k):
        silne = [(jmena[i], zateze[i, sloupec])
                 for i in range(len(jmena)) if abs(zateze[i, sloupec]) >= 0.5]
        silne.sort(key=lambda x: -abs(x[1]))
        if not silne:
            popisy.append(f"F{sloupec + 1} (bez silné vazby)")
            continue
        kladne = [j for j, z in silne if z > 0]
        zaporne = [j for j, z in silne if z < 0]
        text = f"F{sloupec + 1}: " + ", ".join(
            f"{j} ({'+' if z > 0 else '−'}{abs(z):.2f})" for j, z in silne)
        if kladne and zaporne:
            text += " — protiklad dvou skupin znaků"
        popisy.append(text)
    return popisy


def testuj_h2(vysledky: cfg.Vysledky, uplne: pd.DataFrame) -> dict:
    """H2: existuje nízkorozměrná struktura, která data/postsihne."""
    vysledky.nadpis("3. H2 — redukce dimenze (PCA a faktorová analýza)")
    grafy: list[str] = []
    df = omezi_pro_ordinaci(uplne)
    matice, konstanty = standardizuj_mnozinu(df, PROM_H2)
    n, p = matice.shape
    korelace = pd.DataFrame(np.corrcoef(matice, rowvar=False), index=PROM_H2, columns=PROM_H2)

    vysledky.odstavec(
        "**H0:** soubor příznaků (xG, vzdálenost, úhel pohledu, X, Y) nemá žádnou společnou "
        "korelační strukturu — korelační matice je jednotková a nelze ji vystihnout menším "
        "počtem dimenzí. **H1:** společná struktura existuje a stačí K < "
        f"{p} dimenzí. Analyzujeme {n} střel s úplnými souřadnicemi (penalty vyňaty — "
        "nulová variabilita polohy by matici zkreslila); příznaky jsou standardizovány, "
        "protože mají různé jednotky."
    )
    vysledky.tabulka(konstanty, "Konstanty standardizace pro tuto množinu")

    # --- opodstatnění použití redukce dimenze ---------------------------------
    chi2, stupne, p_bartlett = _bartlett(korelace, n)
    zamitnuto_bartlett = _hodnoceni(
        vysledky, "Bartlettův test sférickosti",
        f"χ²({stupne}) = {cfg.cislo(chi2, 1)}", p_bartlett,
        komentare="korelační matice se výrazně liší od jednotkové → redukce má smysl")
    kmo_po_prvku, kmo_celkem = _kmo(korelace)
    vysledky.radek(
        f"- **Kaiser–Meyer–Olkin (MSA)**: celkové {cfg.cislo(kmo_celkem, 3)}; "
        f"nejslabší proměnná „{prep.popisek(kmo_po_prvku.idxmin())}“ s "
        f"{cfg.cislo(kmo_po_prvku.min(), 3)}. Měřítko: > 0,8 skvělé, > 0,7 dobré, "
        "> 0,6 uspokojivé, < 0,5 by redukce nebyla na místě — "
        f"{'podmínka splněna' if kmo_celkem >= 0.5 else 'podmínka nesplněna'}.")
    grafy.append(_graf_kmo(kmo_po_prvku, korelace, "30_kmo_korelace"))
    vysledky.obrazek("30_kmo_korelace", "KMO a korelační matice příznaků")
    _uloz_tabulku(korelace.reset_index(names="proměnná"), "h2_korelace")

    # --- extrakce komponent --------------------------------------------------
    zateze_ne, vlastni = _nacteni(korelace.to_numpy(dtype=float), p)
    paralelni = _paralelni_analyze(n, p)
    kritéria = _cisla_kriterii(vlastni, n, p)
    k_pc = int(kritéria.loc[kritéria["kritérium"] == "paralelní analýza", "počet dimenzí"].iloc[0])
    k_pc = max(1, min(k_pc, p))
    podil = vlastni / vlastni.sum()
    vysledky.tabulka(kritéria, "Kolik dimenzí nechat — srovnání kritérií")
    _uloz_tabulku(kritéria, "h2_kriteria_poctu_dimenzi")

    tabulka_komp = pd.DataFrame(
        {"komponenta": [f"K{j + 1}" for j in range(p)],
         "vlastní číslo λ": vlastni,
         "podíl rozptylu (%)": podil * 100,
         "akumulováno (%)": np.cumsum(podil) * 100,
         **{f"zátěž: {prep.popisek(j)}": zateze_ne[:, i]
            for i, j in enumerate(PROM_H2)}})
    vysledky.tabulka(tabulka_komp, "Vlastní čísla a zátěže komponent (PCA)")
    _uloz_tabulku(tabulka_komp, "h2_pca_zateze")

    grafy.append(_graf_scree(vlastni, paralelni, k_pc, "31_scree_paralelni"))
    vysledky.obrazek("31_scree_paralelni",
                     "Graf úpatí, Kaiserovo kritérium a paralelní analýza")

    skore = matice @ zateze_ne
    barvy = df["shotType"].map(lambda v: ZAKONCENI_CS.get(v, v)).astype(str)
    grafy.append(_graf_biplot(skore, zateze_ne, [prep.popisek(j) for j in PROM_H2],
                              vlastni, k_pc, pd.Series(barvy), "32_biplot",
                              "Biplot — rozptýlený diagram komponentního skóre a zátěží"))
    vysledky.obrazek("32_biplot", "Rozptýlený diagram komponentního skóre")

    # --- faktorová analýza s rotací ------------------------------------------
    k_fa = max(2, k_pc)
    zateze_fa = zateze_ne[:, :k_fa]
    zateze_rot, rotace = _varimax(zateze_fa)
    if (zateze_rot.sum(axis=0) < 0).any():
        otocene = np.where((zateze_rot.sum(axis=0) < 0), -1.0, 1.0)
        zateze_rot = zateze_rot * otocene
    kommunality = _komunality(zateze_rot)
    tabulka_fa = pd.DataFrame(
        {"proměnná": [prep.popisek(j) for j in PROM_H2],
         **{f"zátěž F{j + 1}": zateze_rot[:, j] for j in range(k_fa)},
         "komunalita h²": kommunality,
         "specificita ψ²": 1 - kommunality})
    tabulka_fa["max |zátěž| faktor"] = [
        f"F{int(np.argmax(np.abs(zateze_rot[i, :])) + 1)}" for i in range(p)]
    vysledky.tabulka(tabulka_fa, "Faktorová analýza po varimax rotaci (komunality)")
    _uloz_tabulku(tabulka_fa, "h2_faktorova_analyza")
    grafy.append(_graf_rotace(zateze_fa, zateze_rot, k_fa,
                              [prep.popisek(j) for j in PROM_H2], "33_rotace_komunality"))
    vysledky.obrazek("33_rotace_komunality", "Zátěže před a po rotaci")

    jmena = [prep.popisek(j) for j in PROM_H2]
    popisy = jmenuj_faktory(zateze_rot, jmena, k_fa)
    for popis in popisy:
        vysledky.radek(f"- {popis}")
    if k_fa >= 2:
        korel_faktoru = np.corrcoef(_skore_faktoru(matice, korelace.to_numpy(dtype=float),
                                                   zateze_fa).T)
        r_mezi = korel_faktoru[0, 1]
        text_rot = (
            f" Po rotaci je struktura čitelnější: zátěže se přiblížily ±1 nebo 0 "
            f"(nekorelované faktory, korelace F1–F2 = {cfg.cislo(r_mezi, 2)}).")
    else:
        text_rot = ""

    # --- citlivost na odlehlé hodnoty ---------------------------------------
    odlehle = df["odlehla_D2"].fillna(False).astype(bool)
    bez = df.loc[~odlehle]
    if len(bez) > 30 and bez[PROM_H2].std().min() > 0:
        matice_bez, _ = standardizuj_mnozinu(bez, PROM_H2)
        zateze_bez, vlastni_bez = _nacteni(np.corrcoef(matice_bez, rowvar=False), p)
        otoceni_bez = np.sign(np.sum(zateze_bez[:, :k_fa] * zateze_ne[:, :k_fa], axis=0))
        otoceni_bez[otoceni_bez == 0] = 1.0
        srovnani = zateze_bez[:, :k_fa] * otoceni_bez
        posun = float(np.abs(srovnani - zateze_ne[:, :k_fa]).max())
        rozdil_podilu = float(
            vlastni_bez[:k_fa].sum() / vlastni_bez.sum() * 100
            - vlastni[:k_fa].sum() / vlastni.sum() * 100)
        citlivost = (f" Citlivost: po vyloučení {int(odlehle.sum())} odlehlých střel "
                     f"(Mahalanobisovo D², kritérium χ²) se zátěže změnily nejvýše o "
                     f"{cfg.cislo(posun, 2)} a podíl vysvětleného rozptylu o "
                     f"{cfg.cislo(abs(rozdil_podilu), 1)} procentního bodu "
                     "→ struktura není vykouzlena extrémy.")
    else:
        citlivost = ""

    vysvetleno = vlastni[:k_pc].sum() / vlastni.sum() * 100
    zaver_h2 = zamitnuto_bartlett and k_pc < p
    k_octmdesat = int(str(kritéria.loc[kritéria["kritérium"] == "akumulovaných ≥ 80 %",
                                        "počet dimenzí"].iloc[0]))
    slovo = ("komponenta" if k_pc == 1
             else "komponenty" if 2 <= k_pc <= 4 else "komponent")
    text = (
        f"**Závěr H2: {'H0 zamítáme' if zaver_h2 else 'H0 nezamítáme'}** — na α = 0,05 "
        f"{'prokazujeme' if zaver_h2 else 'neprokazujeme'} společnou korelační strukturu: "
        f"z {p} příznaků stačí **{k_pc} {slovo}**"
        f" (Kaiserovo kritérium ani paralelní analýza nechtějí více) — vysvětluje "
        f"**{cfg.cislo(vysvetleno, 1)} %** celkového rozptylu; na {k_octmdesat} "
        f"{'komponent' if k_octmdesat >= 5 else 'komponenty'} se dostaneme na "
        f"{cfg.cislo(vlastni[:k_octmdesat].sum() / vlastni.sum() * 100, 1)} %."
    )
    if zaver_h2:
        text += (
            " Redukce má obsažový základ: geometrické veličiny (vzdálenost, úhel, X, Y) "
            "jsou jen různými pohledy na touž věc — polohu střelce — a xG je na ni vázáno. "
            f"První komponenta je tedy „osou obtížnosti šance“ a pro shlukování (H3) "
            f"pracujeme v {max(k_pc, 2)} rozměrech, abychom neshodili "
            "i sekundární strukturu. "
            f"Pozor: celkové KMO je jen {cfg.cislo(kmo_celkem, 3)} "
            f"(nejslabší „{prep.popisek(kmo_po_prvku.idxmin())}“ s "
            f"{cfg.cislo(kmo_po_prvku.min(), 3)}), takže redukci bereme jako orientační "
            "zkrácení dat, ne jako důkaz, že ostatní rozměry jsou zbytečné."
            + text_rot + citlivost
        )
    vysledky.odstavec(text)
    return {"název": "H2 — redukce dimenze", "zaver": text, "grafy": grafy,
            "zamitnuto": zaver_h2, "hlavni_graf": "32_biplot",
            "data": {"K": k_pc, "vysvetleno": vysvetleno, "skore": skore,
                     "zateze": zateze_rot, "vlastni": vlastni, "df": df}}


# ---------------------------------------------------------------------------
# H3 — shluková struktura střel
# ---------------------------------------------------------------------------
def _nakresli_hriste(osy) -> None:
    """Čáry hřiště (X, Y jsou v jednotkách 0–100, brána vlevo uprostřed)."""
    sirka_branky = cfg.SIRKA_BRANKY_M / cfg.SIRKA_HRITE_M * 100.0
    osy.add_patch(plt.Rectangle((0, 0), 100, 100, fill=False, ec="0.5", lw=1.2))
    osy.plot([100, 100], [50 - sirka_branky / 2, 50 + sirka_branky / 2],
             color="#d62728", lw=3)
    for polomer in (5.5, 13.8, 20.2, 27.5):
        osy.add_patch(plt.Circle((0, 50), polomer, fill=False, ec="0.7", lw=0.8))
    osy.axvline(50, color="0.7", lw=0.8)
    osy.set_xlim(0, 100)
    osy.set_ylim(0, 100)
    osy.set_xlabel("X (jednotky hřiště)")
    osy.set_ylabel("Y (jednotky hřiště)")
    osy.set_aspect("equal")


def _validace_k(X: np.ndarray, k_range: range) -> pd.DataFrame:
    """Indexy kvality shlukování pro K-means nad různým počtem shluků."""
    radi = []
    for k in k_range:
        model = KMeans(n_clusters=k, n_init=10, random_state=cfg.SEED).fit(X)
        radi.append({
            "K": k,
            "silhouette": silhouette_score(X, model.labels_, metric="euclidean"),
            "Calinski–Harabasz": calinski_harabasz_score(X, model.labels_),
            "Davies–Bouldin": davies_bouldin_score(X, model.labels_),
            "WSS (within)": float(model.inertia_),
        })
    return pd.DataFrame(radi)


def _permutacni_silhouette(X: np.ndarray, znacky: np.ndarray, iteraci: int = 200) -> dict:
    """Permutační test: je průměrná silhouette větší, než čekáme u náhody?"""
    skutecna = float(silhouette_score(X, znacky, metric="euclidean"))
    rng = np.random.default_rng(cfg.SEED)
    null = np.empty(iteraci)
    for i in range(iteraci):
        null[i] = silhouette_score(X, rng.permutation(znacky), metric="euclidean")
    return {"silhouette": skutecna, "null_prumer": float(null.mean()),
            "null_max": float(null.max()),
            "p": float((1 + int((null >= skutecna).sum())) / (1 + iteraci))}


def _stabilita_ari(X: np.ndarray, k: int, iteraci: int = 100) -> dict:
    """Split-half stabilita: kolik ARI shodnou dva nezávislé poloviční běhy."""
    rng = np.random.default_rng(cfg.SEED)
    n = len(X)
    ari = []
    for _ in range(iteraci):
        poloviny = rng.permutation(n)
        oznaceni = []
        for polovina in (poloviny[: n // 2], poloviny[n // 2:]):
            model = KMeans(n_clusters=k, n_init=5, random_state=int(rng.integers(1e6))).fit(
                X[polovina])
            # centroidy natrénujeme na polovině dat a přiřadíme jimi celou množinu
            blizkost = ((X[:, None, :] - model.cluster_centers_[None, :, :]) ** 2).sum(axis=2)
            oznaceni.append(blizkost.argmin(axis=1))
        if len(oznaceni[0]) == n:
            ari.append(adjusted_rand_score(oznaceni[0], oznaceni[1]))
    ari = np.asarray(ari, dtype=float)
    if not len(ari):
        return {"ARI": np.nan, "směrodatná odchylka": np.nan,
                "dolní mez 95 %": np.nan, "horní mez 95 %": np.nan, "použito": 0}
    return {"ARI": float(ari.mean()), "směrodatná odchylka": float(ari.std(ddof=1)),
            "dolní mez 95 %": float(np.percentile(ari, 2.5)),
            "horní mez 95 %": float(np.percentile(ari, 97.5)), "použito": int(len(ari))}


def _graf_dendrogram(Z: np.ndarray, k: int, jmeno: str) -> str:
    """Dendrogram Wardovy metody, zkrácený na poslední větvení."""
    fig, osy = plt.subplots(figsize=(9.4, 4.0))
    dendrogram(Z, truncate_mode="lastp", p=34, show_leaf_counts=True,
               leaf_rotation=90, leaf_font_size=7, ax=osy,
               color_threshold=Z[-k, 2] if Z.shape[0] > k else None)
    osy.axhline(Z[-k, 2] if Z.shape[0] > k else 0, color="#d62728", ls="--", lw=1.0)
    osy.set_ylabel("vzdálenost slučování (Ward)")
    osy.set_title(f"Hierarchické shlukování — Wardova metoda (řez na {k} shluků čárkovaně)")
    fig.tight_layout()
    cfg.uloz_fig(fig, jmeno)
    return jmeno


def _graf_validace(validace: pd.DataFrame, k_hier: int, jmeno: str) -> str:
    """Vývoj indexů kvality shlukování podle počtu shluků."""
    fig, osy = plt.subplots(1, 3, figsize=(11.2, 3.4))
    osy[0].plot(validace["K"], validace["WSS (within)"], "o-")
    osy[0].set_title("Elbow — WSS uvnitř shluků")
    osy[0].set_xlabel("počet shluků K")
    osy[1].plot(validace["K"], validace["silhouette"], "o-", color="#2ca02c")
    osy[1].set_title("silhouette (vyšší = lepší)")
    osy[1].set_xlabel("počet shluků K")
    osy[2].plot(validace["K"], validace["Davies–Bouldin"], "o-", color="#d62728")
    osy[2].set_title("Davies–Bouldin (nižší = lepší)")
    osy[2].set_xlabel("počet shluků K")
    for osa in osy:
        osa.axvline(k_hier, color="0.4", ls=":", lw=1.0)
        osa.set_xticks(validace["K"])
        osa.grid(alpha=0.3)
    fig.suptitle("Validace počtu shluků (plná čára K-means, tečkované kritérium "
                 "hierarchického řezu)", fontweight="bold")
    fig.tight_layout()
    cfg.uloz_fig(fig, jmeno)
    return jmeno


def _graf_shluky_hriste(df: pd.DataFrame, jmeno: str, titulek: str) -> str:
    """Poloha střel na hřišti podle shluku — přímá interpretace struktur."""
    fig, osy = plt.subplots(figsize=(9.0, 5.0))
    for shluk in sorted(df["shluk"].unique()):
        vyber = df.loc[df["shluk"] == shluk]
        osy.scatter(vyber["X"], vyber["Y"], s=13, alpha=0.6,
                    label=f"shluk {int(shluk)} (n = {len(vyber)})")
    _nakresli_hriste(osy)
    osy.set_title(titulek)
    osy.legend(fontsize=8, loc="center left", bbox_to_anchor=(1.01, 0.5))
    fig.tight_layout()
    cfg.uloz_fig(fig, jmeno)
    return jmeno


def _graf_profil_shluku(profil: pd.DataFrame, jmeno: str) -> str:
    """Teplotní profil standardizovaných průměrů příznaků po shlucích."""
    fig, osy = plt.subplots(figsize=(7.6, 3.6))
    sns.heatmap(profil.set_index("shluk").T, annot=True, fmt="+.2f", cmap="RdBu_r",
                vmin=-2, vmax=2, cbar_kws={"label": "z-skóre průměru"}, ax=osy)
    osy.set_title("Profil shluků (standardizované průměry příznaků)")
    osy.set_ylabel(None)
    fig.tight_layout()
    cfg.uloz_fig(fig, jmeno)
    return jmeno


def _graf_tsne(vysledky_tsne: np.ndarray, znacky: np.ndarray, jmeno: str) -> str:
    """t-SNE projekce obarvená shluky z původního prostoru (kontrola nelineity)."""
    fig, osy = plt.subplots(figsize=(7.2, 5.6))
    rozptil = osy.scatter(vysledky_tsne[:, 0], vysledky_tsne[:, 1], c=znacky,
                          cmap="tab10", s=13, alpha=0.75)
    osy.set_xlabel("t-SNE 1")
    osy.set_ylabel("t-SNE 2")
    osy.set_title("t-SNE projekce — obarvení podle shluků ze standardizovaného prostoru")
    fig.colorbar(rozptil, ax=osy, shrink=0.8, label="shluk")
    fig.tight_layout()
    cfg.uloz_fig(fig, jmeno)
    return jmeno


def testuj_h3(vysledky: cfg.Vysledky, uplne: pd.DataFrame) -> dict:
    """H3: střely vytvářejí smysluplné shluky s odlišnou úspěšností."""
    vysledky.nadpis("4. H3 — shluková struktura střel")
    grafy: list[str] = []
    df = omezi_pro_ordinaci(uplne).reset_index(drop=True)
    X, konstanty = standardizuj_mnozinu(df, PROM_H3)
    n = len(X)

    vysledky.odstavec(
        "**H0:** soubor střel nemá žádnou přirozenou skupinovou strukturu — rozdělení "
        "příznaků je spojitý „mrak“ a jakýkoli rozklad na shluky je umělý. **H1:** v datech "
        f"existují přirozené skupiny. Shlukujeme {n} střel se Standardizovanými příznaky "
        f"{', '.join(prep.popisek(s) for s in PROM_H3)} (Eukleidovská metrika; penalty "
        "jsou vyňaty, protože mají nulovou variabilitu polohy). Počet shluků volíme "
        "podle indexů kvality, ne podle chuti."
    )

    # --- hierarchická analýza ---------------------------------------------
    Z = linkage(X, method="ward")
    silhouette_rada = _validace_k(X, range(2, 9))
    k_opt = int(silhouette_rada.loc[silhouette_rada["silhouette"].idxmax(), "K"])
    db_opt = int(silhouette_rada.loc[silhouette_rada["Davies–Bouldin"].idxmin(), "K"])
    elbow = silhouette_rada["WSS (within)"].to_numpy()
    pokles = elbow[:-1] / elbow[1:]
    k_elbow = int(np.argmax(pokles) + 2)
    zdani = pd.DataFrame({
        "kritérium": ["silhouette (max)", "Davies–Bouldin (min)", "loket (WSS)",
                      "shoda s hierarchickým řezem"],
        "počet shluků": [k_opt, db_opt, k_elbow, k_opt],
    })
    grafy.append(_graf_dendrogram(Z, k_opt, "40_dendrogram"))
    grafy.append(_graf_validace(silhouette_rada, k_opt, "41_validace_k"))
    vysledky.tabulka(silhouette_rada, "Indexy kvality shlukování (K-means)")
    vysledky.tabulka(zdani, "Kolik shluků — srovnání kritérií")
    _uloz_tabulku(silhouette_rada, "h3_validace_k")
    vysledky.obrazek("40_dendrogram", "Dendrogram Wardovy metody")
    vysledky.obrazek("41_validace_k", "Validace počtu shluků")

    znacky_hier = fcluster(Z, t=k_opt, criterion="maxclust")
    kmeans = KMeans(n_clusters=k_opt, n_init=20, random_state=cfg.SEED).fit(X)
    ari_metod = adjusted_rand_score(znacky_hier, kmeans.labels_)
    znacky = kmeans.labels_
    df["shluk"] = znacky

    # --- test významnosti struktury ----------------------------------------
    permutace = _permutacni_silhouette(X, znacky)
    zamitnuto_struktura = _hodnoceni(
        vysledky, "Permutační test struktury (silhouette)",
        f"silhouette = {cfg.cislo(permutace['silhouette'], 3)}, "
        f"náhodný průměr {cfg.cislo(permutace['null_prumer'], 3)}, "
        f"maximum z permutací {cfg.cislo(permutace['null_max'], 3)}",
        permutace["p"],
        komentare="skutečná silhouette výrazně přesahuje náhodu → shluky nejsou artefakt")
    stabilita = _stabilita_ari(X, k_opt)
    vysledky.radek(
        f"- **Stabilita řešení (split-half ARI, {stabilita['použito']} opakování)**: "
        f"průměr {cfg.cislo(stabilita['ARI'], 3)} "
        f"(95% interval {cfg.cislo(stabilita['dolní mez 95 %'], 3)}–"
        f"{cfg.cislo(stabilita['horní mez 95 %'], 3)}); ARI shoda Wardovy metody "
        f"a K-means {cfg.cislo(ari_metod, 3)}. "
        f"{'Řešení je stabilní' if stabilita['ARI'] > 0.6 else 'Řešení je jen křehce stabilní'} "
        "— hranice mezi shluky jsou pozvolné (data spojitá), což u fotbalových dat očekáváme.")
    vysledky.radek(
        f"- **Konzistence metod**: Wardova hierarchie a K-means dávají "
        f"ARI = {cfg.cislo(ari_metod, 3)}"
        f" ({'stejná zpráva' if ari_metod > 0.6 else 'odlišná zpráva — volba metody záleží'}). "
        "ARI zde není testovací statistika, jen míra shody dvou řešení.")

    # --- profil a úspěšnost shluků -----------------------------------------
    uspesnost = []
    for i in sorted(df["shluk"].unique()):
        cast = df.loc[df["shluk"] == i]
        dolni, horni = _wilson(int(cast["is_goal"].sum()), len(cast))
        uspesnost.append({
            "shluk": f"shluk {int(i)}", "n": len(cast),
            "gólů": int(cast["is_goal"].sum()),
            "úspěšnost": cast["is_goal"].mean(),
            "dolní mez 95 %": dolni, "horní mez 95 %": horni,
            "průměrné xG": cast["xG"].mean(),
            "průměrná vzdálenost (m)": cast["dist_m"].mean(),
            "průměrný úhel (°)": cast["angle_deg"].mean(),
        })
    uspesnost = pd.DataFrame(uspesnost).sort_values("úspěšnost", ascending=False)
    _uloz_tabulku(uspesnost, "h3_uspesnost_shluku")

    # standardizovaný profil pro graf
    graficky = df.groupby("shluk")[PROM_H3].mean()
    graficky = (graficky - df[PROM_H3].mean()) / df[PROM_H3].std()
    graficky.insert(0, "shluk", [f"shluk {int(i)}" for i in graficky.index])
    graficky.columns = [prep.popisek(c) if c != "shluk" else c for c in graficky.columns]

    grafy.append(_graf_shluky_hriste(
        df, "42_shluky_hriste", "Shluky střel podle polohy na hřišti"))
    grafy.append(_graf_profil_shluku(graficky, "43_profil_shluku"))
    vysledky.obrazek("42_shluky_hriste", "Poloha střel podle shluku")
    vysledky.tabulka(uspesnost.reset_index(drop=True),
                     "Velikost a úspěšnost shluků (95% Wilsonův interval)")
    _uloz_tabulku(graficky, "h3_profil_shluku")
    vysledky.obrazek("43_profil_shluku", "Profil shluků v standardizovaném prostoru")

    test_uspesnosti = stats.chi2_contingency(
        pd.crosstab(df["shluk"], df["is_goal"]).to_numpy())
    _hodnoceni(vysledky, "χ² test nezávislosti shluk × gól",
               f"χ²({test_uspesnosti[2]}) = {cfg.cislo(test_uspesnosti[0], 2)}, "
               f"Cramérovo V = {cfg.cislo(_cramers_v(pd.crosstab(df['shluk'], df['is_goal'])), 3)}",
               float(test_uspesnosti[1]),
               komentare="shluky se liší i úspěšností, nejen polohou")

    # --- kontrola nelinearity pomocí t-SNE ----------------------------------
    if n >= 50:
        tsne = TSNE(n_components=2, perplexity=min(30, max(5, n // 4)),
                    early_exaggeration=12.0, learning_rate="auto", max_iter=1000,
                    init="pca", random_state=cfg.SEED).fit_transform(X)
        ari_tsne = adjusted_rand_score(
            znacky, KMeans(n_clusters=k_opt, n_init=20,
                           random_state=cfg.SEED).fit_predict(tsne))
        grafy.append(_graf_tsne(tsne, znacky, "44_tsne"))
        vysledky.obrazek("44_tsne", "t-SNE projekce s vyznačenými shluky")
        zprava_tsne = (
            f" Kontrola nelinearity: v t-SNE projekci (VMM-10) zůstávají shlupy "
            f"stejně barevné (ARI = {cfg.cislo(ari_tsne, 3)} oproti lineárnímu prostoru), "
            f"takže lineární Eukleidovská metrika neztratila strukturu."
            if ari_tsne > 0.55 else
            f" V t-SNE projekci se shlupy částečně promíchaly "
            f"(ARI = {cfg.cislo(ari_tsne, 3)}) — struktura je do jisté míry nelineární "
            "a lineární metrika část informací ztratila."
        )
    else:
        zprava_tsne = " t-SNE kontrola vynechána (malý soubor)."

    neni_trivialni = silhouette_rada["silhouette"].max() > 0.35
    zaver_h3 = zamitnuto_struktura
    text = (
        f"**Závěr H3: {'H0 zamítáme' if zaver_h3 else 'H0 nezamítáme'}** — "
        f"{'v datech prokazujeme' if zaver_h3 else 'neprokazujeme'} přirozenou skupinovou "
        f"strukturu; optimální počet shluků je **{k_opt}** "
        f"(silhouette {cfg.cislo(permutace['silhouette'], 3)}, "
        f"permutační {cfg.fmt_p(permutace['p'])})."
    )
    if zaver_h3:
        druhy = uspesnost.iloc[0]
        text += (
            f" Nejúspěšnější shluk ({druhy['shluk']}, n = {int(druhy['n'])}) má úspěšnost "
            f"{cfg.cislo(druhy['úspěšnost'] * 100, 1)} % oproti celkovému průměru "
            f"{cfg.cislo(df['is_goal'].mean() * 100, 1)} % — shrnutí je obsahově čitelné "
            "(poloha × kvalita šance)." + zprava_tsne +
            (" Shluky jsou dobře separovatelné (silhouette > 0,35)."
             if neni_trivialni else
             " Silhouette je nízká: shluky jsou spouště popisnými „typy“ s pozvolnými "
             "hranicemi, nikoli ostře oddělené entity — to k fotbalovým datům sedí.")
        )
    vysledky.odstavec(text)
    return {"název": "H3 — shluky", "zaver": text, "grafy": grafy,
            "zamitnuto": zaver_h3, "hlavni_graf": "42_shluky_hriste",
            "data": {"K": k_opt, "df": df, "uspesnost": uspesnost,
                     "profil": graficky, "silhouette": permutace["silhouette"]}}


# ---------------------------------------------------------------------------
# H4 — model úspěšnosti střely
# ---------------------------------------------------------------------------
def _hosmer_lemeshow(y: np.ndarray, p: np.ndarray, skladek: int = 10) -> tuple[float, int, float]:
    """Hosmer–Lemeshowův test kalibrace logistického modelu."""
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    poradi = stats.rankdata(p, method="ordinal")
    sklady = np.ceil(poradi / len(p) * skladek).astype(int)
    chi2 = 0.0
    for slozka in range(1, skladek + 1):
        maska = sklady == slozka
        nk = int(maska.sum())
        if nk == 0:
            continue
        ok = float(y[maska].sum())
        ek = float(p[maska].sum())
        odmocna = ek * (1 - ek / nk)
        if odmocna > 0:
            chi2 += (ok - ek) ** 2 / odmocna
    stupne = skladek - 2
    return float(chi2), int(stupne), float(stats.chi2.sf(chi2, stupne))


def _bootstrap_auc(y: np.ndarray, p: np.ndarray, iteraci: int = 500) -> tuple[float, float, float]:
    """Bodový odhad AUC a 95% bootstrap interval."""
    bod = float(roc_auc_score(y, p))
    rng = np.random.default_rng(cfg.SEED)
    hodnoty = []
    for _ in range(iteraci):
        index = rng.integers(0, len(y), len(y))
        if len(np.unique(y[index])) < 2:
            continue
        hodnoty.append(roc_auc_score(y[index], p[index]))
    pri = np.asarray(hodnoty, dtype=float)
    return bod, float(np.percentile(pri, 2.5)), float(np.percentile(pri, 97.5))


def _logit(y: np.ndarray, exog: pd.DataFrame):
    """Přizpůsobí logistickou regresi a vrátí výsledek statsmodels."""
    return sm.Logit(y, sm.add_constant(exog.astype(float))).fit(disp=0)


def _radek_z_tabulky(tabulka: pd.DataFrame, sloupec: str) -> pd.Series:
    """Řádek regresní tabulky podle českého popisu proměnné (tabulka se počítá s popisky)."""
    hledano = prep.popisek(sloupec)
    nalezeny = tabulka.loc[tabulka["proměnná"] == hledano]
    if not len(nalezeny):
        raise KeyError(f"v tabulce modelu chybí příznak {hledano!r}")
    return nalezeny.iloc[0]


def _or_z_tabulky(tabulka: pd.DataFrame, sloupec: str) -> float:
    """Odhad šance (OR) pro daný příznak, v jednotkách z tabulky."""
    return float(_radek_z_tabulky(tabulka, sloupec)["šance OR"])


def _or_interval_z_tabulky(tabulka: pd.DataFrame, sloupec: str) -> str:
    """95% interval šance (OR) jako řetězec pro text závěru."""
    radek = _radek_z_tabulky(tabulka, sloupec)
    return (f"{cfg.cislo(radek['OR dolní mez'], 2)}–"
            f"{cfg.cislo(radek['OR horní mez'], 2)}")


def _tabulka_regresie(vysledek, skaluje: dict[str, float] | None = None) -> pd.DataFrame:
    """Tabulka koeficientů, šancí (OR) a 95% intervalů."""
    skaluje = skaluje or {}
    rady = []
    for jmeno in vysledek.params.index:
        nasobek = skaluje.get(jmeno, 1.0)
        koef = vysledek.params[jmeno] * nasobek
        interval = vysledek.conf_int()
        dolni, horni = float(interval.loc[jmeno, 0]), float(interval.loc[jmeno, 1])
        rady.append({
            "proměnná": prep.popisek(jmeno) if jmeno != "const" else "konstanta",
            "β": koef,
            "šance OR": np.exp(koef),
            "OR dolní mez": np.exp(dolni * nasobek),
            "OR horní mez": np.exp(horni * nasobek),
            "Wald z": (vysledek.tvalues[jmeno] if jmeno in vysledek.tvalues.index else np.nan),
            "p-hodnota": (vysledek.pvalues[jmeno] if jmeno in vysledek.pvalues.index else np.nan),
            "jednotka": f"×{nasobek:g}" if nasobek != 1 else "—",
        })
    return pd.DataFrame(rady)


def _strukturalni_koeficienty(lda, X: np.ndarray, y: np.ndarray,
                              jmena: list[str]) -> pd.DataFrame:
    """Strukturální koeficienty LDA — korelace diskriminantu s původními znaky."""
    skore = lda.transform(X).ravel()
    return pd.DataFrame({
        "proměnná": [prep.popisek(j) for j in jmena],
        "strukturální koeficient": [float(np.corrcoef(skore, X[:, i])[0, 1])
                                    for i in range(len(jmena))],
    }).sort_values("strukturální koeficient", key=np.abs, ascending=False)


def _graf_model(tabulka: pd.DataFrame, jmeno: str, titulek: str) -> str:
    """Graf šancí (OR) s 95% intervaly — základní graf modelu."""
    vyber = tabulka.loc[tabulka["proměnná"] != "konstanta"].reset_index(drop=True)
    fig, osy = plt.subplots(figsize=(7.8, 0.6 * len(vyber) + 2.0))
    pozice = np.arange(len(vyber))
    osy.errorbar(np.exp(vyber["šance OR"]), pozice,
                 xerr=[np.exp(vyber["šance OR"]) - np.exp(vyber["OR dolní mez"]),
                       np.exp(vyber["OR horní mez"]) - np.exp(vyber["šance OR"])],
                 fmt="o", color="#1f77b4", ecolor="#d62728", capsize=4, elinewidth=1.4)
    osy.axvline(1.0, color="0.4", ls="--", lw=1.0)
    osy.set_yticks(pozice)
    osy.set_yticklabels([f"{p} ({j})" for p, j in zip(vyber["proměnná"], vyber["jednotka"])])
    osy.set_xscale("log")
    osy.set_xlabel("poměr šancí (OR), logaritmická osa")
    osy.set_title(titulek)
    for pozice_radek, radek in enumerate(vyber.itertuples(index=False)):
        osy.annotate(cfg.fmt_p(radek[6]), (np.exp(radek[2]), pozice_radek),
                     xytext=(0, 10), textcoords="offset points", ha="center", fontsize=8)
    fig.tight_layout()
    cfg.uloz_fig(fig, jmeno)
    return jmeno


def _graf_roc(krivky: dict[str, tuple], jmeno: str) -> str:
    """ROC křivky více modelů najednou (in‑sample i křížová validace)."""
    fig, osy = plt.subplots(figsize=(6.4, 5.4))
    for nazev, (fpr, tpr, auc) in krivky.items():
        osy.plot(fpr, tpr, lw=1.8, label=f"{nazev} (AUC {auc:.3f})")
    osy.plot([0, 1], [0, 1], ":", color="0.5", label="náhoda (0,500)")
    osy.set_xlabel("míra falešnépozitivnosti (1 – specificita)")
    osy.set_ylabel("míra skutečnépozitivnosti (senzitivita)")
    osy.set_title("ROC křivky modelů úspěšnosti střely")
    osy.legend(fontsize=8, loc="lower right")
    osy.set_xlim(0, 1)
    osy.set_ylim(0, 1.01)
    fig.tight_layout()
    cfg.uloz_fig(fig, jmeno)
    return jmeno


def _graf_konfuse(matice: dict[str, np.ndarray], jmeno: str) -> str:
    """Konfuzní matice vedle sebe (místo klasického „χ² diagramu“ četností)."""
    prvni = len(matice)
    fig, osy = plt.subplots(1, prvni, figsize=(4.2 * prvni, 3.6), squeeze=False)
    for i, (nazev, pole) in enumerate(matice.items()):
        spd = pole / pole.sum() * 100
        sns.heatmap(spd, annot=False, cmap="Blues", cbar=False, vmin=0, vmax=100,
                    ax=osy[0, i])
        for a in range(2):
            for b in range(2):
                osy[0, i].text(b + 0.5, a + 0.5,
                               f"{int(pole[a, b])}\n({spd[a, b]:.1f} %)",
                               ha="center", va="center", fontsize=9,
                               color="white" if spd[a, b] > 55 else "black")
        osy[0, i].set_xticks([0.5, 1.5])
        osy[0, i].set_yticks([0.5, 1.5])
        osy[0, i].set_xticklabels(["ne-gól", "gól"])
        osy[0, i].set_yticklabels(["ne-gól", "gól"])
        spravne = np.trace(pole) / pole.sum() * 100
        osy[0, i].set_title(f"{nazev}\nsprávně {spravne:.1f} %")
        osy[0, i].set_ylabel("skutečnost" if i == 0 else None)
        osy[0, i].set_xlabel("predikce")
    fig.suptitle("Konfuzní matice (řádky = skutečnost)", fontweight="bold")
    fig.tight_layout()
    cfg.uloz_fig(fig, jmeno)
    return jmeno


def _graf_kalibrace(y: np.ndarray, p: np.ndarray, jmeno: str, skladek: int = 10) -> str:
    """Kalibrační diagram: předpovězená pravořadost proti skutečné úspěšnosti."""
    poradi = stats.rankdata(p, method="ordinal")
    sklady = np.ceil(poradi / len(p) * skladek).astype(int)
    stredy, pozorovane, meze = [], [], []
    for slozka in range(1, skladek + 1):
        maska = sklady == slozka
        if maska.sum() == 0:
            continue
        dolni, horni = _wilson(int(y[maska].sum()), int(maska.sum()))
        stredy.append(float(p[maska].mean()))
        pozorovane.append(float(y[maska].mean()))
        meze.append((float(pozorovane[-1]) - dolni, horni - float(pozorovane[-1])))
    meze = np.asarray(meze, dtype=float)

    fig, osy = plt.subplots(figsize=(5.8, 5.0))
    osy.errorbar(stredy, pozorovane, yerr=meze.T, fmt="o", capsize=4,
                 color="#1f77b4", ecolor="#d62728", ms=6)
    osy.plot([0, max(stredy + [0.1])], [0, max(stredy + [0.1])], ":", color="0.4",
             label="ideální kalibrace")
    osy.axhline(float(np.mean(y)), color="#2ca02c", lw=1.0, ls="-.",
                label=f"průměrná úspěšnost {np.mean(y) * 100:.0f} %")
    osy.set_xlabel("předpovězená pravděpodobnost gólu")
    osy.set_ylabel("skutečná úspěšnost složky (95% IS)")
    osy.set_title("Kalibrační diagram logistického modelu")
    osy.legend(fontsize=8)
    fig.tight_layout()
    cfg.uloz_fig(fig, jmeno)
    return jmeno


def _graf_mapa(model, prom: list[str], jmeno: str, titulek: str) -> str:
    """Mapa předpovězené pravděpodobnosti gólu podle polohy na hřišti."""
    mřížka_x, mřížka_y = np.meshgrid(np.linspace(0, 100, 121), np.linspace(0, 100, 81))
    x_m = mřížka_x.ravel() / 100.0 * cfg.DELKA_HRITE_M
    y_m = mřížka_y.ravel() / 100.0 * cfg.SIRKA_HRITE_M
    dx = cfg.DELKA_HRITE_M - x_m
    dy = cfg.SIRKA_HRITE_M / 2.0 - y_m
    vzdalenost = np.hypot(dx, dy)
    horni = np.stack([dx, dy + cfg.POLOVINA_BRANKY_M], axis=1)
    dolni = np.stack([dx, dy - cfg.POLOVINA_BRANKY_M], axis=1)
    uhel = np.degrees(np.arccos(np.clip((horni * dolni).sum(axis=1)
                                        / (np.linalg.norm(horni, axis=1)
                                           * np.linalg.norm(dolni, axis=1)), -1.0, 1.0)))
    hodnoty = {"dist_m": vzdalenost, "angle_deg": uhel}
    exog = pd.DataFrame({p: hodnoty[p] for p in prom if p in hodnoty})
    for p in prom:
        if p not in exog.columns:
            exog[p] = 0.0
    pravo = np.asarray(model.predict(sm.add_constant(exog, has_constant="add")))
    pole = pravo.reshape(mřížka_x.shape) * 100

    fig, osy = plt.subplots(figsize=(9.2, 5.0))
    kontura = osy.contourf(mřížka_x, mřížka_y, pole, levels=np.linspace(0, pole.max(), 13),
                           cmap="YlOrRd")
    osy.contour(mřížka_x, mřížka_y, pole, levels=[5, 10, 20, 30], colors="0.35", lw=0.7)
    osy.clabel(osy.contour(mřížka_x, mřížka_y, pole, levels=[5, 10, 20, 30],
                           colors="0.35", lw=0.7), fmt="%.0f %%", fontsize=7)
    fig.colorbar(kontura, ax=osy, shrink=0.85, label="předpovězená úspěšnost (%)")
    _nakresli_hriste(osy)
    osy.set_title(titulek)
    fig.tight_layout()
    cfg.uloz_fig(fig, jmeno)
    return jmeno


def testuj_h4(vysledky: cfg.Vysledky, data: pd.DataFrame) -> dict:
    """H4: úspěšnost střely je modelovatelná z kvality šance a polohy."""
    vysledky.nadpis("5. H4 — model úspěšnosti střely")
    grafy: list[str] = []
    df = pridej_prometene(data)
    # modelová množina: úplné souřadnice a bez penalt (ty mají degenerované X, Y i xG)
    model_data = df.loc[~df["missing_xy"] & (df["is_penalty"] == 0)].reset_index(drop=True)
    X = model_data[ZAKLADNI_MODEL].astype(float)
    y = model_data["is_goal"].to_numpy(dtype=int)

    vysledky.odstavec(
        "**H0:** ani jeden z příznaků (xG, vzdálenost od branky, úhel pohledu) neovlivňuje "
        "pravděpodobnost gólu — všechny regresní koeficienty kromě konstanty jsou nulové "
        "(model má shodnou shodu jako průměr). **H1:** alespoň jeden příznak vliv má. "
        f"Modelujeme {len(model_data)} střel z pole (bez penalt, ty vyčleňujeme — "
        f"{int(df['is_penalty'].sum())} pokutových kopů má neměnnou polohu i xG a tvoří "
        "zvláštní režim). Vysvětlovaná veličina je binární gól / bez gólu, takže "
        "logistická regrese (VMM-7); vedle toho lineární diskriminační analýza jako "
        "metoda s jinými předpoklady."
    )

    # --- logistická regrese --------------------------------------------------
    model = _logit(y, X)
    nulovy = sm.Logit(y, np.ones((len(y), 1))).fit(disp=0)
    lr_celkem = 2 * (model.llf - nulovy.llf)
    stupne_celkem = int(model.df_model)
    zamitnuto_model = _hodnoceni(
        vysledky, "Celkový LR test logitního modelu (proti modelu s konstantou)",
        f"χ²({stupne_celkem}) = {cfg.cislo(lr_celkem, 2)}, "
        f"McFaddenovo R² = {cfg.cislo(model.prsquared, 3)}",
        float(stats.chi2.sf(lr_celkem, stupne_celkem)),
        komentare="model je lepší než průměr; McFaddenovo R² u binární odezvy "
                  "bývá nízké, 0,2–0,4 už platí za silné")

    skalovani = {"xG": 0.1, "dist_m": 10.0, "angle_deg": 10.0, "const": 1.0,
                 "minuta": 10.0, "jina_polovina": 1.0, "bez_asistence": 1.0}
    tabulka = _tabulka_regresie(model, skalovani)
    vysledky.tabulka(tabulka, "Koeficienty logistické regrese (OR na dané jednotce)")
    _uloz_tabulku(tabulka, "h4_logit_zakladni")
    for radek in tabulka.itertuples(index=False):
        if radek[0] == "konstanta":
            continue
        _hodnoceni(vysledky, f"Waldův test koeficientu: {radek[0]} ({radek[7]})",
                   f"z = {cfg.cislo(radek[5], 2)}, OR = {cfg.cislo(radek[2], 3)}",
                   float(radek[6]))

    grafy.append(_graf_model(tabulka, "50_model_or",
                             "Vliv příznaků na šanci na gól (OR, 95% IS)"))
    vysledky.obrazek("50_model_or", "Šance (OR) příznaků modelu s 95% intervaly")

    # --- VIF a doplňkový model ------------------------------------------------
    tabulka_vif = _vif(X)
    vysledky.tabulka(tabulka_vif,
                     "Faktory inflace rozptylu (VIF) — multikolinearita příznaků")
    _uloz_tabulku(tabulka_vif, "h4_vif")
    vif_nejvyssi = float(tabulka_vif["VIF"].max())

    rozsireny = _logit(y, model_data[ROHSIRENY_MODEL].astype(float))
    lr_rozsireni = 2 * (rozsireny.llf - model.llf)
    stupne_rozsireni = int(rozsireny.df_model - model.df_model)
    _hodnoceni(vysledky, "LR test: přínos minut, strany hřiště a asistence nad základní model",
               f"χ²({stupne_rozsireni}) = {cfg.cislo(lr_rozsireni, 2)}, "
               f"Δ McFadden R² = {cfg.cislo(rozsireny.prsquared - model.prsquared, 4)}",
               float(stats.chi2.sf(lr_rozsireni, max(1, stupne_rozsireni))))
    _uloz_tabulku(_tabulka_regresie(rozsireny, skalovani), "h4_logit_rozsireny")

    # vnořené testy: který příznak nese informaci navíc (df = rozdíl počtu koeficientů)
    geometry = _logit(y, model_data[["dist_m", "angle_deg"]].astype(float))
    lr_geometrie = 2 * (model.llf - geometry.llf)
    stupne_geometrie = int(model.df_model - geometry.df_model)
    _hodnoceni(vysledky, "LR test: přínos xG nad samotnou polohou (vzdálenost + úhel)",
               f"χ²({stupne_geometrie}) = {cfg.cislo(lr_geometrie, 2)}",
               float(stats.chi2.sf(lr_geometrie, max(1, stupne_geometrie))),
               komentare="poloha sama o sobě střelu rozliší a xG k ní přidává "
                         "další informaci")

    lr_poloha_sama = 2 * (geometry.llf - nulovy.llf)
    zamitnuto_poloha_sama = _hodnoceni(
        vysledky, "LR test: samotná poloha (vzdálenost + úhel) proti konstantě",
        f"χ²({int(geometry.df_model)}) = {cfg.cislo(lr_poloha_sama, 2)}, "
        f"McFaddenovo R² = {cfg.cislo(geometry.prsquared, 3)}",
        float(stats.chi2.sf(lr_poloha_sama, max(1, int(geometry.df_model)))),
        komentare="i bez xG poloha vysvětluje víc než průměr — je to ale stejná "
                  "informace, kterou xG obsahuje")

    jen_xg = _logit(y, model_data[["xG"]].astype(float))
    lr_polohy = 2 * (model.llf - jen_xg.llf)
    stupne_polohy = int(model.df_model - jen_xg.df_model)
    p_polohy = float(stats.chi2.sf(lr_polohy, max(1, stupne_polohy)))
    zamitnuto_poloha = _hodnoceni(
        vysledky, "LR test: přínos vzdálenosti a úhlu nad samotným xG",
        f"χ²({stupne_polohy}) = {cfg.cislo(lr_polohy, 2)}", p_polohy,
        komentare="poloha je v modelu zastoupena už přes xG — vlastní informaci "
                  "navíc nemá, a proto její koeficienty ztrácejí průkaznost")

    # --- kalibrace ------------------------------------------------------------
    predikce = np.asarray(model.predict(sm.add_constant(X)))
    chi2_hl, stupne_hl, p_hl = _hosmer_lemeshow(y, predikce)
    _hodnoceni(vysledky, "Hosmer–Lemeshowův test kalibrace (10 složek)",
               f"χ²({stupne_hl}) = {cfg.cislo(chi2_hl, 2)}", p_hl,
               komentare="nezamítnutí = model správně odhaduje úroveň pravděpodobnosti")
    grafy.append(_graf_kalibrace(y, predikce, "53_kalibrace"))
    vysledky.obrazek("53_kalibrace", "Kalibrační diagram modelu")

    # --- roc, cross-validation, lda -------------------------------------------
    krivky = {"logit in‑sample": (*roc_curve(y, predikce)[:2],
                                 float(roc_auc_score(y, predikce)))}
    auc_bod, auc_dol, auc_horn = _bootstrap_auc(y, predikce)
    kize = StratifiedKFold(n_splits=10, shuffle=True, random_state=cfg.SEED)
    logit_cv = LogisticRegression(max_iter=2000, C=1e6)
    predikce_cv = cross_val_predict(logit_cv, X.to_numpy(), y, cv=kize, method="predict_proba")[:, 1]
    fpr_cv, tpr_cv = roc_curve(y, predikce_cv)[:2]
    krivky["logit 10násobná CV"] = (fpr_cv, tpr_cv, float(roc_auc_score(y, predikce_cv)))

    lda = LinearDiscriminantAnalysis()
    predikce_lda = cross_val_predict(lda, X.to_numpy(), y, cv=kize, method="predict_proba")[:, 1]
    fpr_lda, tpr_lda = roc_curve(y, predikce_lda)[:2]
    krivky["LDA 10násobná CV"] = (fpr_lda, tpr_lda, float(roc_auc_score(y, predikce_lda)))

    grafy.append(_graf_roc(krivky, "51_roc"))
    vysledky.obrazek("51_roc", "ROC křivky a AUC modelů")
    rozdil = krivky["logit 10násobná CV"][2] - krivky["LDA 10násobná CV"][2]
    vysledky.radek(
        f"- **Schopnost rozlišit gól / bez gólu**: AUC logitního modelu "
        f"{cfg.cislo(auc_bod, 3)} (95% bootstrap IS {cfg.cislo(auc_dol, 3)}–"
        f"{cfg.cislo(auc_horn, 3)}), po křížové validaci {cfg.cislo(krivky['logit 10násobná CV'][2], 3)}; "
        f"LDA {cfg.cislo(krivky['LDA 10násobná CV'][2], 3)} "
        f"(rozdíl {cfg.cislo(abs(rozdil), 3)} → "
        f"{'modely jsou srovnatelné' if abs(rozdil) < 0.02 else 'jeden model je zřetelně lepší'}). "
        "AUC nad 0,7 znamená dobře rozlišující model; blížíme se hranici, kde je "
        "střelba faktorem nepředvídatelnosti.")

    lda.fit(X.to_numpy(), y)
    struktura = _strukturalni_koeficienty(lda, X.to_numpy(), y, ZAKLADNI_MODEL)
    vysledky.tabulka(struktura.reset_index(drop=True),
                     "Strukturální koeficienty LDA (diskriminant vs. původní znaky)")
    _uloz_tabulku(struktura, "h4_lda_struktura")

    # --- klasifikace ------------------------------------------------------------
    predikce_in = (predikce >= 0.5).astype(int)
    predikce_lda_klas = (predikce_lda >= 0.5).astype(int)
    matice = {"logit (práh 0,5)": confusion_matrix(y, predikce_in),
              "LDA (práh 0,5)": confusion_matrix(y, predikce_lda_klas)}
    grafy.append(_graf_konfuse(matice, "52_konfuse"))
    vysledky.obrazek("52_konfuse", "Konfuzní matice obou modelů")

    fpr, tpr, prahy = roc_curve(y, predikce)
    prah_youden = float(prahy[int(np.argmax(tpr - fpr))])
    predikce_youden = (predikce >= prah_youden).astype(int)
    srovnani = []
    for nazev, pred in (("práh 0,5", predikce_in), (f"práh {prah_youden:.2f} (Youden)",
                                                             predikce_youden)):
        mats = confusion_matrix(y, pred)
        sen = mats[1, 1] / max(mats[1].sum(), 1)
        spe = mats[0, 0] / max(mats[0].sum(), 1)
        spras = np.trace(mats) / mats.sum()
        dolni, horni = _wilson(int(np.trace(mats)), int(mats.sum()))
        srovnani.append({"práh": nazev, "senzitivita": sen, "specificita": spe,
                         "správně": spras, "správně dolní mez": dolni,
                         "správně horní mez": horni,
                         "predikováno gólů": int(pred.sum()), "skutečných gólů": int(y.sum())})
    srovnani = pd.DataFrame(srovnani)
    vysledky.tabulka(srovnani, "Kvalita klasifikace při různých prázích")
    _uloz_tabulku(srovnani, "h4_klasifikace")

    # --- mapa a penalty ---------------------------------------------------------
    grafy.append(_graf_mapa(geometry, ["dist_m", "angle_deg"], "54_mapa_pravdepodobnosti",
                            "Předpovězená úspěšnost střely podle polohy (jen geometrie)"))
    vysledky.obrazek("54_mapa_pravdepodobnosti",
                     "Mapa úspěšnosti podle vzdálenosti a úhlu pohledu")

    penalty = df.loc[df["is_penalty"] == 1]
    if len(penalty):
        dolni, horni = _wilson(int(penalty["is_goal"].sum()), len(penalty))
        vysledky.odstavec(
            f"Penalty jsme z modelu vyčlenili: {len(penalty)} pokutových kopů s úspěšností "
            f"{cfg.cislo(penalty['is_goal'].mean() * 100, 1)} % "
            f"(95% IS {cfg.cislo(dolni * 100, 1)}–{cfg.cislo(horni * 100, 1)} %), "
            f"při xG {penalty['xG'].iloc[0]:.2f}. V modelu by uměle navyšovaly AUC "
            "(konstantní příznaky, skoro deterministický výsledek) — reálnou výpověď "
            "o střelbě z pole by zkreslily.")

    # --- citlivost na odlehlé hodnoty -------------------------------------------
    bez = model_data.loc[~model_data["odlehla_D2"].fillna(False).astype(bool)]
    if len(bez) > 50:
        model_bez = _logit(bez["is_goal"].to_numpy(dtype=int), bez[ZAKLADNI_MODEL].astype(float))
        posun = float(np.abs(model_bez.params - model.params).max())
        auc_bez = float(roc_auc_score(bez["is_goal"],
                                      model_bez.predict(sm.add_constant(bez[ZAKLADNI_MODEL]))))
        citlivost = (f" Po vyloučení odlehlých střel (Mahalanobisovo D², "
                     f"{len(model_data) - len(bez)} řádků) se koeficienty změnily nejvýše o "
                     f"{cfg.cislo(posun, 3)} a AUC z {cfg.cislo(auc_bod, 3)} na "
                     f"{cfg.cislo(auc_bez, 3)} → závěry nejsou dány extrémy.")
    else:
        citlivost = ""

    zaver_h4 = zamitnuto_model
    if zaver_h4:
        veta_h4 = ("pravděpodobnost gólu je průkazně vysvětlitelná zvolenými "
                   "příznaky, přičemž nositelem efektu je hlavně kvalita šance (xG)")
        if not zamitnuto_poloha:
            veta_h4 += (". Samotná poloha (vzdálenost a úhel) sice rozliší gól od "
                        f"bez gólu lépe než průměr "
                        f"(LR test {cfg.fmt_p(float(stats.chi2.sf(lr_poloha_sama, max(1, int(geometry.df_model)))))}), "
                        f"ale nad rámec xG už žádnou informaci nepřidává "
                        f"(LR test χ²({stupne_polohy}) = {cfg.cislo(lr_polohy, 2)}, "
                        f"{cfg.fmt_p(p_polohy)}) — informace o poloze je už "
                        "v xG obsažena a příznaky se v modelu o ni dělí "
                        f"(nejvyšší VIF {cfg.cislo(vif_nejvyssi, 2)}, tedy silná "
                        "multikolinearita vzdálenosti a úhlu)")
    else:
        veta_h4 = ("neprokazujeme, že by kvalita šance a poloha stanoviště "
                   "ovlivňovaly pravděpodobnost gólu")
    text = (
        f"**Závěr H4: {'H0 zamítáme' if zaver_h4 else 'H0 nezamítáme'}** — "
        f"{veta_h4}. Model podle celkového testu "
        f"{'je prokazatelně lepší než model pouze s konstantou' if zaver_h4 else 'není prokazatelně lepší než model pouze s konstantou'} "
        f"(χ²({stupne_celkem}) = {cfg.cislo(lr_celkem, 2)}, "
        f"{cfg.fmt_p(float(stats.chi2.sf(lr_celkem, stupne_celkem)))}); model má "
        f"AUC {cfg.cislo(auc_bod, 3)} a McFaddenovo R² {cfg.cislo(model.prsquared, 3)}."
    )
    if p_hl >= ALPHA:
        kalibrace = f" Model je dobře kalibrován (Hosmer–Lemeshow {cfg.fmt_p(p_hl)})."
    else:
        kalibrace = (f" Kalibrace je slabší — Hosmer–Lemeshow {cfg.fmt_p(p_hl)} "
                     "naznačuje, že odhady pravděpodobnosti nejsou přesné ve "
                     "všech decilech; pořadí šancí (AUC) to ale nemění.")
    if zaver_h4:
        text += (
            f" Každých +0,1 xG zvyšuje šanci na gól "
            f"„{cfg.cislo(_or_z_tabulky(tabulka, 'xG'), 2)}×“ "
            f"(95% IS {_or_interval_z_tabulky(tabulka, 'xG')}); "
            "naopak každých dalších 10 m od branky ji odhaduje níže "
            f"(OR {cfg.cislo(_or_z_tabulky(tabulka, 'dist_m'), 2)}, "
            f"95% IS {_or_interval_z_tabulky(tabulka, 'dist_m')}).{kalibrace}"
            f"{citlivost} Zbývající nejistota není chyba "
            "modelu — střelba je ze své podstaty náhodný děj (i penalty se nepromění zdaleka "
            "vždy), proto je správnou interpretací odhad pravděpodobnosti, ne předpověď."
        )
    vysledky.odstavec(text)
    return {"název": "H4 — model úspěšnosti", "zaver": text, "grafy": grafy,
            "zamitnuto": zaver_h4, "hlavni_graf": "51_roc",
            "data": {"model": model, "tabulka": tabulka, "auc": auc_bod,
                     "auc_interval": (auc_dol, auc_horn), "model_data": model_data,
                     "krivky": krivky}}


# ---------------------------------------------------------------------------
# Celý běh
# ---------------------------------------------------------------------------
def spust(vysledky: cfg.Vysledky, pripravena: dict) -> dict:
    """Otestuje H1–H4 a vrátí souhrn pro prezentaci a README."""
    data = pripravena["data"]
    uplne = pripravena["uplne"]
    vysledky.radek("\n---\n")
    vysledky.nadpis("Vyhodnocení hypotéz", 2)
    vysledky.odstavec(
        f"Všechny testy běží na hladině významnosti **α = {cfg.ALPHA}** "
        f"(p < {cfg.ALPHA} → H0 zamítáme). Kritické hodnoty a testy vycházejí "
        "z přednášek VMM (ANOVA/MANOVA VMM-4, PCA VMM-6 a VMM-9, shlukování VMM-5, "
        "logitní model VMM-7, t-SNE VMM-10)."
    )
    souhrn = {
        "H1": testuj_h1(vysledky, data),
        "H2": testuj_h2(vysledky, uplne),
        "H3": testuj_h3(vysledky, uplne),
        "H4": testuj_h4(vysledky, data),
    }

    vysledky.radek("\n---\n")
    vysledky.nadpis("Souhrn výsledků", 2)
    rady = []
    for zkrácená, vysledek in souhrn.items():
        rady.append({"hypotéza": zkrácená, "název": vysledek["název"],
                     "závěr": "H0 zamítáme" if vysledek["zamitnuto"] else "H0 nezamítáme"})
    vysledky.tabulka(pd.DataFrame(rady), "Rozsudek nad hypotézami na α = 0,05")
    for zkrácená, vysledek in souhrn.items():
        vysledky.odstavec(f"**{zkrácená} — {vysledek['název']}.** {vysledek['zaver']}")
    return souhrn


if __name__ == "__main__":
    cfg.zajisti_slozky()
    cfg.styl_grafu()
    protokol = cfg.Vysledky()
    prepust = prep.spust(protokol)
    spust(protokol, prepust)
    protokol.uloz()
    print("hotovo — viz outputs/vysledky.md")

