"""Agent orchestration: route -> resolve entities -> run tool -> LLM verdict -> validate -> render."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from tenderai.agent.composer import facts_for_llm, render, template_verdict, verdict_brief
from tenderai.agent.llm import LLM, LLMError
from tenderai.agent.router import Route, route
from tenderai.agent.tools import ToolResult, Tools
from tenderai.agent.validator import claims_from, fix_id_terms, organisation_bins, validate

log = logging.getLogger(__name__)
MIN_ITEM_SIMILARITY = 0.45

VERDICT_SYSTEM = {
    "ru": ("Ты — аналитик государственных закупок. Напиши вердикт из 1–3 предложений на русском языке: главный вывод и "
           "ключевые цифры. Опирайся на ЧЕРНОВИК и ФАКТЫ; числа пиши точно так, как в ФАКТАХ (с пробелами между разрядами и ₸). "
           "Не используй числа, БИНы и даты, которых нет в ФАКТАХ; ничего не додумывай. Не называй диапазон Q1–Q3 "
           "доверительным интервалом. Не повторяй вопрос. Без markdown-заголовков и списков."),
    "kk": ("Сен — мемлекеттік сатып алулар талдаушысысың. Қазақ тілінде 1–3 сөйлемнен тұратын қорытынды жаз: басты тұжырым "
           "және негізгі сандар. ЖОБА мен ФАКТІЛЕРГЕ сүйен; сандарды ФАКТІЛЕРДЕГІДЕЙ дәл жаз. ФАКТІЛЕРДЕ жоқ сандарды, "
           "БСН мен күндерді қолданба; ештеңе ойдан шығарма. Markdown тақырыптарсыз және тізімсіз."),
}


def _refusal(reason: str, r: Route) -> ToolResult:
    """No search was run; the parameters show what was asked, so the answer cannot read as 'all 27 customers'."""
    asked = {"intent": r.intent, "customer": r.customer_text, "years": r.years or r.out_of_scope_years,
             "item": r.item_text}
    return ToolResult("none", {k: v for k, v in asked.items() if v}, "—", 0, {}, found=False, limitations=[reason])


@dataclass
class AgentAnswer:
    question: str
    markdown: str
    route: dict
    tool: dict
    verdict_source: str                # llm | template
    validation: dict = field(default_factory=dict)
    timings: dict = field(default_factory=dict)


class Agent:
    def __init__(self, tools: Tools | None = None, llm: LLM | None = None, use_llm: bool = True):
        self.tools = tools or Tools()
        self.llm = llm if llm is not None else (LLM() if use_llm else None)
        if self.llm is not None and not self.llm.available():
            log.warning("LLM %s unavailable — falling back to rule-based routing and template verdicts", self.llm.model)
            self.llm = None

    def ask(self, question: str) -> AgentAnswer:
        t0 = time.time()
        r = route(question, self.llm)
        t_route = time.time()
        result, resolved = self._dispatch(r, question)
        if resolved and resolved.get("variants") and result.found:
            # the variants behind the answer, so the verdict may name them ("АИ-92") and the validator accepts it
            result.comparison = {**result.comparison, "variants": resolved["variants"]}
        t_tool = time.time()
        verdict, source, val = self._verdict(question, r, result)
        md = render(result, r.lang, verdict, resolved)
        return AgentAnswer(question, md, r.as_dict(), result.as_dict(), source, val,
                           {"route_s": round(t_route - t0, 2), "tool_s": round(t_tool - t_route, 2),
                            "verdict_s": round(time.time() - t_tool, 2)})

    # ------------------------------------------------------------------
    def _dispatch(self, r: Route, question: str = "") -> tuple[ToolResult, dict | None]:
        t = self.tools
        if r.off_topic:
            return _refusal("вопрос не относится к госзакупкам: агент отвечает только по закупкам 27 заказчиков "
                            "за 2024–2026 гг.", r), None
        if r.out_of_scope_years:
            return _refusal(f"период {', '.join(map(str, r.out_of_scope_years))} вне хранилища: данные есть только "
                            f"за 2024–2026 гг.", r), None
        if r.intent == "fair_price" and r.lot_ref:  # the lot already identifies the customer and the item
            return t.fair_price_for_lot(r.lot_ref), None
        bins = r.bins or None
        if not bins and r.customer_text:
            found, missing = t.resolve_customer_parts(r.customer_text)
            if missing:  # never fall back to all 27 customers: the answer would credit their totals to this one
                return _refusal(f"заказчик «{'», «'.join(missing)}» не найден среди 27 заказчиков хранилища", r), None
            bins = [c["biin"] for c in found]
        if not bins and not r.customer_text and r.router == "rules" and question:
            # no LLM to pull the customer's name out of the question: look for it in the question itself
            found, other = t.detect_customers(question)
            if other:
                return _refusal(f"заказчик «{', '.join(other)}…» не найден среди 27 заказчиков хранилища", r), None
            bins = [c["biin"] for c in found] or None
        enstru, text, resolved = r.enstru_code, r.item_text, None
        codes: str | list[str] | None = enstru
        if enstru:
            text = None
        elif r.item_text:
            cands = t.resolve_items(r.item_text)
            if cands and (cands[0]["tier"] <= 2 or float(cands[0]["sim"] or 0) >= MIN_ITEM_SIMILARITY):
                resolved = cands[0]
                enstru, text = resolved["enstru_code"], None
                codes = resolved.get("codes") or enstru
        if r.intent == "fair_price":
            if r.lot_ref:
                return t.fair_price_for_lot(r.lot_ref), None
            return ToolResult("fair_price_for_lot", {}, "—", 0, {}, found=False,
                              limitations=["для оценки справедливости укажите номер лота (например, «лот №80800385-ОИ6»)"]), None
        # grounding: an item that is neither a known product code nor any purchase name was misread from the question
        # (e.g. the classifier's own name "ЕНСТРУ"); say so instead of reporting an empty result as "no purchases"
        if text and not t.item_text_found(text, plans=r.intent == "volume_anomaly"):
            return _refusal(f"предмет «{text}» не распознан: нет такого кода ЕНС ТРУ и нет закупок с таким наименованием",
                            r), None
        # `codes`: the variant(s) the question asks for ("бензин АИ-92" -> one code, "бензин" -> every grade)
        if r.intent == "price_anomaly":
            return t.price_anomalies(r.threshold or 0.30, codes, text, bins, r.year, direction=r.direction,
                                     top_k=r.top_k), resolved
        if r.intent == "volume_anomaly":
            return t.volume_anomalies(bins, codes, text, r.year, top_k=r.top_k), resolved
        if r.intent == "compare" and len(r.years) >= 2 and not (bins and len(bins) >= 2):
            return t.compare_years(r.years, bins, codes, text), resolved
        if r.intent == "compare":
            # prices are comparable only within one unit: the one asked about, else the resolved item's
            unit = r.unit or (resolved["base_unit"] if resolved else None)
            return t.compare_customers(bins, r.year, codes, text, base_unit=unit), resolved
        if r.intent == "analytics" and enstru:
            return t.price_stats(codes, r.unit or (resolved["base_unit"] if resolved else None), r.year), resolved
        if r.intent == "analytics" and text:  # item named but not confidently resolved: show matching purchases
            return t.search_procurements(text, bins, None, r.year, top_k=r.top_k, base_unit=r.unit), resolved
        return t.search_procurements(text, bins, codes, r.year, top_k=r.top_k, base_unit=r.unit), resolved

    def _verdict(self, question: str, r: Route, result: ToolResult) -> tuple[str, str, dict]:
        fallback = template_verdict(result, r.lang)
        if self.llm is None or not result.found:
            return fallback, "template", {}
        facts = facts_for_llm(result)
        brief = verdict_brief(result, r.lang)
        msgs = [{"role": "system", "content": VERDICT_SYSTEM[r.lang]},
                {"role": "user", "content": f"ВОПРОС: {question}\n{brief}"}]
        last = {}
        for attempt in range(2):
            try:
                text = self.llm.chat(msgs, max_tokens=220).strip()
            except LLMError as e:
                log.warning("verdict LLM failed: %s", e)
                break
            text = fix_id_terms(text, organisation_bins(result))
            v = validate(text, {"facts": facts, "brief": brief}, claims_from(result))
            last = {"attempt": attempt + 1, "checked": v.checked, "unsupported": v.unsupported, "issues": v.issues}
            if v.ok and text:
                return text, "llm", last
            problems = []
            if v.unsupported:
                problems.append(f"Числа {v.unsupported} отсутствуют в ФАКТАХ.")
            problems += v.issues
            msgs += [{"role": "assistant", "content": text},
                     {"role": "user", "content": " ".join(problems) + " Перепиши, используя только числа из ФАКТОВ "
                                                                     "и в том значении, которое у них в ФАКТАХ."}]
        return fallback, "template", last
