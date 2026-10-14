"""Deterministic analytical tools. The LLM never computes numbers: it picks a tool and its parameters,
and every figure in an answer comes from a ToolResult produced here.

Every result carries what the explainability requirement asks for: parameters/filters used, method,
sample size N, the source object IDs with portal links, data-quality notes and limitations.
"""
from __future__ import annotations

import math
import re
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from itertools import pairwise
from typing import Any

import psycopg
from psycopg.rows import dict_row

from tenderai.analytics.fair_price import Observation, evaluate_fair_price
from tenderai.analytics.price_index import MonthlyIndex, load_index
from tenderai.analytics.volumes import VolumeRow, detect_volume_anomalies
from tenderai.config import TARGET_BINS, get_settings

PORTAL = "https://goszakup.gov.kz/ru"
ROBUST_Z = 3.0


@dataclass
class ToolResult:
    tool: str
    params: dict
    method: str
    n_analyzed: int                      # objects that took part in the analysis
    summary: dict                        # headline figures
    rows: list[dict] = field(default_factory=list)   # top-K detail rows (each with id + url)
    comparison: dict = field(default_factory=dict)   # baselines / sample description
    dq: dict = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)
    found: bool = True
    examples: list[dict] = field(default_factory=list)  # Top-K purchases with links when `rows` is a table

    def as_dict(self) -> dict:
        return asdict(self)


class Tools:
    def __init__(self, database_url: str | None = None):
        self.url = database_url or get_settings().agent_database_url
        self._index: MonthlyIndex | None = None
        self._targets: list[dict] | None = None
        self._dq_cache: dict[str, tuple[float, float]] = {}

    def _conn(self) -> psycopg.Connection:
        return psycopg.connect(self.url, row_factory=dict_row, autocommit=True)

    def _q(self, sql: str, params: Any = None) -> list[dict]:
        with self._conn() as c:
            return c.execute(sql, params).fetchall()

    # ------------------------------------------------------------------ resolution helpers
    def resolve_customers(self, text: str | None) -> list[dict]:
        """BINs in the text, or the target customers its names refer to (see resolve_customer_parts)."""
        return self.resolve_customer_parts(text)[0]

    def resolve_customer_parts(self, text: str | None) -> tuple[list[dict], list[str]]:
        """Match each customer name in the text ("A; B", "A и B") to one target customer.

        Returns (matched customers, names that matched nothing). Names are compared word by word after
        collapsing the long official forms ("Управления делами Президента" -> "удп"), so "Медицинский центр
        УДП" finds the Medical Centre and not the hospital that merely belongs to it. Each name yields its single
        best match: similar names (the Almaty branch, the other motor pool) are not pulled in as extras.
        """
        if not text:
            return [], []
        bins = re.findall(r"\b\d{12}\b", text)
        if bins:
            return (self._q("SELECT biin, name_ru FROM core.subject WHERE biin = ANY(%s)", (bins,)) or [
                {"biin": b, "name_ru": None} for b in bins]), []
        found, missing = [], []
        for part in _split_names(text):
            want = _name_tokens(part)
            best, best_key = None, None
            for t in self._load_targets():
                hit = sum(any(_same_word(w, x) for x in t["tokens"]) for w in want)
                score = hit / len(want) if want else 0.0
                # ties: fewer words beyond the query (the organisation itself, not its branch or subdivision)
                extra = sum(not any(_same_word(x, w) for w in want) for x in t["tokens_ru"])
                key = (score, -("филиал" in t["tokens_ru"] and "филиал" not in want), -extra)
                if score >= 0.5 and (best_key is None or key > best_key):
                    best, best_key = t, key
            if best is None:
                missing.append(part)
            elif best["biin"] not in {f["biin"] for f in found}:
                found.append({"biin": best["biin"], "name_ru": best["name_ru"], "query": part})
        return found, missing

    def _load_targets(self) -> list[dict]:
        if self._targets is None:
            self._targets = [{**r, "tokens": _name_tokens(f"{r['name_ru'] or ''} {r['name_kz'] or ''}"),
                              "tokens_ru": _name_tokens(r["name_ru"] or "")}
                             for r in self._q("SELECT biin, name_ru, name_kz FROM core.subject WHERE is_target")]
        return self._targets

    def detect_customers(self, question: str) -> tuple[list[dict], list[str]]:
        """Find customers named inside a whole question, for rules-only mode (no LLM to extract the name).

        Every run of up to 6 words is tried, longest first; a run counts when each of its words belongs to one
        customer's name ("Автохозяйства УДП", "Медицинский центр УДП"). A single word must name exactly one customer
        literally ("ЦКБ"). Returns (customers, organisation words that matched none — "акимат", "министерство"),
        so the caller can refuse rather than answer about all 27 customers.
        """
        targets = self._load_targets()
        words = _name_tokens(question)
        found: dict[str, dict] = {}
        covered: set[int] = set()
        for length in range(min(6, len(words)), 0, -1):
            for i in range(len(words) - length + 1):
                if covered.intersection(range(i, i + length)):
                    continue
                span = words[i:i + length]
                cands = [t for t in targets if all(any(_same_word(w, x) for x in t["tokens"]) for w in span)]
                if not cands or (length == 1 and (len(cands) != 1 or span[0] not in cands[0]["tokens"]
                                                  or len(span[0]) < 3)):
                    continue
                best = max(cands, key=lambda t: (-("филиал" in t["tokens_ru"] and "филиал" not in span),
                                                 -sum(not any(_same_word(x, w) for w in span) for x in t["tokens_ru"])))
                found.setdefault(best["biin"], {"biin": best["biin"], "name_ru": best["name_ru"],
                                                "query": " ".join(span)})
                covered.update(range(i, i + length))
        unknown = [w for k, w in enumerate(words) if k not in covered and _OTHER_ORG.match(w)]
        return list(found.values()), unknown

    def item_text_found(self, text: str, plans: bool = False) -> bool:
        """Does any purchase name match this free-text item, with the same predicate the tools filter by?
        `plans` checks planned volumes (mart.volume_yearly, substring) instead of priced purchases."""
        if plans:
            return bool(self._q("SELECT 1 FROM mart.volume_yearly WHERE name_clean LIKE '%%' || lower(%s) || '%%' LIMIT 1",
                                (text,)))
        return bool(self._q("""SELECT 1 FROM mart.price_obs
                               WHERE name_clean %% lower(%(t)s) OR name_clean LIKE '%%' || lower(%(t)s) || '%%' LIMIT 1""",
                            {"t": text}))

    def resolve_items(self, text: str, limit: int = 5) -> list[dict]:
        """ENSTRU codes whose item names match free text, best first (see rank_items).

        One product is often split into variant codes that share a name and differ only in the plan description
        (mart.enstru_variant): "бензин АИ-92" must resolve to the АИ-92 code, "бензин" to every grade. The winner's
        `codes` holds the variants the text asks for, `variants` describes them.
        """
        t = text.lower().strip()
        stems = [w[: max(4, len(w) - 2)] for w in _item_words(t) if len(w) >= 3]
        like = " OR ".join(["name_clean LIKE %s"] * len(stems)) or "false"
        rows = self._q(
            # the code's most common name represents it (min() could pick a stray "приобретение …" spelling)
            f"""SELECT enstru_code, base_unit, mode() WITHIN GROUP (ORDER BY name_clean) AS name, count(*) AS n,
                       max(similarity(name_clean, %s)) AS sim
                FROM mart.price_obs
                WHERE name_clean %% %s OR name_clean LIKE '%%' || %s || '%%' OR ({like})
                GROUP BY enstru_code, base_unit""",
            [t, t, t, *[f"%{s}%" for s in stems]])
        variants = self._variants({r["enstru_code"] for r in rows})
        return rank_items(t, rows, variants)[:limit]

    def _variants(self, codes: set[str]) -> dict[str, list[tuple[str, int]]]:
        """Plan descriptions of each code, most frequent first; rare ones (< 10% of the code's plan points) are noise."""
        out: dict[str, list[tuple[str, int]]] = {}
        for r in self._q("""SELECT enstru_code, variant, n FROM mart.enstru_variant WHERE enstru_code = ANY(%s)
                            ORDER BY enstru_code, n DESC""", (list(codes),)) if codes else []:
            out.setdefault(r["enstru_code"], []).append((r["variant"], r["n"]))
        return {c: [(v, n) for v, n in vs if n >= 0.1 * sum(k for _, k in vs)] for c, vs in out.items()}

    def variant_labels(self, codes: list[str]) -> dict[str, str]:
        """Code -> its main plan description ("марка АИ-92"), for labelling answers about several variants."""
        return {c: vs[0][0] for c, vs in self._variants(set(codes)).items() if vs}

    def price_index(self) -> MonthlyIndex:
        if self._index is None:
            with psycopg.connect(self.url, autocommit=True) as c:
                self._index = load_index(c)
        return self._index

    def dq_rate(self, entity: str) -> float:
        """Share of objects excluded from statistics by an error-level DQ rule (cached 10 min: changes only on ETL)."""
        hit = self._dq_cache.get(entity)
        if hit and time.monotonic() - hit[0] < 600:
            return hit[1]
        r = self._q(
            """SELECT (SELECT count(DISTINCT obj_id) FROM core.dq_issue WHERE entity = %s AND severity = 'error')::float
                      / greatest((SELECT count(*) FROM core.""" + {"lot": "lot", "contract_unit": "contract_unit",
                                                                     "plan_point": "plan_point"}[entity] + "), 1) AS r",
            (entity,))
        rate = float(r[0]["r"]) if r else 0.0
        self._dq_cache[entity] = (time.monotonic(), rate)
        return rate

    def _quality(self, r: ToolResult, entities: tuple[str, ...] = ("contract_unit", "lot")) -> ToolResult:
        """Every answer reports data quality and a confidence level. Tools without a model-specific confidence
        (Fair Price has its own) get the aggregate one: sample size and the share of records excluded by DQ rules."""
        rates = {f"{e}_excluded_rate": round(self.dq_rate(e), 4) for e in entities}
        r.dq = {**rates, **r.dq}
        if r.found and "confidence_label" not in r.summary:
            r.summary["confidence"], r.summary["confidence_label"] = aggregate_confidence(
                r.n_analyzed, max(rates.values()))
        return r

    def _examples(self, where: str, p: dict, top_k: int = 10) -> list[dict]:
        """The largest purchases behind an aggregate answer, with portal links (the Top-K section)."""
        return self._q(f"""SELECT obj_id, source, customer_bin, enstru_code, name_clean, base_unit,
                                  round(price::numeric, 2) price, quantity, round(amount::numeric, 2) amount, month, url
                           FROM mart.price_obs WHERE {where} ORDER BY amount DESC LIMIT {int(top_k)}""", p)

    # ------------------------------------------------------------------ 1. search
    def search_procurements(self, text: str | None = None, customer_bins: list[str] | None = None,
                            enstru_code: str | list[str] | None = None, year: int | None = None,
                            source: str | None = None, top_k: int = 10, base_unit: str | None = None) -> ToolResult:
        """`enstru_code` may be a list: one item name is often spread over several codes (laptops: four).
        `base_unit` keeps only one unit when the question names it ("в пачках")."""
        where, p = ["true"], {}
        if text:
            where.append("(name_clean %% lower(%(t)s) OR name_clean LIKE '%%' || lower(%(t)s) || '%%')"); p["t"] = text
        if customer_bins:
            where.append("customer_bin = ANY(%(b)s)"); p["b"] = customer_bins
        if enstru_code:
            where.append("enstru_code = ANY(%(e)s)")
            p["e"] = [enstru_code] if isinstance(enstru_code, str) else list(enstru_code)
        if year:
            where.append("year = %(y)s"); p["y"] = year
        if source:
            where.append("source = %(s)s"); p["s"] = source
        if base_unit:
            where.append("base_unit = %(u)s"); p["u"] = base_unit
        w = " AND ".join(where)
        agg = self._q(f"""SELECT count(*) n, sum(amount) total, count(DISTINCT customer_bin) customers,
                                 min(month) first_month, max(month) last_month
                          FROM mart.price_obs WHERE {w}""", p)[0]
        rows = self._q(f"""SELECT obj_id, source, customer_bin, supplier_biin, enstru_code, name_clean, base_unit,
                                  round(price::numeric, 2) price, quantity, round(amount::numeric, 2) amount,
                                  month, city_kato, url
                           FROM mart.price_obs WHERE {w} ORDER BY amount DESC LIMIT {int(top_k)}""", p)
        return self._quality(ToolResult(
            tool="search_procurements",
            params={"text": text, "customer_bins": customer_bins, "enstru_code": enstru_code, "year": year,
                    "source": source, "base_unit": base_unit},
            method="фильтрация витрины mart.price_obs (договоры + лоты без договора), сортировка по сумме",
            limitations=["суммы и цены без НДС; лот с заключённым договором учтён один раз — по строке договора"],
            n_analyzed=agg["n"], summary={"count": agg["n"], "total_amount_kzt": _r(agg["total"]),
                                          "customers": agg["customers"], "period": [agg["first_month"], agg["last_month"]]},
            rows=rows, found=agg["n"] > 0))

    # ------------------------------------------------------------------ 2. price anomalies
    def price_anomalies(self, threshold: float = 0.30, enstru_code: str | list[str] | None = None, text: str | None = None,
                        customer_bins: list[str] | None = None, year: int | None = None,
                        direction: str = "over", include_services: bool = False, top_k: int = 10) -> ToolResult:
        # filters that define the comparison scope (without the deviation threshold)
        scope, p = ["true"], {"th": threshold, "z": ROBUST_Z}
        if not include_services:
            scope.append("NOT is_service")
        if enstru_code:   # one code, or every variant code of a product (each compared within its own code)
            scope.append("enstru_code = ANY(%(e)s)"); p["e"] = _as_list(enstru_code)
        if text:
            scope.append("(name_clean %% lower(%(t)s) OR name_clean LIKE '%%' || lower(%(t)s) || '%%')"); p["t"] = text
        if customer_bins:
            scope.append("customer_bin = ANY(%(b)s)"); p["b"] = customer_bins
        if year:
            scope.append("year = %(y)s"); p["y"] = year
        sc = " AND ".join(scope)
        rule = "deviation > %(th)s" if direction == "over" else "deviation < -%(th)s"
        robust = "robust_z_log > %(z)s" if direction == "over" else "robust_z_log < -%(z)s"
        agg = self._q(f"""SELECT count(*) n, count(DISTINCT (enstru_code, base_unit)) groups,
                                 count(*) FILTER (WHERE {rule}) flagged_rule,
                                 count(*) FILTER (WHERE {rule} AND {robust} AND NOT likely_unit_error) flagged_robust,
                                 count(*) FILTER (WHERE {rule} AND likely_unit_error) unit_errors,
                                 sum(amount - peer_weighted_avg * quantity)
                                     FILTER (WHERE {rule} AND {robust} AND NOT likely_unit_error) overpay_robust,
                                 sum(amount - peer_weighted_avg * quantity) FILTER (WHERE {rule} AND NOT likely_unit_error) overpay_rule
                          FROM mart.price_deviation WHERE {sc}""", p)[0]
        rows = self._q(f"""SELECT obj_id, source, customer_bin, enstru_code, name_clean, base_unit,
                                  round(price::numeric, 2) price, round(peer_weighted_avg::numeric, 2) peer_weighted_avg,
                                  round(group_median::numeric, 2) group_median,
                                  round((deviation * 100)::numeric, 1) deviation_pct, round(robust_z_log::numeric, 1) robust_z,
                                  coalesce({robust}, false) AND NOT likely_unit_error AS confirmed, likely_unit_error,
                                  n_peers, quantity,
                                  round(amount::numeric, 2) amount, month, url
                           FROM mart.price_deviation WHERE {sc} AND {rule}
                           ORDER BY likely_unit_error, coalesce({robust}, false) DESC,
                                    {"(amount - peer_weighted_avg * quantity) DESC NULLS LAST" if direction == "over"
                                     else "deviation"}
                           LIMIT {int(top_k)}""", p)
        over = direction == "over"
        lims = ["средневзвешенная считается в сопоставимых ценах (официальный ИПЦ stat.gov.kz по региону и "
                "категории); средняя аналогов показана в ценах месяца каждой закупки",
                "разброс цен внутри одного кода ЕНС ТРУ велик (разные фасовки/характеристики), поэтому "
                "правило «> порога» дополнено робастной проверкой: подтверждённые аномалии — robust z (лог-цена) > 3",
                "цена выше аналогов более чем в 21 раз считается вероятной ошибкой единицы/количества (лот «одной суммой»), "
                "такие позиции показаны отдельно и не входят в оценку переплаты"]
        if not include_services:
            lims.append("услуги и работы (ед. изм. «услуга»/«работа») исключены: цена за единицу для них не сопоставима")
        return self._quality(ToolResult(
            tool="price_anomalies",
            params={"threshold": threshold, "direction": direction, "enstru_code": enstru_code, "text": text,
                    "customer_bins": customer_bins, "year": year, "include_services": include_services},
            method=("отклонение цены за единицу (без НДС, в базовой ед. изм., в сопоставимых ценах по ИПЦ) от "
                    "средневзвешенной по количеству цены остальных закупок того же ЕНС ТРУ и единицы "
                    "(leave-one-out, группы с ≥3 аналогами); "
                    "подтверждение — робастный z-score логарифма цены в группе (медиана/MAD) > 3"),
            n_analyzed=agg["n"],
            summary={"flagged": agg["flagged_rule"], "share_pct": _pct(agg["flagged_rule"], agg["n"]),
                     "confirmed": agg["flagged_robust"], "confirmed_share_pct": _pct(agg["flagged_robust"], agg["n"]),
                     "likely_unit_errors": agg["unit_errors"],
                     "estimated_overpayment_kzt": _r(agg["overpay_rule"]) if over else None,
                     "confirmed_overpayment_kzt": _r(agg["overpay_robust"]) if over else None,
                     "enstru_groups_compared": agg["groups"]},
            rows=rows, dq={"contract_unit_excluded_rate": round(self.dq_rate("contract_unit"), 4)},
            limitations=lims, found=agg["n"] > 0), ("contract_unit",))

    # ------------------------------------------------------------------ 3. fair price of a lot
    def fair_price_for_lot(self, lot_ref: str) -> ToolResult:
        lot = self._find_lot(lot_ref)
        if not lot:
            return ToolResult("fair_price_for_lot", {"lot_ref": lot_ref}, "—", 0, {}, found=False,
                              limitations=[f"лот '{lot_ref}' не найден в хранилище (2024–2026, 27 заказчиков)"])
        # actual contracted price wins over the announced one
        cu = self._q("""SELECT u.id, u.unit_price_base, u.quantity_base, c.sign_date, c.url FROM core.contract_unit u
                        JOIN core.contract c ON c.id = u.contract_id AND c.is_latest
                        WHERE u.lot_id = %s AND NOT u.deleted AND u.unit_price_base > 0 ORDER BY u.id LIMIT 1""",
                     (lot["id"],))
        if cu:
            price, qty, when, obj, url = (float(cu[0]["unit_price_base"]), float(cu[0]["quantity_base"]),
                                          cu[0]["sign_date"], f"contract_unit:{cu[0]['id']}", cu[0]["url"])
            price_kind = "цена по договору"
        else:
            price, qty, when, obj, url = (float(lot["unit_price_base"] or 0), float(lot["count_base"] or 0),
                                          lot["publish_date"], f"lot:{lot['id']}", lot["url"])
            price_kind = "цена лота (договор ещё не заключён)"
        if not lot["enstru_code"] or not lot["base_unit"]:
            # some lots carry no ENSTRU code or unit; their contract line (price observation) usually does
            o = self._q("""SELECT enstru_code, base_unit FROM mart.price_obs WHERE lot_id = %s
                           AND enstru_code IS NOT NULL AND base_unit IS NOT NULL LIMIT 1""", (lot["id"],))
            if o:
                lot = {**lot, **o[0]}
        if not lot["enstru_code"] or not lot["base_unit"] or price <= 0:
            return ToolResult("fair_price_for_lot", {"lot_ref": lot_ref}, "—", 0, {"lot": _lot_brief(lot)}, found=False,
                              limitations=["у лота нет кода ЕНС ТРУ, единицы измерения или цены — сравнение невозможно"])
        month = (when.date() if when else date(lot["year"] or 2025, 1, 1)).replace(day=1)
        target = Observation(obj, price, qty, lot["enstru_code"], lot["base_unit"], month, lot["customer_bin"],
                             lot["kato_code"], lot["region_code"], "contract" if cu else "lot")
        cands = self._q("""SELECT obj_id, price, quantity, enstru_code, base_unit, month, customer_bin, city_kato, region_code, source
                           FROM mart.price_obs WHERE base_unit = %s AND (enstru_code = %s OR enstru_code LIKE %s)""",
                        (lot["base_unit"], lot["enstru_code"], lot["enstru_code"].split(".")[0] + ".%"))
        obs = [Observation(c["obj_id"], float(c["price"]), float(c["quantity"]), c["enstru_code"], c["base_unit"],
                           c["month"], c["customer_bin"], c["city_kato"], c["region_code"], c["source"]) for c in cands]
        idx = self.price_index()
        res = evaluate_fair_price(target, obs, idx if idx.levels else None, idx.name, self.dq_rate("lot"))
        if res is None:
            return ToolResult("fair_price_for_lot", {"lot_ref": lot_ref}, "Fair Price", 0, {"lot": _lot_brief(lot)},
                              found=False, limitations=["нет сопоставимых закупок других заказчиков по этому ЕНС ТРУ и единице"])
        urls = {r["obj_id"]: r for r in self._q(
            """SELECT obj_id, customer_bin, round(price::numeric, 2) price, quantity, month, city_kato, url
               FROM mart.price_obs WHERE obj_id = ANY(%s)""", (res.sample_ids[:10],))}
        lims = list(res.limitations)
        qty_check = self._quantity_check(lot)
        if lot.get("kato_source") not in ("lot", "plan"):
            lims.append(f"место поставки лота определено косвенно (источник: {lot.get('kato_source') or 'нет'})")
        return ToolResult(
            tool="fair_price_for_lot", params={"lot_ref": lot_ref},
            method=("Fair Price: медиана цен аналогов (тот же ЕНС ТРУ и ед. изм., другие заказчики, ±18 мес.) "
                    "с поправками на инфляцию/сезонность и региональный коэффициент; полоса справедливости = Q1–Q3"),
            n_analyzed=res.n,
            summary={"lot": _lot_brief(lot), "price_kind": price_kind, "target_price": round(price, 2),
                     "target_url": url, "fair_price": res.fair_price, "fair_band": [res.band_low, res.band_high],
                     "deviation_pct": round(res.deviation * 100, 1), "robust_z": res.robust_z, "verdict": res.verdict,
                     "confidence": res.confidence, "confidence_label": res.confidence_label,
                     "quantity_check": qty_check},
            rows=[urls[i] for i in res.sample_ids[:10] if i in urls],
            comparison={"level": res.comparison_level, "n": res.n, "weighted_mean": res.weighted_mean,
                        "adjustments": res.adjustments},
            dq={"lot_excluded_rate": round(self.dq_rate("lot"), 4)}, limitations=lims)

    def _quantity_check(self, lot: dict) -> dict | None:
        """Is the planned quantity of this item normal for this customer? Year of the lot vs the median of the
        customer's earlier years for the same ENSTRU and unit (the volume-anomaly rule, applied to one lot)."""
        if not lot.get("year"):
            return None
        hist = {r["year"]: float(r["quantity"]) for r in self._q(
            """SELECT year, quantity FROM mart.volume_yearly
               WHERE customer_bin = %s AND enstru_code = %s AND base_unit = %s""",
            (lot["customer_bin"], lot["enstru_code"], lot["base_unit"])) if r["quantity"] and float(r["quantity"]) > 0}
        cur = hist.get(lot["year"])
        prev = {y: q for y, q in hist.items() if y < lot["year"]}
        if cur is None or not prev:
            return {"year": lot["year"], "quantity": cur, "previous": prev, "ratio": None, "flagged": None}
        ratio = cur / statistics.median(prev.values())
        return {"year": lot["year"], "quantity": cur, "previous": prev, "ratio": round(ratio, 2),
                "flagged": ratio > 1.5}

    def _find_lot(self, ref: str) -> dict | None:
        ref = ref.strip().lstrip("№#").strip()
        sql = """SELECT id, lot_number, announcement_id, number_anno, customer_bin, name_ru, name_clean, count, amount,
                        unit_price_base, count_base, enstru_code, base_unit, kato_code, region_code, kato_source,
                        publish_date, year, url
                 FROM core.lot WHERE NOT is_deleted AND {} ORDER BY in_scope DESC, id DESC LIMIT 1"""
        if ref.isdigit():
            r = self._q(sql.format("id = %s"), (int(ref),))
            if r:
                return r[0]
        r = self._q(sql.format("lot_number = %s"), (ref,)) or self._q(sql.format("number_anno = %s"), (ref,))
        return r[0] if r else None

    # ------------------------------------------------------------------ 4. volume anomalies
    def volume_anomalies(self, customer_bins: list[str] | None = None, enstru_code: str | list[str] | None = None,
                         text: str | None = None, year: int | None = None, ratio_threshold: float = 1.5,
                         include_services: bool = False, top_k: int = 10) -> ToolResult:
        codes = None if not enstru_code else {enstru_code} if isinstance(enstru_code, str) else set(enstru_code)
        rows = self._q("""SELECT customer_bin, enstru_code, base_unit, year, quantity, amount, n_points, name_clean,
                                 plan_point_ids FROM mart.volume_yearly""")
        allrows = [VolumeRow(r["customer_bin"], r["enstru_code"], r["base_unit"], r["year"], float(r["quantity"]),
                             float(r["amount"] or 0), r["n_points"], r["name_clean"], r["plan_point_ids"]) for r in rows]
        res = detect_volume_anomalies(allrows, ratio_threshold)
        sel = [a for a in res
               if (not customer_bins or a.customer_bin in customer_bins)
               and (not codes or a.enstru_code in codes)
               and (not text or (a.name_clean and text.lower() in a.name_clean))
               and (not year or a.year == year)
               and (include_services or a.base_unit not in ("service", "work"))]
        flagged = sorted((a for a in sel if a.flagged), key=lambda a: (a.confirmed, a.extra_amount), reverse=True)
        confirmed = [a for a in flagged if a.confirmed]
        first_ids = [a.plan_point_ids[0] for a in flagged[:top_k] if a.plan_point_ids]
        links = {r["id"]: r["url"] for r in self._q("SELECT id, url FROM core.plan_point WHERE id = ANY(%s)", (first_ids,))}
        detail = [{"customer_bin": a.customer_bin, "enstru_code": a.enstru_code, "name_clean": a.name_clean,
                   "base_unit": a.base_unit, "year": a.year, "quantity": a.quantity,
                   "baseline_quantity": a.baseline_quantity, "previous_years": a.previous,
                   "ratio": round(a.ratio, 2), "robust_z": None if a.robust_z is None else round(a.robust_z, 2),
                   "confirmed": a.confirmed, "extra_amount": round(a.extra_amount, 2),
                   "isolation_forest_score": a.if_score, "amount": round(a.amount, 2),
                   "plan_point_ids": a.plan_point_ids[:5],
                   "url": links.get(a.plan_point_ids[0]) if a.plan_point_ids else None}
                  for a in flagged[:top_k]]
        return self._quality(ToolResult(
            tool="volume_anomalies",
            params={"customer_bins": customer_bins, "enstru_code": enstru_code, "text": text, "year": year,
                    "ratio_threshold": ratio_threshold, "include_services": include_services},
            method=(f"годовой объём по пунктам плана (последние версии) на заказчика × ЕНС ТРУ × ед. изм.; "
                    f"флаг: объём / медиана предыдущих лет > {ratio_threshold} и robust z (лог-объём) > 3 "
                    f"при ≥2 предыдущих годах; подтверждено: z > 3 (или рост ≥ 3× при одном предыдущем годе) и "
                    f"дополнительные плановые расходы ≥ 1 млн ₸; Isolation Forest — дополнительный балл"),
            n_analyzed=len(sel),
            summary={"series_compared": len(sel), "flagged": len(flagged), "share_pct": _pct(len(flagged), len(sel)),
                     "confirmed": len(confirmed), "confirmed_share_pct": _pct(len(confirmed), len(sel)),
                     "confirmed_extra_amount_kzt": round(sum(a.extra_amount for a in confirmed), 2)},
            rows=detail, dq={"plan_point_excluded_rate": round(self.dq_rate("plan_point"), 4)},
            limitations=["история ограничена 2024–2026 гг.: для 2025 г. база — один предыдущий год",
                         "сравниваются плановые объёмы; фактическое исполнение может отличаться",
                         *([] if include_services else
                           ["услуги и работы исключены: их «количество» — число договоров, а не объём"])],
            found=len(sel) > 0), ("plan_point",))

    # ------------------------------------------------------------------ 5. compare customers
    def compare_customers(self, customer_bins: list[str] | None = None, year: int | None = None,
                          enstru_code: str | list[str] | None = None, text: str | None = None,
                          base_unit: str | None = None) -> ToolResult:
        bins = customer_bins or list(TARGET_BINS)
        where, p = ["customer_bin = ANY(%(b)s)"], {"b": bins}
        if year:
            where.append("year = %(y)s"); p["y"] = year
        if enstru_code:
            where.append("enstru_code = ANY(%(e)s)"); p["e"] = _as_list(enstru_code)
        if text:
            where.append("(name_clean %% lower(%(t)s) OR name_clean LIKE '%%' || lower(%(t)s) || '%%')"); p["t"] = text
        if base_unit:
            where.append("base_unit = %(u)s"); p["u"] = base_unit
        w = " AND ".join(where)
        rows = self._q(f"""SELECT o.customer_bin, s.name_ru, count(*) n, round(sum(amount)::numeric, 2) total_amount,
                                  count(*) FILTER (WHERE source = 'contract') n_contract_lines,
                                  round((sum(amount) / nullif(sum(quantity), 0))::numeric, 2) weighted_avg_price,
                                  round((percentile_cont(0.5) WITHIN GROUP (ORDER BY price))::numeric, 2) median_price,
                                  count(DISTINCT supplier_biin) suppliers
                           FROM mart.price_obs o LEFT JOIN core.subject s ON s.biin = o.customer_bin
                           WHERE {w} GROUP BY 1, 2 ORDER BY total_amount DESC""", p)
        units = self._q(f"SELECT DISTINCT base_unit FROM mart.price_obs WHERE {w}", p)
        lims = []
        if not (enstru_code or text) or len(units) > 1 or len(_as_list(enstru_code)) > 1:
            lims.append("средние цены сопоставимы только внутри одного ЕНС ТРУ и единицы измерения")
        return self._quality(ToolResult(
            tool="compare_customers", params={"customer_bins": customer_bins, "year": year, "enstru_code": enstru_code,
                                              "text": text, "base_unit": base_unit},
            method="агрегирование mart.price_obs по заказчикам: количество позиций, сумма, средневзвешенная и медианная цена",
            n_analyzed=sum(r["n"] for r in rows), summary={"customers": len(rows)}, rows=rows, limitations=lims,
            found=bool(rows), examples=self._examples(w, p)))

    def compare_years(self, years: list[int], customer_bins: list[str] | None = None,
                      enstru_code: str | list[str] | None = None, text: str | None = None) -> ToolResult:
        where, p = ["year = ANY(%(y)s)"], {"y": years}
        if customer_bins:
            where.append("customer_bin = ANY(%(b)s)"); p["b"] = customer_bins
        if enstru_code:
            where.append("enstru_code = ANY(%(e)s)")
            p["e"] = [enstru_code] if isinstance(enstru_code, str) else list(enstru_code)
        if text:
            where.append("(name_clean %% lower(%(t)s) OR name_clean LIKE '%%' || lower(%(t)s) || '%%')"); p["t"] = text
        w = " AND ".join(where)
        rows = self._q(f"""SELECT year, count(*) n, round(sum(amount)::numeric, 2) total_amount,
                                  count(DISTINCT customer_bin) customers,
                                  round((sum(amount) / nullif(sum(quantity), 0))::numeric, 2) weighted_avg_price
                           FROM mart.price_obs WHERE {w} GROUP BY year ORDER BY year""", p)
        for prev, cur in pairwise(rows):
            cur["change_pct"] = round((float(cur["total_amount"]) / float(prev["total_amount"]) - 1) * 100, 1) \
                if prev["total_amount"] else None
        lims = [] if (enstru_code or text) else ["средние цены сопоставимы только внутри одного ЕНС ТРУ и единицы измерения"]
        if 2026 in years:
            lims.append("2026 год неполный: данные по дату последней загрузки")
        return self._quality(ToolResult(
            tool="compare_years", params={"years": years, "customer_bins": customer_bins, "enstru_code": enstru_code,
                                          "text": text},
            method="агрегирование mart.price_obs по годам: количество позиций, сумма, число заказчиков, изменение к предыдущему году",
            n_analyzed=sum(r["n"] for r in rows), summary={"years": len(rows)}, rows=rows, limitations=lims,
            found=bool(rows), examples=self._examples(w, p)))

    # ------------------------------------------------------------------ 6. price statistics
    def price_stats(self, enstru_code: str | list[str], base_unit: str | None = None, year: int | None = None) -> ToolResult:
        """Several codes = variants of one product (АИ-92 / АИ-95): one row per variant, never a pooled average,
        because the variants are different goods and their mix changes from year to year."""
        codes = _as_list(enstru_code)
        shown = codes[:MAX_VARIANTS]
        rows = self._q("""SELECT enstru_code, base_unit, period, n, n_customers, round(weighted_avg::numeric, 2) weighted_avg,
                                 round(median::numeric, 2) median, round(q1::numeric, 2) q1, round(q3::numeric, 2) q3,
                                 round(min_price::numeric, 2) min_price, round(max_price::numeric, 2) max_price
                          FROM mart.price_stats WHERE enstru_code = ANY(%s) AND (%s::text IS NULL OR base_unit = %s)
                          ORDER BY array_position(%s, enstru_code), base_unit, period""",
                       (shown, base_unit, base_unit, shown))
        lims = ["один код ЕНС ТРУ может объединять разные фасовки и характеристики — ориентируйтесь на "
                "медиану и диапазон Q1–Q3, а не только на среднюю"]
        if len({r["enstru_code"] for r in rows}) > 1:
            labels = self.variant_labels(shown)
            for r in rows:
                r["variant"] = labels.get(r["enstru_code"])
            lims.append("варианты товара (разные коды ЕНС ТРУ) показаны отдельно: это разные товары, их цены не "
                        "сводятся в одну среднюю")
        if len(codes) > len(shown):
            lims.append(f"показаны {len(shown)} вариантов из {len(codes)} (с наибольшим числом закупок)")
        n = sum(r["n"] for r in rows if r["period"] == (str(year) if year else "all"))
        where, p = ["enstru_code = ANY(%(e)s)"], {"e": shown}
        if base_unit:
            where.append("base_unit = %(u)s"); p["u"] = base_unit
        if year:
            where.append("year = %(y)s"); p["y"] = year
        return self._quality(ToolResult(
            "price_stats", {"enstru_code": enstru_code, "base_unit": base_unit, "year": year},
            "описательная статистика цен за единицу (без НДС) по ЕНС ТРУ и году", n,
            {"groups": len(rows)}, rows=rows, found=bool(rows), examples=self._examples(" AND ".join(where), p),
            limitations=lims))


# ---------------------------------------------------------------------- name matching
# Long official forms collapse to the abbreviations people actually type ("Медицинский центр УДП").
_ABBREVIATIONS = [
    (r"управлени[яе] делами президента(?: республики казахстан| рк)?", "удп"),
    (r"(?:қр|қазақстан республикасы) президенті(?:нің)? іс басқармасы\w*", "удп"),
    (r"управлени[ея] материально-технического обеспечения", "умто"),
    (r"департамент\w* хозяйственного обслуживания", "дхо"),
    (r"республики казахстан|республика казахстан|қазақстан республикасы", "рк"),
]
_NAME_STOP = {"ргп", "на", "пхв", "гу", "ао", "тоо", "жшс", "ргку", "рмк", "ркк", "мкм", "республиканское", "государственное",
              "государственный", "предприятие", "праве", "хозяйственного", "ведения", "учреждение", "при", "рк", "и",
              "акционерное", "общество", "товарищество", "ограниченной", "ответственностью", "республикалық",
              "мемлекеттік", "кәсіпорны", "мекемесі", "шаруашылық", "жүргізу", "құқығындағы", "акционерлік", "қоғамы",
              "заказчик", "заказчика", "тапсырыс", "беруші", "для", "по", "в", "у", "қр"}
_ITEM_NOISE = {"для", "пачках", "пачка", "пачки", "упаковка", "штук", "штуки", "литр", "литра", "литров", "килограмм",
               "закупки", "закупка", "товары",
               # prepositions, and words that introduce a characteristic ("батарейка типа АА", "бумага формата А4")
               "на", "по", "из", "от", "со", "тип", "типа", "марка", "марки", "сорт", "сорта", "вид", "вида", "формат",
               "формата", "класс", "класса", "до", "за", "не"}
_WORD = re.compile(r"[\wёәғқңөұүһі]+(?:-[\wёәғқңөұүһі]+)*")
MAX_VARIANTS = 8   # per-variant tables show at most this many codes (the most purchased)


def _as_list(code: str | list[str] | None) -> list[str]:
    return [] if not code else [code] if isinstance(code, str) else list(code)


def _item_words(text: str) -> list[str]:
    """Words of an item query. Two-letter words stay when they can name a variant ("а4", "аа")."""
    return [w for w in _WORD.findall(text.lower().replace("ё", "е")) if w not in _ITEM_NOISE and len(w) >= 2]


def _word_match(a: str, b: str) -> bool:
    """Same word up to the ending; hyphens ignored, so "аи-92" ~ "аи92"."""
    a, b = a.replace("-", ""), b.replace("-", "")
    return a == b or _same_word(a, b)


def rank_items(text: str, rows: list[dict], variants: dict[str, list[tuple[str, int]]]) -> list[dict]:
    """Rank candidate (code, unit) rows for a free-text item; the winner gets `codes` and `variants`.

    Each query word must match either a word of the code's name (the item) or a word of its plan description (the
    variant). Tiers, on the item words: 0 — they equal the name; 1 — the name contains them as words; 2 — each matches
    a name word ("бумага офисная" ~ "бумага для офисного оборудования"); 3 — some query word matches neither, trigram
    similarity only. Within a tier, more query words found in the name win ("дизельное топливо" is the fuel, not a
    "преобразователь топлива" for diesel engines), then more variant words, then the product with more purchases
    across its variants (so "бензин" means motor petrol, not "бензидамин", and "батарейки" the batteries split over
    seven type codes rather than the single largest "батарея" code). The winner's `codes` are the codes sharing its name, tier and
    matched variant words: one variant when the text names it ("бензин аи-92"), every variant when it does not,
    most purchased first; `variants` describes the first MAX_VARIANTS of them.
    """
    words = _item_words(text)
    for r in rows:
        name = (r["name"] or "").replace("ё", "е")
        name_words = _WORD.findall(name)
        var_words = {w for v, _ in variants.get(r["enstru_code"], []) for w in _WORD.findall(v.lower())}
        item = [w for w in words if any(_word_match(w, x) for x in name_words)]
        var = [w for w in words if w not in item and any(_word_match(w, x) for x in var_words)]
        r["variant_words"], r["item_words"] = var, item
        item_text = " ".join(item)
        if not item or len(item) + len(var) < len(words):
            r["tier"] = 3
        elif name == item_text:
            r["tier"] = 0
        elif re.search(r"(^|\s)" + re.escape(item_text) + r"(\s|$)", name):
            r["tier"] = 1
        else:
            r["tier"] = 2
    group_n: dict[tuple, int] = {}
    for r in rows:
        r["group"] = (r["name"], r["tier"], tuple(r["variant_words"]))
        group_n[r["group"]] = group_n.get(r["group"], 0) + r["n"]
    rows.sort(key=lambda r: (r["tier"], *((-len(r["item_words"]), -len(r["variant_words"]), -group_n[r["group"]], -r["n"])
                                         if r["tier"] < 3 else (-float(r["sim"] or 0), -r["n"]))))
    if rows:
        top = rows[0]
        same = [r for r in rows if r["group"] == top["group"]]
        n_by_code: dict[str, int] = {}
        for r in same:
            n_by_code[r["enstru_code"]] = n_by_code.get(r["enstru_code"], 0) + r["n"]
        ranked = sorted(n_by_code, key=lambda c: -n_by_code[c])
        top["codes"] = ranked
        top["variants"] = [{"enstru_code": c, "variant": variants[c][0][0] if variants.get(c) else None,
                            "n": n_by_code[c]} for c in ranked[:MAX_VARIANTS]]
    return rows


# organisation types that are not among the 27 customers: naming one means "someone else", not "all customers"
_OTHER_ORG = re.compile(r"^(?:акимат|министерств|маслихат|комитет|департамент|агентств|университет|акимдік|"
                        r"министрлі|әкімдік|әкімшілі)\w*$")


def _name_tokens(text: str) -> list[str]:
    s = text.lower().replace("ё", "е")
    for rx, abbr in _ABBREVIATIONS:
        s = re.sub(rx, f" {abbr} ", s)
    words = re.findall(r"[\wәғқңөұүһі]+(?:-[\wәғқңөұүһі]+)*", s)
    return [w for w in words if w not in _NAME_STOP and len(w) > 1]


def _split_names(text: str) -> list[str]:
    parts = re.split(r"\s*(?:;|,|\s+и\s+|\s+мен\s+|\s+және\s+|\s+vs\.?\s+|\s+против\s+)\s*", text.strip())
    return [p for p in (x.strip(" «»\"'.") for x in parts) if p]


def _same_word(a: str, b: str) -> bool:
    """Same word up to the ending: 'центр' ~ 'центра', 'автошаруашылығында' ~ 'автошаруашылығы'."""
    if a == b:
        return True
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n >= 4 and n >= min(len(a), len(b)) - 2 and max(len(a), len(b)) - n <= 4


def aggregate_confidence(n: int, dq_rate: float) -> tuple[float, str]:
    """Confidence of a deterministic aggregate: 0.6 · min(1, ln N / ln 30) + 0.4 · (1 − DQ exclusion rate).
    high ≥ 0.70, medium ≥ 0.45, otherwise low; fewer than 5 objects is always low."""
    score = 0.6 * min(1.0, math.log(max(n, 1)) / math.log(30)) + 0.4 * (1 - min(max(dq_rate, 0.0), 1.0))
    label = "high" if score >= 0.70 else "medium" if score >= 0.45 else "low"
    return round(score, 2), ("low" if n < 5 else label)


def _r(x) -> float | None:
    return None if x is None else round(float(x), 2)


def _pct(a: int, b: int) -> float:
    return round(100.0 * a / b, 1) if b else 0.0


def _lot_brief(lot: dict) -> dict:
    return {k: lot.get(k) for k in ("id", "lot_number", "number_anno", "customer_bin", "name_ru", "enstru_code",
                                     "base_unit", "count", "amount", "kato_code", "url")}
