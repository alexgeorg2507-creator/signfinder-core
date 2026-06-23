"""Юрисдикционные профили для ревью договоров (v1.20)."""
from __future__ import annotations

_JURISDICTIONS: dict[str, dict[str, str]] = {
    "ru": {
        "name": "Российская Федерация (ГК РФ)",
        "context": (
            "Договор по российскому праву. Существенные условия по ГК РФ: "
            "предмет договора, цена/порядок расчётов, срок. Проверь наличие "
            "реквизитов сторон (ИНН/ОГРН для юрлиц), раздела ответственности, "
            "порядка разрешения споров."
        ),
    },
    "pl": {
        "name": "Rzeczpospolita Polska (Kodeks cywilny)",
        "context": (
            "Umowa wedlug prawa polskiego. Sprawdz: strony i ich dane "
            "(NIP/REGON/KRS), przedmiot umowy, wynagrodzenie i terminy platnosci, "
            "okres obowiazywania, odpowiedzialnosc stron, rozstrzyganie sporow."
        ),
    },
    "en": {
        "name": "General / EU jurisdiction",
        "context": (
            "Contract under general/EU principles. Check: parties and their "
            "identification details, subject matter, consideration and payment "
            "terms, term/duration, liability clause, governing law and dispute "
            "resolution."
        ),
    },
    "mk": {
        "name": "Republika Severna Makedonija",
        "context": (
            "Договор според македонското право. Провери: страни и нивни "
            "податоци, предмет на договорот, цена и рокови на плаќање, "
            "времетраење, одговорност на страните, решавање спорови."
        ),
    },
}

_DEFAULT = {
    "name": "General jurisdiction",
    "context": (
        "Contract review. Check: parties identification, subject matter, "
        "payment terms, duration, liability, signature spots for both sides."
    ),
}


def get_jurisdiction(language: str) -> tuple[str, str]:
    """Вернуть (name, context) для языка. Составной язык 'mk, en' → первый код."""
    code = (language or "").split(",")[0].strip()[:2].lower()
    prof = _JURISDICTIONS.get(code, _DEFAULT)
    return prof["name"], prof["context"]
