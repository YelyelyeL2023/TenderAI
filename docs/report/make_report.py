"""TenderAI report: how the application works, and how it meets the assignment (ТЗ).
Usage: python make_report.py <inputs.json> <out.pdf>"""
import json
import re
import sys
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).parent
_helpers = (HERE / "make_pdf.py").read_text(encoding="utf-8").split(
    "# ================================================================ CONTENT")[0]
exec(_helpers)  # styles, fonts, table/code/callout helpers, box/arrow/lane, system & entity diagrams, on_page

def on_page(c, doc):  # noqa: F811 — same layout as the architecture PDF, report title in the header
    c.saveState()
    if doc.page > 1:
        c.setFont("Body", 7.5)
        c.setFillColor(MUTED)
        c.drawString(20 * mm, A4[1] - 12 * mm, "TenderAI · Report")
        c.drawRightString(A4[0] - 20 * mm, A4[1] - 12 * mm, "How the application works")
        c.setStrokeColor(RULE)
        c.setLineWidth(0.4)
        c.line(20 * mm, A4[1] - 14 * mm, A4[0] - 20 * mm, A4[1] - 14 * mm)
        c.drawCentredString(A4[0] / 2, 10 * mm, str(doc.page))
    c.restoreState()


pdfmetrics.registerFont(TTFont("Sym", F + "seguisym.ttf"))  # check / warning marks used in Top-K lines

D = json.load(open(sys.argv[1], encoding="utf-8"))
OUT = sys.argv[2]
story.clear()


def n(x):
    return f"{int(x):,}".replace(",", " ")


def pct(x):
    return f"{x * 100:.1f}%"


def request_flow():
    W, H = 482, 124
    d = Drawing(W, H)
    steps = [("1 Route", ["rules first,", "then LLM schema"]),
             ("2 Resolve", ["customers → BIN,", "item → ENSTRU"]),
             ("3 Tool", ["one SQL/Python", "call, no LLM"]),
             ("4 Verdict", ["LLM writes", "1–3 sentences"]),
             ("5 Validate", ["numbers, BINs,", "dates + claims"]),
             ("6 Render", ["6 fixed sections", "+ portal links"])]
    w, gap, x = 70, 12.4, 2
    for i, (t, sub) in enumerate(steps):
        box(d, x, 66, w, 50, [t] + sub, C_LLM if t[0] in "14" else (C_AG if i % 2 else C_DB))
        if i < len(steps) - 1:
            arrow(d, x + w, 91, x + w + gap, 91)
        x += w + gap
    red = colors.HexColor("#b42318")
    vx, tx = 2 + 4 * (w + gap) + w / 2, 2 + 3 * (w + gap) + w / 2
    d.add(Line(vx, 66, vx, 48, strokeColor=red, strokeWidth=0.9))
    d.add(Line(vx, 48, tx, 48, strokeColor=red, strokeWidth=0.9))
    arrow(d, tx, 48, tx, 66, col=red)
    d.add(String(tx + 6, 37, "unsupported number or misused figure -> one corrective retry", fontName="Body-Italic",
                 fontSize=6.8, fillColor=red))
    d.add(String(tx + 6, 28, "fails again or LLM down -> template verdict", fontName="Body-Italic", fontSize=6.8,
                 fillColor=red))
    box(d, 2 + (w + gap), 4, 2 * w + gap, 22, ["refusal with a reason", ], colors.HexColor("#fff4e5"), size=7)
    arrow(d, 2 + (w + gap) + w / 2, 66, 2 + (w + gap) + w / 2, 26)
    d.add(String(2 + (w + gap) * 3 + 6, 10, "unknown customer · year outside 2024–2026 · off-topic · no lot number",
                 fontName="Body-Italic", fontSize=6.8, fillColor=MUTED))
    return d


def md_answer(md: str):
    """Render an agent markdown answer as compact flowables (the answer as the user sees it)."""
    st = ParagraphStyle("ans", parent=ss["cell"], fontSize=8, leading=10.6)
    sth = ParagraphStyle("ansh", parent=ss["cell"], fontName="Body-Bold", fontSize=8.2, leading=11,
                         textColor=ACCENT, spaceBefore=4)
    items, table = [], []

    def inline(t):
        t = escape(t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"`(.+?)`", r"<font name='Mono'>\1</font>", t)
        t = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"<font color='#1d4e89'>\1</font>", t)
        t = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<i>\1</i>", t)
        return re.sub("([✔⚠])", r"<font name='Sym'>\1</font>", t)

    def flush():
        if table:
            rows = [[Paragraph(inline(c.strip()), st) for c in r.strip().strip("|").split("|")] for r in table
                    if not re.match(r"^\|[-|\s]+\|$", r.strip())]
            t = Table(rows, repeatRows=1)
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, RULE), ("BACKGROUND", (0, 0), (-1, 0), CODE_BG),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
            items.append(t)
            table.clear()

    for line in md.splitlines():
        if line.startswith("|"):
            table.append(line)
            continue
        flush()
        if line.startswith("### "):
            items.append(Paragraph(inline(line[4:]), sth))
        elif line.strip():
            m = re.match(r"^(- |\d+\. )(.*)", line)
            items.append(Paragraph(("• " if m and m.group(1) == "- " else (m.group(1) if m else "")) +
                                   inline(m.group(2) if m else line), st))
    flush()
    box_t = Table([[items]], colWidths=[170 * mm])
    box_t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, RULE), ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                               ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                               ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    return box_t


def trim_answer(md: str, max_details: int = 5) -> str:
    out, k = [], 0
    for line in md.splitlines():
        if re.match(r"^\d+\. ", line):
            k += 1
            if k > max_details:
                continue
        out.append(line)
    return "\n".join(out)


C, M, EC, EG = D["core_counts"], D["mart_counts"], D.get("eval_corpus", {}), D.get("eval_golden", {})
EH, EHR = D.get("eval_heldout") or {}, D.get("eval_heldout_rules") or {}
EX = D["examples"]
LI = D.get("last_incremental") or {}

# ================================================================ COVER
story.append(Spacer(1, 45 * mm))
story.append(Paragraph("TenderAI", ss["title"]))
story.append(Spacer(1, 4))
story.append(Paragraph("How the application works, and how it meets the assignment", ss["subtitle"]))
story.append(Spacer(1, 9 * mm))
cover = Table([[Paragraph(
    "<b>Task</b>: AI agent for analysing Kazakhstan public procurement (ТЗ «AI-агент анализа государственных закупок РК»)<br/>"
    "<b>Scope</b>: 27 customer organisations (BINs from the assignment), 2024–2026, goszakup.gov.kz OWS v3<br/>"
    "<b>Stack</b>: Python, PostgreSQL 16, FastAPI, Ollama + Qwen3.5 9B on a local GPU, Docker / Kubernetes<br/>"
    f"<b>Data as of</b>: {D['watermark'][:16]} UTC &nbsp;·&nbsp; <b>Report date</b>: {date.today():%d %B %Y}",
    ss["callout"])]], colWidths=[170 * mm])
cover.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), ACCENT_LIGHT), ("LINEBEFORE", (0, 0), (0, -1), 3, ACCENT),
                           ("LEFTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 8),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
story.append(cover)
story.append(Spacer(1, 10 * mm))
BIG = ParagraphStyle("big", fontName="Body-Bold", fontSize=19, leading=24, textColor=INK)


def tile(value, label):
    return [Paragraph(value, BIG), Paragraph(label, ss["cell"])]


kpi = Table([[tile(pct(EH.get("fact_accuracy", 0)), f"fact accuracy, {EH.get('cases', 0)}-question held-out set (target ≥ 85%)"),
              tile(pct(EC.get("fact_accuracy", 0)), f"fact accuracy, {EC.get('cases', 0)}-question corpus"),
              tile("3 h", "incremental sync interval (target: lag ≤ 24 h)"),
              tile(n(M["price_obs"]), "priced observations behind the answers")]], colWidths=[42.5 * mm] * 4)
kpi.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.4, RULE), ("INNERGRID", (0, 0), (-1, -1), 0.4, RULE),
                         ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 6),
                         ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
story.append(kpi)
story.append(Spacer(1, 9 * mm))
story.append(Paragraph("<b>Contents</b>", ss["h2"]))
toc = ["1. Summary", "2. Requirements compliance", "3. Architecture", "4. Data scope", "5. Ingestion and freshness",
       "6. Storage schema", "7. ETL and data quality", "8. Analytics and metrics", "9. The AI agent",
       "10. Example answers", "11. Evaluation", "12. API, web chat, deployment, security", "13. Risks and limitations",
       "14. Running the system"]
half = (len(toc) + 1) // 2
tt = Table([[Paragraph("<br/>".join(toc[:half]), ss["toc"]), Paragraph("<br/>".join(toc[half:]), ss["toc"])]],
           colWidths=[85 * mm, 85 * mm])
tt.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
story.append(tt)
story.append(PageBreak())

# ================================================================ 1
h1("1. Summary")
p("TenderAI answers questions about public procurement in Russian or Kazakh. It loads plans, announcements, lots, "
  "contracts and organisations for the 27 customers named in the assignment from the OWS v3 API, cleans them into "
  "an analytical warehouse, and answers through an agent that combines deterministic SQL/Python tools with a local "
  "language model.")
callout("<b>Core design rule: the language model never calculates.</b> Every figure comes from SQL or Python. The "
        "model only classifies the question and writes a 1–3 sentence verdict; every number, BIN and date in that "
        "verdict is checked against the tool output, and so is the role each number plays. Five of the six answer "
        "sections are rendered directly from the tool output.")
p("What a user gets for every question:")
bullets([
    "A <b>verdict</b> of 1–3 sentences with the key figures.",
    "The <b>parameters</b> used: period, BINs, filters, how the item and customers were recognised, N.",
    "The <b>analytics</b>: averages, medians, baselines, deviations, comparisons.",
    "The <b>method</b> in one paragraph.",
    "<b>Confidence and limitations</b>: a confidence level, data-quality exclusion rates, applicable caveats.",
    "<b>Top-K</b> purchases, contracts or plan lines with direct goszakup.gov.kz links.",
])
p(f"On a {EH.get('cases')}-question held-out set the agent was not tuned on, it extracts facts with "
  f"<b>{pct(EH.get('fact_accuracy', 0))}</b> accuracy ({EH.get('facts_correct')} of {EH.get('facts_total')}, three runs); "
  f"on the {EC.get('cases')}-question development corpus, {pct(EC.get('fact_accuracy', 0))}. The assignment requires at "
  f"least 85%. Answers take about 4–6 seconds on an 8 GB laptop GPU.")

# ================================================================ 2
h1("2. Requirements compliance")
p("Each requirement of the assignment (ТЗ), how the application meets it, and where to find it.")
table(["Assignment requirement", "How it is met", "Where"], [
    ["Data: OWS v3, 27 BINs, 2024–2026", "GraphQL + REST ingest for exactly these BINs and years; related plan lines and "
     "announcements outside the filter are fetched by ID", "tenderai/config.py, ingest/"],
    ["Storage schema (PostgreSQL/…)", "PostgreSQL 16 with raw / core / mart / ops schemas, 10 migrations", "db/sql, §6"],
    ["ETL: units, junk in lot names", "Unit normalisation to base units with factors; lot and plan names cleaned; DQ rules",
     "etl/units.py, lot_names.py, dq.sql"],
    ["Analytical layer (marts for the LLM)", "price_obs, price_stats, price_deviation, volume_yearly, dq_summary",
     "etl/marts.sql, §6"],
    ["Questions in KZ / RU", "Language detected per question; answers and section titles in the same language",
     "agent/router.py, i18n.py"],
    ["Question types: search, comparison, analytics, anomalies, fairness of price and quantity",
     "Six intents and seven tools; fairness of a lot covers both its price (Fair Price) and its quantity "
     "(customer's planned volume vs earlier years)", "agent/tools.py, §9"],
    ["Factual analysis, explainable and reproducible", "Deterministic tools; answers rebuilt from stored data; every "
     "figure traceable to an object ID", "§9"],
    ["Fact accuracy ≥ 85% (numbers, dates, BINs)",
     f"{pct(EH.get('fact_accuracy', 0))} on the {EH.get('cases')}-question held-out set (unseen questions); "
     f"{pct(EC.get('fact_accuracy', 0))} on the {EC.get('cases')}-question corpus, {pct(EG.get('fact_accuracy', 0))} on the "
     f"golden set",
     "eval/, §11"],
    ["Incremental update, lag ≤ 24 h", "Sync every 3 h (worker or K8s CronJob): changes by indexDate + deletions from the "
     "journal; /health reports lag and the SLA flag", "ingest/incremental.py, §5"],
    ["Mandatory: anomalies > 30% from the weighted average by ENSTRU", "Leave-one-out weighted average in constant "
     "prices, threshold from the question, robust z confirmation", "§8.2"],
    ["Mandatory: fairness of a lot vs other agencies in the same city", "Fair Price with same city → region → national "
     "→ code prefix comparison levels, other customers only", "§8.1"],
    ["Mandatory: atypical growth of quantity vs previous years", "Ratio to the median of earlier years + robust z; "
     "confirmed tier; Isolation Forest as a secondary score", "§8.3"],
    ["Methods: median, IQR, MAD, percentiles; unsupervised ML; rules", "All used: medians/IQR/MAD/quartiles, robust z, "
     "Isolation Forest, rule-based routing and thresholds", "analytics/"],
    ["Fair Price: comparable ENSTRU codes, regional coefficient, inflation and seasonality",
     "Formal 6-step definition: comparable set, official CPI by region and category, seasonal index, shrunk regional "
     "coefficient, median and Q1–Q3 band, confidence", "§8.1"],
    ["Explainability: IDs, N compared, objects analysed, method, confidence and limitations",
     "Mandatory in every answer, including comparisons and statistics (Top-K examples, aggregate confidence)", "§9.5"],
    ["Only data from the API and warehouse; no generic phrases or guessing; numbers required",
     "Numbers come only from tools; validator rejects unsupported or misused figures; questions without data get a "
     "'no data' answer with the reason, never invented numbers", "§9.4"],
    ["Answer structure (6 sections)", "Verdict · parameters · analytics · metric · confidence and limitations · Top-K",
     "agent/composer.py"],
    ["Data coverage: announcements, plans, contracts, lots, goods/works/services, customers, suppliers, statuses, "
     "dictionaries", "All loaded; subject type (goods/works/services) on plans, announcements and contracts; 12 REST "
     "dictionaries", "§4"],
    ["Architecture API → storage, schema, analytics, agent logic; LLM uses tools", "§3, §6, §8, §9", "docs/"],
    ["Non-functional: data quality control", "DQ rules with error/warning severity; rates in every answer", "§7"],
    ["Non-functional: API token protection", "Token only in env / secrets, SecretStr, request logging silenced", "§12"],
    ["Non-functional: on-prem Docker / Kubernetes", "docker-compose (5 services) and K8s manifests; local LLM, no "
     "internet needed at query time", "deploy/, §12"],
    ["Deliverables: architecture, schema, analytics, examples, risks", "docs/architecture.md, schema.md, analytics.md, "
     "examples.md, risks.md, data_scope.md, and this report", "docs/"],
], [46, 92, 32])

# ================================================================ 3
h1("3. Architecture")
p("Four layers: external sources, ingestion, the PostgreSQL warehouse, and the agent with its API. The agent reads "
  "only the cleaned core and mart schemas through a read-only database role; the local LLM runs next to it.")
story.append(system_diagram())
caption("Figure 1. Components and data flow.")
table(["Component", "Responsibility", "Code"], [
    ["OWS client", "Async HTTP, bearer token, 4 parallel requests, retries with backoff, cursor paging, errors in "
     "HTTP-200 bodies detected", "ows/client.py"],
    ["Backfill / incremental", "Full load; then every 3 h: changes since the watermark (2 h overlap) and deletions from "
     "the journal; follow-up fetches; ETL rebuild", "ingest/"],
    ["ETL", "raw → core → marts in one transaction (~1 min)", "etl/"],
    ["Analytics", "Fair Price, price index and CPI, robust statistics, volume anomalies", "analytics/"],
    ["Router, tools, validator, composer", "Question understanding, deterministic tools, fact and claim checks, "
     "six-section answer", "agent/"],
    ["API and web chat", "POST /ask, GET /health, chat page", "api/"],
    ["Worker", "Incremental sync every 3 h", "worker/"],
], [36, 104, 30])

# ================================================================ 4
h1("4. Data scope")
p("The OWS v3 entities used and why. GraphQL is the main path: it carries full lot data and supports filters by BIN, "
  "year and indexDate; REST is used for dictionaries and the change journal.")
table(["Entity", "Loaded by", "Used for"], [
    ["Plans (plan lines)", "customer BIN + plan year 2024–2026, plus referenced lines by ID", "ENSTRU code, unit, planned "
     "price and quantity, delivery KATO, goods/works/services type; volume anomalies"],
    ["Announcements (TrdBuy)", "organiser BIN + publish date, plus referenced by ID", "date, trade method, status, link"],
    ["Lots", "customer BIN", "announced price and quantity; link to plan line and announcement"],
    ["Contracts + line items", "customer BIN + sign date", "actual unit prices without VAT, supplier, status, amendments"],
    ["Subjects", "the 27 customers + every supplier on a contract", "names, addresses (fallback location), roles"],
    ["Dictionaries (12, REST)", "/v3/refs/*", "units, KATO, trade methods, all statuses, subject types, finance sources"],
    ["Journal (REST)", "/v3/journal", "deletions for the incremental sync"],
], [38, 60, 72])
st = ", ".join(f"{escape(str(a))}: {n(b)}" for a, b in D.get("subject_types", []))
p(f"Current warehouse: {n(C['plan_point'])} plan line versions, {n(C['announcement'])} announcements, "
  f"{n(C['lot'])} lots ({n(C['lot_in_scope'])} in scope), {n(C['contract'])} contract versions with "
  f"{n(C['contract_unit'])} line items, {n(C['subject'])} organisations; {n(D['raw_count'])} raw API objects in total. "
  f"Latest plan lines by subject type — {st}.")

# ================================================================ 5
h1("5. Ingestion and freshness")
bullets([
    "<b>Backfill</b> (about 25 min): dictionaries; per-BIN loads of plans, announcements, lots and contracts, with the "
    "API's reported total stored next to the rows fetched; plan lines and announcements referenced from outside the "
    "filter; customers and all suppliers.",
    "<b>Incremental</b> (every 3 h): the same per-BIN queries with indexDate ≥ watermark − 2 h; deletions from the "
    "national journal (server-filtered to action=D; objects re-indexed inside the window are kept); newly referenced "
    "objects; then the ETL; then the watermark moves.",
    "<b>Idempotent storage</b>: raw payloads keyed by (entity, id) with a content hash, so re-runs are safe; deletions "
    "are soft.",
    "<b>Freshness monitoring</b>: GET /health reports the data timestamp, lag in hours and whether the 24-hour target is met.",
    "<b>Journal volume</b>: the national journal normally holds about 105,000 changes a day, but 4.6 million on "
    "12 October 2026 when the portal re-indexed plans in bulk. Reading it in full would have taken about 2.5 hours, so "
    "the sync asks the server for deletions only (action=D: 746 pages that day) and never deletes an object it has "
    "just fetched live.",
])
runs = "; ".join(f"#{r['run_id']} {r['kind']} {r['status']} at {r['started'][:16]}" for r in D["runs"])
p(f"Latest runs: {escape(runs)}. Data timestamp after the latest sync: <b>{D['watermark'][:16]} UTC</b>.")
li_del = LI.get("deleted") or {}
p(f"The latest successful sync covered two days of the journal (1,644 deletion pages) and took about an hour, well "
  f"within the 3-hour cycle; it soft-deleted {n(sum(li_del.values()))} objects "
  f"({', '.join(f'{k}: {n(v)}' for k, v in li_del.items() if v)}) and rebuilt the warehouse in about a minute. "
  f"Run #10 (the old full-journal scan) was stopped to make that fix and run #11 was interrupted by a machine "
  f"shutdown; both are recorded as failed with the reason, and nothing from them was committed.")

# ================================================================ 6
h1("6. Storage schema")
table(["Schema", "Purpose"], [
    ["raw", "Exact API payloads (JSONB) and dictionaries; change hash; soft-delete flag"],
    ["core", "Typed, cleaned and linked entities: plan_point, announcement, lot, contract, contract_unit, subject, "
     "dictionaries, cpi, dq_issue, target_customer"],
    ["mart", "What the agent reads: price_obs, price_stats, price_deviation, volume_yearly, dq_summary"],
    ["ops", "Run log, per-BIN reconciliation, watermarks, lists of objects the API no longer returns"],
], [20, 150])
story.append(entity_diagram())
caption("Figure 2. How procurement records link: plan line → lot → contract line.")
table(["Mart", "Grain", f"Rows"], [
    ["price_obs", "one priced observation: a contract line, or a lot with no contract", n(M["price_obs"])],
    ["price_stats", "ENSTRU × unit × period", n(M["price_stats"])],
    ["price_deviation", "observation + leave-one-out peer average + robust z", n(M["price_deviation"])],
    ["volume_yearly", "customer × ENSTRU × unit × year (planned)", n(M["volume_yearly"])],
], [34, 106, 30])
p("The agent connects as <font name='Mono'>tenderai_agent</font>: read-only, core and mart only, 15-second statement "
  "timeout. The LLM never writes SQL; tools use fixed templates with bound parameters.")

# ================================================================ 7
h1("7. ETL and data quality")
table(["Step", "What happens"], [
    ["Units", "Each MKEI unit maps to a base unit and factor (g/kg/t → kg, ml/L/m³ → L, thousand pcs → pcs); "
     "packaging units (pack, set) stay separate and are compared only with themselves"],
    ["Names", "Lot and plan names are cleaned of numbering, quotes, specification tails and noise into name_clean"],
    ["Versions", "Only the latest plan version and the final contract of each amendment chain are used"],
    ["Location", "Delivery KATO: lot → plan → announcement → customer address, with the source recorded"],
    ["Prices", "Without VAT, per base unit; a contract line replaces the lot it came from"],
    ["CPI", "Official stat.gov.kz bulletins (Jan 2024 – Sep 2026), by region and category, loaded into core.cpi"],
    ["DQ rules", f"Errors (non-positive price or quantity, missing unit or code) are excluded from statistics; "
     f"warnings are kept and lower confidence. {n(C['dq_issue'])} issues recorded"],
], [24, 146])
if D.get("dq_rules"):
    table(["Entity", "Rule", "Severity", "Count"], [[a, escape(b), c, n(d)] for a, b, c, d in D["dq_rules"][:8]],
          [30, 90, 22, 28])

# ================================================================ 8
h1("8. Analytics and metrics")
h2("8.1 Fair Price (formal definition)")
table(["Step", "Rule"], [
    ["1. Comparable set", "Same ENSTRU code and base unit, other customers, within ±18 months. The first level with "
     "N ≥ 5 is used: same delivery city → same region → national → coarser code prefix."],
    ["2. Time", "p' = p<sub>i</sub> · I(t) / I(t<sub>i</sub>) · S(m<sub>t</sub>) / S(m<sub>i</sub>). I is the official CPI "
     "of the comparable's region and the item's category; S is a seasonal index (only with N ≥ 30 over ≥ 6 months)."],
    ["3. Region", "K(r) = w · median(p' in r) / median(p') + (1 − w), w = n<sub>r</sub> / (n<sub>r</sub> + 10); prices "
     "are moved to the target region."],
    ["4. Fair price", "Median of adjusted prices; fair band = [Q1, Q3]; weighted mean reported alongside."],
    ["5. Verdict", "Overpriced if deviation > +30% or robust z > 3; underpriced (dumping risk) if < −30% or z < −3; fair "
     "if inside the band or within ±10%; otherwise elevated / below market."],
    ["6. Confidence", "0.35 · min(1, ln N / ln 30) + 0.25 / (1 + IQR/median) + 0.20 · level + 0.10 · (1 − DQ) + 0.10 · "
     "region known."],
], [28, 142])
p("<b>Quantity of the lot.</b> The same answer judges the quantity: the customer's planned volume of that code and "
  "unit in the lot's year against the median of its earlier years; above 1.5× is reported as atypical.")
h2("8.2 Price anomalies")
p("Every observation is converted to constant prices with the official CPI. Its baseline is the quantity-weighted "
  "average of the <b>other</b> purchases of the same code and unit (leave-one-out, at least 3 peers), so an outlier "
  "cannot hide in its own average. Deviation above the threshold from the question (30% by default) flags it; a "
  "robust z of the log price above 3 confirms it. Prices more than 21× the peers are set aside as likely unit errors. "
  "Services priced per '1 service' are excluded. 'Below market' questions use the same method in the other direction.")
h2("8.3 Volume anomalies")
p("Planned quantity per customer × code × unit × year against the median of earlier years. Flagged above 1.5× (and "
  "robust z > 3 when two earlier years exist); confirmed when the growth is statistically extreme and the extra "
  "planned spend is at least 1 million KZT. An Isolation Forest score is shown as a secondary signal only.")
h2("8.4 Confidence of other answers")
p("Search, comparisons, statistics and anomaly answers carry an aggregate confidence: 0.6 · min(1, ln N / ln 30) + "
  "0.4 · (1 − DQ exclusion rate); high ≥ 0.70, medium ≥ 0.45, low otherwise, and always low under 5 objects.")

# ================================================================ 9
h1("9. The AI agent")
story.append(request_flow())
caption("Figure 3. Lifecycle of one question. Red steps call the local LLM; everything else is deterministic.")
h2("9.1 Understanding the question")
bullets([
    "<b>Rules first</b>: language, BINs, ENSTRU codes, lot numbers, years (2025 vs 2024 means 2025; years outside "
    "2024–2026 are refused), thresholds, top-K, a unit named in the question, 'below market' direction.",
    "<b>Then the LLM</b> fills a JSON schema: intent, item, customer names, year, threshold, top-K. Rule facts always "
    "win; ranking questions are comparisons; a fairness intent without a lot falls back to the rules.",
])
h2("9.2 Resolving names")
bullets([
    "<b>Customers</b>: several names are split; each is matched word by word against the 27 customers after collapsing "
    "official forms (e.g. 'Управления делами Президента' → 'УДП'); one best match per name. An unmatched name is "
    "<b>refused</b>, never widened to all customers.",
    "<b>Items</b>: matched to ENSTRU codes in tiers — exact name, phrase, all words, trigram similarity — preferring the "
    "most-purchased code; search covers every code sharing the item's name.",
])
h2("9.3 Tools")
table(["Intent", "Tool", "Output"], [
    ["search", "search_procurements", "count, total, Top-K purchases"],
    ["analytics", "price_stats", "weighted average, median, Q1–Q3, min/max per year + examples"],
    ["compare", "compare_customers / compare_years", "per-customer or per-year table + examples"],
    ["price_anomaly", "price_anomalies", "flagged, confirmed, estimated overpayment, Top-K deviations"],
    ["fair_price", "fair_price_for_lot", "Fair Price, band, deviation, verdict, confidence, quantity check"],
    ["volume_anomaly", "volume_anomalies", "series compared, flagged, confirmed, extra spend, Top-K series"],
], [28, 52, 90])
h2("9.4 Verdict and validation")
p("The LLM receives the question, a draft and the facts as JSON, and writes 1–3 sentences (temperature 0, fixed "
  "seed, thinking off). The validator then:")
bullets([
    "matches every number, BIN and date (Russian and Kazakh formats, '12,5 млн') to the facts;",
    "checks the role of numbers: the compared total cannot be presented as the flagged count; a price outside Q1–Q3 "
    "cannot be called inside it;",
    "corrects 'ИНН' on an organisation's number to 'БИН'.",
])
p("On a problem the model gets one corrective retry naming it; if that fails too, a template verdict is used.")
h2("9.5 Answer format")
table(["Assignment section", "Answer section", "Source"], [
    ["Вердикт / краткий вывод", "Вердикт (1–3 sentences)", "LLM, validated; or template"],
    ["Параметры поиска / использованные данные", "Параметры поиска и использованные данные", "tool"],
    ["Аналитика / сравнение", "Аналитика и сравнение", "tool"],
    ["Метрика оценки", "Метрика оценки", "tool"],
    ["Уверенность / ограничения", "Уверенность и ограничения", "tool"],
    ["Детализация / примеры Top-K", "Детализация (Top-K) with links", "tool"],
], [55, 70, 45])

# ================================================================ 10
story.append(PageBreak())  # the answer boxes are kept whole, so start them on a fresh page
h1("10. Example answers")
p("Real answers from the current data, as the agent returns them (portal links shortened to their labels; "
  "long Top-K lists trimmed to 5).")
titles = {"anomaly": "Mandatory type: price anomalies", "fair": "Mandatory type: fairness of a lot",
          "volume": "Mandatory type: volume growth", "kk": "Kazakh question", "refusal": "Customer outside the 27"}
for key in ("anomaly", "fair", "volume", "kk", "refusal"):
    e = EX.get(key)
    if not e:
        continue
    story.append(KeepTogether([
        Paragraph(titles[key], ss["h2"]),
        Paragraph(f"<b>Question:</b> {escape(e['question'])} <font color='#52606d'>· intent {e['route']['intent']} · "
                  f"verdict: {e['verdict_source']} · {sum(e['timings'].values()):.1f} s</font>", ss["body"]),
        md_answer(trim_answer(e["markdown"]))]))
    story.append(Spacer(1, 8))

# ================================================================ 11
h1("11. Evaluation")
p("Four question sets check answers automatically. Each case lists the facts (BINs, codes, numbers) a correct answer "
  "must contain; numbers are parsed from Russian/Kazakh formatting with 0.5% tolerance. Facts the question itself "
  "contains (a BIN or lot number the user typed) are not scored, and small numbers must appear outside the Top-K list. "
  "Fair Price verdicts and refusals ('no data', out of scope) are scored as separate checks. The held-out set was "
  "written after the agent was finished, on customers, items and wordings used nowhere else, and the agent is not "
  "changed to pass it; eval/check_leakage.py (also a unit test) verifies that no evaluation question is among the "
  "LLM's few-shot examples and no evaluation identifier or figure appears in the code.")


def chk(r):
    return f"{pct(r['check_accuracy'])} ({r['checks_passed']}/{r['checks_total']})" if r.get("checks_total") else "—"


def facts(r):
    return f"{pct(r.get('fact_accuracy', 0))} ({r.get('facts_correct')}/{r.get('facts_total')})"


table(["Set", "Cases", "Fact accuracy", "Verdict / refusal checks", "Intent accuracy"], [
    *[[label, r.get("cases"), facts(r), chk(r), pct(r.get("intent_accuracy", 0))] for label, r in (
        ("Held-out (eval/heldout.jsonl): fresh questions, not tuned on", EH),
        ("Corpus (eval/corpus.jsonl): hand-picked questions incl. traps; used during development", EC),
        ("Golden set (eval/golden.jsonl): templated questions from SQL", EG),
        ("Held-out, without the LLM (rules fallback)", EHR),
        ("Corpus, without the LLM", D.get("eval_corpus_rules") or {}),
        ("Golden set, without the LLM", D.get("eval_golden_rules") or {})) if r],
], [70, 16, 34, 30, 24])
p("Product variants. One product is often split into codes that differ only in the plan description (petrol АИ-92 / "
  "АИ-95, summer / winter diesel, A4 / A3 paper, battery types). Item resolution reads those descriptions "
  "(mart.enstru_variant): a named variant ('бензин АИ-95') selects its code, a bare product name ('бензин') selects "
  "every variant; spending and anomalies cover all of them, and price statistics are shown per variant, never pooled. "
  "Before this change the held-out set scored 71.0% and the corpus 80.7%. Remaining misses: Kazakh item names the LLM "
  "transliterates instead of translating ('пияз' became 'пьяз'), a customer name the LLM also copies into the item "
  "field ('Бүркіт'; varies between sessions), and abbreviations ('дизтопливо').")
p("Without the LLM the agent still routes questions and recognises customers by name in the question text, and "
  "answers with template verdicts. It cannot turn free-text item names into product codes, which is why the "
  "corpus, whose questions name items in words, scores lower in that mode; questions that give a code, a lot "
  "number or a BIN are unaffected.")
EP, ECR = D.get("eval_paraphrases") or {}, D.get("eval_corpus") or {}
if EP.get("repeats"):
    h2("Stability")
    p("The LLM is not fully deterministic, so a single pass can hide answers that only go wrong some of the time. "
      f"Every question is therefore also asked {EP['repeats']} times, and the paraphrase set (eval/paraphrases.jsonl) "
      "asks each mandatory question type (and one analytics question) 5–7 different ways in Russian and Kazakh; every wording in "
      "a group must give the same facts.")
    table(["Set", "Questions × runs", "Fact accuracy", "Worst run", "Same answer in every run"], [
        *([["Held-out", f"{EH['cases']} × {EH['repeats']}", pct(EH["fact_accuracy"]),
            pct(EH["worst_run_fact_accuracy"]), pct(EH["stable_share"])]] if EH.get("repeats") else []),
        *([["Corpus", f"{ECR['cases']} × {ECR['repeats']}", pct(ECR["fact_accuracy"]),
            pct(ECR["worst_run_fact_accuracy"]), pct(ECR["stable_share"])]] if ECR.get("repeats") else []),
        ["Paraphrases (4 groups)", f"{EP['cases']} × {EP['repeats']}", pct(EP["fact_accuracy"]),
         pct(EP["worst_run_fact_accuracy"]), pct(EP["stable_share"])],
    ], [52, 32, 28, 26, 32])
    p("The first paraphrase run found a Kazakh wording ('ТЖҚ саны күрт өскен…') wrongly refused as off-topic; the "
      "check now applies only to unclassified questions. A user-reported answer that analysed 0 purchases, because "
      "the model took 'ЕНСТРУ' as the item, led to two safeguards: classifier words are never items, and an item "
      "matching no product code and no purchase name is reported as unrecognised instead of 'no data'.")
p("The corpus was written after studying the data: customers named by name (not BIN), everyday items, lots with a "
  "known fair / overpriced / underpriced verdict, and edge cases (unknown customer, year outside the period, "
  "off-topic, missing lot number, item names that fuzzy matching gets wrong). Its reference answers are computed with "
  "independent SQL, and volume anomalies with an independent implementation of the rule.")
table(["Stage", "Fact accuracy", "Main problems"], [
    ["First run with the LLM", "66.9%", "unknown customers widened to all 27; 'бензин' matched a medicine; two "
     "customers in one question; years; 'below market'"],
    ["After matching and routing fixes", "99.3%", "unit named in the question ignored; numbers used in the wrong role"],
    ["After units and claim checks", "100.0%", "—"],
    ["Re-run on refreshed data (13 Oct), corpus rebuilt", "96.0%", "a code represented by a stray 'приобретение …' "
     "name; the LLM's routing of a two-year comparison varied between runs"],
    ["After name cleaning and a year-comparison rule", "100.0%", "—"],
    ["Review (14 Oct): references corrected, scoring stricter, few-shot examples replaced",
     "80.7%", "variant codes (АИ-92 / АИ-95, summer / winter diesel, A4, laundry soap)"],
    ["Product variants told apart by plan description", pct(EC.get("fact_accuracy", 0)),
     "a Kazakh item name the LLM did not translate"],
], [46, 26, 98])
p("Caveat: the fixes were prompted by this corpus, so its score is optimistic. A later review found that several of "
  "its reference answers treated one variant code as the whole product (petrol = АИ-92 only, diesel = summer only, "
  "'A4 paper' including A3 and A5, 'soap' = laundry soap only) and that the router's few-shot examples contained "
  "evaluation questions. Corrected references, stricter scoring and new examples gave 80.7% on the corpus; telling "
  f"product variants apart raised it to {pct(EC.get('fact_accuracy', 0))}. The held-out set above is the honest measure.")

# ================================================================ 12
h1("12. API, web chat, deployment, security")
table(["Endpoint", "Behaviour"], [
    ["GET /", "Web chat: question box, examples, answers rendered as six labelled sections with links"],
    ["POST /ask", "Full structured answer: markdown, route, tool result, verdict source, validation, timings; requests "
     "are serialised because one local GPU serves the LLM"],
    ["GET /health", "Database, LLM availability, data timestamp, lag in hours, 24 h SLA flag, last ingest run"],
], [24, 146])
table(["Deployment", "Contents"], [
    ["Docker Compose", "postgres 16, ollama (GPU), one-off migrate, api, worker — one non-root application image"],
    ["Kubernetes", "Postgres StatefulSet, Ollama Deployment with a GPU, migrate Job, API Deployment with readiness on "
     "/health, incremental CronJob every 3 h, secrets in a Secret"],
], [30, 140])
bullets([
    "The OWS token lives only in the environment or a secret, is a SecretStr in settings and never reaches logs.",
    "The agent's database role is read-only and limited to core and mart.",
    "The LLM runs locally with no internet, tools or SQL access; user-supplied text reaches it only inside a JSON facts "
    "block, and its output is validated.",
])

# ================================================================ 13
h1("13. Risks and limitations")
table(["Risk", "Mitigation"], [
    ["Two ENSTRU formats, broad codes, wide price spread within a code", "Exact then prefix matching; leave-one-out "
     "baselines; robust z confirmation; medians and IQR"],
    ["≈70% of plan lines without a delivery location", "Fallback chain with recorded source; stated in the answer"],
    ["Planned vs executed volumes; short history (2024–2026)", "Stated in every volume answer; z-test only with two "
     "earlier years"],
    ["Only 27 customers, so peers are the other 26", "N and comparison level shown; more BINs is a config change"],
    ["CPI bulletins end in September 2026", "Nearest month used; add new bulletins and re-run load-cpi"],
    ["LLM wording errors", "Fact and claim validation, retry, template fallback; deterministic sections"],
    ["Name and item recognition", "Tiered matching, refusal of unknown customers, resolved code shown to the user"],
    ["One 8 GB GPU", "Requests serialised; template answers when the LLM is unavailable"],
], [70, 100])
callout("A price 'anomaly' is a signal for review, not proof of a violation. The fair band reflects what the other "
        "26 customers actually paid; it is not an external market benchmark.")

# ================================================================ 14
h1("14. Running the system")
code("""
cp .env.example .env                           # OWS_TOKEN
docker compose -f deploy/docker-compose.yml up -d postgres ollama
docker compose -f deploy/docker-compose.yml exec ollama ollama pull qwen3.5:9b
python -m tenderai.cli migrate
python -m tenderai.cli backfill                # ~25 min
python -m tenderai.cli load-cpi cpi_dataset
python -m tenderai.cli etl
python -m tenderai.cli serve                   # http://127.0.0.1:8010
python -m tenderai.cli worker                  # incremental sync every 3 h
python eval/run_eval.py eval/corpus.jsonl      # fact accuracy
""")

doc = SimpleDocTemplate(OUT, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=20 * mm,
                        bottomMargin=18 * mm, title="TenderAI report", author="TenderAI",
                        subject="How TenderAI works and how it meets the assignment")
doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
print("ok", OUT)
