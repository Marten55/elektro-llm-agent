# Elektro agent — LLM s výpočtovými nástrojmi

Python skript, ktorý zavolá LLM API, nechá model použiť vlastné výpočtové nástroje
a ich výsledky vráti späť modelu, aby z nich zostavil finálnu odpoveď.

Doménou sú elektrotechnické výpočty: z výkonu spotrebiča určiť prúd, z prúdu a dĺžky
vedenia navrhnúť prierez vodiča a istič. Sú to výpočty, ktoré jazykový model spamäti
spoľahlivo nezvládne — a práve preto dávajú tool callingu zmysel.

> Domáca úloha ku kurzu — zadanie *„Python skript pro LLM API"*.

---

## Ako to funguje

```
používateľ ──► LLM ──► "potrebujem nástroj vypocitaj_prud(7500, 400, 0.85)"
                 ▲                            │
                 │                            ▼
                 │                    lokálna Python funkcia
                 │                            │
                 └──── výsledok {"prud_a": 12.74} ◄──┘
                 │
                 ├──► "teraz navrhni_istic(12.74, 'motor')"   ──► {"oznacenie": "C13"}
                 ├──► "teraz navrhni_prierez(13, 45, 400)"    ──► {"prierez": 1.5, ...}
                 │
                 ├──► "chýba mi dĺžka" ──► opytaj_sa_pouzivatela(...)
                 │         ▲                          │
                 │         └──── "45 metrov" ◄────  používateľ
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
| `opytaj_sa_pouzivatela` | *nič* — položí doplňujúcu otázku a počká na odpoveď | — |

`navrhni_prierez` posudzuje **dve nezávislé kritériá** — úbytok napätia aj prúdovú
zaťažiteľnosť — a vracia prísnejší z nich, spolu s informáciou, ktoré rozhodlo.

Štvrtý nástroj nič nepočíta a ako jediný má vedľajší efekt — číta vstup od používateľa.
Preto je definovaný v `main.py`, nie v `tools.py`, ktorý zostáva čistý a testovateľný
bez API aj bez I/O. Pre model je to však bežný tool call ako ktorýkoľvek iný.

Normalizované rady a konštanty (`ρ_Cu = 0,0178`, `ρ_Al = 0,0286 Ω·mm²/m`,
prierezy podľa STN EN 60228, ističe podľa STN EN 60898-1, zaťažiteľnosť podľa
IEC 60364-5-52 tab. B.52.4 pre uloženie B2) sú v `tools.py`.

---

## Čo urobí agent, keď v zadaní chýba údaj

Tichý odhad je pri dimenzovaní nebezpečný — poddimenzovaný kábel vyzerá v odpovedi
rovnako presvedčivo ako správny. Agent preto chýbajúce údaje rozdeľuje do troch skupín:

| Údaj | Správanie |
|---|---|
| výkon / prúd, napätie, dĺžka vedenia | **spýta sa** cez `opytaj_sa_pouzivatela` a počká |
| cos φ, účinnosť, materiál, typ obvodu, typ ističa | **odhadne** podľa kontextu a **prizná to** |
| počet fáz | **neháda** — nástroj ho odvodí z napätia |

**Počet fáz sa neodhaduje zámerne.** Predvolené `fazy=3` by pri 230 V spotrebiči
podhodnotilo prúd o 42 % (2300 W → 6,42 A namiesto 11,11 A), teda presne smerom
k poddimenzovanému vodiču. Nástroje si preto počet fáz odvodia z napätia
(230 V → 1f, 400 V → 3f, tolerancia ±10 %) a rozpor typu `napatie_v=230, fazy=3`
odmietnu chybou. Pri neštandardnom napätí si `fazy` vypýtajú.

Každý výsledok obsahuje pole **`pouzite_predvolby`** so zoznamom hodnôt, ktoré si
nástroj domyslel. Model tak vidí rozdiel medzi *„používateľ povedal cos φ = 0,85"*
a *„doplnili sme 0,9"* — a system prompt mu ukladá tieto predpoklady v odpovedi uviesť.

```jsonc
// vypocitaj_prud(vykon_w=2300, napatie_v=230)   — zadané len dve čísla
{ "prud_a": 11.11, "fazy": 1, "cos_fi": 0.9, "ucinnost": 1.0,
  "pouzite_predvolby": ["fazy", "cos_fi", "ucinnost"] }
```

Keď agent beží bez terminálu (presmerovaný vstup, CI, ukážkový beh), nástroj sa
nepýta — vráti modelu pokyn, nech zvolí bežnú hodnotu a označí ju za predpoklad.
Skript sa teda nikdy nezasekne na čakaní na vstup.

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
| OpenRouter *(predvolené, zadarmo)* | `openrouter/nvidia/nemotron-3-super-120b-a12b:free` | `OPENROUTER_API_KEY` |
| OpenAI | `openai/gpt-4o-mini` | `OPENAI_API_KEY` |
| Anthropic | `anthropic/claude-sonnet-4-5` | `ANTHROPIC_API_KEY` |
| Google | `gemini/gemini-2.5-flash` | `GEMINI_API_KEY` |
| Ollama / LM Studio | `ollama/llama3.2` | bez kľúča, treba `API_BASE` |

### Ktorý model zvoliť

Predvolený je **free model, aby projekt bežal bez platenej registrácie**. Modely
s príponou `:free` však zdieľajú limit u providera a občas vrátia `429` alebo `500`
bez ohľadu na tvoj kľúč. Skript takéto volanie sám zopakuje (viď
[Ošetrenie chýb](#ako-je-riešené-ošetrenie-chýb)); ak provider vypadne nadlho,
stačí prepnúť `MODEL` na iný free model s podporou tool callingu
(`openrouter/openai/gpt-oss-20b:free`, `openrouter/inclusionai/ling-3.0-tiny:free`).

Porovnanie na scenári *„stroj 7,5 kW, 400 V, 45 m"* — či model prenesie čísla
z nástrojov do odpovede správne a či prizná použité predvolby:

| Model | Čísla sedia | Priznal predvolby |
|---|---|---|
| `google/gemini-2.5-flash` *(platený, ~2 centy za celé demo)* | áno | áno |
| `nvidia/nemotron-3-super-120b-a12b:free` *(predvolený)* | áno | áno |
| `openai/gpt-4o-mini` | áno | nie |
| `google/gemma-4-26b-a4b-it:free` | **nie** — v zhrnutí uviedol „3x16 A" namiesto `C13` | áno |

Ten posledný riadok je zároveň dobrou ilustráciou, prečo výpočty patria do Pythonu:
nástroj vrátil správne `C13`, model to skomolil až pri prepisovaní do vety. Čísla
v odpovedi si preto vždy over proti výstupom nástrojov vypísaným nad ňou. Bol to
pôvodne predvolený model; nahradil ho nemotron, ktorý v tom istom scenári uviedol
`C13` správne.

> **Poznámka k prenositeľnosti schém.** Nie každý provider strávi celý JSON Schema.
> Google odmietne celú definíciu nástroja, ak `enum` obsahuje čísla — povoľuje ho
> len pri reťazcoch. Parameter `fazy` preto `enum` nemá a jeho rozsah stráži až
> Python (`_over_fazy`), ktorý modelu vráti zrozumiteľnú chybu. Rovnaká funkcia
> znesie aj `"3"` namiesto `3`, lebo menšie modely posielajú čísla ako text.

---

## Testy

Výpočty majú vlastné testy, ktoré bežia **bez API kľúča a bez internetu** — matematika
sa overí skôr, než ju dostane do rúk jazykový model.

```bash
uv run test_tools.py
```

```
OK      test_dlhsie_vedenie_vyzaduje_vacsi_prierez
OK      test_fazy_sa_odvodia_z_napatia
OK      test_fazy_v_rozpore_s_napatim_vyhodia_chybu
OK      test_hlinik_vyzaduje_vacsi_prierez
OK      test_istic_charakteristika_podla_zataze
OK      test_istic_odhali_poddimenzovany_kabel
OK      test_istic_potvrdi_spravny_kabel
OK      test_istic_zaokruhluje_nahor
OK      test_kompletny_scenar_dielna
OK      test_neplatne_vstupy_vyhodia_chybu
OK      test_nestandardne_napatie_si_vypyta_fazy
OK      test_pouzite_predvolby_rozlisuju_zadane_od_domyslenych
OK      test_prierez_je_z_normalizovanej_rady
OK      test_prierez_rozhoduje_ubytok
OK      test_prierez_rozhoduje_zatazitelnost
OK      test_prud_jednofazovy
OK      test_prud_trojfazovy
OK      test_prud_zohladnuje_ucinnost
OK      test_typ_obvodu_urcuje_limit_ubytku

19/19 testov prešlo.
```

---

## Ukážkový beh

Otázka, ktorá vyžaduje reťazenie troch nástrojov:

> *„Mám v dielni stroj 7,5 kW na 400 V, rozvádzač je 45 m ďaleko. Aký kábel a istič potrebujem?"*

Skutočné výstupy nástrojov v tomto scenári:

```jsonc
// vypocitaj_prud(vykon_w=7500, napatie_v=400, cos_fi=0.85)
{ "prud_a": 12.74, "fazy": 3, "vzorec": "I = P / (√3 × U × cosφ × η)",
  "pouzite_predvolby": ["fazy", "ucinnost"] }   // fázy odvodené zo 400 V

// navrhni_istic(prud_a=12.74, typ="motor")
{ "menovity_prud_a": 13, "charakteristika": "C", "oznacenie": "C13",
  "pouzite_predvolby": ["material"] }

// navrhni_prierez(prud_a=13, dlzka_m=45, napatie_v=400, cos_fi=0.85)
{
  "navrhnuty_prierez_mm2": 1.5,
  "rozhodujuce_kriterium": "úbytok napätia",
  "teoreticky_minimalny_prierez_mm2": 1.278,
  "skutocny_ubytok_v": 10.22,
  "skutocny_ubytok_pct": 2.56,
  "povoleny_ubytok_pct": 3.0,
  "zatazitelnost_navrhnuteho_a": 15.0,
  "pouzite_predvolby": ["fazy", "material", "max_ubytok_pct"]
}

// navrhni_istic(prud_a=12.74, typ="motor", prierez_mm2=1.5, fazy=3)
{ "vyhovuje": true, "podmienka": "Ib ≤ In ≤ Iz",
  "poznamka": "Istič chráni vodič pred preťažením." }
```

Model z týchto výsledkov zostaví odpoveď v prirodzenom jazyku. Nič z toho nepočíta sám —
všetky čísla pochádzajú z Python funkcií. Vďaka `pouzite_predvolby` navyše vie, že cos φ
dostal zadaný, ale meď a limit úbytku si domysleli nástroje — a povie to aj používateľovi.

### Keď údaj chýba

> *„Aký kábel potrebujem na stroj 7,5 kW?"* — chýba napätie aj dĺžka vedenia

```
--- Iterácia 1 ---
  → opytaj_sa_pouzivatela({'otazka': 'Aké je napätie stroja (230 alebo 400 V)
                                      a koľko metrov je od rozvádzača?',
                           'chybajuce_udaje': ['napatie_v', 'dlzka_m']})

  ? Aké je napätie stroja (230 alebo 400 V) a koľko metrov je od rozvádzača?
    (chýba: napatie_v, dlzka_m)
  > 400 V, 45 metrov
  ← {'odpoved_pouzivatela': '400 V, 45 metrov'}

--- Iterácia 2 ---
  → vypocitaj_prud({'vykon_w': 7500, 'napatie_v': 400, 'cos_fi': 0.85})
  ...
```

Odpoveď používateľa sa do histórie zapíše ako bežná `role: "tool"` správa — model na ňu
nadviaže rovnako, ako by nadviazal na výsledok výpočtu. Cyklus v `main.py` sa kvôli tomu
nemusel meniť vôbec.

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
v `main.py` túto cestu zámerne vyvoláva (výkon `-3000 W`), piata zase chýbajúce údaje
(`„Aký kábel potrebujem na stroj 7,5 kW?"`).

Druhý zdroj chýb je samotné API. Free tier vracia `429` alebo `500` aj na úplne
správnu požiadavku — je to stav infraštruktúry providera, nie chyba v skripte.
Volanie sa preto zopakuje s narastajúcou pauzou (3 s, 6 s, 12 s — spolu ~21 s,
zvolené podľa `retry_after_seconds ≈ 22`, ktoré OpenRouter posiela pri vyčerpanom
zdieľanom limite free modelov):

```python
for pokus in range(1, POCET_POKUSOV + 1):
    try:
        return litellm.completion(**kwargs)
    except CHYBY_NA_ZOPAKOVANIE as chyba:
        if pokus == POCET_POKUSOV:
            raise
        time.sleep(ZAKLADNE_CAKANIE_S * 2 ** (pokus - 1))
```

Opakujú sa iba **dočasné** chyby (`RateLimitError`, `InternalServerError`,
`ServiceUnavailableError`, `Timeout`, `APIConnectionError`). Neplatný kľúč či
neexistujúci model v zozname nie sú — opakovanie by ich nespravilo a používateľ
by zbytočne čakal. Keď pokusy dôjdu, `main.py` vypíše vetu s návodom namiesto
tracebacku.

Ďalšie poistky:

- **`MAX_ITERACII`** (predvolene 12) — cyklus sa nemôže zaseknúť donekonečna
- **spracujú sa všetky `tool_calls`**, nielen prvý — model ich môže vyžiadať viac naraz
- **neznámy názov nástroja** vráti modelu zoznam dostupných namiesto `KeyError`
- **nespracovateľný JSON** v argumentoch sa tiež vráti ako chyba, nie ako pád
- **rozpor medzi napätím a počtom fáz** (`230 V` + `fazy=3`) sa odmietne chybou,
  namiesto aby ticho prešiel s podhodnoteným prúdom
- **uzavretý alebo prerušený vstup** pri doplňujúcej otázke (`EOFError`,
  `KeyboardInterrupt`) sa zachytí a agent prejde na predpoklady — nespadne
  a znovu sa už nepýta

---

## Štruktúra

```
.
├── main.py           # agent — cyklus LLM ↔ nástroje + doplňujúce otázky
├── tools.py          # výpočty + JSON schémy nástrojov pre LLM (bez I/O)
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
