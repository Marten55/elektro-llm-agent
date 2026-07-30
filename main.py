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

import litellm
from dotenv import load_dotenv

from tools import DOSTUPNE_FUNKCIE, TOOLS

load_dotenv()

# -----------------------------------------------------------------------------
# Konfigurácia nezávislá na providerovi.
#
# LiteLLM umožňuje tomu istému kódu hovoriť s ľubovoľným poskytovateľom.
# Mení sa iba .env — samotný cyklus nižšie zostáva rovnaký:
#
#   MODEL   ... reťazec modelu, napr.
#                 openrouter/google/gemma-4-31b-it:free   (zadarmo)
#                 openai/gpt-4o-mini
#                 anthropic/claude-sonnet-4-5
#                 gemini/gemini-2.5-flash
#                 ollama/llama3.2                          (lokálne, s API_BASE)
#   <PROVIDER>_API_KEY ... príslušný kľúč; LiteLLM ho načíta automaticky
#   API_BASE ... voliteľné, len pre lokálnych/self-hosted providerov
# -----------------------------------------------------------------------------
MODEL = os.environ.get("MODEL", "openrouter/google/gemma-4-31b-it:free")
API_BASE = os.environ.get("API_BASE")
MAX_ITERACII = int(os.environ.get("MAX_ITERACII", "8"))

SYSTEM_PROMPT = """\
Si asistent slovenského elektrikára. Pomáhaš s dimenzovaním elektrických obvodov.

Pravidlá:
- Na každý výpočet použi dostupné nástroje. Nikdy nepočítaj spamäti a nehádaj čísla.
- Úlohy rieš postupne: najprv zisti prúd, potom navrhni prierez vodiča a istič.
- Ak v zadaní chýba údaj (napr. účinník), zvoľ bežnú hodnotu a v odpovedi ju uveď.
- Odpovedaj po slovensky, stručne a prakticky — konkrétny kábel, prierez a istič.
- Na záver pripomeň, že ide o orientačný návrh, ktorý musí posúdiť revízny technik.
"""


class ElektroAgent:
    """Agent, ktorý strieda volania LLM a lokálnych výpočtových nástrojov."""

    def __init__(self, model: str = MODEL, api_base: str | None = API_BASE, verbose: bool = True):
        self.model = model
        self.api_base = api_base
        self.verbose = verbose
        self.max_iteracii = MAX_ITERACII

    def _log(self, sprava: str) -> None:
        if self.verbose:
            print(sprava)

    def _zavolaj_llm(self, messages: list[dict]):
        """Jedno volanie LLM API so zoznamom dostupných nástrojov."""
        kwargs = {
            "model": self.model,
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto",
        }
        # api_base posielame len pri lokálnych providerov (LM Studio, Ollama).
        if self.api_base:
            kwargs["api_base"] = self.api_base
        return litellm.completion(**kwargs)

    def _vykonaj_nastroj(self, nazov: str, argumenty: dict) -> dict:
        """
        Spustí nástroj a vráti jeho výsledok.

        Chybu nešíri ďalej — zabalí ju do odpovede pre model. Ten sa tak môže
        opraviť (napr. doplniť chýbajúci parameter) namiesto pádu skriptu.
        """
        funkcia = DOSTUPNE_FUNKCIE.get(nazov)
        if funkcia is None:
            return {"error": f"Nástroj '{nazov}' neexistuje. Dostupné: {list(DOSTUPNE_FUNKCIE)}"}
        try:
            return funkcia(**argumenty)
        except (ValueError, TypeError, KeyError) as chyba:
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
]


def main() -> None:
    print(f"Model: {MODEL}" + (f"  (api_base={API_BASE})" if API_BASE else ""))
    agent = ElektroAgent()

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


if __name__ == "__main__":
    main()
