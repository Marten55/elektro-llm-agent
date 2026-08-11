"""
Elektro agent — LLM s prístupom k elektrotechnickým výpočtom.

Skript zavolá LLM API, model si sám vyberie a zavolá potrebné nástroje,
výsledky sa vrátia späť do modelu a ten z nich zostaví finálnu odpoveď.
Cyklus beží dovtedy, kým model prestane žiadať nástroje.

Spustenie:
    uv run main.py                          # ukážkové scenáre
    uv run main.py "otázka"                 # vlastná otázka
"""

import json
import os
import sys
import time

import litellm
from dotenv import load_dotenv

from tools import DOSTUPNE_FUNKCIE, TOOLS

load_dotenv()

# LiteLLM pri každej chybe providera vypíše na výstup ešte svoje odkazy na
# dokumentáciu a GitHub issues. V ukážkovom behu to len zaplavuje výstup —
# chybu si hlásime sami, zrozumiteľnejšie.
litellm.suppress_debug_info = True

# -----------------------------------------------------------------------------
# Konfigurácia nezávislá na providerovi.
#
# LiteLLM umožňuje tomu istému kódu hovoriť s ľubovoľným poskytovateľom.
# Mení sa iba .env — samotný cyklus nižšie zostáva rovnaký:
#
#   MODEL   ... reťazec modelu, napr.
#                 openrouter/nvidia/nemotron-3-super-120b-a12b:free  (zadarmo)
#                 openai/gpt-4o-mini
#                 anthropic/claude-sonnet-4-5
#                 gemini/gemini-2.5-flash
#                 ollama/llama3.2                          (lokálne, s API_BASE)
#   <PROVIDER>_API_KEY ... príslušný kľúč; LiteLLM ho načíta automaticky
#   API_BASE ... voliteľné, len pre lokálnych/self-hosted providerov
# -----------------------------------------------------------------------------
MODEL = os.environ.get("MODEL", "openrouter/nvidia/nemotron-3-super-120b-a12b:free")
API_BASE = os.environ.get("API_BASE")
# Doplňujúce otázky spotrebúvajú iterácie rovnako ako výpočty, preto je limit
# vyšší než pri samotnom reťazci troch nástrojov.
MAX_ITERACII = int(os.environ.get("MAX_ITERACII", "12"))
# Odpovede agenta sú krátke, ale limit treba poslať explicitne: OpenRouter si
# rezervuje kredit na plné výstupné okno modelu (u niektorých 65 000 tokenov)
# a s malým zostatkom request odmietne skôr, než sa vôbec odošle.
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "2000"))
# Free tier providerov vracia 429 alebo 500 aj na úplne správnu požiadavku —
# je to stav ich infraštruktúry, nie chyba v skripte. Bez zopakovania by z toho
# bol traceback namiesto odpovede, preto volanie skúšame viackrát s narastajúcou
# pauzou (3 s, 6 s, 12 s). Súčet ~21 s je zvolený podľa `retry_after_seconds`,
# ktoré OpenRouter posiela pri vyčerpanom zdieľanom limite free modelov (~22 s).
POCET_POKUSOV = int(os.environ.get("POCET_POKUSOV", "4"))
ZAKLADNE_CAKANIE_S = float(os.environ.get("ZAKLADNE_CAKANIE_S", "3"))

# Dočasné chyby na strane providera — opakovanie má zmysel.
CHYBY_NA_ZOPAKOVANIE = (
    litellm.exceptions.RateLimitError,  # 429 — vyčerpaný limit free tieru
    litellm.exceptions.ServiceUnavailableError,  # 503 — provider preťažený
    litellm.exceptions.InternalServerError,  # 500 — chyba na ich strane
    litellm.exceptions.APIConnectionError,  # výpadok siete
    litellm.exceptions.Timeout,
    litellm.exceptions.APIError,  # všeobecná chyba providera
)

# Chyby konfigurácie — opakovanie ich nespraví. Nech padnú hneď a zrozumiteľne,
# nech používateľ nečaká 6 sekúnd na to, že má zlý kľúč.
CHYBY_KONFIGURACIE = (
    litellm.exceptions.AuthenticationError,  # chýbajúci alebo neplatný kľúč
    litellm.exceptions.NotFoundError,  # model neexistuje
    litellm.exceptions.BadRequestError,  # zlá požiadavka, napr. model bez tool callingu
)

SYSTEM_PROMPT = """\
Si asistent slovenského elektrikára. Pomáhaš s dimenzovaním elektrických obvodov.

Pravidlá:
- Na každý výpočet použi dostupné nástroje. Nikdy nepočítaj spamäti a nehádaj čísla.
- Úlohy rieš postupne: najprv zisti prúd, potom navrhni prierez vodiča a istič.
- Odpovedaj po slovensky, stručne a prakticky — konkrétny kábel, prierez a istič.
- Na záver pripomeň, že ide o orientačný návrh, ktorý musí posúdiť revízny technik.

Keď v zadaní chýba údaj, rozlišuj:

1. NEODHADUJ a použi nástroj `opytaj_sa_pouzivatela` — výkon alebo prúd záťaže,
   napätie (230/400 V) a dĺžku vedenia. Tieto sa vymyslieť nedajú; nesprávny
   odhad vedie k poddimenzovanému káblu. Pýtaj sa NARAZ na všetko, čo chýba,
   nie po jednom údaji.
2. ODHADNI a v odpovedi to prizná — účinník cos φ, účinnosť, materiál jadra,
   typ obvodu a charakteristiku ističa. Vyber podľa kontextu (motor → cos φ
   0,85 a charakteristika C; ohrievač → cos φ 1,0; zásuvky → typ_obvodu
   'ostatne'). Nástroje vracajú pole `pouzite_predvolby` — čokoľvek je v ňom,
   si domysleli ony, nie používateľ, a musíš to v odpovedi uviesť.
3. NEZADÁVAJ počet fáz pri 230 V ani 400 V — nástroj si ho odvodí sám. Zadaj ho
   len pri neštandardnom napätí, keď si to nástroj vypýta.
"""

# Nástroj bez výpočtu: jediný, ktorý má vedľajší efekt — prečíta vstup od
# používateľa. Preto žije tu, a nie v `tools.py`, ktorý je zámerne čistý
# a testovateľný bez API aj bez I/O.
NASTROJ_OTAZKA = {
    "type": "function",
    "function": {
        "name": "opytaj_sa_pouzivatela",
        "description": (
            "Položí používateľovi doplňujúcu otázku a počká na odpoveď. Použi vždy, keď "
            "na výpočet chýba údaj, ktorý sa nedá rozumne odhadnúť — výkon či prúd "
            "záťaže, napätie alebo dĺžka vedenia. Nepýtaj sa na hodnoty, pre ktoré "
            "existuje bežný predpoklad (cos φ, materiál vodiča) — tie zvoľ sám a "
            "v odpovedi ich prizná. Do jednej otázky zhrň všetko, čo chýba."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "otazka": {
                    "type": "string",
                    "description": (
                        "Otázka po slovensky, konkrétna a zrozumiteľná pre elektrikára. "
                        "Napr. 'Aké je napätie spotrebiča (230 alebo 400 V) a koľko metrov "
                        "je od rozvádzača?'"
                    ),
                },
                "chybajuce_udaje": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Zoznam názvov chýbajúcich parametrov, napr. ['napatie_v', 'dlzka_m']."
                    ),
                },
            },
            "required": ["otazka"],
        },
    },
}


def _je_interaktivny() -> bool:
    """
    Zistí, či je koho sa pýtať.

    Jedno volanie `sys.stdin.isatty()` nestačí: pri odpojenom vstupe býva
    `sys.stdin` rovno `None` a pád by nastal ešte pred prvým výpočtom.
    Windows navyše pri presmerovaní z NUL hlási isatty() = True — tam sa
    neinteraktívnosť prejaví až ako EOFError, ktorý rieši `_opytaj_sa`.
    """
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


class ElektroAgent:
    """Agent, ktorý strieda volania LLM a lokálnych výpočtových nástrojov."""

    def __init__(
        self,
        model: str = MODEL,
        api_base: str | None = API_BASE,
        verbose: bool = True,
        interaktivny: bool | None = None,
    ):
        self.model = model
        self.api_base = api_base
        self.verbose = verbose
        self.max_iteracii = MAX_ITERACII
        # Bez terminálu (presmerovaný vstup, CI, ukážkový beh) sa nemá koho pýtať.
        self.interaktivny = _je_interaktivny() if interaktivny is None else interaktivny

        # Výpočtové nástroje prichádzajú z `tools.py`; otázka na používateľa
        # je špecifická pre tento agent, tak sa pridáva až tu.
        self.nastroje = TOOLS + [NASTROJ_OTAZKA]
        self.funkcie = {**DOSTUPNE_FUNKCIE, "opytaj_sa_pouzivatela": self._opytaj_sa}

    def _log(self, sprava: str) -> None:
        if self.verbose:
            print(sprava)

    def _opytaj_sa(self, otazka: str, chybajuce_udaje: list[str] | None = None) -> dict:
        """
        Položí používateľovi otázku a vráti jeho odpoveď ako výsledok nástroja.

        Pre model je to bežný tool call — odpoveď sa mu vráti rovnakou cestou ako
        výsledok výpočtu a on na ňu nadviaže v ďalšej iterácii.
        """
        bez_pouzivatela = {
            "error": (
                "Používateľ nie je dostupný. Zvoľ bežnú hodnotu, jasne ju označ "
                "za predpoklad a pokračuj vo výpočte — znovu sa už nepýtaj."
            )
        }
        if not self.interaktivny:
            return bez_pouzivatela

        print(f"\n  ? {otazka}")
        if chybajuce_udaje:
            print(f"    (chýba: {', '.join(chybajuce_udaje)})")
        try:
            odpoved = input("  > ").strip()
        except EOFError:
            # Vstup je presmerovaný z prázdneho zdroja — na Windows to isatty()
            # neodhalí, prejaví sa to až tu.
            print("  (vstup nie je dostupný)")
            self.interaktivny = False
            return bez_pouzivatela

        if not odpoved:
            return bez_pouzivatela
        return {"odpoved_pouzivatela": odpoved}

    def _zavolaj_llm(self, messages: list[dict]):
        """
        Jedno volanie LLM API so zoznamom dostupných nástrojov.

        Dočasnú chybu providera (429, 500, výpadok siete) skúsi zopakovať —
        inak by beh skončil tracebackom kvôli niečomu, čo o pár sekúnd prejde.
        Chyby konfigurácie sa nechytajú vôbec, takže padnú okamžite.
        """
        kwargs = {
            "model": self.model,
            "messages": messages,
            "tools": self.nastroje,
            "tool_choice": "auto",
            "max_tokens": MAX_TOKENS,
        }
        # api_base posielame len pri lokálnych providerov (LM Studio, Ollama).
        if self.api_base:
            kwargs["api_base"] = self.api_base

        for pokus in range(1, POCET_POKUSOV + 1):
            try:
                return litellm.completion(**kwargs)
            except CHYBY_NA_ZOPAKOVANIE as chyba:
                # Posledný pokus už neodkladáme — chybu pustíme ďalej.
                if pokus == POCET_POKUSOV:
                    raise
                cakanie = ZAKLADNE_CAKANIE_S * 2 ** (pokus - 1)
                self._log(
                    f"  ! {type(chyba).__name__} — pokus {pokus}/{POCET_POKUSOV} zlyhal, "
                    f"skúšam znova o {cakanie:g} s"
                )
                time.sleep(cakanie)

    def _vykonaj_nastroj(self, nazov: str, argumenty: dict) -> dict:
        """
        Spustí nástroj a vráti jeho výsledok.

        Chybu nešíri ďalej — zabalí ju do odpovede pre model. Ten sa tak môže
        opraviť (napr. doplniť chýbajúci parameter) namiesto pádu skriptu.

        EOFError a KeyboardInterrupt sú tu kvôli `opytaj_sa_pouzivatela`: uzavretý
        alebo prerušený vstup nesmie zhodiť celý beh.
        """
        funkcia = self.funkcie.get(nazov)
        if funkcia is None:
            return {"error": f"Nástroj '{nazov}' neexistuje. Dostupné: {list(self.funkcie)}"}
        try:
            return funkcia(**argumenty)
        except (ValueError, TypeError, KeyError, EOFError, KeyboardInterrupt) as chyba:
            return {"error": f"{type(chyba).__name__}: {chyba}"}

    def spusti(self, otazka: str) -> str:
        """Prevedie otázku cyklom LLM → nástroje → LLM až po finálnu odpoveď."""
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": otazka},
        ]

        for iteracia in range(1, self.max_iteracii + 1):
            self._log(f"\n--- Iterácia {iteracia} ---")
            odpoved = self._zavolaj_llm(messages)
            sprava = odpoved.choices[0].message

            # Model nežiada nástroj → má finálnu odpoveď a končíme.
            if not sprava.tool_calls:
                finalna = sprava.content
                messages.append({"role": "assistant", "content": finalna})
                return finalna

            # Do histórie musí ísť aj správa modelu s tool callmi, inak
            # nasledujúce tool odpovede nemajú na čo nadväzovať.
            messages.append(
                {
                    "role": "assistant",
                    "content": sprava.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in sprava.tool_calls
                    ],
                }
            )

            # Spracujeme VŠETKY tool cally, nielen prvý — model ich môže
            # v jednej odpovedi vyžiadať viac naraz.
            for tool_call in sprava.tool_calls:
                nazov = tool_call.function.name
                try:
                    argumenty = json.loads(tool_call.function.arguments or "{}")
                except json.JSONDecodeError as chyba:
                    vysledok = {"error": f"Argumenty nie sú platný JSON: {chyba}"}
                else:
                    self._log(f"  → {nazov}({argumenty})")
                    vysledok = self._vykonaj_nastroj(nazov, argumenty)

                self._log(f"  ← {vysledok}")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": nazov,
                        "content": json.dumps(vysledok, ensure_ascii=False),
                    }
                )

        return (
            f"Nepodarilo sa dospieť k odpovedi ani po {self.max_iteracii} iteráciách. "
            "Skús otázku rozdeliť na menšie časti."
        )


UKAZKY = [
    (
        "Jeden nástroj",
        "Aký prúd tečie trojfázovým motorom s výkonom 5,5 kW pri 400 V a účinníku 0,85?",
    ),
    (
        "Reťazenie nástrojov",
        "Mám v dielni stroj 7,5 kW na 400 V, rozvádzač je 45 m ďaleko. "
        "Aký kábel a istič potrebujem?",
    ),
    (
        "Porovnanie variantov",
        "Potrebujem napojiť zásuvkový okruh 16 A na 230 V, dĺžka 30 m. "
        "Porovnaj medený a hliníkový vodič — aký prierez vyjde v oboch prípadoch?",
    ),
    (
        "Ošetrenie chyby",
        "Aký prúd tečie spotrebičom s výkonom -3000 W pri 230 V?",
    ),
    (
        "Doplňujúca otázka",
        "Aký kábel potrebujem na stroj 7,5 kW?",  # chýba napätie aj dĺžka vedenia
    ),
]


def main() -> None:
    # Windows: pri presmerovanom výstupe je kódovanie cp1250 a znaky ako √ alebo ×
    # vo výsledkoch nástrojov by zhodili print(). UTF-8 vynútime explicitne.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print(f"Model: {MODEL}" + (f"  (api_base={API_BASE})" if API_BASE else ""))
    agent = ElektroAgent()
    if not agent.interaktivny:
        print("Neinteraktívny beh — namiesto doplňujúcich otázok použije agent predpoklady.")

    # Chyby API prekladáme na vetu — traceback je pre používateľa nečitateľný
    # a nepovie mu, že stačí doplniť kľúč alebo to skúsiť o chvíľu znova.
    try:
        # Otázka z príkazového riadku má prednosť pred ukážkami.
        if len(sys.argv) > 1:
            otazka = " ".join(sys.argv[1:])
            print(f"\nOtázka: {otazka}")
            print(f"\n=== Odpoveď ===\n{agent.spusti(otazka)}")
            return

        for poradie, (nazov, otazka) in enumerate(UKAZKY, start=1):
            print(f"\n\n{'=' * 70}\nUkážka {poradie}: {nazov}\n{'=' * 70}")
            print(f"Otázka: {otazka}")
            print(f"\n=== Odpoveď ===\n{agent.spusti(otazka)}")
    except CHYBY_KONFIGURACIE as chyba:
        print(f"\nCHYBA: LLM API odmietlo požiadavku — {type(chyba).__name__}")
        print(f"Detail: {chyba}")
        print(
            "\nSkontroluj v .env kľúč a hodnotu MODEL. Model musí podporovať tool calling:"
            "\nhttps://openrouter.ai/models?supported_parameters=tools"
        )
        sys.exit(1)
    except CHYBY_NA_ZOPAKOVANIE as chyba:
        print(f"\nCHYBA: LLM API neodpovedalo ani po {POCET_POKUSOV} pokusoch.")
        print(f"Detail: {type(chyba).__name__}: {chyba}")
        print(
            "\nIde o dočasný stav na strane providera. Skús to o chvíľu znova, "
            "alebo prepni MODEL v .env na iný."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
