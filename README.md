# Elektro agent — LLM s výpočtovými nástrojmi

Python skript, ktorý zavolá LLM API, nechá model použiť vlastné výpočtové nástroje
a ich výsledky vráti späť modelu, aby z nich zostavil finálnu odpoveď.

Doménou sú elektrotechnické výpočty: z výkonu spotrebiča určiť prúd, z prúdu a dĺžky
vedenia navrhnúť prierez vodiča a istič. Sú to výpočty, ktoré jazykový model spamäti
spoľahlivo nezvládne — a práve preto dávajú tool callingu zmysel.

> Semestrálna práca ku kurzu — zadanie *„Python skript pro LLM API"*.

---

## Ako to funguje

```
používateľ ──► LLM ──► "potrebujem nástroj vypocitaj_prud(7500, 400, 3, 0.85)"
                 ▲                            │
                 │                            ▼
                 │                    lokálna Python funkcia
                 │                            │
                 └──── výsledok {"prud_a": 12.74} ◄──┘
                 │
                 ├──► "teraz navrhni_istic(12.74, 'motor')"   ──► {"oznacenie": "C13"}
                 ├──► "teraz navrhni_prierez(13, 45, 400, 3)" ──► {"prierez": 1.5, ...}
                 │
                 └──► finálna odpoveď v prirodzenom jazyku
```

Cyklus v `main.py` beží dovtedy, kým model prestane žiadať nástroje — nie je to jedno
pevne zadrôtované volanie. Model si sám volí, ktoré nástroje a v akom poradí použije.

---

## Nástroje

| Nástroj | Čo počíta | Vzorec |
|---|---|---|
| `vypocitaj_prud` | prevádzkový prúd zo záťaže | `I = P / (√3 × U × cosφ × η)` (3f)<br>`I = P / (U × cosφ × η)` (1f) |
| `navrhni_prierez` | prierez vodiča v mm² | `S = (√3 × ρ × L × I × cosφ) / ΔU_max` (3f)<br>`S = (2 × ρ × L × I × cosφ) / ΔU_max` (1f) |
| `navrhni_istic` | menovitý prúd a charakteristiku | najbližší vyšší z rady EN 60898-1 + kontrola `Ib ≤ In ≤ Iz` |

`navrhni_prierez` posudzuje **dve nezávislé kritériá** — úbytok napätia aj prúdovú
zaťažiteľnosť — a vracia prísnejší z nich, spolu s informáciou, ktoré rozhodlo.

Normalizované rady a konštanty (`ρ_Cu = 0,0178`, `ρ_Al = 0,0286 Ω·mm²/m`,
prierezy podľa STN EN 60228, ističe podľa STN EN 60898-1, zaťažiteľnosť podľa
IEC 60364-5-52 tab. B.52.4 pre uloženie B2) sú v `tools.py`.

---

## Inštalácia a spustenie

Projekt používa [uv](https://docs.astral.sh/uv/).

```bash
git clone <URL_TOHTO_REPA>
cd elektro-llm-agent

cp .env.example .env      # na Windows: copy .env.example .env
# do .env doplň API kľúč

uv sync
uv run main.py
```

Vlastná otázka namiesto ukážok:

```bash
uv run main.py "Ohrievač 3,5 kW na 230 V, 25 m od rozvádzača. Aký kábel?"
```

### Voľba modelu

Skript používa [LiteLLM](https://docs.litellm.ai/), takže rovnaký kód funguje
s ktorýmkoľvek providerom. Mení sa iba `.env`:

| Provider | `MODEL` | Kľúč |
|---|---|---|
| OpenRouter *(predvolené, zadarmo)* | `openrouter/google/gemma-4-31b-it:free` | `OPENROUTER_API_KEY` |
| OpenAI | `openai/gpt-4o-mini` | `OPENAI_API_KEY` |
| Anthropic | `anthropic/claude-sonnet-4-5` | `ANTHROPIC_API_KEY` |
| Google | `gemini/gemini-2.5-flash` | `GEMINI_API_KEY` |
| Ollama / LM Studio | `ollama/llama3.2` | bez kľúča, treba `API_BASE` |

---

## Testy

Výpočty majú vlastné testy, ktoré bežia **bez API kľúča a bez internetu** — matematika
sa overí skôr, než ju dostane do rúk jazykový model.

```bash
uv run test_tools.py
```

```
OK      test_dlhsie_vedenie_vyzaduje_vacsi_prierez
OK      test_hlinik_vyzaduje_vacsi_prierez
OK      test_istic_charakteristika_podla_zataze
OK      test_istic_odhali_poddimenzovany_kabel
OK      test_istic_potvrdi_spravny_kabel
OK      test_istic_zaokruhluje_nahor
OK      test_kompletny_scenar_dielna
OK      test_neplatne_vstupy_vyhodia_chybu
OK      test_prierez_je_z_normalizovanej_rady
OK      test_prierez_rozhoduje_ubytok
OK      test_prierez_rozhoduje_zatazitelnost
OK      test_prud_jednofazovy
OK      test_prud_trojfazovy
OK      test_prud_zohladnuje_ucinnost

14/14 testov prešlo.
```

---

## Ukážkový beh

Otázka, ktorá vyžaduje reťazenie troch nástrojov:

> *„Mám v dielni stroj 7,5 kW na 400 V, rozvádzač je 45 m ďaleko. Aký kábel a istič potrebujem?"*

Skutočné výstupy nástrojov v tomto scenári:

```jsonc
// vypocitaj_prud(vykon_w=7500, napatie_v=400, fazy=3, cos_fi=0.85)
{ "prud_a": 12.74, "vzorec": "I = P / (√3 × U × cosφ × η)" }

// navrhni_istic(prud_a=12.74, typ="motor")
{ "menovity_prud_a": 13, "charakteristika": "C", "oznacenie": "C13" }

// navrhni_prierez(prud_a=13, dlzka_m=45, napatie_v=400, fazy=3, cos_fi=0.85)
{
  "navrhnuty_prierez_mm2": 1.5,
  "rozhodujuce_kriterium": "úbytok napätia",
  "teoreticky_minimalny_prierez_mm2": 1.278,
  "skutocny_ubytok_v": 10.22,
  "skutocny_ubytok_pct": 2.56,
  "povoleny_ubytok_pct": 3.0,
  "zatazitelnost_navrhnuteho_a": 15.0
}

// navrhni_istic(prud_a=12.74, typ="motor", prierez_mm2=1.5, fazy=3)
{ "vyhovuje": true, "podmienka": "Ib ≤ In ≤ Iz",
  "poznamka": "Istič chráni vodič pred preťažením." }
```

Model z týchto výsledkov zostaví odpoveď v prirodzenom jazyku. Nič z toho nepočíta sám —
všetky čísla pochádzajú z Python funkcií.

---

## Ako je riešené ošetrenie chýb

Ak nástroj dostane nezmyselný vstup, výnimka sa **nešíri von a skript nespadne** —
zabalí sa do odpovede pre model:

```python
try:
    return funkcia(**argumenty)
except (ValueError, TypeError, KeyError) as chyba:
    return {"error": f"{type(chyba).__name__}: {chyba}"}
```

Model tak dostane `{"error": "ValueError: Výkon musí byť kladné číslo (vo wattoch)."}`,
pochopí, čo urobil zle, a môže sa opraviť alebo sa doplňujúco spýtať. Štvrtá ukážka
v `main.py` túto cestu zámerne vyvoláva (výkon `-3000 W`).

Ďalšie poistky:

- **`MAX_ITERACII`** (predvolene 8) — cyklus sa nemôže zaseknúť donekonečna
- **spracujú sa všetky `tool_calls`**, nielen prvý — model ich môže vyžiadať viac naraz
- **neznámy názov nástroja** vráti modelu zoznam dostupných namiesto `KeyError`
- **nespracovateľný JSON** v argumentoch sa tiež vráti ako chyba, nie ako pád

---

## Štruktúra

```
.
├── main.py           # agent — cyklus LLM ↔ nástroje
├── tools.py          # výpočty + JSON schémy nástrojov pre LLM
├── test_tools.py     # testy výpočtov (bez API)
├── pyproject.toml    # závislosti (uv)
├── .env.example      # vzor konfigurácie, bez skutočných kľúčov
└── .gitignore        # .env sa nikdy necommituje
```

---

## Obmedzenia

> ⚠️ **Toto je školský projekt, nie nástroj na navrhovanie reálnych elektroinštalácií.**

Model je zámerne zjednodušený a **neberie do úvahy** okrem iného:

- súčiniteľ súčasnosti a náročnosti
- teplotu okolia inú než 30 °C a zoskupenie viacerých káblov v trase
- iné spôsoby uloženia než B2 (v rúrke v stene)
- skratovú odolnosť, impedanciu poruchovej slučky a čas odpojenia
- selektivitu istiacich prvkov a prúdové chrániče
- rozbehové prúdy motorov nad rámec voľby charakteristiky

Skutočnú elektroinštaláciu musí navrhnúť a odsúhlasiť oprávnená osoba
a skontrolovať revízny technik.
