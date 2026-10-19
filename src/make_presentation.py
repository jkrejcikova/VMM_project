"""Vygeneruje prezentaci „Střelecká úspěšnost“ z vygenerovaných výstupů.

Prezentace nevzniká z ručně opsaných čísel – veškerý text i grafy se berou z
`outputs/vysledky.md` a `outputs/*.png`, které vygenerovala analýza. Cesta ke
snídkám je tedy jediná a reprodukovatelná (podle pravidel v AGENTS.md).

Použití:
    uv run python src/analysis.py            # nejdřív analýza
    uv run python src/make_presentation.py   # potom prezentace
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Emu, Inches, Pt

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as cfg  # noqa: E402

# ---------------------------------------------------------------------------
# Rozměry snímku (16:9) a vzhled
# ---------------------------------------------------------------------------
SIRKA = Inches(13.333)
VYSKA = Inches(7.5)
OKRAJ = Inches(0.55)

BARVINA = RGBColor(0x1B, 0x3A, 0x5C)     # tmavě modrá pro nadpisy
TEXT = RGBColor(0x22, 0x26, 0x2B)        # základní text
TLUSENA = RGBColor(0x5C, 0x66, 0x70)     # pomocný text
AKCENT = RGBColor(0xB4, 0x45, 0x2A)      # zvýraznění (H0 zamítáme)
POZADI = RGBColor(0xF5, 0xF7, 0xF9)      # podklad

ZDROJ = "Zdroj: vlastní analýza, outputs/vysledky.md"

# ---------------------------------------------------------------------------
# Čtení čísel a textů z protokolu (jediný zdroj pravdy)
# ---------------------------------------------------------------------------
def cisty(text: str) -> str:
    """Odstraní markdown značky, aby se text neobjevil na snímku i s hvězdičkami."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = text.replace("**", "").replace("`", "")
    return re.sub(r"\s+", " ", text).strip()


def zkrat(text: str, nejdele: int) -> str:
    """Zkrátí text na míru snímku a ukončí ho elipsou uprostřed slova."""
    if len(text) <= nejdele:
        return text
    return text[:nejdele - 3].rsplit(" ", 1)[0] + " …"


def nacti_protokol() -> str:
    """Přečte protokol; bez něj není co prezentovat."""
    cesta = cfg.OUT_DIR / "vysledky.md"
    if not cesta.exists():
        raise SystemExit(
            f"Chybí {cesta} — nejdřív spusť `uv run python src/analysis.py`."
        )
    return cesta.read_text(encoding="utf-8")


def zavery(protokol: str) -> dict:
    """Vybere závěry H1–H4 i s rozsudkem: {'H1': ('H0 zamítáme', 'text…')}."""
    vysledky = {}
    for zkr, rozsudek, text in re.findall(
        r"\*\*Závěr (H\d): (.+?)\*\* — (.+)", protokol
    ):
        vysledky[zkr] = (cisty(rozsudek), cisty(text))
    return vysledky


def hypotezy(protokol: str) -> dict:
    """Vybere formulaci H0 a H1 z kapitol protokolu: {'H1': ('H0: …', 'H1: …')}."""
    vysledky = {}
    for klic, radek in re.findall(r"^## \d+\. (H\d)[^\n]*\n\n(.+)", protokol,
                                  flags=re.MULTILINE):
        h0 = re.search(r"\*\*H0:\*\*\s*(.+?)\s*\*\*H1:\*\*", radek)
        h1 = re.search(r"\*\*H1:\*\*\s*(.+?\.(?:\s|$))", radek)
        if h0 and h1:
            vysledky[klic] = (f"H0: {cisty(h0.group(1))}", f"H1: {cisty(h1.group(1))}")
    return vysledky


def vetty(text: str, nejvyse: int = 4) -> list[str]:
    """Rozdělí souvislý závěr na odrážky; dlouhé věty zkrátí na míru snímku."""
    vystup = []
    for cast in re.split(r"(?<=[.!?])\s+", text):
        if len(cast.strip()) > 25:
            vystup.append(zkrat(cast.strip(), 230))
        if len(vystup) >= nejvyse:
            break
    return vystup


def obrazek(jmeno: str) -> Path:
    """Najde vygenerovaný PNG podle předčíslí (např. „31“ → 31_scree_paralelni.png)."""
    nalezeny = sorted(cfg.OUT_DIR.glob(f"{jmeno}*.png"))
    if not nalezeny:
        raise SystemExit(f"V outputs/ chybí graf {jmeno}*.png — nejdřív spusť analýzu.")
    return nalezeny[0]


def popisek_obrazku(jmeno: str) -> str:
    """Titulek grafu bereme z protokolu (záznam ![titulek](soubor)), nikoli opsujeme ručně."""
    klic = obrazek(jmeno).name
    protokol = (cfg.OUT_DIR / "vysledky.md").read_text(encoding="utf-8")
    for titulek, soubor in re.findall(r"!\[(.*?)\]\((.*?)\)", protokol):
        if Path(soubor).name == klic:
            return cisty(titulek)
    return klic


# ---------------------------------------------------------------------------
# základní stavební prvky snímků
# ---------------------------------------------------------------------------
def novy_snimek(prs: Presentation):
    """Prázdný snímek bez předlohových placeholderů."""
    snimek = prs.slides.add_slide(prs.slide_layouts[6])
    podklad = snimek.background.fill
    podklad.solid()
    podklad.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    return snimek


def textovy_box(snimek, x, y, sirka, vyska):
    """Textový box bez rámečku, s zalamováním řádků."""
    krabice = snimek.shapes.add_textbox(x, y, sirka, vyska)
    krabice.text_frame.word_wrap = True
    krabice.text_frame.margin_left = 0
    krabice.text_frame.margin_right = 0
    return krabice.text_frame


def nadpis(snimek, text: str, podtitulek: str | None = None) -> None:
    """Nadpis snímku a volitelný menší podtitulek."""
    ram = textovy_box(snimek, OKRAJ, Inches(0.3), SIRKA - 2 * OKRAJ, Inches(1.0))
    akce = ram.paragraphs[0].add_run()
    akce.text = text
    akce.font.size = Pt(28)
    akce.font.bold = True
    akce.font.color.rgb = BARVINA
    if podtitulek:
        druhy = ram.add_paragraph()
        druhy.space_before = Pt(3)
        akce2 = druhy.add_run()
        akce2.text = podtitulek
        akce2.font.size = Pt(13)
        akce2.font.color.rgb = TLUSENA


def odrazky(snimek, polozky, x, y, sirka, vyska, velkost: int = 15,
            barva=None) -> None:
    """Odrážkový seznam s textem přejatým z protokolu."""
    ram = textovy_box(snimek, x, y, sirka, vyska)
    for poradi, polozka in enumerate(polozky):
        odstavec = ram.paragraphs[0] if poradi == 0 else ram.add_paragraph()
        odstavec.space_after = Pt(9)
        akce = odstavec.add_run()
        akce.text = f"• {polozka}"
        akce.font.size = Pt(velkost)
        akce.font.color.rgb = barva if barva is not None else TEXT


def obrazek_na_snimku(snimek, jmeno, x, y, sirka, vyska) -> None:
    """Vloží PNG zachováním poměru stran; titulek bere z protokolu."""
    cesta = obrazek(jmeno)
    sirka_px, vyska_px = Image.open(cesta).size
    meritko = min(sirka / sirka_px, vyska / vyska_px)
    nova_s, nova_v = int(sirka_px * meritko), int(vyska_px * meritko)
    snimek.shapes.add_picture(
        str(cesta), x + int((sirka - nova_s) / 2), y, width=nova_s, height=nova_v
    )
    ram = textovy_box(snimek, x, y + vyska + Inches(0.03), sirka, Inches(0.34))
    odstavec = ram.paragraphs[0]
    odstavec.alignment = PP_ALIGN.CENTER
    akce = odstavec.add_run()
    akce.text = popisek_obrazku(jmeno)
    akce.font.size = Pt(11)
    akce.font.italic = True
    akce.font.color.rgb = TLUSENA


def tabulka_na_snimku(snimek, hlavicky, radky, x, y, sirka, vyska,
                      velkost: int = 12, sirky=None):
    """Tabulka s hodnotami prejatymi z protokolu (závěry hypotéz, koeficienty modelu)."""
    tabulka = snimek.shapes.add_table(
        len(radky) + 1, len(hlavicky), x, y, sirka, vyska
    ).table
    tabulka.first_row = True
    if sirky:
        for sloupec, sirka_sloupce in enumerate(sirky):
            tabulka.columns[sloupec].width = int(sirka_sloupce)
    for sloupec, nazev in enumerate(hlavicky):
        buňka = tabulka.cell(0, sloupec)
        buňka.text = str(nazev)
        buňka.fill.solid()
        buňka.fill.fore_color.rgb = BARVINA
        for odstavec in buňka.text_frame.paragraphs:
            for akce in odstavec.runs:
                akce.font.size = Pt(velkost)
                akce.font.bold = True
                akce.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    for r, radek in enumerate(radky, start=1):
        for sloupec, hodnota in enumerate(radek):
            buňka = tabulka.cell(r, sloupec)
            buňka.text = str(hodnota)
            if r % 2 == 0:
                buňka.fill.solid()
                buňka.fill.fore_color.rgb = POZADI
            for odstavec in buňka.text_frame.paragraphs:
                for akce in odstavec.runs:
                    akce.font.size = Pt(velkost)
                    akce.font.color.rgb = TEXT
    return tabulka


def paticka(snimek, poradi: int, celkem: int) -> None:
    """Patička se zdrojem a číslem snímku."""
    ram = textovy_box(snimek, OKRAJ, VYSKA - Inches(0.45),
                      SIRKA - 2 * OKRAJ, Inches(0.3))
    odstavec = ram.paragraphs[0]
    odstavec.alignment = PP_ALIGN.RIGHT
    akce = odstavec.add_run()
    akce.text = f"{ZDROJ} · {poradi}/{celkem}"
    akce.font.size = Pt(9)
    akce.font.color.rgb = TLUSENA


# ---------------------------------------------------------------------------
# text z kapitol protokolu
# ---------------------------------------------------------------------------
def vety_z_kapitoly(protokol: str, nadpis_kapitoly: str, nejvyse: int = 4,
                    nejdele: int = 240) -> list[str]:
    """Vybere souvislé věty z kapitoly protokolu; tabulky a popisky obrázků přeskočí."""
    casti = protokol.split(f"## {nadpis_kapitoly}", 1)
    if len(casti) < 2:
        return []
    telo = casti[1].split("\n## ", 1)[0]
    vystup = []
    for odstavec in telo.split("\n\n"):
        cisty_text = cisty(odstavec)
        if not cisty_text or cisty_text.startswith(("*Obrázek", "!", "|")):
            continue
        for veta in re.split(r"(?<=[.!?])\s+", cisty_text):
            if len(veta) < 60 or veta.endswith((":", "—")):
                continue
            if len(veta) > nejdele:
                veta = veta[:nejdele - 3].rsplit(" ", 1)[0] + " …"
            vystup.append(veta)
            if len(vystup) >= nejvyse:
                return vystup
    return vystup


def snimek_zaver(prs, titulek, podtitulek, klic, zav, graf):
    """Snímek se závěrem hypotézy vlevo a hlavním grafem vpravo."""
    snimek = novy_snimek(prs)
    nadpis(snimek, titulek, podtitulek)
    rozsudek, text = zav[klic]
    odrazky(snimek, [f"{klic}: {rozsudek}"] + vetty(text, 3),
            OKRAJ, Inches(1.55), Inches(5.35), Inches(5.1), velkost=14)
    obrazek_na_snimku(snimek, graf, Inches(6.2), Inches(1.45),
                      Inches(6.55), Inches(5.05))
    return snimek


def snimek_dva_grafy(prs, titulek, podtitulek, prvni, druhy,
                     texty=None):
    """Dva grafy vedle sebe, volitelný komentář pod nimi."""
    snimek = novy_snimek(prs)
    nadpis(snimek, titulek, podtitulek)
    obrazek_na_snimku(snimek, prvni, OKRAJ, Inches(1.6), Inches(5.95), Inches(4.35))
    obrazek_na_snimku(snimek, druhy, Inches(6.83), Inches(1.6),
                      Inches(5.95), Inches(4.35))
    if texty:
        odrazky(snimek, texty, OKRAJ, Inches(6.25), SIRKA - 2 * OKRAJ,
                Inches(0.85), velkost=12)
    return snimek


def souhrn_tabulka(protokol: str) -> list[list[str]]:
    """Řádky souhrnné tabulky „Rozsudek nad hypotézami“ z protokolu."""
    radky = re.findall(r"^\|\s*(H\d)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$",
                       protokol, flags=re.MULTILINE)
    vysledek = []
    for klic, nazev, rozsudek in radky:
        nazev = cisty(nazev)
        if nazev.startswith(klic):          # „H1 — H1 — …“ z duplicity názvu
            nazev = nazev[len(klic):].strip(" —")
        vysledek.append([klic, nazev, rozsudek])
    return vysledek


def titulni_snimek(prs, udaje: list[str]) -> None:
    """Úvodní snímek: název, podtitul a pár údajů o datech."""
    snimek = novy_snimek(prs)
    podklad = snimek.background.fill
    podklad.solid()
    podklad.fore_color.rgb = BARVINA

    ram = textovy_box(snimek, OKRAJ, Inches(2.15), SIRKA - 2 * OKRAJ, Inches(1.6))
    akce = ram.paragraphs[0].add_run()
    akce.text = "Střelecká úspěšnost"
    akce.font.size = Pt(52)
    akce.font.bold = True
    akce.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    druhy = ram.add_paragraph()
    akce2 = druhy.add_run()
    akce2.text = "Multivariační analýza střel: ANOVA, PCA, shlukování a logistický model"
    akce2.font.size = Pt(20)
    akce2.font.color.rgb = RGBColor(0xD7, 0xE3, 0xEE)

    odrazky(snimek, udaje, OKRAJ, Inches(4.35), SIRKA - 2 * OKRAJ,
            Inches(2.2), velkost=14, barva=RGBColor(0xD7, 0xE3, 0xEE))


# ---------------------------------------------------------------------------
# sestavení prezentace
def udaje_o_souboru(protokol: str) -> str:
    """Údaj o datech na titulek — číslice bereme z protokolu, ne z hlavy."""
    predeps = re.search(
        r"Načteno \*\*(\d+)\*\* záznamů o střelách \((\d+) střelců, "
        r"(\d+) zápasů, sezóny ([^)]*)\)", protokol)
    if not predeps:
        return "data: data/shots_outcome_Final.csv"
    radku, strelcu, zapasu, sezony = predeps.groups()
    return (f"data: data/shots_outcome_Final.csv — {radku} záznamů o střelách, "
            f"{strelcu} střelců, {zapasu} zápasů, {sezony.strip()}")


def vyber_vety(text: str, klice: list[str], nejvyse: int = 2) -> list[str]:
    """Z dlouhého odstavce vybere věty s hledaným výrazem (na limitace a poznámky)."""
    nalezeny = [v for v in vetty(text, 8) if any(k in v for k in klice)]
    return nalezeny[:nejvyse]


def snimek_text(prs, titulek, podtitulek, texty, graf=None):
    """Odrážky vlevo, volitelný graf vpravo."""
    snimek = novy_snimek(prs)
    nadpis(snimek, titulek, podtitulek)
    sirka_textu = Inches(5.35) if graf else SIRKA - 2 * OKRAJ
    odrazky(snimek, texty, OKRAJ, Inches(1.6), sirka_textu, Inches(5.1), velkost=14)
    if graf:
        obrazek_na_snimku(snimek, graf, Inches(6.2), Inches(1.6),
                          Inches(6.55), Inches(4.9))
    return snimek


def snimek_hypotezy(prs, hyp: dict):
    """Snímek s formulací H0 a H1 pro všechny čtyři hypotézy."""
    snimek = novy_snimek(prs)
    nadpis(snimek, "Hypotézy: H0 a H1",
           "vše se testuje na α = 0,05 a vždy s explicitním závěrem "
           "„H0 zamítáme“ / „H0 nezamítáme“")
    radky = []
    for klic in ("H1", "H2", "H3", "H4"):
        if klic not in hyp:
            continue
        h0, h1 = hyp[klic]
        radky.append([klic, zkrat(h0, 250)[4:], zkrat(h1, 250)[4:]])
    tabulka_na_snimku(snimek, ["hypotéza", "H0 (nulová hypotéza)",
                               "H1 (alternativní hypotéza)"], radky,
                      OKRAJ, Inches(1.75), SIRKA - 2 * OKRAJ, Inches(4.6),
                      velkost=11,
                      sirky=[Inches(1.0), Inches(5.6), Inches(5.6)])
    return snimek


def snimek_souhrn(prs, radky: list[list[str]], zav: dict):
    """Rozsudek nad hypotézami na α = 0,05."""
    snimek = novy_snimek(prs)
    nadpis(snimek, "Rozsudek nad hypotézami",
           "každý test byl vyhodnocen na α = 0,05 s explicitním závěrem")
    tabulka_na_snimku(snimek, ["hypotéza", "co zkoumáme", "závěr na α = 0,05"],
                      radky, OKRAJ, Inches(1.75), Inches(8.6), Inches(2.2),
                      velkost=13,
                      sirky=[Inches(1.3), Inches(4.5), Inches(2.8)])
    shrnuti = [
        f"{klic} — {cisty(zav[klic][0])}: {vetty(zav[klic][1], 1)[0]}"
        for klic in ("H1", "H2", "H3", "H4") if klic in zav
    ]
    odrazky(snimek, shrnuti, OKRAJ, Inches(4.3), SIRKA - 2 * OKRAJ,
            Inches(2.4), velkost=13)
    return snimek


def snimek_limitace(prs, zav: dict):
    """Co z analýzy plyne a kde jsou meze — věty přejaté z protokolu."""
    snimek = novy_snimek(prs)
    nadpis(snimek, "Závěr: co ukazuje analýza a kde jsou meze",
           "limitace hlásíme sami, nečekáme, až se na ně zeptá zadávající")
    poznatky = [
        vetty(zav["H1"][1], 1)[0],
        vetty(zav["H2"][1], 1)[0],
        vetty(zav["H3"][1], 1)[0],
        vetty(zav["H4"][1], 1)[0],
    ]
    meze = (vyber_vety(zav["H2"][1], ["KMO"], 1)
            + vyber_vety(zav["H3"][1], ["t-SNE"], 1)
            + vyber_vety(zav["H4"][1], ["odlehl", "Penalt", "penalt"], 1)
            + ["Data jsou observační, ne experimentální — model ukazuje "
               "asociaci, nikoli příčinu: z polohy nelze usuzovat, že střelec "
               "„umí“ víc, jen že střílel z lepší pozice."])
    ram = textovy_box(snimek, OKRAJ, Inches(1.5), Inches(5.9), Inches(0.4))
    akce = ram.paragraphs[0].add_run()
    akce.text = "Co ukazuje analýza"
    akce.font.size = Pt(16)
    akce.font.bold = True
    akce.font.color.rgb = BARVINA
    odrazky(snimek, poznatky, OKRAJ, Inches(2.0), Inches(5.9), Inches(4.6),
            velkost=13)
    druhy_x = Inches(6.85)
    ram2 = textovy_box(snimek, druhy_x, Inches(1.5), Inches(5.9), Inches(0.4))
    akce2 = ram2.paragraphs[0].add_run()
    akce2.text = "Kde jsou meze"
    akce2.font.size = Pt(16)
    akce2.font.bold = True
    akce2.font.color.rgb = BARVINA
    odrazky(snimek, meze, druhy_x, Inches(2.0), Inches(5.9), Inches(4.6),
            velkost=13)
    return snimek


def main() -> int:
    """Sestaví .pptx z protokolu a z grafů v outputs/; uloží do presentation/."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    protokol = nacti_protokol()
    zav = zavery(protokol)
    hyp = hypotezy(protokol)
    chybějící = [klic for klic in ("H1", "H2", "H3", "H4") if klic not in zav]
    if chybějící:
        raise SystemExit(f"V protokolu chybí závěr {', '.join(chybějící)}.")

    pocet_grafu = len(list(cfg.OUT_DIR.glob("*.png")))
    pocet_tabulek = len(list(cfg.TABULKY_DIR.glob("*.csv")))
    priprava = vety_z_kapitoly(protokol, "1. Předzpracování dat", 5)
    priprava_text = " ".join(priprava)

    prs = Presentation()
    prs.slide_width, prs.slide_height = SIRKA, VYSKA

    titulni_snimek(prs, [
        udaje_o_souboru(protokol),
        "metody: ANOVA · MANOVA · χ² test · PCA a faktorová analýza · "
        "Wardova metoda · K-means · logistická regrese",
        f"výstupy: {pocet_grafu} grafů, {pocet_tabulek} tabulek a protokol "
        f"outputs/vysledky.md",
        "reprodukovatelnost: uv run python src/analysis.py → "
        "uv run python src/make_presentation.py",
    ])

    snimek_text(prs, "Data a předzpracování",
                "co jsme se souřadnicemi, chybějícími hodnotami a extrémy "
                "udělali, než jsme začali testovat", priprava[1:4], "03")
    snimek_dva_grafy(
        prs, "Kvalita dat: chybějící hodnoty a odlehlé střely",
        "chybějící souřadnice řešíme vynecháním (ne imputací), "
        "odlehlé hodnoty neodstraňujeme mlčky", "01", "04",
        texty=vyber_vety(priprava_text, ["MCAR", "Mahalanobis", "odlehl"], 2))
    snimek_dva_grafy(
        prs, "Přehled vztahů mezi příznaky",
        "distribuce příznaků a korelační matice před redukcí dimenze",
        "05", "02",
        texty=vyber_vety(priprava_text, ["korelac", "standardiz"], 2))
    snimek_hypotezy(prs, hyp)

    snimek_zaver(prs, "H1 — zakončení a herní situace",
                 "ANOVA, Welchův test, Kruskal–Wallis, MANOVA a χ² test "
                 "nezávislosti; efekty měříme přes η² a ω²", "H1", zav, "16")
    snimek_dva_grafy(prs, "H1 — herní situace a nezávislost kategorií",
                     "profil podle situace a kontingenční tabulky "
                     "(χ² diagram, Cramérovo V)", "26", "27")
    snimek_dva_grafy(prs, "H1 — srovnání rozdělení parametrů střely",
                     "krabicové grafy: vzdálenost od branky a úhel odkrytý brankou",
                     "12", "24")

    snimek_zaver(prs, "H2 — redukce dimenze",
                 "Bartlettův test sférickosti, KMO, graf úpatí, paralelní analýza, "
                 "varimax rotace, komunality", "H2", zav, "31")
    snimek_dva_grafy(prs, "H2 — biplot a rotované faktory",
                     "které příznaky drží první komponentu a co se změní po rotaci",
                     "32", "33")

    snimek_zaver(prs, "H3 — shluková struktura střel",
                 "Wardova metoda, K-means, permutační test silhouette, "
                 "stabilita rozdělení (ARI)", "H3", zav, "41")
    snimek_dva_grafy(prs, "H3 — shluky na hřišti a jejich profil",
                     "kde na hřišti které shluky leží a čím se liší "
                     "(úspěšnost, poloha, xG)", "42", "43")
    snimek_dva_grafy(prs, "H3 — dendrogram a kontrola nelinearity",
                     "hierarchický pohled (Ward) a t-SNE projekce", "40", "44")

    snimek_zaver(prs, "H4 — model úspěšnosti střely",
                 "logistická regrese, Waldovy testy, šance (OR), AUC, "
                 "cross-validace, LDA", "H4", zav, "51")
    snimek_dva_grafy(prs, "H4 — velikost efektu a kalibrace",
                     "šance příznaků a kalibrační křivka modelu "
                     "(Hosmer–Lemeshow)", "50", "53")
    snimek_dva_grafy(prs, "H4 — klasifikace a mapa pravděpodobnosti",
                     "konfuse při Youdenově prahu a mapa gólových šancí "
                     "pouze z polohy", "52", "54")

    snimek_souhrn(prs, souhrn_tabulka(protokol), zav)
    snimek_limitace(prs, zav)

    snimky = list(prs.slides)
    for poradi, snimek in enumerate(snimky, start=1):
        if poradi > 1:
            paticka(snimek, poradi, len(snimky))

    cfg.zajisti_slozky()
    cesta = cfg.PREZ_DIR / "VMM_strelecka_uspesnost.pptx"
    prs.save(cesta)
    print(f"prezentace: {cesta} ({len(snimky)} snímků)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
