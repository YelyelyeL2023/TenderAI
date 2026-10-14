"""Question understanding: intent + parameters, RU and KZ.

The LLM fills a JSON schema; a keyword/regex parser runs on every question too and is used when the LLM
is unavailable or returns something invalid. Hard facts that regexes extract reliably (BINs, lot numbers,
years, percentages) always override the LLM, which keeps fact extraction stable.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field

from tenderai.agent.llm import LLM, LLMError

log = logging.getLogger(__name__)

INTENTS = ("search", "compare", "analytics", "price_anomaly", "fair_price", "volume_anomaly")


@dataclass
class Route:
    intent: str
    lang: str                       # ru | kk
    item_text: str | None = None    # what is being bought, free text
    customer_text: str | None = None
    bins: list[str] = field(default_factory=list)
    lot_ref: str | None = None
    enstru_code: str | None = None
    year: int | None = None
    years: list[int] = field(default_factory=list)   # every in-scope year mentioned, in order
    out_of_scope_years: list[int] = field(default_factory=list)  # e.g. 2022 when only that was asked
    threshold: float | None = None  # fraction, 0.3 = 30%
    direction: str = "over"         # over | under (price anomalies: overpriced vs dumping)
    unit: str | None = None         # base unit named in the question ("в пачках" -> u5111)
    off_topic: bool = False         # nothing in the question is about procurement
    top_k: int = 10
    router: str = "rules"           # rules | llm

    def as_dict(self) -> dict:
        return asdict(self)


SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "item_text": {"type": ["string", "null"], "description": "goods/service name in nominative case, Russian"},
        "customer_text": {"type": ["string", "null"]},
        "year": {"type": ["integer", "null"]},
        "threshold_pct": {"type": ["number", "null"]},
        "top_k": {"type": ["integer", "null"]},
    },
    "required": ["intent", "item_text", "customer_text", "year", "threshold_pct", "top_k"],
}

# The few-shot examples must not be (or paraphrase) questions from the evaluation sets in eval/: an example with its
# expected parse would hand the model the answer and inflate the scores. Check with eval/check_leakage.py.
SYSTEM = """Ты — классификатор вопросов о государственных закупках Казахстана. Вопрос может быть на русском или казахском.
Определи intent:
- search: найти/показать закупки, лоты, договоры
- compare: сравнить заказчиков/ведомства/годы между собой
- analytics: средняя/медианная цена, статистика, динамика, сколько потрачено
- price_anomaly: закупки с отклонением цены от средней/средневзвешенной, завышенные цены
- fair_price: оценить адекватность/справедливость цены конкретного лота
- volume_anomaly: нетипичное завышение количества/объёма по сравнению с прошлыми годами
item_text — конкретный предмет закупки (товар/услуга) ПО-РУССКИ в именительном падеже (казахские названия переводи
на русский), без слов «закупки», «цена»; null, если конкретный предмет не назван (общие слова «ТРУ», «товары», «закупки»
— это null). customer_text — название заказчика/ведомства так, как оно написано в вопросе, целиком (вместе со словами
вроде «Автохозяйство», «Больница», «Автошаруашылығы»); если заказчиков несколько — через «;»; null, если заказчик
не назван (общие слова «заказчики», «ведомства» — это null). threshold_pct — порог в процентах, если указан.

Примеры:
«Найди закупки антисептика с отклонением цены > 25%» -> {"intent":"price_anomaly","item_text":"антисептик","customer_text":null,"year":null,"threshold_pct":25,"top_k":null}
«Сүт 2024 жылы орташа қаншадан сатып алынды?» -> {"intent":"analytics","item_text":"молоко","customer_text":null,"year":2024,"threshold_pct":null,"top_k":null}
«Сравни расходы на услуги охраны между ведомствами в 2026 году» -> {"intent":"compare","item_text":"услуги охраны","customer_text":null,"year":2026,"threshold_pct":null,"top_k":null}
«Жоспарланған тауар саны өткен жылдарға қарағанда әдеттен тыс көбейген тапсырыс берушілерді көрсет» -> {"intent":"volume_anomaly","item_text":null,"customer_text":null,"year":null,"threshold_pct":null,"top_k":null}
«Сравни закупки ЦСЭЭ и ТРК Президента за 2024 год» -> {"intent":"compare","item_text":null,"customer_text":"ЦСЭЭ; ТРК Президента","year":2024,"threshold_pct":null,"top_k":null}
Отвечай только JSON."""

_KK_CHARS = re.compile(r"[әғқңөұүһі]", re.IGNORECASE)
_KK_WORDS = re.compile(r"\b(сатып|алу|баға|бағасы|көлем|жыл|салыстыр|тап|қанша|әділ|ауытқу|тапсырыс)\w*", re.IGNORECASE)

RULES: list[tuple[str, re.Pattern]] = [
    ("fair_price", re.compile(r"адекватн|справедлив|fair\s*price|обоснован\w*(?:\s+ли)?\s+цен|әділ|негізді",
                              re.IGNORECASE)),
    ("volume_anomaly", re.compile(r"(завышени|рост|вырос|увеличени|нетипичн)\w*.{0,40}(количеств|объ[её]м)|"
                                  r"(количеств|объ[её]м)\w*.{0,40}(завыш|нетипичн|предыдущ|прошл)|көлем", re.IGNORECASE)),
    ("price_anomaly", re.compile(r"аномал|отклонени|завышен\w* цен|переплат|переплач|ауытқ|демпинг|ниже\s+рын",
                                 re.IGNORECASE)),
    ("compare", re.compile(r"сравн|в сравнении|против|салыстыр", re.IGNORECASE)),
    ("analytics", re.compile(r"средн|медиан|статистик|динамик|сколько|сумм|итог|орташа|қанша", re.IGNORECASE)),
    ("search", re.compile(r"найд|покаж|список|какие|найти|тап|көрсет", re.IGNORECASE)),
]

_BIN = re.compile(r"\b\d{12}\b")
# ENSTRU codes: new "106121.300.000001" and old "10.39.11.100.010.00.0166.000000000000" formats
_ENSTRU = re.compile(r"\b\d{2,6}(?:\.\d{2,12}){2,7}\b")
_LOT = re.compile(r"(?:лот\w*|lot)\s*(?:№|#|номер|n)?\s*([0-9][0-9A-Za-zА-Яа-я\-]*)", re.IGNORECASE)
# Kazakh puts the number first: "12345678-ЗЦП1 лотының"
_LOT_KK = re.compile(r"(?:№\s*)?([0-9]{5,}-[0-9A-Za-zА-Яа-яӘәҒғҚқҢңӨөҰұҮүҺһІі]+)\s+лот", re.IGNORECASE)
_ANNO = re.compile(r"\b\d{6,9}-\d{1,2}\b")
_YEAR = re.compile(r"\b(20[0-4]\d)\b")
IN_SCOPE_YEARS = (2024, 2025, 2026)
# "2025 compared with 2024": the first year is the one asked about, the later one is the base
_YEAR_VS = re.compile(r"сравнени|относительно|против|чем в|салыстырғанда|қарағанда", re.IGNORECASE)
_YEAR_RANGE = re.compile(r"\bс\s+20\d\d\s+(?:по|до)\s+20\d\d|20\d\d\s*[–—-]\s*20\d\d", re.IGNORECASE)
# units named in the question, mapped to the warehouse's base units (tenderai/etl/units.py)
_UNITS: list[tuple[str, re.Pattern]] = [
    ("u5111", re.compile(r"\bпачк\w*|\bбума(?:сы\w*|лар\w*|\b)", re.IGNORECASE)),
    ("L", re.compile(r"\bлитр\w*", re.IGNORECASE)),
    ("kg", re.compile(r"\bкилограмм\w*|\bкг\b", re.IGNORECASE)),
    ("pcs", re.compile(r"\bштук\w*|\bшт\b|\bдана\w*", re.IGNORECASE)),
]
_UNDER = re.compile(r"ниже\s+(?:рын|средн|аналог)|занижен|демпинг|дешевле|төмен", re.IGNORECASE)
# "which customer spends the most" is a comparison across customers, whatever the LLM calls it
_RANKING = re.compile(r"кто из (?:заказчик|ведомств)|как(?:ой|ие|ое) (?:заказчик|ведомств)|больше всех|меньше всех|"
                      r"қай тапсырыс беруші|қай ведомство", re.IGNORECASE)
_ON_TOPIC = re.compile(r"закуп|лот|договор|контракт|цен|стои|тру\b|енс|заказчик|ведомств|поставщ|тендер|объ[её]м|"
                       r"количеств|аномал|расход|потрат|купил|бюджет|сатып|баға|келісім|тапсырыс|көлем|жеткізуші|шығын|"
                       r"тжқ|саны\b|тауар|қызмет|жұмыстар|ауытқ",
                       re.IGNORECASE)
_PCT = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s*%")
_TOPK = re.compile(r"(?:топ|top)[\s\-]*(\d{1,2})", re.IGNORECASE)


def detect_lang(q: str) -> str:
    return "kk" if _KK_CHARS.search(q) or len(_KK_WORDS.findall(q)) >= 2 else "ru"


def rule_route(q: str) -> Route:
    intent = next((name for name, rx in RULES if rx.search(q)), "search")
    r = Route(intent=intent, lang=detect_lang(q))
    _apply_hard_facts(r, q)
    if r.lot_ref and intent in ("search", "analytics"):
        r.intent = "fair_price" if re.search(r"цен|баға", q, re.IGNORECASE) else r.intent
    if r.intent in ("search", "analytics") and _RANKING.search(q):
        r.intent = "compare"
    _finish(r, q)
    return r


def _apply_hard_facts(r: Route, q: str) -> None:
    r.bins = _BIN.findall(q)
    e = _ENSTRU.search(q)
    if e:
        r.enstru_code = e.group(0)
    m = _LOT.search(q) or _LOT_KK.search(q) or _ANNO.search(q)
    if m:
        r.lot_ref = m.group(0) if m.re is _ANNO else m.group(1)
    mentioned = [int(y) for y in _YEAR.findall(q)]
    r.years = list(dict.fromkeys(y for y in mentioned if y in IN_SCOPE_YEARS))
    r.out_of_scope_years = [] if r.years else list(dict.fromkeys(y for y in mentioned if y not in IN_SCOPE_YEARS))
    if len(r.years) == 1 or (r.years and _YEAR_VS.search(q) and not _YEAR_RANGE.search(q)):
        r.year = r.years[0]
    if _UNDER.search(q):
        r.direction = "under"
    units = {u for u, rx in _UNITS if rx.search(q)}
    if len(units) == 1:
        r.unit = units.pop()
    p = _PCT.search(q)
    if p:
        r.threshold = float(p.group(1).replace(",", ".")) / 100
    k = _TOPK.search(q)
    if k:
        r.top_k = max(1, min(int(k.group(1)), 50))


def route(q: str, llm: LLM | None = None) -> Route:
    base = rule_route(q)
    if llm is None:
        return base
    try:
        out = llm.json([{"role": "system", "content": SYSTEM}, {"role": "user", "content": q}], SCHEMA)
    except LLMError as e:
        log.warning("router LLM failed, using rules: %s", e)
        return base
    intent = out.get("intent") if out.get("intent") in INTENTS else base.intent
    # A lot number is decisive for the fairness question; the LLM sometimes calls it "search"
    if base.intent == "fair_price" and base.lot_ref:
        intent = "fair_price"
    # "how much does lettuce cost" is not a fairness question: without a lot number and without fairness
    # words in the question, trust the rules
    if intent == "fair_price" and not base.lot_ref and base.intent != "fair_price":
        intent = base.intent
    if intent in ("search", "analytics") and _RANKING.search(q):
        intent = "compare"
    # "сравни 2024 и 2025" is a year comparison whatever the LLM says (its choice varies between runs)
    if base.intent == "compare" and len(base.years) >= 2 and intent in ("search", "analytics"):
        intent = "compare"
    r = Route(intent=intent, lang=base.lang, item_text=_clean_item(out.get("item_text")),
              customer_text=out.get("customer_text") or None, router="llm")
    _apply_hard_facts(r, q)
    if r.threshold is None and out.get("threshold_pct"):
        r.threshold = float(out["threshold_pct"]) / 100
    if out.get("top_k") and not _TOPK.search(q):
        r.top_k = max(1, min(int(out["top_k"]), 50))
    _finish(r, q)
    return r


def _finish(r: Route, q: str) -> None:
    if r.customer_text and r.lot_ref:  # the LLM sometimes copies the lot number into the customer field
        r.customer_text = r.customer_text.replace(r.lot_ref, "").strip(" ;,") or None
    if r.customer_text and _is_generic_customer(r.customer_text):
        r.customer_text = None
    # off-topic only for the default "search" bucket: a question classified as an anomaly, comparison, analytics
    # or fairness question is about procurement even when its wording misses the vocabulary list
    r.off_topic = r.intent == "search" and not (_ON_TOPIC.search(q) or r.item_text or r.customer_text or r.bins
                                                or r.enstru_code or r.lot_ref or r.years)


_GENERIC_CUSTOMER = re.compile(r"^(?:(?:все|других|другие|остальные|разные|разных|басқа|барлық)\s+)?(?:27\s+)?"
                               r"(?:заказчик|ведомств|госорган|организаци|тапсырыс\s+беруші|ведомство)\w*$",
                               re.IGNORECASE)


def _is_generic_customer(s: str) -> bool:
    return all(_GENERIC_CUSTOMER.match(p.strip()) for p in re.split(r"[;,]", s) if p.strip())


_GENERIC_ITEMS = {"тру", "товары", "товар", "работы", "услуги", "товары работы услуги", "закупки", "лоты"}


def _clean_item(s: str | None) -> str | None:
    if not s:
        return None
    s = re.sub(r"\b(закупк\w*|цен\w*|лот\w*)\b", " ", s, flags=re.IGNORECASE)
    # the classifier itself is not an item: "по данному ЕНСТРУ" means "within each code", not "items named ЕНСТРУ"
    s = re.sub(r"\b(?:по\s+)?(?:данн\w+|этому|этого|каждому|каждого|соответствующ\w+)?\s*"
               r"(?:енс\s*тру|енстру|ктру|кпвэд|тру|классификатор\w*|код\w*\s+енс\w*)\b", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip(" ,.;:")
    return None if not s or s.lower() in _GENERIC_ITEMS else s
