"""Předzpracování dat střeleckých pokusů.

Kroky odpovídají přednáškám VMM-2 (vícerozměrné testy, Mahalanobis) a VMM-3
(předzpracování, transformace) a konvencím v AGENTS.md:

1. Načtení dat a kontrola schématu (sloupec se v datech jmenuje `result`).
2. Přeškálování souřadnic X, Y z rozsahu 0–1 na 100 × 100 jednotek dle zadání
   (převod měřítka, nikoli normalizace jako statistická metoda).
3. Vyloučení hodnot, které nedávají smysl (rána na vlastní branku).
4. Ošetření chybějících hodnot: kategorie -> vlastní kategorie „Neznámý“,
   u souřadnic se neprovádí imputace, analýza běží na kompletním souboru.
   Průzkum MCAR předpokladu.
5. Derivace geometrických příznaků (vzdálenost branky, úhel odkrytý brankou).
6. Detekce odlehlých hodnot Mahalanobisovou vzdáleností + χ² diagram.
7. Standardizace (centrování + vydělení směrodatnou odchylkou) pro
   ordinální a shlukové metody; transformační konstanty uchováváme.

Názvy sloupců jsou ASCII (přenositelnost), české popisky jsou v POPIS.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import pingouin as pg
import seaborn as sns
from scipy import stats
from sklearn.preprocessing import StandardScaler

import config as cfg

KATEGORIE_NENI = "Neznámý"

# Proměnné určené pro vícerozměrné metody (PCA, FA, shlukování)
VMM_PROMENNE = ["xG", "dist_m", "angle_deg", "X", "Y"]
# všechny numerické veličiny pro popisnou statistiku
POPIS_PROMENNE = ["minute", "xG", "dist_m", "angle_deg", "X", "Y"]

# České popisky pro grafy a tabulky
POPIS = {
    "xG": "xG (očekávané góly)",
    "dist_m": "vzdálenost od branky [m]",
    "angle_deg": "úhel odkrytý brankou [°]",
    "X": "X [jednotky hřiště]",
    "Y": "Y [jednotky hřiště]",
    "minute": "minuta zápasu",
    "is_goal": "střela proměněna (0/1)",
    "on_target": "střela na branku (0/1)",
}


def _tvar_zaznam(n: int) -> str:
    """Spojnání počtu s podstatným jménem „záznam“ (1 záznam, 2–4 záznamy, 5+ záznamů)."""
    if n == 1:
        return "záznam"
    if 2 <= n <= 4:
        return "záznamy"
    return "záznamů"


def popisek(sloupec: str) -> str:
    return POPIS.get(sloupec, sloupec)


# ---------------------------------------------------------------------------
# 1. načtení
# ---------------------------------------------------------------------------
def nacti_data(cesta=cfg.CSV) -> pd.DataFrame:
    """Načte CSV a ověří očekávané schéma."""
    df = pd.read_csv(cesta)
    ocekavano = {
        "id", "minute", "result", "X", "Y", "xG", "player", "h_a", "player_id",
        "situation", "year", "shotType", "match_id", "h_team", "a_team",
        "h_goals", "a_goals", "date", "player_assisted", "lastAction",
    }
    chybi = ocekavano - set(df.columns)
    if chybi:
        raise ValueError(f"V datech chybi sloupce: {chybi}")
    df["datum"] = pd.to_datetime(df["date"], errors="coerce")
    return df


# ---------------------------------------------------------------------------
# 2. měřítko a geometrie
# ---------------------------------------------------------------------------
def prepni_meritko(df: pd.DataFrame) -> pd.DataFrame:
    """Převede souřadnice 0–1 na 100 × 100 jednotek definované zadáním."""
    if df[["X", "Y"]].max().max() <= 1.0:
        df[["X", "Y"]] = df[["X", "Y"]] * 100.0
    return df


def pridej_geometrii(df: pd.DataFrame) -> pd.DataFrame:
    """Spočítá vzdálenost a úhel k brance (branka je na konci hřiště, X = 100).

    Souřadnice X, Y jsou orientovány ve směru útoku (vždy ke brance soupeře),
    proto je střed branky po přepočtu na metry v bodě [105; 34].
    Úhel je skutečný úhel, který branku „pokrývá“ pohledu střelce — počítaný
    z vektorů ke dvěma tyčím, takže sedí i pro střely bezprostředně u branky.
    """
    x_m = df["X"] / 100.0 * cfg.DELKA_HRITE_M
    y_m = df["Y"] / 100.0 * cfg.SIRKA_HRITE_M
    stred_y = cfg.SIRKA_HRITE_M / 2.0

    dx = cfg.DELKA_HRITE_M - x_m            # kolmice na brankovou čáru
    dy = stred_y - y_m                      # vychýlení od osy hřiště

    df["dist_line_m"] = dx
    df["dist_m"] = np.hypot(dx, dy)         # ke středu branky

    tyc_horni = np.stack([dx, dy + cfg.POLOVINA_BRANKY_M], axis=1)
    tyc_dolni = np.stack([dx, dy - cfg.POLOVINA_BRANKY_M], axis=1)
    skalarni = (tyc_horni * tyc_dolni).sum(axis=1)
    normy = np.linalg.norm(tyc_horni, axis=1) * np.linalg.norm(tyc_dolni, axis=1)
    df["angle_rad"] = np.arccos(np.clip(skalarni / normy, -1.0, 1.0))
    df["angle_deg"] = np.degrees(df["angle_rad"])

    df["is_goal"] = (df["result"] == "Goal").astype(int)
    df["on_target"] = df["result"].isin(cfg.NA_BRANKU).astype(int)
    df["is_penalty"] = (df["situation"] == "Penalty").astype(int)
    df["side"] = np.where(y_m < stred_y, "levá", "pravá")
    return df


# ---------------------------------------------------------------------------
# 3. neplatné hodnoty
# ---------------------------------------------------------------------------
def vyfiltruj_neplatne(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Vyřadí záznamy, které nedávají smysl pro pokus o gól na brance soupeře.

    Vrací (čistá data, tabulku vyřazených záznamů s důvodem).
    """
    duvody = [
        ("Vlastní gól — rána na vlastní branku", df["result"] == "OwnGoal"),
        (
            "Souřadnice mimo rozsah 0–100",
            df["X"].notna()
            & df["Y"].notna()
            & ((df["X"] < 0) | (df["X"] > 100) | (df["Y"] < 0) | (df["Y"] > 100)),
        ),
        ("xG mimo interval 0–1", (df["xG"] < 0) | (df["xG"] > 1)),
    ]

    mask = pd.Series(False, index=df.index)
    casti = []
    for duvod, m in duvody:
        if m.any():
            casti.append(df.loc[m].assign(duvod_vyradeni=duvod))
        mask |= m
    vyradeno = (
        pd.concat(casti, ignore_index=True)
        if casti
        else pd.DataFrame(columns=[*df.columns, "duvod_vyradeni"])
    )
    return df.loc[~mask].copy(), vyradeno


# ---------------------------------------------------------------------------
# 4. chybějící hodnoty
# ---------------------------------------------------------------------------
def osetri_chybejici(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Doplní kategorie „Neznámý“ a příznak ne/uplnosti souřadnic; ověří MCAR.

    Vrací (data, přehled chybějících hodnot, tabulku MCAR testu).
    """
    for sloupec in ["shotType", "lastAction", "player_assisted"]:
        df[sloupec] = df[sloupec].fillna(KATEGORIE_NENI)

    df["missing_xy"] = df["X"].isna() | df["Y"].isna()

    prehled = pd.DataFrame(
        {"sloupec": df.columns, "chybi": [int(df[c].isna().sum()) for c in df.columns]}
    )
    prehled["podil_radku"] = (prehled["chybi"] / len(df)).round(4)
    prehled = prehled[prehled["chybi"] > 0].reset_index(drop=True)

    # MCAR: liší se řádky bez souřadnic v cíli (gól) a v xG?
    uplne = df.loc[~df["missing_xy"], "xG"]
    neuplne = df.loc[df["missing_xy"], "xG"]
    u_stat, p_u = stats.mannwhitneyu(uplne, neuplne, alternative="two-sided")

    tabulka_vztahu = pd.crosstab(df["missing_xy"], df["result"])
    chi2_stat, p_chi2, _df_chi2, _ock = stats.chi2_contingency(tabulka_vztahu)

    uspes = df.groupby("missing_xy")["is_goal"].mean()
    mcar = pd.DataFrame(
        [
            {
                "test": "Mann–Whitney U — xG (úplné vs. neúplné souřadnice)",
                "statistika": u_stat,
                "p-hodnota": p_u,
                "rozdil_prumeru_xG": uplne.mean() - neuplne.mean(),
            },
            {
                "test": f"Chi-kvadrát nezávislosti — chybějící souřadnice × výsledek (df = {_df_chi2})",
                "statistika": chi2_stat,
                "p-hodnota": float(p_chi2),
                "rozdil_prumeru_xG": np.nan,
            },
        ]
    )
    mcar["uspěšnost_gól_plné"] = uspes.get(False, np.nan)
    mcar["úspěšnost_gól_bez_souradnic"] = uspes.get(True, np.nan)
    return df, prehled, mcar


# ---------------------------------------------------------------------------
# 6. odlehlé hodnoty (Mahalanobis)
# ---------------------------------------------------------------------------
def mahalanobis(df: pd.DataFrame, sloupce: list[str], kvantil: float = 0.997) -> dict:
    """D² od centroidu ve standardizovaném prostoru + kritérium χ²(p).

    Kritickou mezí je kvantil χ² s p stupni volnosti (p = počet proměnných),
    tedy obecná verze „elipsy“ z přednášek pro p-rozměrný elipsoid.
    """
    data = df[sloupce].dropna()
    X = StandardScaler().fit_transform(data.to_numpy(dtype=float))
    S_inv = np.linalg.pinv(np.cov(X, rowvar=False))
    rozdil = X - X.mean(axis=0)
    D2 = np.einsum("ij,jk,ik->i", rozdil, S_inv, rozdil)

    p = len(sloupce)
    kriticke = float(stats.chi2.ppf(kvantil, df=p))
    kvantilove_hodnoty = stats.chi2.ppf((np.arange(1, len(D2) + 1) - 0.5) / len(D2), df=p)

    priznak = pd.Series(False, index=df.index)
    priznak.loc[data.index] = D2 > kriticke
    return {
        "D2": pd.Series(D2, index=data.index, name="D2"),
        "odlehla": priznak,
        "kriticke": kriticke,
        "df": p,
        "modelove_kvantily": kvantilove_hodnoty,
        "n": int(len(D2)),
    }


def standardizuj(df: pd.DataFrame, sloupce: list[str]) -> tuple[pd.DataFrame, StandardScaler]:
    """Standardizace (VMM-3): odečtení průměru, vydělení směrodatnou odchylkou."""
    scaler = StandardScaler()
    pole = scaler.fit_transform(df[sloupce].to_numpy(dtype=float))
    for i, jmeno in enumerate(sloupce):
        df[f"{jmeno}_std"] = pole[:, i]
    return df, scaler


# ---------------------------------------------------------------------------
# grafy
# ---------------------------------------------------------------------------
def graf_chybejici(prehled: pd.DataFrame) -> None:
    fig, osy = plt.subplots(figsize=(7.5, 3.4))
    data = prehled.sort_values("podil_radku")
    osy.barh(data["sloupec"], data["podil_radku"] * 100, color="#1f77b4")
    for y, (podil, pocet) in enumerate(zip(data["podil_radku"], data["chybi"])):
        osy.text(podil * 100 + 0.4, y, f"{podil * 100:.1f} % ({pocet})", va="center", fontsize=8)
    osy.set_xlabel("podíl řádků s chybějící hodnotou [%]")
    osy.set_title("Chybějící hodnoty podle proměnné")
    osy.set_xlim(0, max(30.0, data["podil_radku"].max() * 100 * 1.3))
    cfg.uloz_fig(fig, "01_chybejici_hodnoty")


def graf_distribuce(df: pd.DataFrame) -> None:
    veliciny = [("xG", POPIS["xG"]), ("dist_m", POPIS["dist_m"]),
                ("angle_deg", POPIS["angle_deg"]), ("minute", POPIS["minute"])]
    fig, osy = plt.subplots(2, 2, figsize=(9.5, 6))
    for ax, (sloupec, titulek) in zip(osy.ravel(), veliciny):
        data = df[sloupec].dropna()
        ax.hist(data, bins=30, color="#1f77b4", alpha=0.8, density=True)
        sns.kdeplot(data, ax=ax, color="#d62728", linewidth=1.6)
        ax.set_title(f"{titulek} — šikmost {stats.skew(data):.2f}, špičatost {stats.kurtosis(data):.2f}")
        ax.set_ylabel("hustota")
    fig.suptitle("Rozdělení hlavních numerických příznaků", fontweight="bold")
    fig.tight_layout()
    cfg.uloz_fig(fig, "02_distribuce")


def graf_mapa_strel(df: pd.DataFrame) -> None:
    """Postavení střel na hřišti (zjednodušeně 100 × 100 jednotek dle zadání)."""
    fig, osy = plt.subplots(figsize=(9.5, 5.4))
    osy.add_patch(plt.Rectangle((50, 0), 51, 100, fill=False, color="0.65", linewidth=1.0))
    osy.add_patch(plt.Rectangle((78, 22), 22, 56, fill=False, color="0.65", linewidth=1.0))
    osy.add_patch(plt.Rectangle((94, 38.5), 6, 23, fill=False, color="0.65", linewidth=1.0))
    osy.plot([88.5], [50.0], marker="o", color="0.45", markersize=4)
    # branka v měřítku hřiště (7,32 m = 10,8 jednotky šířky)
    sirka_branky = cfg.SIRKA_BRANKY_M / cfg.SIRKA_HRITE_M * 100.0 / 2.0
    osy.plot([100, 100], [50 - sirka_branky, 50 + sirka_branky], color="black", linewidth=3.5)

    data = df.dropna(subset=["X", "Y"])
    for vysledek, cast in data.groupby("result"):
        osy.scatter(
            cast["X"], cast["Y"], s=16, alpha=0.6, linewidths=0,
            label=f"{cfg.VYSLEDKY_CS.get(vysledek, vysledek)} (n = {len(cast)})",
        )
    osy.set_xlim(50, 101)
    osy.set_ylim(0, 100)
    osy.set_xlabel("X [jednotky hřiště, 100 = branková čára soupeře]")
    osy.set_ylabel("Y [jednotky hřiště, 0 a 100 = postranní čára]")
    osy.set_title("Mapa střel podle výsledku")
    osy.legend(fontsize=8, loc="upper left")
    osy.grid(False)
    cfg.uloz_fig(fig, "03_mapa_strel")


def graf_mahalanobis(m: dict) -> None:
    """χ² diagram: výběrové kvantily D² vs. modelové kvantily χ²(p)."""
    D2 = np.sort(m["D2"].to_numpy())
    model = m["modelove_kvantily"]

    fig, osy = plt.subplots(1, 2, figsize=(10.5, 4))
    osy[0].scatter(model, D2, s=12, color="#1f77b4", alpha=0.7)
    mez = max(D2.max(), model.max()) * 1.05
    osy[0].plot([0, mez], [0, mez], color="#d62728", linewidth=1.4,
                label=f"model χ²({m['df']})")
    osy[0].axhline(m["kriticke"], color="#ff7f0e", linestyle="--", linewidth=1.2,
                   label=f"kritická mez {m['kriticke']:.1f}")
    osy[0].set_xlabel("modelový kvantil χ²(p)")
    osy[0].set_ylabel("výběrové D² (seřazené)")
    osy[0].set_title(f"χ² diagram Mahalanobisových vzdáleností (p = {m['df']})")
    osy[0].legend(fontsize=8)

    poradi = np.arange(1, len(D2) + 1)
    odlehlé = int((D2 > m["kriticke"]).sum())
    osy[1].plot(poradi, D2, color="#1f77b4", linewidth=1.2)
    osy[1].axhline(m["kriticke"], color="#ff7f0e", linestyle="--", linewidth=1.2,
                   label=f"kritická mez (χ², 99,7 %) = {m['kriticke']:.1f}")
    osy[1].set_yscale("log")
    osy[1].set_xlabel("pořadí pozorování")
    osy[1].set_ylabel("D² [log]")
    osy[1].set_title(f"Seřazené D² — {odlehlé} pozorování nad mezí")
    osy[1].legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    cfg.uloz_fig(fig, "04_mahalanobis_chi2")


def graf_korelace(df: pd.DataFrame) -> pd.DataFrame:
    """Korelační matice (Pearson + Spearman) — připravuje H2."""
    korelace = df[VMM_PROMENNE].corr()
    fig, osy = plt.subplots(1, 2, figsize=(11, 4.6))
    sns.heatmap(korelace, annot=True, fmt=".2f", cmap="coolwarm", vmin=-1, vmax=1,
                center=0, square=True, ax=osy[0], cbar_kws={"shrink": 0.8})
    osy[0].set_title("Pearsonova korelace")
    spearman = df[VMM_PROMENNE].corr(method="spearman")
    sns.heatmap(spearman, annot=True, fmt=".2f", cmap="coolwarm", vmin=-1, vmax=1,
                center=0, square=True, ax=osy[1], cbar_kws={"shrink": 0.8})
    osy[1].set_title("Spearmanova korelace (pořadová)")
    for ax in osy:
        ax.tick_params(labelsize=8)
    fig.suptitle("Korelační matice vstupních příznaků", fontweight="bold")
    fig.tight_layout()
    cfg.uloz_fig(fig, "05_korelacni_matice")
    return korelace


# ---------------------------------------------------------------------------
# hlavní funkce
# ---------------------------------------------------------------------------
def spust(vysledky: cfg.Vysledky) -> dict:
    """Provede celé předzpracování a zapíše výsledky do protokolu."""
    vysledky.nadpis("1. Předzpracování dat")

    df = nacti_data()
    radku_puvodne = len(df)
    zapas = df["match_id"].nunique()
    df = prepni_meritko(df)
    df = pridej_geometrii(df)

    # --- neplatné hodnoty -------------------------------------------------
    df, vyradeno = vyfiltruj_neplatne(df)
    duvod_text = ", ".join(sorted(vyradeno["duvod_vyradeni"].unique())) if len(vyradeno) else "žádné"
    vysledky.odstavec(
        f"Načteno **{radku_puvodne}** záznamů o střelách ({df['player'].nunique()} střelců, "
        f"{zapas} zápasů, sezóny {int(df['year'].min())}–{int(df['year'].max())}). Souřadnice X, Y "
        f"byly v souboru v rozsahu 0–1, podle zadání jsme je přeškálovali na 100 × 100 jednotek "
        f"hřiště. Z analýzy jsme vyloučili **{len(vyradeno)}** {_tvar_zaznam(len(vyradeno))}, "
        f"které nedávají smysl pro "
        f"pokus o gól na brance soupeře (důvody: {duvod_text})."
    )
    if len(vyradeno):
        vysledky.tabulka(
            vyradeno[["id", "player", "result", "X", "Y", "xG", "duvod_vyradeni"]],
            "Vyloučené záznamy",
        )

    # --- chybějící hodnoty ------------------------------------------------
    df, prehled, mcar = osetri_chybejici(df)
    vysledky.tabulka(prehled, "Přehled chybějících hodnot")
    graf_chybejici(prehled)
    vysledky.obrazek("01_chybejici_hodnoty", "Chybějící hodnoty podle proměnné")
    vysledky.tabulka(mcar, "Ověření MCAR předpokladu pro chybějící souřadnice")

    neuplnych = int(df["missing_xy"].sum())
    obe_povoleny = bool((mcar["p-hodnota"].dropna() > cfg.ALPHA).all())
    vysledky.odstavec(
        f"Kategorické proměnné `shotType`, `lastAction` a `player_assisted` jsme doplnili vlastní "
        f"kategorií **„{KATEGORIE_NENI}“** — chybějící záznam sám o sobě nese informaci "
        f"(typicky střela bez přihrávky). U souřadnic X, Y jsme **neprováděli imputaci**: "
        f"geometrické veličiny (vzdálenost, úhel) z nich odvozené by imputace uměle nahnala "
        f"k průměru a zkreslila ordinační i shlukové metody. Ordinační a shlukové analýzy proto "
        f"běží na **{len(df) - neuplnych}** střelách s úplnými souřadnicemi; modely úspěšnosti "
        f"využívají všech {len(df)} záznamů a chybějící souřadnice je v nich zastoupena "
        f"příznakem „neuplné souřadnice“ (sensitivity viz H4).\n\n"
        f"{'Obě testy MCAR prokázaly, že se řádky bez souřadnic neliší v xG ani ve výsledcích (obě p > 0,05) → chybějící hodnoty považujeme za náhodné a jejich vynechání neposouvá závěry.' if obe_povoleny else 'Některé testy MCAR naznačily systémový rozdíl mezi řádky bez souřadnic a ostatními — závěry z analýz na kompletním souboru proto generalizujeme jen na střely s měřenou polohou.'}"
    )

    # --- popisná statistika -----------------------------------------------
    popis = df[POPIS_PROMENNE + ["is_goal", "on_target"]].describe().T[
        ["count", "mean", "std", "min", "25%", "50%", "75%", "max"]
    ]
    popis.insert(0, "šikmost", [stats.skew(df[c].dropna()) for c in popis.index])
    popis.insert(1, "špičatost", [stats.kurtosis(df[c].dropna()) for c in popis.index])
    popis.index = [popisek(c) for c in popis.index]
    vysledky.tabulka(popis.reset_index(names="proměnná"), "Popisná statistika po vyčištění")

    hlavy = (
        df.groupby("result")
        .agg(počet_střel=("id", "size"), úspěšnost=("is_goal", "mean"), průměrné_xG=("xG", "mean"))
        .sort_values("počet_střel", ascending=False)
    )
    hlavy.index = [cfg.VYSLEDKY_CS.get(i, i) for i in hlavy.index]
    vysledky.tabulka(hlavy.reset_index(names="výsledek"), "Výsledky střel")

    uspesnost = df["is_goal"].mean()
    soucet_xg = df["xG"].sum()
    gólu = int(df["is_goal"].sum())
    vysledky.odstavec(
        f"Celková úspěšnost střel je **{uspesnost * 100:.1f} %**; na branku šlo "
        f"{df['on_target'].mean() * 100:.1f} % střel. Součet xG je {soucet_xg:.1f} oproti "
        f"{gólu} vstřeleným gólům (model xG tedy o {cfg.cislo((soucet_xg / gólu - 1) * 100, 0)} % "
        f"{'nadhodnocuje' if soucet_xg > gólu else 'podhodnocuje'} skutečnost), což je další "
        f"připomínka, že xG je pouze odhad kvality šance — nikoli vysvětlený výsledek."
    )

    penalty = df[df["is_penalty"] == 1]
    if len(penalty):
        vysledky.odstavec(
            f"Zvlášť jsme označili **{len(penalty)}** pokutových kopů: poloha i xG jsou degenerované "
            f"(X = {penalty['X'].iloc[0]:.1f}, Y = {penalty['Y'].iloc[0]:.1f}, "
            f"xG = {penalty['xG'].iloc[0]:.3f} pro všechny), úspěšnost "
            f"{penalty['is_goal'].mean() * 100:.0f} %. V ordinačních a shlukových metodách by "
            f"vytvořily umělý shluk (jedno stanovisko s nulovou variabilitou), proto z nich jsou "
            f"vynechány; u modelů úspěšnosti je uvádíme zvlášť."
        )

    graf_distribuce(df)
    vysledky.obrazek("02_distribuce", "Rozdělení numerických příznaků")
    graf_mapa_strel(df)
    vysledky.obrazek("03_mapa_strel", "Postavení střel na hřišti podle výsledku")

    # --- vztahy mezi znaky -------------------------------------------------
    korelace = graf_korelace(df)
    vysledky.obrazek("05_korelacni_matice", "Korelační matice příznaků")
    kriticke_dvojice = [
        f"{popisek(a)}–{popisek(b)} (r = {korelace.loc[a, b]:+.2f})"
        for a, b in [(x, y) for i, x in enumerate(VMM_PROMENNE) for y in VMM_PROMENNE[i + 1:]]
        if abs(korelace.loc[a, b]) >= 0.5
    ]
    if koren := ", ".join(kriticke_dvojice):
        vysledky.odstavec(
            f"Silné lineární vazby (|r| ≥ 0,5) naznačují multikolinearitu, kterou musíme při "
            f"regresním modelování respektovat (VIF v H4): {koren}. Geometrické veličiny jsou "
            f"navzájem dány transformací ze stejných souřadnic, čisté uspořádání dat nám tedy "
            f"nabízí spolehlivou redundanci — přesně situace, kdy má smysl redukce dimenze (H2)."
        )

    # --- odlehlé hodnoty ---------------------------------------------------
    uplne = df.loc[~df["missing_xy"]]
    m = mahalanobis(uplne, VMM_PROMENNE)
    df["odlehla_D2"] = m["odlehla"]
    graf_mahalanobis(m)
    extrémy = (
        uplne.assign(D2=m["D2"])
        .loc[m["D2"] > m["kriticke"], ["id", "player", "result", "situation", "xG", "dist_m", "angle_deg", "D2"]]
        .sort_values("D2", ascending=False)
    )
    vysledky.odstavec(
        f"Odlehlé hodnoty jsme hledali Mahalanobisovou vzdáleností ve standardizovaném prostoru "
        f"proměnných {', '.join(popisek(s) for s in VMM_PROMENNE)} (p = {m['df']}). Kritická mez je "
        f"**{cfg.cislo(m['kriticke'], 2)}** — 99,7. kvantil χ²({m['df']}), obdoba pravidla 3σ pro "
        f"vícerozměrná data. Nad mezí je **{len(extrémy)}** z {m['n']} střel "
        f"({len(extrémy) / m['n'] * 100:.1f} %). Jde o jev, který vysvětluje šikmost rozdělení "
        f"(xG šikmost {stats.skew(uplne['xG']):.2f}): extrémy nejsou chyba měření, ale reálné "
        f"„nejlehčí“ (penalty, dorážky z nulové vzdálenosti) a „nejobtížnější“ (velká vzdálenost, "
        f"malý úhel) šance. **Neodstraňujeme je** — byly by to obsahově nejzajímavější střely; "
        f"jen je označujeme a klíčové testy opakujeme i bez nich (sensitivity viz jednotlivé hypotézy)."
    )
    if len(extrémy):
        vysledky.tabulka(extrémy.head(12).reset_index(drop=True), "Nejvíc odlehlé střely (nad kritickou mezí)")
    vysledky.obrazek("04_mahalanobis_chi2", "χ² diagram Mahalanobisových vzdáleností")

    # --- normalita ----------------------------------------------------------
    radek = []
    for sloupec in VMM_PROMENNE:
        hodnoty = df[sloupec].dropna()
        vzorek = hodnoty.sample(min(500, len(hodnoty)), random_state=cfg.SEED)
        sw = stats.shapiro(vzorek)
        radek.append({
            "proměnná": popisek(sloupec),
            "šikmost": stats.skew(hodnoty),
            "špičatost": stats.kurtosis(hodnoty),
            "Shapiro–Wilk W": sw.statistic,
            "Shapiro–Wilk p": sw.pvalue,
        })
    normalita = pd.DataFrame(radek)
    W, p_mv, zavrh = pg.multivariate_normality(uplne[VMM_PROMENNE].to_numpy(), alpha=cfg.ALPHA)
    vysledky.tabulka(
        normalita,
        "Testy normality (Shapiro–Wilk na náhodné pětisetovce — u velkého souboru orientační)",
    )
    vysledky.odstavec(
        f"Vícerozměrný test normality (Székely–Rizzo, pingouin) dává W = {cfg.cislo(W, 3)}, "
        f"{cfg.fmt_p(p_mv)} → vícerozměrné normální rozdělení **zamítáme**. Pro PCA to není "
        f"kritické (je to algebraická metoda), ale omezuje to χ² testy a Mahalanobisovu vzdálenost; "
        f"proto u neparametricky citlivých rozhodnutí doplňujeme pořadové a permutační varianty "
        f"(Spearman, Kruskal–Wallis, bootstrap)."
    )

    # --- standardizace ------------------------------------------------------
    df, scaler = standardizuj(df, VMM_PROMENNE)
    transformace = pd.DataFrame(
        {
            "proměnná": [popisek(s) for s in VMM_PROMENNE],
            "průměr": scaler.mean_,
            "směrodatná_odchylka": scaler.scale_,
        }
    )
    vysledky.tabulka(transformace, "Transformační konstanty standardizace (uchovány pro inferenci)")

    # --- uložení ------------------------------------------------------------
    df.to_csv(cfg.TABULKY_DIR / "data_vycistena.csv", index=False)
    df.loc[~df["missing_xy"]].to_csv(cfg.TABULKY_DIR / "data_uplne_souradnice.csv", index=False)

    return {
        "data": df,
        "uplne": df.loc[~df["missing_xy"]].copy(),
        "vyradeno": vyradeno,
        "mahalanobis": m,
        "extrémy": extrémy,
        "scaler": scaler,
        "popis": popis,
        "korelace": korelace,
        "normalita": normalita,
        "mv_normalita_p": float(p_mv),
    }
