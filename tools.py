"""
Elektrotechnické výpočty, ktoré agent sprístupňuje jazykovému modelu ako nástroje.

Každá funkcia je čistý výpočet bez vedľajších efektov: dostane čísla, vráti slovník.
LLM ich nevolá priamo — volá ich agent v `main.py` na základe tool callu.

POZOR: zjednodušený model pre školské účely. Viď disclaimer v README.md.
"""

import math

# --- Konštanty a normalizované rady ------------------------------------------

# Merný odpor vodiča pri prevádzkovej teplote [Ω·mm²/m].
# Vyššie než tabuľková hodnota pri 20 °C, aby výpočet bol na strane bezpečnosti.
REZIVITA = {"Cu": 0.0178, "Al": 0.0286}

# Normalizovaná rada prierezov jadier [mm²] — STN EN 60228.
PRIEREZY = [1.5, 2.5, 4, 6, 10, 16, 25, 35, 50, 70, 95, 120, 150, 185, 240]

# Menovité prúdy ističov [A] — STN EN 60898-1.
ISTICE = [6, 10, 13, 16, 20, 25, 32, 40, 50, 63, 80, 100]

# Vypínacie charakteristiky ističov a ich typické použitie.
CHARAKTERISTIKY = {
    "bezny": ("B", "svetelné a zásuvkové okruhy, odporová záťaž"),
    "motor": ("C", "motory, čerpadlá, klimatizácie, induktívna záťaž"),
    "tvrdy_rozbeh": ("D", "transformátory, zváračky, stroje s tvrdým rozbehom"),
}

# Zjednodušená prúdová zaťažiteľnosť medených káblov [A].
# Uloženie B2 (viacžilový kábel v inštalačnej rúrke v stene), PVC izolácia,
# teplota okolia 30 °C, bez zoskupenia. Odvodené z IEC 60364-5-52, tab. B.52.4.
ZATAZITELNOST_CU = {
    1.5: {1: 16.5, 3: 15.0},
    2.5: {1: 23.0, 3: 20.0},
    4: {1: 30.0, 3: 27.0},
    6: {1: 38.0, 3: 34.0},
    10: {1: 52.0, 3: 46.0},
    16: {1: 69.0, 3: 62.0},
    25: {1: 90.0, 3: 80.0},
    35: {1: 111.0, 3: 99.0},
    50: {1: 133.0, 3: 118.0},
    70: {1: 168.0, 3: 149.0},
    95: {1: 201.0, 3: 179.0},
    120: {1: 232.0, 3: 206.0},
    150: {1: 258.0, 3: 225.0},
    185: {1: 294.0, 3: 255.0},
    240: {1: 344.0, 3: 297.0},
}

# Hliník má vyšší merný odpor → nižšiu zaťažiteľnosť. Zjednodušený prepočet.
KOEF_HLINIK = 0.78

# Odporúčané limity úbytku napätia od miesta pripojenia [%] — STN 33 2000-5-52.
LIMIT_UBYTKU = {"osvetlenie": 3.0, "ostatne": 5.0}

# Keď typ obvodu nepoznáme, platí prísnejší z limitov — poddimenzovaný vodič
# je horšia chyba než zbytočne hrubý.
PREDVOLENY_UBYTOK_PCT = min(LIMIT_UBYTKU.values())

# Menovité napätia sústavy [V] podľa počtu fáz. Slúžia na odvodenie počtu fáz,
# keď ho volajúci nezadá — pozri `_odvod_fazy`.
NAPATIA = {1: 230.0, 3: 400.0}
TOLERANCIA_NAPATIA = 0.10

# Hodnoty, ktoré sa doplnia, keď ich volajúci nezadá. Sú na jednom mieste zámerne:
# každý nástroj vracia v `pouzite_predvolby` zoznam toho, čo si domyslel, aby model
# vedel rozlíšiť zadaný údaj od predpokladu. `fazy` tu chýba schválne — neháda sa,
# odvodzuje sa z napätia.
PREDVOLBY = {
    "cos_fi": 0.9,
    "ucinnost": 1.0,
    "material": "Cu",
    "typ": "bezny",
}


# --- Pomocné funkcie ---------------------------------------------------------


def _zatazitelnost(prierez_mm2: float, fazy: int, material: str) -> float:
    """Prúdová zaťažiteľnosť daného prierezu [A]."""
    zaklad = ZATAZITELNOST_CU[prierez_mm2][fazy]
    return zaklad * KOEF_HLINIK if material == "Al" else zaklad


def _over_fazy(fazy) -> int:
    """
    Overí počet fáz a vráti ho ako celé číslo.

    Prevod cez reťazec je tu zámerne: slabšie modely posielajú v tool calle
    čísla ako text ("3"). Desatinné hodnoty ani nezmysly neprejdú.
    """
    try:
        cislo = int(str(fazy).strip())
    except (TypeError, ValueError):
        cislo = None
    if cislo not in (1, 3):
        raise ValueError("Parameter 'fazy' musí byť 1 (jednofázové) alebo 3 (trojfázové).")
    return cislo


def _over_material(material: str) -> None:
    if material not in REZIVITA:
        raise ValueError("Parameter 'material' musí byť 'Cu' (meď) alebo 'Al' (hliník).")


def _predvolba(nazov: str, hodnota, doplnene: list[str]):
    """
    Vráti zadanú hodnotu, alebo predvolenú — a tú si poznačí do `doplnene`.

    Vďaka tomu nástroj v odpovedi presne povie, ktoré čísla pochádzajú zo zadania
    a ktoré si domyslel.
    """
    if hodnota is not None:
        return hodnota
    doplnene.append(nazov)
    return PREDVOLBY[nazov]


def _odvod_fazy(napatie_v: float, fazy: int | None, doplnene: list[str]) -> int:
    """
    Určí počet fáz z napätia, ak ho volajúci nezadal.

    Počet fáz sa zámerne nerieši predvolenou hodnotou: pri 230 V by trojfázový
    vzorec podhodnotil prúd o 42 % — teda smerom k poddimenzovanému vodiču
    a ističu. Radšej sa odvodí z napätia, prípadne si vypýta doplnenie.
    """
    odvodene = next(
        (f for f, u in NAPATIA.items() if abs(napatie_v - u) <= u * TOLERANCIA_NAPATIA),
        None,
    )

    if fazy is None:
        if odvodene is None:
            raise ValueError(
                f"Z napätia {napatie_v} V sa počet fáz odvodiť nedá (štandard je 230 V "
                "jednofázovo a 400 V trojfázovo). Zadaj parameter 'fazy' explicitne."
            )
        doplnene.append("fazy")
        return odvodene

    fazy = _over_fazy(fazy)
    if odvodene is not None and fazy != odvodene:
        raise ValueError(
            f"Napätie {napatie_v} V a fazy={fazy} si odporujú — {NAPATIA[odvodene]:.0f} V "
            f"je {'jednofázové' if odvodene == 1 else 'trojfázové'} napätie. "
            "Oprav jeden z tých dvoch parametrov."
        )
    return fazy


# --- Nástroj 1: prúd zo záťaže -----------------------------------------------


def vypocitaj_prud(
    vykon_w: float,
    napatie_v: float,
    fazy: int | None = None,
    cos_fi: float | None = None,
    ucinnost: float | None = None,
) -> dict:
    """
    Vypočíta prevádzkový prúd spotrebiča z jeho výkonu.

        jednofázové:  I = P / (U × cosφ × η)
        trojfázové:   I = P / (√3 × U × cosφ × η)
    """
    if vykon_w <= 0:
        raise ValueError("Výkon musí byť kladné číslo (vo wattoch).")
    if napatie_v <= 0:
        raise ValueError("Napätie musí byť kladné číslo (vo voltoch).")

    doplnene: list[str] = []
    fazy = _odvod_fazy(napatie_v, fazy, doplnene)
    cos_fi = _predvolba("cos_fi", cos_fi, doplnene)
    ucinnost = _predvolba("ucinnost", ucinnost, doplnene)

    if not 0 < cos_fi <= 1:
        raise ValueError("Účinník cos φ musí byť v rozsahu (0; 1].")
    if not 0 < ucinnost <= 1:
        raise ValueError("Účinnosť musí byť v rozsahu (0; 1].")

    if fazy == 3:
        prud = vykon_w / (math.sqrt(3) * napatie_v * cos_fi * ucinnost)
        vzorec = "I = P / (√3 × U × cosφ × η)"
    else:
        prud = vykon_w / (napatie_v * cos_fi * ucinnost)
        vzorec = "I = P / (U × cosφ × η)"

    return {
        "prud_a": round(prud, 2),
        "vykon_w": vykon_w,
        "napatie_v": napatie_v,
        "fazy": fazy,
        "cos_fi": cos_fi,
        "ucinnost": ucinnost,
        "vzorec": vzorec,
        "pouzite_predvolby": doplnene,
    }


# --- Nástroj 2: návrh prierezu vodiča ----------------------------------------


def navrhni_prierez(
    prud_a: float,
    dlzka_m: float,
    napatie_v: float,
    fazy: int | None = None,
    max_ubytok_pct: float | None = None,
    typ_obvodu: str | None = None,
    cos_fi: float | None = None,
    material: str | None = None,
) -> dict:
    """
    Navrhne prierez vodiča. Posudzuje dve nezávislé kritériá a berie prísnejšie:

      1. úbytok napätia
             jednofázové:  S = (2 × ρ × L × I × cosφ) / ΔU_max
             trojfázové:   S = (√3 × ρ × L × I × cosφ) / ΔU_max
      2. prúdová zaťažiteľnosť (tabuľka pre uloženie B2)
    """
    if prud_a <= 0:
        raise ValueError("Prúd musí byť kladné číslo (v ampéroch).")
    if dlzka_m <= 0:
        raise ValueError("Dĺžka vedenia musí byť kladné číslo (v metroch).")
    if napatie_v <= 0:
        raise ValueError("Napätie musí byť kladné číslo (vo voltoch).")

    doplnene: list[str] = []
    fazy = _odvod_fazy(napatie_v, fazy, doplnene)
    cos_fi = _predvolba("cos_fi", cos_fi, doplnene)
    material = _predvolba("material", material, doplnene)
    _over_material(material)

    # Explicitný limit má prednosť pred typom obvodu; ak nie je ani jedno,
    # platí prísnejšia z hodnôt v LIMIT_UBYTKU.
    if max_ubytok_pct is None:
        if typ_obvodu is None:
            max_ubytok_pct = PREDVOLENY_UBYTOK_PCT
            doplnene.append("max_ubytok_pct")
        elif typ_obvodu not in LIMIT_UBYTKU:
            raise ValueError(
                f"Neznámy typ obvodu '{typ_obvodu}'. Použi jednu z hodnôt: "
                f"{', '.join(LIMIT_UBYTKU)}."
            )
        else:
            max_ubytok_pct = LIMIT_UBYTKU[typ_obvodu]

    if not 0 < max_ubytok_pct <= 100:
        raise ValueError("Maximálny úbytok napätia musí byť v rozsahu (0; 100] %.")

    ro = REZIVITA[material]
    koef = math.sqrt(3) if fazy == 3 else 2
    ubytok_max_v = napatie_v * max_ubytok_pct / 100

    # Kritérium 1 — úbytok napätia.
    s_teoreticky = (koef * ro * dlzka_m * prud_a * cos_fi) / ubytok_max_v
    podla_ubytku = next((s for s in PRIEREZY if s >= s_teoreticky), None)

    # Kritérium 2 — prúdová zaťažiteľnosť.
    podla_zatazenia = next(
        (s for s in PRIEREZY if _zatazitelnost(s, fazy, material) >= prud_a), None
    )

    if podla_ubytku is None or podla_zatazenia is None:
        raise ValueError(
            f"Požiadavku nepokryje ani najväčší prierez v rade ({PRIEREZY[-1]} mm²). "
            "Skontroluj zadané hodnoty alebo rozdeľ vedenie na viac paralelných vetiev."
        )

    navrhnuty = max(podla_ubytku, podla_zatazenia)
    rozhodlo = "úbytok napätia" if podla_ubytku >= podla_zatazenia else "prúdová zaťažiteľnosť"

    # Skutočný úbytok na navrhnutom priereze.
    ubytok_v = (koef * ro * dlzka_m * prud_a * cos_fi) / navrhnuty

    return {
        "navrhnuty_prierez_mm2": navrhnuty,
        "rozhodujuce_kriterium": rozhodlo,
        "teoreticky_minimalny_prierez_mm2": round(s_teoreticky, 3),
        "prierez_podla_ubytku_mm2": podla_ubytku,
        "prierez_podla_zatazitelnosti_mm2": podla_zatazenia,
        "skutocny_ubytok_v": round(ubytok_v, 2),
        "skutocny_ubytok_pct": round(ubytok_v / napatie_v * 100, 2),
        "povoleny_ubytok_pct": max_ubytok_pct,
        "zatazitelnost_navrhnuteho_a": round(_zatazitelnost(navrhnuty, fazy, material), 1),
        "material": material,
        "dlzka_m": dlzka_m,
        "prud_a": prud_a,
        "fazy": fazy,
        "typ_obvodu": typ_obvodu,
        "pouzite_predvolby": doplnene,
    }


# --- Nástroj 3: návrh ističa -------------------------------------------------


def navrhni_istic(
    prud_a: float,
    typ: str | None = None,
    prierez_mm2: float | None = None,
    fazy: int | None = None,
    material: str | None = None,
) -> dict:
    """
    Vyberie najbližší vyšší menovitý prúd ističa z normalizovanej rady
    a určí vypínaciu charakteristiku podľa typu záťaže.

    Ak je zadaný prierez vodiča, overí základnú podmienku istenia:  Ib ≤ In ≤ Iz
    """
    if prud_a <= 0:
        raise ValueError("Prúd musí byť kladné číslo (v ampéroch).")

    doplnene: list[str] = []
    typ = _predvolba("typ", typ, doplnene)
    material = _predvolba("material", material, doplnene)
    _over_material(material)

    # Tento nástroj nepozná napätie, takže fázy odvodiť nevie. Používa ich len
    # na kontrolu zaťažiteľnosti kábla nižšie, kde je 3 bezpečnejšia voľba —
    # trojfázové uloženie má v tabuľke nižšiu zaťažiteľnosť než jednofázové.
    if fazy is None:
        fazy = 3
        if prierez_mm2 is not None:
            doplnene.append("fazy")
    else:
        fazy = _over_fazy(fazy)

    if typ not in CHARAKTERISTIKY:
        raise ValueError(
            f"Neznámy typ záťaže '{typ}'. Použi jednu z hodnôt: {', '.join(CHARAKTERISTIKY)}."
        )

    menovity = next((i for i in ISTICE if i >= prud_a), None)
    if menovity is None:
        raise ValueError(
            f"Prúd {prud_a} A presahuje najväčší istič v rade ({ISTICE[-1]} A). "
            "Pre takúto záťaž treba iný typ prístroja (napr. výkonový istič)."
        )

    charakteristika, pouzitie = CHARAKTERISTIKY[typ]

    vysledok = {
        "menovity_prud_a": menovity,
        "charakteristika": charakteristika,
        "oznacenie": f"{charakteristika}{menovity}",
        "typ_zataze": typ,
        "vhodne_pre": pouzitie,
        "prevadzkovy_prud_a": prud_a,
        "pouzite_predvolby": doplnene,
    }

    # Kontrola Ib ≤ In ≤ Iz — istič nesmie pustiť viac, než vydrží kábel.
    if prierez_mm2 is not None:
        if prierez_mm2 not in ZATAZITELNOST_CU:
            raise ValueError(
                f"Prierez {prierez_mm2} mm² nie je v normalizovanej rade: {PRIEREZY}"
            )
        iz = _zatazitelnost(prierez_mm2, fazy, material)
        vyhovuje = menovity <= iz
        vysledok["kontrola_kabla"] = {
            "prierez_mm2": prierez_mm2,
            "zatazitelnost_kabla_a": round(iz, 1),
            "vyhovuje": vyhovuje,
            "podmienka": "Ib ≤ In ≤ Iz",
            "poznamka": (
                "Istič chráni vodič pred preťažením."
                if vyhovuje
                else f"NEVYHOVUJE: istič {menovity} A prepustí viac než znesie "
                f"kábel {prierez_mm2} mm² ({iz:.1f} A). Zvoľ väčší prierez."
            ),
        }

    return vysledok


# --- Register nástrojov a ich schémy pre LLM ---------------------------------

DOSTUPNE_FUNKCIE = {
    "vypocitaj_prud": vypocitaj_prud,
    "navrhni_prierez": navrhni_prierez,
    "navrhni_istic": navrhni_istic,
}

# Štandardná OpenAI schéma nástrojov. LiteLLM ju preloží pre ktoréhokoľvek providera.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "vypocitaj_prud",
            "description": (
                "Vypočíta prevádzkový prúd elektrického spotrebiča z jeho výkonu, "
                "napätia a účinníka. Použi vždy, keď je záťaž zadaná vo wattoch alebo "
                "kilowattoch a potrebuješ prúd v ampéroch (napr. pred návrhom ističa "
                "alebo prierezu vodiča)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "vykon_w": {
                        "type": "number",
                        "description": "Činný výkon spotrebiča vo wattoch, napr. 7500 pre 7,5 kW.",
                    },
                    "napatie_v": {
                        "type": "number",
                        "description": "Menovité napätie vo voltoch: 230 pre jednofázové, 400 pre trojfázové.",
                    },
                    "fazy": {
                        # Bez "enum": [1, 3] — Google/Gemini povoľuje enum len pri
                        # reťazcoch a číselný variant odmietne celú schému. Rozsah
                        # stráži `_over_fazy`, ktorá modelu vráti zrozumiteľnú chybu.
                        "type": "integer",
                        "description": (
                            "Počet fáz. NEUVÁDZAJ, ak je napätie 230 V alebo 400 V — nástroj "
                            "si ho odvodí sám (230 V → 1, 400 V → 3). Zadaj len pri "
                            "neštandardnom napätí, kde sa odvodiť nedá."
                        ),
                    },
                    "cos_fi": {
                        "type": "number",
                        "description": (
                            "Účinník cos φ. Odporová záťaž (ohrievač, priamy ohrev) 1,0; "
                            "motory 0,8–0,85; bežná zmiešaná záťaž 0,9. Ak neuvedieš, "
                            "doplní sa 0,9 a nástroj to oznámi v 'pouzite_predvolby'."
                        ),
                    },
                    "ucinnost": {
                        "type": "number",
                        "description": (
                            "Účinnosť η zariadenia (0 až 1). Použi len ak je zadaný výkon "
                            "na hriadeli motora, nie príkon zo siete. Ak neuvedieš, "
                            "doplní sa 1,0 (teda bez prepočtu)."
                        ),
                    },
                },
                "required": ["vykon_w", "napatie_v"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "navrhni_prierez",
            "description": (
                "Navrhne prierez vodiča v mm² pre daný prúd a dĺžku vedenia. Posudzuje "
                "úbytok napätia aj prúdovú zaťažiteľnosť a vráti prísnejší z oboch výsledkov "
                "spolu so skutočným úbytkom napätia. Použi po tom, čo poznáš prúd."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prud_a": {
                        "type": "number",
                        "description": "Prevádzkový prúd v ampéroch (výstup z vypocitaj_prud).",
                    },
                    "dlzka_m": {
                        "type": "number",
                        "description": "Dĺžka vedenia v metroch od rozvádzača k spotrebiču (jednosmerne).",
                    },
                    "napatie_v": {
                        "type": "number",
                        "description": "Menovité napätie vo voltoch: 230 alebo 400.",
                    },
                    "fazy": {
                        # Bez "enum": [1, 3] — Google/Gemini povoľuje enum len pri
                        # reťazcoch a číselný variant odmietne celú schému. Rozsah
                        # stráži `_over_fazy`, ktorá modelu vráti zrozumiteľnú chybu.
                        "type": "integer",
                        "description": (
                            "Počet fáz. NEUVÁDZAJ pri 230 V ani 400 V — nástroj si ho odvodí "
                            "z napätia. Zadaj len pri neštandardnom napätí."
                        ),
                    },
                    "typ_obvodu": {
                        "type": "string",
                        "enum": ["osvetlenie", "ostatne"],
                        "description": (
                            "Druh obvodu — určí povolený úbytok napätia podľa normy: "
                            "'osvetlenie' = 3 %, 'ostatne' = 5 % (zásuvky, stroje, ohrev). "
                            "Ak neuvedieš ani toto, ani max_ubytok_pct, použije sa "
                            "prísnejšia hodnota 3 %."
                        ),
                    },
                    "max_ubytok_pct": {
                        "type": "number",
                        "description": (
                            "Maximálny povolený úbytok napätia v percentách. Zadaj len ak "
                            "chceš prebiť hodnotu odvodenú z 'typ_obvodu' vlastným limitom."
                        ),
                    },
                    "cos_fi": {
                        "type": "number",
                        "description": (
                            "Účinník cos φ záťaže. Ak neuvedieš, doplní sa 0,9 a nástroj "
                            "to oznámi v 'pouzite_predvolby'."
                        ),
                    },
                    "material": {
                        "type": "string",
                        "enum": ["Cu", "Al"],
                        "description": (
                            "Materiál jadra: Cu = meď, Al = hliník. Ak neuvedieš, doplní sa Cu."
                        ),
                    },
                },
                "required": ["prud_a", "dlzka_m", "napatie_v"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "navrhni_istic",
            "description": (
                "Vyberie menovitý prúd a vypínaciu charakteristiku ističa z normalizovanej "
                "rady. Ak zadáš aj prierez vodiča, overí podmienku istenia Ib ≤ In ≤ Iz, "
                "teda či istič skutočne ochráni kábel. Použi po výpočte prúdu."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prud_a": {
                        "type": "number",
                        "description": "Prevádzkový prúd záťaže v ampéroch.",
                    },
                    "typ": {
                        "type": "string",
                        "enum": ["bezny", "motor", "tvrdy_rozbeh"],
                        "description": (
                            "Typ záťaže — určuje charakteristiku. 'bezny' = B (svetlá, "
                            "zásuvky), 'motor' = C (motory, čerpadlá, klimatizácie), "
                            "'tvrdy_rozbeh' = D (zváračky, transformátory). Ak neuvedieš, "
                            "doplní sa 'bezny' — pri motore to však spôsobí vypínanie "
                            "pri rozbehu, tak ho uveď."
                        ),
                    },
                    "prierez_mm2": {
                        "type": "number",
                        "description": (
                            "Voliteľné: prierez vodiča v mm² pre kontrolu, či istič ochráni "
                            "kábel. Zadaj, ak už prierez poznáš."
                        ),
                    },
                    "fazy": {
                        # Bez "enum": [1, 3] — Google/Gemini povoľuje enum len pri
                        # reťazcoch a číselný variant odmietne celú schému. Rozsah
                        # stráži `_over_fazy`, ktorá modelu vráti zrozumiteľnú chybu.
                        "type": "integer",
                        "description": (
                            "Počet fáz — použije sa len pri kontrole zaťažiteľnosti kábla. "
                            "Ak neuvedieš, počíta sa s prísnejšou trojfázovou hodnotou."
                        ),
                    },
                    "material": {
                        "type": "string",
                        "enum": ["Cu", "Al"],
                        "description": "Materiál jadra vodiča. Ak neuvedieš, doplní sa Cu.",
                    },
                },
                "required": ["prud_a"],
            },
        },
    },
]
