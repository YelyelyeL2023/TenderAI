"""Deterministic answer rendering (markdown) in the user's language.

Sections follow the assignment: verdict, search parameters / data used, analytics & comparison,
metric, confidence & limitations, Top-K with direct links. Only the verdict may be LLM-written;
everything else is rendered from the ToolResult so figures cannot drift.
"""
from __future__ import annotations

import json
from datetime import date, datetime

from tenderai.agent.i18n import limitation, method
from tenderai.agent.tools import ToolResult

H = {
    "ru": {"verdict": "Вердикт", "params": "Параметры поиска и использованные данные", "analytics": "Аналитика и сравнение",
           "metric": "Метрика оценки", "confidence": "Уверенность и ограничения", "details": "Детализация (Top-{k})",
           "period": "Период", "bins": "Заказчики (БИН)", "filters": "Фильтры", "n": "Объектов в анализе",
           "all27": "все 27 заказчиков из перечня", "none": "нет", "dq": "Исключено по качеству данных", "conf": "Уверенность",
           "nodata": "Данных, удовлетворяющих запросу, в хранилище нет — ответ без цифр не даётся.",
           "item_resolved": "Предмет закупки распознан как", "variants": "варианты", "variant": "вариант",
           "asked": "Запрошено (поиск не выполнялся)", "src": "Источник: API OWS v3 → хранилище TenderAI"},
    "kk": {"verdict": "Қорытынды", "params": "Іздеу параметрлері және пайдаланылған деректер", "analytics": "Талдау және салыстыру",
           "metric": "Бағалау әдісі", "confidence": "Сенімділік және шектеулер", "details": "Толығырақ (Top-{k})",
           "period": "Кезең", "bins": "Тапсырыс берушілер (БСН)", "filters": "Сүзгілер", "n": "Талдаудағы объектілер",
           "all27": "тізімдегі барлық 27 тапсырыс беруші", "none": "жоқ", "dq": "Деректер сапасы бойынша алынып тасталды", "conf": "Сенімділік",
           "nodata": "Сұраққа сәйкес деректер қоймада жоқ — цифрсыз жауап берілмейді.",
           "item_resolved": "Сатып алу нысаны анықталды", "variants": "нұсқалар", "variant": "нұсқа",
           "asked": "Сұралды (іздеу жүргізілмеді)", "src": "Дереккөз: OWS v3 API → TenderAI қоймасы"},
}

VERDICT_WORDS = {
    "ru": {"fair": "цена в пределах рыночного диапазона", "elevated": "цена выше рыночной, но в пределах 30%",
           "overpriced_anomaly": "цена аномально завышена", "underpriced_anomaly": "цена аномально занижена (риск демпинга)",
           "below_market": "цена ниже рыночной"},
    "kk": {"fair": "баға нарықтық диапазонда", "elevated": "баға нарықтықтан жоғары, бірақ 30% шегінде",
           "overpriced_anomaly": "баға аномальды жоғары", "underpriced_anomaly": "баға аномальды төмен (демпинг қаупі)",
           "below_market": "баға нарықтықтан төмен"},
}
CONF_WORDS = {"ru": {"high": "высокая", "medium": "средняя", "low": "низкая"},
              "kk": {"high": "жоғары", "medium": "орташа", "low": "төмен"}}


def fmt(x, digits: int = 2) -> str:
    if x is None:
        return "—"
    if isinstance(x, (date, datetime)):
        return x.strftime("%m.%Y") if isinstance(x, date) and x.day == 1 else x.strftime("%d.%m.%Y")
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    if v in (float("inf"), float("-inf")):
        return "∞" if v > 0 else "−∞"
    s = f"{v:,.{digits}f}".replace(",", " ").replace(".", ",")
    return s.removesuffix(",00")


INDEX_NAMES = {
    "ru": {"official_cpi_stat_gov_kz": "официальный ИПЦ stat.gov.kz", "internal_procurement_index": "внутренний индекс цен закупок",
           "none": "не применялся"},
    "kk": {"official_cpi_stat_gov_kz": "stat.gov.kz ресми ТБИ", "internal_procurement_index": "сатып алулардың ішкі баға индексі",
           "none": "қолданылмады"},
}
CPI_CATEGORIES = {
    "ru": {"food": "продовольственные товары", "nonfood": "непродовольственные товары", "services": "платные услуги",
           "total": "все товары и услуги"},
    "kk": {"food": "азық-түлік тауарлары", "nonfood": "азық-түлікке жатпайтын тауарлар", "services": "ақылы қызметтер",
           "total": "барлық тауарлар мен қызметтер"},
}


def adjustments_line(adj: dict, lang: str) -> str:
    ru = lang == "ru"
    idx = INDEX_NAMES[lang].get(adj.get("price_index"), adj.get("price_index"))
    cat = adj.get("cpi_category")
    if cat:
        idx += f" ({CPI_CATEGORIES[lang].get(cat, cat)}, {'по региону каждого аналога' if ru else 'әр аналогтың өңірі бойынша'})"
    rng = adj.get("time_factor_range")
    if rng:
        idx += f", {'коэффициенты' if ru else 'коэффициенттер'} {fmt(rng[0], 3)}–{fmt(rng[1], 3)}"
    k = adj.get("regional_coefficient_target")
    reg = (f"{fmt(k, 3)} ({adj.get('regions_in_sample')} {'регионов в выборке' if ru else 'өңір'})" if isinstance(k, (int, float))
           else ("не применялся — все аналоги из одного региона" if ru else "қолданылмады — аналогтар бір өңірден"))
    seas = isinstance(adj.get("seasonal"), dict)
    seas_t = ("применена" if seas else "не применялась (мало данных)") if ru else ("қолданылды" if seas else "қолданылмады")
    return (f"- Инфляция: {idx}; {'региональный коэффициент' if ru else 'өңірлік коэффициент'}: {reg}; "
            f"{'сезонность' if ru else 'маусымдық'}: {seas_t}")


def quantity_line(q: dict, unit: str, lang: str) -> str:
    """Fairness of the quantity: the customer's planned volume of this item in the lot's year vs earlier years."""
    ru = lang == "ru"
    head = "- Количество (план заказчика по этому ЕНС ТРУ)" if ru else "- Саны (тапсырыс берушінің осы ЕНС ТРУ бойынша жоспары)"
    if q.get("quantity") is None or not q.get("previous"):
        return head + (": нет плановой истории за предыдущие годы — объём сравнить не с чем" if ru else
                       ": алдыңғы жылдардағы жоспар жоқ — көлемді салыстыруға болмайды")
    prev = ", ".join(f"{y}: {fmt(v, 0)}" for y, v in sorted(q["previous"].items()))
    verdict = (("нетипичный рост (> 1,5× к медиане прошлых лет)" if q["flagged"] else "в пределах обычного")
               if ru else ("әдеттен тыс өсу (өткен жылдар медианасынан > 1,5×)" if q["flagged"] else "әдеттегі шекте"))
    return f"{head}: {q['year']} — {fmt(q['quantity'], 0)} {unit} vs {prev} (×{fmt(q['ratio'])}) — {verdict}"


def template_verdict(r: ToolResult, lang: str) -> str:
    s, ru = r.summary, lang == "ru"
    if not r.found:
        # say why. Refusals and fair-price failures carry the reason as their first limitation (unknown customer,
        # period outside the warehouse, lot not found); other tools' limitations are method notes, so for them the
        # reason is the filters that matched nothing.
        if r.tool in ("none", "fair_price_for_lot") and r.limitations:
            why = limitation(r.limitations[0], lang).rstrip(".")
        else:
            f = {k: v for k, v in r.params.items() if v not in (None, [], "", False) and k not in
                 ("threshold", "direction", "ratio_threshold", "include_services", "top_k")}
            shown = "; ".join(f"{k} = {', '.join(map(str, v)) if isinstance(v, list) else v}" for k, v in f.items())
            why = ((f"по заданным фильтрам ({shown}) записей нет" if ru else f"берілген сүзгілер ({shown}) бойынша жазба жоқ")
                   if shown else "")
        return H[lang]["nodata"] + (f" {why[:1].upper()}{why[1:]}." if why else "")
    if r.tool == "fair_price_for_lot":
        w = VERDICT_WORDS[lang][s["verdict"]]
        return (f"Лот {s['lot']['lot_number']}: {w} — {fmt(s['target_price'])} ₸ против справедливой цены "
                f"{fmt(s['fair_price'])} ₸ (отклонение {fmt(s['deviation_pct'], 1)}%, N={r.n_analyzed})." if ru else
                f"{s['lot']['lot_number']} лоты: {w} — {fmt(s['target_price'])} ₸, әділ баға {fmt(s['fair_price'])} ₸ "
                f"(ауытқу {fmt(s['deviation_pct'], 1)}%, N={r.n_analyzed}).")
    if r.tool == "price_anomalies" and r.params.get("direction") == "under":
        th = fmt(r.params["threshold"] * 100, 0)
        return (f"Из {r.n_analyzed} сопоставимых позиций {s['flagged']} ({fmt(s['share_pct'], 1)}%) имеют цену ниже "
                f"средневзвешенной по ЕНС ТРУ более чем на {th}% (возможный демпинг); из них {s['confirmed']} "
                f"подтверждены как резко выбивающиеся (robust z < −3)." if ru else
                f"{r.n_analyzed} салыстырмалы позицияның {s['flagged']}-інде ({fmt(s['share_pct'], 1)}%) баға ЕНС ТРУ "
                f"бойынша орташа өлшенгеннен {th}%-дан астам төмен (демпинг қаупі); оның {s['confirmed']}-і күрт "
                f"ауытқу ретінде расталды (robust z < −3).")
    if r.tool == "price_anomalies":
        th = fmt(r.params["threshold"] * 100, 0)
        return (f"Из {r.n_analyzed} сопоставимых позиций {s['flagged']} ({fmt(s['share_pct'], 1)}%) имеют цену выше "
                f"средневзвешенной по ЕНС ТРУ более чем на {th}%; из них {s['confirmed']} подтверждены как резко "
                f"выбивающиеся (robust z > 3) с оценкой возможной переплаты {fmt(s['confirmed_overpayment_kzt'], 0)} ₸." if ru else
                f"{r.n_analyzed} салыстырмалы позицияның {s['flagged']}-інде ({fmt(s['share_pct'], 1)}%) баға ЕНС ТРУ "
                f"бойынша орташа өлшенгеннен {th}%-дан астам жоғары; оның {s['confirmed']}-і күрт ауытқу ретінде "
                f"расталды (robust z > 3), ықтимал артық төлем — {fmt(s['confirmed_overpayment_kzt'], 0)} ₸.")
    if r.tool == "volume_anomalies":
        return (f"Из {s['series_compared']} рядов «заказчик × ЕНС ТРУ» рост планового количества более чем в 1,5 раза "
                f"выявлен в {s['flagged']} ({fmt(s['share_pct'], 1)}%); подтверждены как нетипичные и существенные "
                f"{s['confirmed']} случаев с дополнительными плановыми расходами {fmt(s['confirmed_extra_amount_kzt'], 0)} ₸." if ru else
                f"«Тапсырыс беруші × ЕНС ТРУ» {s['series_compared']} қатарының {s['flagged']}-інде ({fmt(s['share_pct'], 1)}%) "
                f"жоспарлы көлем 1,5 еседен астам өсті; оның {s['confirmed']}-і әдеттен тыс және елеулі деп расталды, "
                f"қосымша жоспарлы шығыстар — {fmt(s['confirmed_extra_amount_kzt'], 0)} ₸.")
    if r.tool == "search_procurements":
        return (f"Найдено {s['count']} позиций на сумму {fmt(s['total_amount_kzt'], 0)} ₸ у {s['customers']} заказчиков." if ru
                else f"{s['customers']} тапсырыс берушіде жалпы сомасы {fmt(s['total_amount_kzt'], 0)} ₸ болатын "
                     f"{s['count']} позиция табылды.")
    if r.tool == "compare_customers":
        top = r.rows[0]
        return (f"Сравнено {s['customers']} заказчиков; наибольший объём — {top['name_ru'] or top['customer_bin']} "
                f"({fmt(top['total_amount'], 0)} ₸, {top['n']} позиций)." if ru else
                f"{s['customers']} тапсырыс беруші салыстырылды; ең үлкен көлем — {top['name_ru'] or top['customer_bin']} "
                f"({fmt(top['total_amount'], 0)} ₸, {top['n']} позиция).")
    if r.tool == "compare_years":
        parts = [f"{x['year']} — {fmt(x['total_amount'], 0)} ₸ ({x['n']} {'позиций' if ru else 'позиция'})"
                 for x in r.rows]
        last = r.rows[-1] if len(r.rows) > 1 else None
        chg = (f"; {'изменение' if ru else 'өзгеріс'} {last['year']} {'к' if ru else '/'} {r.rows[-2]['year']}: "
               f"{fmt(last['change_pct'], 1)}%") if last and last.get("change_pct") is not None else ""
        return ("Сравнение по годам: " if ru else "Жылдар бойынша салыстыру: ") + "; ".join(parts) + chg + "."
    if r.tool == "price_stats":
        want = str(r.params.get("year") or "all")
        picked = []   # one row per (variant code, unit): the asked period, else the whole period
        for key in dict.fromkeys((x["enstru_code"], x["base_unit"]) for x in r.rows):
            group = [x for x in r.rows if (x["enstru_code"], x["base_unit"]) == key]
            picked.append(next((x for x in group if x["period"] == want), None)
                          or next((x for x in group if x["period"] == "all"), group[0]))
        if len(picked) == 1:
            a = picked[0]
            per = "2024–2026" if a["period"] == "all" else a["period"]
            return (f"По ЕНС ТРУ {a['enstru_code']} ({a['base_unit']}) за {per}: средневзвешенная цена {fmt(a['weighted_avg'])} ₸, "
                    f"медиана {fmt(a['median'])} ₸; {a['n']} закупок у {a['n_customers']} заказчиков." if ru else
                    f"ЕНС ТРУ {a['enstru_code']} ({a['base_unit']}), {per}: орташа өлшенген баға {fmt(a['weighted_avg'])} ₸, "
                    f"медиана {fmt(a['median'])} ₸; {a['n_customers']} тапсырыс берушінің {a['n']} сатып алуы.")
        parts = [f"{a.get('variant') or a['enstru_code']} ({a['enstru_code']}, {a['base_unit']}, "
                 f"{'2024–2026' if a['period'] == 'all' else a['period']}) — "
                 + (f"средневзвешенная {fmt(a['weighted_avg'])} ₸, медиана {fmt(a['median'])} ₸, {a['n']} закупок" if ru else
                    f"орташа өлшенген {fmt(a['weighted_avg'])} ₸, медиана {fmt(a['median'])} ₸, {a['n']} сатып алу")
                 for a in picked]
        return ((f"Цены по {len(picked)} вариантам товара (отдельно, без общей средней): " if ru else
                 f"Тауардың {len(picked)} нұсқасы бойынша бағалар (бөлек, жалпы орташасыз): ") + "; ".join(parts) + ".")
    return ""


def _params_block(r: ToolResult, lang: str, resolved: dict | None) -> list[str]:
    h, p = H[lang], {k: v for k, v in r.params.items() if v not in (None, [], "")}
    if r.tool == "none":  # refused before any search: show what was asked, not a default "all 27 customers"
        asked = "; ".join(f"{k} = {', '.join(map(str, v)) if isinstance(v, list) else v}" for k, v in p.items())
        return [f"- **{h['asked']}:** {asked or '—'}", f"- **{h['n']}:** 0", f"- _{h['src']}_"]
    bins = p.pop("customer_bins", None)
    year = p.pop("year", None)
    out = [f"- **{h['period']}:** {year if year else '2024–2026'}",
           f"- **{h['bins']}:** {', '.join(bins) if bins else h['all27']}"]
    if p:
        out.append(f"- **{h['filters']}:** " + "; ".join(
            f"{k} = {fmt(v) if isinstance(v, float) else ', '.join(map(str, v)) if isinstance(v, list) else v}"
            for k, v in p.items()))
    if resolved:
        variants = resolved.get("variants") or []
        line = (f"- **{h['item_resolved']}:** ЕНС ТРУ `{resolved['enstru_code']}` — {resolved['name']} "
                f"({resolved['base_unit']}, N={resolved['n']})")
        if len(variants) > 1:
            more = len(resolved.get("codes") or []) - len(variants)
            line = (f"- **{h['item_resolved']}:** {resolved['name']}; {h['variants']}: "
                    + ", ".join(f"{v['variant'] or '—'} (`{v['enstru_code']}`)" for v in variants)
                    + (f" (+{more})" if more > 0 else ""))
        elif variants and resolved.get("variant_words") and variants[0]["variant"]:
            line += f", {h['variant']}: {variants[0]['variant']}"
        out.append(line)
    out.append(f"- **{h['n']}:** {r.n_analyzed}")
    out.append(f"- _{h['src']}_")
    return out


def _analytics_block(r: ToolResult, lang: str) -> list[str]:
    s, ru = r.summary, lang == "ru"
    out: list[str] = []
    if r.tool == "fair_price_for_lot":
        c = r.comparison
        lot = s["lot"]
        out += [f"- Лот: [{lot['lot_number']}]({lot['url']}) — {lot['name_ru']} "
                f"(ЕНС ТРУ `{lot['enstru_code']}`, {lot['base_unit']}), {'заказчик' if ru else 'тапсырыс беруші'} {lot['customer_bin']}",
                f"- {s['price_kind'] if ru else 'Нысан бағасы'}: **{fmt(s['target_price'])} ₸** / {lot['base_unit']}",
                f"- Fair Price: **{fmt(s['fair_price'])} ₸**, {'диапазон Q1–Q3' if ru else 'Q1–Q3 аралығы'}: "
                f"{fmt(s['fair_band'][0])} – {fmt(s['fair_band'][1])} ₸; {'средневзвешенная' if ru else 'орташа өлшенген'}: {fmt(c['weighted_mean'])} ₸",
                f"- {'Отклонение' if ru else 'Ауытқу'}: **{fmt(s['deviation_pct'], 1)}%**, robust z = {fmt(s['robust_z'])}",
                f"- {'Уровень сравнения' if ru else 'Салыстыру деңгейі'}: `{c['level']}`, N = {c['n']}",
                adjustments_line(c["adjustments"], lang)]
        q = s.get("quantity_check")
        if q:
            out.append(quantity_line(q, lot["base_unit"], lang))
    elif r.tool == "price_anomalies" and r.params.get("direction") == "under":
        out += [f"- {'Сопоставимых позиций (≥3 аналога)' if ru else 'Салыстырмалы позициялар (≥3 аналог)'}: {r.n_analyzed} "
                f"{'в' if ru else ''} {s['enstru_groups_compared']} {'группах ЕНС ТРУ × ед.' if ru else 'ЕНС ТРУ × бірлік тобында'}",
                f"- {'Цена ниже порога (правило)' if ru else 'Шектен төмен баға (ереже)'}: {s['flagged']} ({fmt(s['share_pct'], 1)}%)",
                f"- {'Подтверждено робастной проверкой (z < −3)' if ru else 'Робастты тексеріспен расталды (z < −3)'}: "
                f"**{s['confirmed']}** ({fmt(s['confirmed_share_pct'], 1)}%)"]
    elif r.tool == "price_anomalies":
        out += [f"- {'Сопоставимых позиций (≥3 аналога)' if ru else 'Салыстырмалы позициялар (≥3 аналог)'}: {r.n_analyzed} "
                f"{'в' if ru else ''} {s['enstru_groups_compared']} {'группах ЕНС ТРУ × ед.' if ru else 'ЕНС ТРУ × бірлік тобында'}",
                f"- {'Отклонение более порога (правило)' if ru else 'Шектен асқан ауытқу (ереже)'}: {s['flagged']} "
                f"({fmt(s['share_pct'], 1)}%), {'оценка переплаты' if ru else 'артық төлем'} {fmt(s['estimated_overpayment_kzt'], 0)} ₸",
                f"- {'Подтверждено робастной проверкой (z > 3)' if ru else 'Робастты тексеріспен расталды (z > 3)'}: "
                f"**{s['confirmed']}** ({fmt(s['confirmed_share_pct'], 1)}%), "
                f"{'оценка переплаты' if ru else 'артық төлем'} **{fmt(s['confirmed_overpayment_kzt'], 0)} ₸**",
                f"- {'Вероятные ошибки ед. изм./количества (> 21× аналогов, исключены из оценки)' if ru else 'Өлшем бірлігі/саны қателері ықтимал (> 21×, бағалауға кірмейді)'}: "
                f"{s['likely_unit_errors']}"]
    elif r.tool == "volume_anomalies":
        out += [f"- {'Рядов сравнено' if ru else 'Салыстырылған қатарлар'}: {s['series_compared']}; "
                f"{'рост > 1,5× (правило)' if ru else '> 1,5× өсу (ереже)'}: {s['flagged']} ({fmt(s['share_pct'], 1)}%)",
                f"- {'Подтверждено (статистически нетипично и ≥ 1 млн ₸)' if ru else 'Расталды (статистикалық әдеттен тыс және ≥ 1 млн ₸)'}: "
                f"**{s['confirmed']}** ({fmt(s['confirmed_share_pct'], 1)}%), "
                f"{'доп. плановые расходы' if ru else 'қосымша жоспарлы шығыстар'} **{fmt(s['confirmed_extra_amount_kzt'], 0)} ₸**"]
    elif r.tool in ("search_procurements",):
        out += [f"- {'Позиций' if ru else 'Позициялар'}: {s['count']}, {'сумма' if ru else 'сома'}: {fmt(s['total_amount_kzt'], 0)} ₸, "
                f"{'заказчиков' if ru else 'тапсырыс берушілер'}: {s['customers']}",
                f"- {'Период данных' if ru else 'Деректер кезеңі'}: {fmt(s['period'][0])} – {fmt(s['period'][1])}"]
    elif r.tool == "compare_customers":
        out += ["| БИН | " + ("Заказчик | Позиций | Сумма, ₸ | Средневзв. цена | Медиана | Поставщиков |" if ru else
                             "Тапсырыс беруші | Позиция | Сома, ₸ | Орт. өлш. баға | Медиана | Жеткізушілер |"),
                "|---|---|---|---|---|---|---|"]
        out += [f"| {x['customer_bin']} | {x['name_ru'] or '—'} | {x['n']} | {fmt(x['total_amount'], 0)} | "
                f"{fmt(x['weighted_avg_price'])} | {fmt(x['median_price'])} | {x['suppliers']} |" for x in r.rows]
    elif r.tool == "compare_years":
        out += ["| " + ("Год | Позиций | Сумма, ₸ | Заказчиков | Изменение суммы к пред. году |" if ru else
                        "Жыл | Позиция | Сома, ₸ | Тапсырыс берушілер | Алдыңғы жылға өзгеріс |"),
                "|---|---|---|---|---|"]
        out += [f"| {x['year']} | {x['n']} | {fmt(x['total_amount'], 0)} | {x['customers']} | "
                f"{fmt(x['change_pct'], 1) + '%' if x.get('change_pct') is not None else '—'} |" for x in r.rows]
    elif r.tool == "price_stats":
        multi = len({x["enstru_code"] for x in r.rows}) > 1   # variants of one product: a column says which
        first = (("Вариант (ЕНС ТРУ) | " if ru else "Нұсқа (ЕНС ТРУ) | ") if multi else "")
        out += ["| " + first + ("Период | N | Заказчиков | Средневзв. | Медиана | Q1 | Q3 | Мин | Макс |" if ru else
                                "Кезең | N | Тапсырыс берушілер | Орт. өлш. | Медиана | Q1 | Q3 | Мин | Макс |"),
                "|---" * (10 if multi else 9) + "|"]
        for x in r.rows:
            cell = f"{x.get('variant') or '—'} (`{x['enstru_code']}`, {x['base_unit']}) | " if multi else ""
            out.append(f"| {cell}{x['period']} | {x['n']} | {x['n_customers']} | {fmt(x['weighted_avg'])} | "
                       f"{fmt(x['median'])} | {fmt(x['q1'])} | {fmt(x['q3'])} | {fmt(x['min_price'])} | {fmt(x['max_price'])} |")
    return out


def _details_block(r: ToolResult, lang: str) -> list[str]:
    ru = lang == "ru"
    # table-style answers keep their table in `rows`; their Top-K purchases with links are in `examples`
    rows = r.examples if r.tool in ("compare_customers", "price_stats", "compare_years") else r.rows
    if not rows:
        return []
    out = []
    for i, x in enumerate(rows, 1):
        url = x.get("url")
        label = x.get("obj_id") or x.get("enstru_code")
        name = x.get("name_clean") or ""
        if r.tool == "price_anomalies":
            tag = "✔ " if x.get("confirmed") else ("⚠ " if x.get("likely_unit_error") else "")
            line = (f"{tag}{name} — {fmt(x['price'])} ₸ vs {fmt(x['peer_weighted_avg'])} ₸ "
                    f"(**{'+' if x['deviation_pct'] >= 0 else ''}{fmt(x['deviation_pct'], 1)}%**, z={fmt(x['robust_z'], 1)}, {'аналогов' if ru else 'аналог'} {x['n_peers']}), "
                    f"{'заказчик' if ru else 'тапсырыс беруші'} {x['customer_bin']}, {fmt(x['month'])}")
        elif r.tool == "volume_anomalies":
            prev = ", ".join(f"{y}: {fmt(q, 0)}" for y, q in sorted(x["previous_years"].items()))
            line = (f"{'✔ ' if x.get('confirmed') else ''}{name} ({x['base_unit']}), "
                    f"{'заказчик' if ru else 'тапсырыс беруші'} {x['customer_bin']}: "
                    f"{x['year']} — {fmt(x['quantity'], 0)} vs {prev} (**×{fmt(x['ratio'])}**, z={fmt(x['robust_z'])}, "
                    f"IF={fmt(x['isolation_forest_score'], 3)})")
        elif r.tool == "fair_price_for_lot":
            line = (f"аналог: {fmt(x['price'])} ₸, {'заказчик' if ru else 'тапсырыс беруші'} "
                    f"{x['customer_bin']}, {fmt(x['month'])}, KATO {x.get('city_kato') or '—'}")
        else:
            line = (f"{name} — {fmt(x.get('amount'), 0)} ₸ ({fmt(x.get('quantity'), 0)} × {fmt(x.get('price'))} ₸), "
                    f"{'заказчик' if ru else 'тапсырыс беруші'} {x.get('customer_bin')}, {fmt(x.get('month'))}")
        out.append(f"{i}. [{label}]({url}) — {line}" if url else f"{i}. {label} — {line}")
    return out


def render(r: ToolResult, lang: str, verdict: str, resolved: dict | None = None) -> str:
    h = H[lang]
    parts = [f"### {h['verdict']}", verdict, "", f"### {h['params']}", *_params_block(r, lang, resolved), ""]
    if r.found:
        parts += [f"### {h['analytics']}", *_analytics_block(r, lang), "", f"### {h['metric']}",
                  method(r.tool, r.method, lang), ""]
    conf = r.summary.get("confidence_label")
    parts += [f"### {h['confidence']}"]
    if conf:
        parts.append(f"- **{h['conf']}:** {CONF_WORDS[lang][conf]} ({fmt(r.summary['confidence'])})")
    if r.dq:
        parts.append(f"- **{h['dq']}:** " + "; ".join(f"{k} = {fmt(v * 100, 1)}%" for k, v in r.dq.items()))
    parts += [f"- {limitation(x, lang)}" for x in r.limitations] or [f"- {h['none']}"]
    details = _details_block(r, lang)
    if details:
        parts += ["", f"### {h['details'].format(k=len(details))}", *details]
    return "\n".join(parts)


def verdict_brief(r: ToolResult, lang: str) -> str:
    """Human-labelled facts for the LLM: the same lines the user sees, plus the template verdict as a draft."""
    lines = _analytics_block(r, lang)
    if r.tool == "price_stats":  # tables confuse the model: give one labelled sentence per period instead
        ru = lang == "ru"
        lines = [(f"- {x['variant'] + ', ' if x.get('variant') else ''}"
                  f"период {x['period']}: закупок {x['n']}, заказчиков {x['n_customers']}, средневзвешенная "
                  f"{fmt(x['weighted_avg'])} ₸, медиана {fmt(x['median'])} ₸, Q1 {fmt(x['q1'])} ₸, Q3 {fmt(x['q3'])} ₸")
                 if ru else
                 (f"- {x['variant'] + ', ' if x.get('variant') else ''}"
                  f"кезең {x['period']}: сатып алулар {x['n']}, тапсырыс берушілер {x['n_customers']}, орташа өлшенген "
                  f"{fmt(x['weighted_avg'])} ₸, медиана {fmt(x['median'])} ₸, Q1 {fmt(x['q1'])} ₸, Q3 {fmt(x['q3'])} ₸")
                 for x in r.rows]
    if r.summary.get("confidence_label"):
        lines.append(f"- {H[lang]['conf']}: {CONF_WORDS[lang][r.summary['confidence_label']]}")
    lines += [f"- {x}" for x in r.limitations[:3]]
    lines += [f"- {l}" for l in _details_block(r, lang)[:3]]
    return f"ЧЕРНОВИК: {template_verdict(r, lang)}\nФАКТЫ:\n" + "\n".join(lines)


def facts_for_llm(r: ToolResult, max_rows: int = 5) -> str:
    """Compact JSON the LLM may draw numbers from (and the validator checks against)."""
    return json.dumps({"tool": r.tool, "found": r.found, "params": r.params, "n_analyzed": r.n_analyzed,
                       "summary": r.summary, "comparison": r.comparison, "top_rows": r.rows[:max_rows]},
                      ensure_ascii=False, default=str)
