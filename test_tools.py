"""
Testy výpočtových nástrojov — bežia bez API kľúča a bez pripojenia na internet.

Overujú, že matematika sedí ešte predtým, než ju dostane do rúk jazykový model.

Spustenie:
    uv run test_tools.py        # samostatne
    uv run pytest               # cez pytest, ak je nainštalovaný
"""

import math

from tools import navrhni_istic, navrhni_prierez, vypocitaj_prud


def test_prud_trojfazovy():
    # I = 7500 / (√3 × 400 × 0,85) = 12,74 A
    vysledok = vypocitaj_prud(vykon_w=7500, napatie_v=400, fazy=3, cos_fi=0.85)
    ocakavane = 7500 / (math.sqrt(3) * 400 * 0.85)
    assert abs(vysledok["prud_a"] - ocakavane) < 0.01
    assert vysledok["prud_a"] == 12.74


def test_prud_jednofazovy():
    # I = 2300 / (230 × 1,0) = 10,0 A
    vysledok = vypocitaj_prud(vykon_w=2300, napatie_v=230, fazy=1, cos_fi=1.0)
    assert vysledok["prud_a"] == 10.0


def test_prud_zohladnuje_ucinnost():
    bez = vypocitaj_prud(vykon_w=3000, napatie_v=400, ucinnost=1.0)["prud_a"]
    s_ucinnostou = vypocitaj_prud(vykon_w=3000, napatie_v=400, ucinnost=0.8)["prud_a"]
    assert s_ucinnostou > bez


def test_prierez_rozhoduje_ubytok():
    # Dlhé vedenie s malým prúdom → limituje úbytok napätia.
    vysledok = navrhni_prierez(prud_a=16, dlzka_m=120, napatie_v=400, fazy=3)
    assert vysledok["rozhodujuce_kriterium"] == "úbytok napätia"
    assert vysledok["skutocny_ubytok_pct"] <= vysledok["povoleny_ubytok_pct"]


def test_prierez_rozhoduje_zatazitelnost():
    # Krátke vedenie s veľkým prúdom → limituje prúdová zaťažiteľnosť.
    vysledok = navrhni_prierez(prud_a=40, dlzka_m=5, napatie_v=400, fazy=3)
    assert vysledok["rozhodujuce_kriterium"] == "prúdová zaťažiteľnosť"
    assert vysledok["zatazitelnost_navrhnuteho_a"] >= 40


def test_prierez_je_z_normalizovanej_rady():
    from tools import PRIEREZY

    vysledok = navrhni_prierez(prud_a=25, dlzka_m=40, napatie_v=400)
    assert vysledok["navrhnuty_prierez_mm2"] in PRIEREZY


def test_hlinik_vyzaduje_vacsi_prierez():
    medeny = navrhni_prierez(prud_a=25, dlzka_m=50, napatie_v=400, material="Cu")
    hlinikovy = navrhni_prierez(prud_a=25, dlzka_m=50, napatie_v=400, material="Al")
    assert hlinikovy["navrhnuty_prierez_mm2"] >= medeny["navrhnuty_prierez_mm2"]


def test_dlhsie_vedenie_vyzaduje_vacsi_prierez():
    kratke = navrhni_prierez(prud_a=16, dlzka_m=10, napatie_v=230, fazy=1)
    dlhe = navrhni_prierez(prud_a=16, dlzka_m=80, napatie_v=230, fazy=1)
    assert dlhe["navrhnuty_prierez_mm2"] > kratke["navrhnuty_prierez_mm2"]


def test_istic_zaokruhluje_nahor():
    vysledok = navrhni_istic(prud_a=12.74)
    assert vysledok["menovity_prud_a"] == 13
    assert vysledok["menovity_prud_a"] >= 12.74


def test_istic_charakteristika_podla_zataze():
    assert navrhni_istic(prud_a=10, typ="bezny")["charakteristika"] == "B"
    assert navrhni_istic(prud_a=10, typ="motor")["charakteristika"] == "C"
    assert navrhni_istic(prud_a=10, typ="tvrdy_rozbeh")["charakteristika"] == "D"


def test_istic_odhali_poddimenzovany_kabel():
    # Istič 63 A na kábli 2,5 mm² (20 A) musí prepadnúť.
    vysledok = navrhni_istic(prud_a=60, prierez_mm2=2.5, fazy=3)
    assert vysledok["kontrola_kabla"]["vyhovuje"] is False
    assert "NEVYHOVUJE" in vysledok["kontrola_kabla"]["poznamka"]


def test_istic_potvrdi_spravny_kabel():
    vysledok = navrhni_istic(prud_a=14, prierez_mm2=4, fazy=3)
    assert vysledok["kontrola_kabla"]["vyhovuje"] is True


def test_fazy_sa_odvodia_z_napatia():
    # Kľúčová poistka: pri 230 V by trojfázový vzorec podhodnotil prúd o 42 %.
    jednofazovy = vypocitaj_prud(vykon_w=2300, napatie_v=230, cos_fi=1.0)
    assert jednofazovy["fazy"] == 1
    assert jednofazovy["prud_a"] == 10.0
    assert "fazy" in jednofazovy["pouzite_predvolby"]

    trojfazovy = vypocitaj_prud(vykon_w=7500, napatie_v=400)
    assert trojfazovy["fazy"] == 3

    # Tolerancia ±10 % pokryje aj reálne napätia v sieti.
    assert vypocitaj_prud(vykon_w=2300, napatie_v=240)["fazy"] == 1
    assert navrhni_prierez(prud_a=16, dlzka_m=20, napatie_v=230)["fazy"] == 1


def test_fazy_v_rozpore_s_napatim_vyhodia_chybu():
    pripady = [
        {"vykon_w": 2300, "napatie_v": 230, "fazy": 3},
        {"vykon_w": 7500, "napatie_v": 400, "fazy": 1},
    ]
    for argumenty in pripady:
        try:
            vypocitaj_prud(**argumenty)
        except ValueError:
            continue
        raise AssertionError(f"vypocitaj_prud({argumenty}) mala vyhodiť ValueError")


def test_nestandardne_napatie_si_vypyta_fazy():
    # Z 500 V sa počet fáz odvodiť nedá — nástroj si ho musí vypýtať.
    try:
        vypocitaj_prud(vykon_w=10000, napatie_v=500)
    except ValueError as chyba:
        assert "fazy" in str(chyba)
    else:
        raise AssertionError("Pri 500 V bez 'fazy' mala vzniknúť ValueError")

    # So zadanými fázami už prejde.
    assert vypocitaj_prud(vykon_w=10000, napatie_v=500, fazy=3)["fazy"] == 3


def test_pouzite_predvolby_rozlisuju_zadane_od_domyslenych():
    zadane = vypocitaj_prud(
        vykon_w=5500, napatie_v=400, fazy=3, cos_fi=0.85, ucinnost=1.0
    )
    assert zadane["pouzite_predvolby"] == []

    domyslene = vypocitaj_prud(vykon_w=5500, napatie_v=400)
    assert set(domyslene["pouzite_predvolby"]) == {"fazy", "cos_fi", "ucinnost"}
    # Ohlásená hodnota musí sedieť s tou, s ktorou sa naozaj počítalo.
    assert domyslene["cos_fi"] == 0.9

    assert navrhni_istic(prud_a=10, typ="motor", material="Cu")["pouzite_predvolby"] == []
    assert "typ" in navrhni_istic(prud_a=10)["pouzite_predvolby"]


def test_typ_obvodu_urcuje_limit_ubytku():
    from tools import LIMIT_UBYTKU

    osvetlenie = navrhni_prierez(
        prud_a=32, dlzka_m=60, napatie_v=400, typ_obvodu="osvetlenie"
    )
    ostatne = navrhni_prierez(prud_a=32, dlzka_m=60, napatie_v=400, typ_obvodu="ostatne")
    assert osvetlenie["povoleny_ubytok_pct"] == LIMIT_UBYTKU["osvetlenie"]
    assert ostatne["povoleny_ubytok_pct"] == LIMIT_UBYTKU["ostatne"]

    # Bez typu obvodu platí prísnejší z limitov.
    neurcene = navrhni_prierez(prud_a=32, dlzka_m=60, napatie_v=400)
    assert neurcene["povoleny_ubytok_pct"] == min(LIMIT_UBYTKU.values())
    assert "max_ubytok_pct" in neurcene["pouzite_predvolby"]

    # Explicitný limit prebije typ obvodu.
    prebity = navrhni_prierez(
        prud_a=32, dlzka_m=60, napatie_v=400, typ_obvodu="ostatne", max_ubytok_pct=2.0
    )
    assert prebity["povoleny_ubytok_pct"] == 2.0


def test_neplatne_vstupy_vyhodia_chybu():
    pripady = [
        (vypocitaj_prud, {"vykon_w": -100, "napatie_v": 230}),
        (vypocitaj_prud, {"vykon_w": 1000, "napatie_v": 230, "fazy": 2}),
        (vypocitaj_prud, {"vykon_w": 1000, "napatie_v": 230, "cos_fi": 1.5}),
        (navrhni_prierez, {"prud_a": 16, "dlzka_m": 0, "napatie_v": 400}),
        (navrhni_prierez, {"prud_a": 16, "dlzka_m": 20, "napatie_v": 400, "material": "Fe"}),
        (navrhni_prierez, {"prud_a": 16, "dlzka_m": 20, "napatie_v": 400, "typ_obvodu": "x"}),
        (navrhni_istic, {"prud_a": 5000}),
        (navrhni_istic, {"prud_a": 16, "typ": "neznamy"}),
    ]
    for funkcia, argumenty in pripady:
        try:
            funkcia(**argumenty)
        except ValueError:
            continue
        raise AssertionError(f"{funkcia.__name__}({argumenty}) mala vyhodiť ValueError")


def test_kompletny_scenar_dielna():
    """Reťaz, ktorú v ukážke rieši agent: 7,5 kW stroj, 400 V, 45 m."""
    prud = vypocitaj_prud(vykon_w=7500, napatie_v=400, fazy=3, cos_fi=0.85)["prud_a"]
    istic = navrhni_istic(prud_a=prud, typ="motor")
    prierez = navrhni_prierez(prud_a=istic["menovity_prud_a"], dlzka_m=45, napatie_v=400, fazy=3)
    kontrola = navrhni_istic(
        prud_a=prud, typ="motor", prierez_mm2=prierez["navrhnuty_prierez_mm2"], fazy=3
    )

    assert prud == 12.74
    assert istic["oznacenie"] == "C13"
    assert prierez["skutocny_ubytok_pct"] <= 3.0
    assert kontrola["kontrola_kabla"]["vyhovuje"] is True


if __name__ == "__main__":
    testy = [(nazov, funkcia) for nazov, funkcia in sorted(globals().items()) if nazov.startswith("test_")]
    zlyhalo = 0
    for nazov, funkcia in testy:
        try:
            funkcia()
        except AssertionError as chyba:
            zlyhalo += 1
            print(f"ZLYHAL  {nazov}: {chyba}")
        else:
            print(f"OK      {nazov}")
    print(f"\n{len(testy) - zlyhalo}/{len(testy)} testov prešlo.")
    raise SystemExit(1 if zlyhalo else 0)
