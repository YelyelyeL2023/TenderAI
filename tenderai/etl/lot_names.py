"""Lot / plan item name normalisation.

`clean_name` produces a comparable, human-readable key: it removes procedural boilerplate
("Лот №3", "на 2025 год", "согласно технической спецификации"), quotes, enumeration prefixes,
repeated punctuation, and fixes Latin look-alike letters typed inside Cyrillic words.
The raw name is always kept alongside, so cleaning never loses information.
"""
from __future__ import annotations

import re
import unicodedata

# Latin letters that are visually identical to Cyrillic ones
_LAT2CYR = str.maketrans("aceopxyiABCEHKMOPTXY", "асеорхуіАВСЕНКМОРТХУ")
_CYR = re.compile(r"[а-яёәғқңөұүһі]", re.IGNORECASE)
_LAT = re.compile(r"[a-z]", re.IGNORECASE)

_BOILERPLATE = [
    r"\bлот\s*#?\s*\d+[а-я]?\b",
    r"\b(?:на|за)\s+20\d\d\s*(?:[-–—/]\s*20\d\d\s*)?(?:год[аы]?|гг?\.?)",
    r"\b20\d\d\s*(?:год[аы]?|гг?\.)",
    r"\(?\s*(?:согласно|в\s+соответствии\s+с)\s+(?:прилагаемой\s+)?(?:техническ\w+\s+)?(?:спецификаци\w+|заданию|заданием|тз|тс)\s*\)?",
    r"\(?\s*(?:см\.?|смотреть)\s+(?:техническ\w+\s+)?(?:спецификаци\w+|тз)\s*\)?",
    r"\(?\s*техническ\w+\s+спецификаци\w+\s*\)?",
    r"\bдля\s+нужд\s+\S+(?:\s+\S+){0,3}",
    r"\bгосударственн\w+\s+закупк\w+\b",
    # procedural lead verbs: "Приобретение ручек", "Закуп продуктов" name the purchase, not the item
    r"^\s*(?:приобретение|закуп|закупка|покупка)\s+(?=\S)",
]
_BOILERPLATE_RE = [re.compile(p, re.IGNORECASE) for p in _BOILERPLATE]
_ENUM_PREFIX = re.compile(r"^\s*(?:#\s*)?\d{1,3}(?:\.\d{1,3})*\s*[\.\)\-–:]\s*")
_QUOTES = re.compile(r"[«»\"“”„‟'‘’`]+")
_PUNCT_RUN = re.compile(r"([,;:.\-–—/])\1+")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,;:.)])")
_EMPTY_PARENS = re.compile(r"\(\s*[,;:.\-]*\s*\)")
_WS = re.compile(r"\s+")


def _fix_homoglyphs(token: str) -> str:
    # Only touch mixed tokens that are mostly Cyrillic: "пoстaвкa" -> "поставка"; leave "USB", "Wi-Fi".
    cyr, lat = len(_CYR.findall(token)), len(_LAT.findall(token))
    if cyr and lat and cyr >= lat:
        return token.translate(_LAT2CYR)
    return token


def clean_name(raw: str | None) -> str:
    if not raw:
        return ""
    # NFKC would expand "№" to "No"; keep it as a neutral marker instead
    s = unicodedata.normalize("NFKC", raw.replace("№", "#")).replace("ё", "е").replace("Ё", "Е")
    s = " ".join(_fix_homoglyphs(t) for t in s.split())
    s = _QUOTES.sub(" ", s)
    s = _ENUM_PREFIX.sub("", s)
    for rx in _BOILERPLATE_RE:
        s = rx.sub(" ", s)
    s = _EMPTY_PARENS.sub(" ", s)
    s = _PUNCT_RUN.sub(r"\1", s)
    s = _SPACE_BEFORE_PUNCT.sub(r"\1", s)
    s = _WS.sub(" ", s).strip(" ,;:.-–—/")
    return s.lower()
