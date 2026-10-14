"""Build the TenderAI architecture PDF. Usage: python make_pdf.py <out.pdf>"""
import sys
from datetime import date

from reportlab.graphics.shapes import Drawing, Group, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (CondPageBreak, KeepTogether, PageBreak, Paragraph, Preformatted,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

F = "C:/Windows/Fonts/"
pdfmetrics.registerFont(TTFont("Body", F + "arial.ttf"))
pdfmetrics.registerFont(TTFont("Body-Bold", F + "arialbd.ttf"))
pdfmetrics.registerFont(TTFont("Body-Italic", F + "ariali.ttf"))
pdfmetrics.registerFont(TTFont("Mono", F + "consola.ttf"))
pdfmetrics.registerFont(TTFont("Mono-Bold", F + "consolab.ttf"))
from reportlab.pdfbase.pdfmetrics import registerFontFamily  # noqa: E402

registerFontFamily("Body", normal="Body", bold="Body-Bold", italic="Body-Italic", boldItalic="Body-Bold")
registerFontFamily("Mono", normal="Mono", bold="Mono-Bold", italic="Mono", boldItalic="Mono-Bold")

INK = colors.HexColor("#1f2933")
MUTED = colors.HexColor("#52606d")
ACCENT = colors.HexColor("#1d4e89")
ACCENT_LIGHT = colors.HexColor("#e8eff8")
RULE = colors.HexColor("#cbd2d9")
CODE_BG = colors.HexColor("#f5f7fa")

# lane colours for diagrams
C_SRC = colors.HexColor("#fdf1e3")
C_ING = colors.HexColor("#e6f4ea")
C_DB = colors.HexColor("#e8eff8")
C_AG = colors.HexColor("#f1eafa")
C_LLM = colors.HexColor("#fde8e8")

ss = {
    "title": ParagraphStyle("title", fontName="Body-Bold", fontSize=28, leading=34, textColor=ACCENT),
    "subtitle": ParagraphStyle("subtitle", fontName="Body", fontSize=13, leading=18, textColor=MUTED),
    "h1": ParagraphStyle("h1", fontName="Body-Bold", fontSize=17, leading=22, textColor=ACCENT,
                         spaceBefore=6, spaceAfter=8),
    "h2": ParagraphStyle("h2", fontName="Body-Bold", fontSize=12.5, leading=16, textColor=INK,
                         spaceBefore=10, spaceAfter=4),
    "body": ParagraphStyle("body", fontName="Body", fontSize=9.6, leading=13.6, textColor=INK, spaceAfter=5),
    "bullet": ParagraphStyle("bullet", fontName="Body", fontSize=9.6, leading=13.4, textColor=INK,
                             leftIndent=12, bulletIndent=2, spaceAfter=2),
    "cell": ParagraphStyle("cell", fontName="Body", fontSize=8.4, leading=11, textColor=INK),
    "cellh": ParagraphStyle("cellh", fontName="Body-Bold", fontSize=8.4, leading=11, textColor=colors.white),
    "code": ParagraphStyle("code", fontName="Mono", fontSize=8, leading=10.4, textColor=INK),
    "caption": ParagraphStyle("caption", fontName="Body-Italic", fontSize=8.4, leading=11, textColor=MUTED,
                              alignment=TA_CENTER, spaceBefore=3, spaceAfter=10),
    "callout": ParagraphStyle("callout", fontName="Body", fontSize=9.6, leading=13.6, textColor=INK),
    "toc": ParagraphStyle("toc", fontName="Body", fontSize=10.5, leading=17, textColor=INK),
}

story = []


def h1(t):
    story.append(CondPageBreak(60 * mm))
    story.append(Paragraph(t, ss["h1"]))


def h2(t):
    story.append(CondPageBreak(30 * mm))
    story.append(Paragraph(t, ss["h2"]))


def p(t):
    story.append(Paragraph(t, ss["body"]))


def bullets(items):
    for it in items:
        story.append(Paragraph(it, ss["bullet"], bulletText="•"))
    story.append(Spacer(1, 4))


def code(text):
    t = Table([[Preformatted(text.strip("\n"), ss["code"])]], colWidths=[170 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), CODE_BG), ("BOX", (0, 0), (-1, -1), 0.4, RULE),
                           ("LEFTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 5),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    story.append(t)
    story.append(Spacer(1, 6))


def callout(text):
    t = Table([[Paragraph(text, ss["callout"])]], colWidths=[170 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), ACCENT_LIGHT),
                           ("LINEBEFORE", (0, 0), (0, -1), 3, ACCENT),
                           ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                           ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story.append(t)
    story.append(Spacer(1, 8))


def table(header, rows, widths):
    data = [[Paragraph(h, ss["cellh"]) for h in header]]
    data += [[Paragraph(str(c), ss["cell"]) for c in r] for r in rows]
    t = Table(data, colWidths=[w * mm for w in widths], repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), ACCENT), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("GRID", (0, 0), (-1, -1), 0.3, RULE), ("LEFTPADDING", (0, 0), (-1, -1), 4),
             ("RIGHTPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 3),
             ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f8fafc")))
    t.setStyle(TableStyle(style))
    story.append(t)
    story.append(Spacer(1, 8))


def caption(t):
    story.append(Paragraph(t, ss["caption"]))


# ---------------------------------------------------------------- diagram helpers
def box(d, x, y, w, h, lines, fill, bold_first=True, size=7.6):
    d.add(Rect(x, y, w, h, rx=4, ry=4, fillColor=fill, strokeColor=colors.HexColor("#7b8794"), strokeWidth=0.6))
    n = len(lines)
    lead = size + 2.2
    top = y + h / 2 + (n - 1) * lead / 2 - size * 0.35
    for i, ln in enumerate(lines):
        font = "Body-Bold" if (i == 0 and bold_first) else "Body"
        col = INK if i == 0 else MUTED
        d.add(String(x + w / 2, top - i * lead, ln, fontName=font, fontSize=size if i == 0 else size - 0.6,
                     fillColor=col, textAnchor="middle"))


def arrow(d, x1, y1, x2, y2, col=colors.HexColor("#52606d"), label=None, both=False):
    import math
    d.add(Line(x1, y1, x2, y2, strokeColor=col, strokeWidth=0.9))
    def head(xa, ya, xb, yb):
        a = math.atan2(yb - ya, xb - xa)
        s = 4.5
        d.add(Polygon([xb, yb, xb - s * math.cos(a - 0.4), yb - s * math.sin(a - 0.4),
                       xb - s * math.cos(a + 0.4), yb - s * math.sin(a + 0.4)], fillColor=col, strokeColor=col,
                      strokeWidth=0.1))
    head(x1, y1, x2, y2)
    if both:
        head(x2, y2, x1, y1)
    if label:
        d.add(String((x1 + x2) / 2 + 3, (y1 + y2) / 2 + 2, label, fontName="Body-Italic", fontSize=6.6,
                     fillColor=MUTED))


def lane(d, x, y, w, h, title, fill):
    d.add(Rect(x, y, w, h, rx=6, ry=6, fillColor=fill, strokeColor=None, fillOpacity=0.45))
    lbl = Group(String(0, 0, title, fontName="Body-Bold", fontSize=7.2, fillColor=ACCENT, textAnchor="middle"))
    lbl.translate(x + 10, y + h / 2)
    lbl.rotate(90)
    d.add(lbl)


def system_diagram():
    W, H = 482, 416
    outer = Drawing(W, H)
    d = Group()
    k = 0.962
    d.transform = (k, 0, 0, k, 18, 0)
    # lanes (start left of 0 so the rotated labels sit in a gutter)
    lane(d, -18, 352, 500, 74, "SOURCES", C_SRC)
    lane(d, -18, 262, 500, 84, "INGESTION", C_ING)
    lane(d, -18, 142, 500, 114, "POSTGRESQL 16", C_DB)
    lane(d, -18, 0, 500, 136, "AGENT + API", C_AG)
    # sources
    box(d, 8, 360, 112, 44, ["OWS v3 GraphQL", "Plans · TrdBuy · Lots", "Contract · Subjects"], colors.white)
    box(d, 128, 360, 100, 44, ["OWS REST /v3/refs", "12 dictionaries"], colors.white)
    box(d, 236, 360, 100, 44, ["OWS REST /v3/journal", "national change log"], colors.white)
    box(d, 344, 360, 130, 44, ["stat.gov.kz CPI", "T-15-02-M .xlsx bulletins", "(cpi_dataset/)"], colors.white)
    # ingestion
    box(d, 8, 270, 120, 54, ["OwsClient", "async httpx, 4 parallel,", "retries, cursor paging"], colors.white)
    box(d, 150, 270, 100, 54, ["backfill", "27 BINs × 2024–2026", "+ gap filling"], colors.white)
    box(d, 262, 270, 100, 54, ["incremental", "every 3 h · indexDate", "+ journal deletions"], colors.white)
    box(d, 380, 270, 94, 54, ["load-cpi", "parse bulletins", "-> core.cpi"], colors.white)
    arrow(d, 64, 360, 64, 324)
    arrow(d, 178, 360, 100, 324)
    arrow(d, 286, 360, 300, 324)
    arrow(d, 409, 360, 427, 324)
    arrow(d, 150, 297, 128, 297, both=True)
    d.add(String(68, 263, "used by backfill and incremental", fontName="Body-Italic", fontSize=6.6,
                 fillColor=MUTED, textAnchor="middle"))
    # database
    box(d, 8, 156, 84, 66, ["ops", "ingest_run · counts", "watermark", "subject/object_miss"], colors.white)
    box(d, 120, 156, 100, 66, ["raw", "ows_object (JSONB)", "ref · md5 hash", "soft delete"], colors.white)
    box(d, 250, 156, 104, 66, ["core", "typed + cleaned + linked", "plan_point · lot · contract", "subject · cpi · dq_issue"],
        colors.white)
    box(d, 380, 156, 94, 66, ["mart", "price_obs · price_stats", "price_deviation", "volume_yearly", "dq_summary"],
        colors.white)
    arrow(d, 190, 270, 170, 222)
    arrow(d, 300, 270, 200, 222)
    arrow(d, 165, 270, 60, 222)
    arrow(d, 427, 270, 320, 222)
    arrow(d, 220, 189, 250, 189)
    d.add(String(235, 193, "ETL", fontName="Body-Italic", fontSize=6.6, fillColor=MUTED, textAnchor="middle"))
    arrow(d, 354, 189, 380, 189)
    d.add(String(367, 193, "marts", fontName="Body-Italic", fontSize=6.6, fillColor=MUTED, textAnchor="middle"))
    # agent
    box(d, 8, 76, 104, 40, ["FastAPI", "POST /ask · GET /health", "web chat at /"], colors.white)
    box(d, 130, 76, 92, 40, ["router", "rules + LLM JSON"], colors.white)
    box(d, 240, 76, 100, 40, ["tools", "read-only SQL + analytics"], colors.white)
    box(d, 358, 76, 116, 40, ["composer + validator", "6-section answer"], colors.white)
    box(d, 130, 12, 210, 40, ["Ollama · Qwen3.5 9B (local GPU)", "classifies intent · writes 1–3 sentence verdict"],
        C_LLM)
    box(d, 358, 12, 116, 40, ["worker", "runs the incremental sync", "every 3 h (APScheduler)"], colors.white)
    arrow(d, 112, 96, 130, 96)
    arrow(d, 222, 96, 240, 96)
    arrow(d, 340, 96, 358, 96)
    arrow(d, 300, 116, 300, 156)
    arrow(d, 330, 116, 400, 156)
    d.add(String(296, 126, "read-only role", fontName="Body-Italic", fontSize=6.6, fillColor=MUTED,
                 textAnchor="end"))
    arrow(d, 176, 76, 176, 52, both=True)
    arrow(d, 400, 76, 320, 52, both=True)
    outer.add(d)
    return outer


def request_diagram():
    W, H = 482, 112
    d = Drawing(W, H)
    steps = [("1 Route", ["rules first", "then LLM schema"]),
             ("2 Resolve", ["customers -> BIN", "item -> ENSTRU"]),
             ("3 Tool", ["one SQL/Python", "call, no LLM"]),
             ("4 Verdict", ["LLM writes", "1–3 sentences"]),
             ("5 Validate", ["numbers, BINs,", "dates + claims"]),
             ("6 Render", ["6 fixed sections", "+ portal links"])]
    w, gap = 70, 12.4
    x = 2
    for i, (t, sub) in enumerate(steps):
        fill = C_LLM if t.startswith(("4", "1")) else (C_AG if i % 2 else C_DB)
        box(d, x, 58, w, 50, [t] + sub, fill)
        if i < len(steps) - 1:
            arrow(d, x + w, 83, x + w + gap, 83)
        x += w + gap
    # retry loop
    vx = 2 + 4 * (w + gap)
    d.add(Line(vx + w / 2, 58, vx + w / 2, 40, strokeColor=colors.HexColor("#b42318"), strokeWidth=0.9))
    d.add(Line(vx + w / 2, 40, 2 + 3 * (w + gap) + w / 2, 40, strokeColor=colors.HexColor("#b42318"), strokeWidth=0.9))
    arrow(d, 2 + 3 * (w + gap) + w / 2, 40, 2 + 3 * (w + gap) + w / 2, 58, col=colors.HexColor("#b42318"))
    d.add(String(2 + 3 * (w + gap) + w / 2 + 6, 30, "unsupported or misused figure -> one corrective retry",
                 fontName="Body-Italic", fontSize=6.8, fillColor=colors.HexColor("#b42318")))
    d.add(String(2 + 3 * (w + gap) + w / 2 + 6, 21, "fails again or LLM down -> template verdict",
                 fontName="Body-Italic", fontSize=6.8, fillColor=colors.HexColor("#b42318")))
    d.add(String(2, 2, "Shaded red = steps that call the local LLM. Every other step is deterministic.",
                 fontName="Body-Italic", fontSize=7, fillColor=MUTED))
    return d


def entity_diagram():
    W, H = 482, 132
    d = Drawing(W, H)
    box(d, 4, 70, 92, 46, ["plan_point", "annual plan line", "ENSTRU · unit · KATO"], C_DB)
    box(d, 130, 70, 92, 46, ["lot", "item in a tender", "pointList[0] -> plan"], C_DB)
    box(d, 256, 70, 104, 46, ["contract_unit", "contract line item", "actual unit price"], C_DB)
    box(d, 386, 70, 92, 46, ["contract", "signed deal", "amendment chain"], C_DB)
    box(d, 130, 6, 92, 42, ["announcement", "TrdBuy · tender call"], C_DB)
    box(d, 386, 6, 92, 42, ["subject", "customer / supplier"], C_DB)
    arrow(d, 96, 93, 130, 93)
    arrow(d, 222, 93, 256, 93)
    arrow(d, 386, 93, 360, 93)
    arrow(d, 176, 48, 176, 70)
    arrow(d, 432, 48, 432, 70)
    d.add(Line(96, 80, 110, 80, strokeColor=MUTED, strokeWidth=0.6))
    d.add(Line(110, 80, 110, 62, strokeColor=MUTED, strokeWidth=0.6))
    d.add(Line(110, 62, 300, 62, strokeColor=MUTED, strokeWidth=0.6))
    arrow(d, 300, 62, 300, 70)
    d.add(String(232, 54, "contract lines also link to plan_point directly", fontName="Body-Italic", fontSize=6.6,
                 fillColor=MUTED))
    return d


# ---------------------------------------------------------------- page decoration
def on_page(c, doc):
    c.saveState()
    if doc.page > 1:
        c.setFont("Body", 7.5)
        c.setFillColor(MUTED)
        c.drawString(20 * mm, A4[1] - 12 * mm, "TenderAI · Architecture")
        c.drawRightString(A4[0] - 20 * mm, A4[1] - 12 * mm, "goszakup.gov.kz OWS v3 analytics agent")
        c.setStrokeColor(RULE)
        c.setLineWidth(0.4)
        c.line(20 * mm, A4[1] - 14 * mm, A4[0] - 20 * mm, A4[1] - 14 * mm)
        c.drawCentredString(A4[0] / 2, 10 * mm, str(doc.page))
    c.restoreState()


# ================================================================ CONTENT
story.append(Spacer(1, 55 * mm))
story.append(Paragraph("TenderAI", ss["title"]))
story.append(Spacer(1, 4))
story.append(Paragraph("Architecture of an explainable AI agent for Kazakhstan public procurement analytics",
                       ss["subtitle"]))
story.append(Spacer(1, 10 * mm))
cover = Table([[Paragraph(
    "<b>Scope</b>: 27 customer organisations, 2024–2026, data from goszakup.gov.kz OWS v3<br/>"
    "<b>Stack</b>: Python 3.11+, PostgreSQL 16, FastAPI, Ollama with Qwen3.5 9B (on-prem GPU)<br/>"
    "<b>Languages</b>: questions and answers in Russian and Kazakh<br/>"
    f"<b>Document date</b>: {date.today():%d %B %Y}, written from the current repository state<br/>"
    "<b>Data as of</b>: 13 October 2026, 14:02 UTC (latest incremental sync)", ss["callout"])]],
    colWidths=[170 * mm])
cover.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), ACCENT_LIGHT), ("LINEBEFORE", (0, 0), (0, -1), 3, ACCENT),
                           ("LEFTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 8),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
story.append(cover)
story.append(Spacer(1, 14 * mm))
story.append(Paragraph("<b>Contents</b>", ss["h2"]))
toc = ["1. Overview and design principles", "2. System architecture", "3. Repository layout",
       "4. Data source: OWS v3", "5. Ingestion", "6. Storage schema", "7. ETL: raw to core to marts",
       "8. Analytics methods", "9. The agent", "10. API, web chat and worker", "11. Deployment",
       "12. Security", "13. Testing and evaluation", "14. Risks and limitations", "15. Operating the system"]
half = (len(toc) + 1) // 2
tt = Table([[Paragraph("<br/>".join(toc[:half]), ss["toc"]), Paragraph("<br/>".join(toc[half:]), ss["toc"])]],
           colWidths=[85 * mm, 85 * mm])
tt.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
story.append(tt)
story.append(PageBreak())

# 1 ------------------------------------------------------------------------
h1("1. Overview and design principles")
p("TenderAI is an analytical agent over Kazakhstan's public procurement portal, goszakup.gov.kz. It loads "
  "procurement plans, tender announcements, lots, contracts and organisations for 27 customer organisations "
  "(identified by BIN, the Kazakh business ID) from 2024 to 2026. Users ask questions in Russian or Kazakh, and "
  "every answer cites the source records with portal links, the sample size N, the method, a confidence score "
  "and the limitations.")
p("The agent handles six kinds of question, called <i>intents</i>:")
table(["Intent", "Example question", "What it returns"], [
    ["search", "Show purchases of A4 paper by customer X", "Matching lots and contract lines with links"],
    ["compare", "Compare spending on communication services between agencies in 2024; compare 2024 and 2025",
     "Per-customer or per-year totals and prices"],
    ["analytics", "What was the average diesel price in 2025?", "Price statistics for one product code"],
    ["price_anomaly", "Find purchases with a price deviation above 25%", "Observations far from the peer weighted average"],
    ["fair_price", "Is the price of lot No. 80800385-OI6 fair?", "Fair Price, band, deviation, verdict, confidence, "
     "and whether the lot's quantity is normal for the customer"],
    ["volume_anomaly", "Find purchases whose volume jumped versus earlier years", "Customer × product series with atypical growth"],
], [26, 76, 68])
h2("Design principles")
callout("<b>The LLM never calculates.</b> SQL and Python produce every figure. The LLM does two narrow jobs: "
        "it classifies the question into a JSON schema, and it writes a 1–3 sentence verdict. Every number, BIN "
        "and date in that verdict is checked against the tool output before it is shown. If the check fails twice, "
        "or the LLM is offline, a template verdict is used instead.")
bullets([
    "<b>Explainability by construction.</b> Five of the six answer sections are rendered directly from the tool "
    "result, never by the LLM. Each answer lists parameters, N, method, confidence, limitations and the Top-K "
    "object IDs with links.",
    "<b>Raw data is kept verbatim.</b> API payloads are stored as JSONB, so cleaning rules can change and the "
    "derived tables can be rebuilt in about a minute without calling the API again.",
    "<b>Idempotent, restartable ingestion.</b> Upserts are keyed by (entity, id) with a content hash, so re-running "
    "any load is safe.",
    "<b>Fully on-prem.</b> The LLM runs locally in Ollama on the GPU and has no internet, SQL or tool access. "
    "The only outbound calls are to the OWS API, and only during ingestion.",
    "<b>Graceful degradation.</b> Without the LLM the system still answers using rule-based routing, customer "
    "names found in the question text, and template verdicts.",
    "<b>Refuse rather than guess.</b> A customer outside the 27, a year outside 2024–2026, an off-topic question or a "
    "fairness question without a lot number gets a 'no data' answer that states the reason, never figures for "
    "something else.",
])

# 2 ------------------------------------------------------------------------
h1("2. System architecture")
p("The system has four layers. External sources feed an ingestion layer that writes raw payloads into "
  "PostgreSQL. An ETL step turns raw data into typed, linked <b>core</b> tables and then into analytical "
  "<b>mart</b> tables. The agent reads only core and mart through a read-only database role, and the local LLM "
  "sits beside it.")
story.append(system_diagram())
caption("Figure 1. Components and data flow. Arrows show the direction data moves.")
h2("The pipeline in one line")
code("""
OWS v3 GraphQL/REST --> ingest (async, retries, idempotent upserts) --> raw (JSONB)
        `-- journal (deletions only, action=D) --> incremental sync every 3 h (lag < 24 h)
raw --> ETL (SQL + Python: units, names, KATO, ENSTRU, DQ rules) --> core --> marts
marts --> deterministic tools (Fair Price, anomalies, volumes, stats) --> local LLM verdict
      --> fact + claim validator --> templated 6-section answer --> FastAPI /ask + web chat
""")
h2("Glossary")
table(["Term", "Meaning"], [
    ["OWS v3", "The public data API of goszakup.gov.kz (version 3), at ows.goszakup.gov.kz/v3"],
    ["BIN / IIN", "12-digit business ID / individual ID (sole traders are registered by IIN)"],
    ["ENSTRU", "The national classifier of goods, works and services; every plan line carries an ENSTRU code"],
    ["KATO", "The administrative-territorial classifier; the first 2 digits give the region (oblast or major city)"],
    ["MKEI", "The unit-of-measure classifier (kg, litre, piece, pack...)"],
    ["CPI", "Consumer price index from the Bureau of National Statistics (stat.gov.kz), used to adjust for inflation"],
    ["DQ", "Data quality: rules in etl/dq.sql that flag errors (excluded) and warnings (kept, lower confidence)"],
], [28, 142])

# 3 ------------------------------------------------------------------------
h1("3. Repository layout")
table(["Path", "Role"], [
    ["tenderai/config.py", "Settings from environment / .env (pydantic-settings); the 27 target BINs and years 2024–2026"],
    ["tenderai/cli.py", "Entry point: migrate, backfill, incremental, etl, load-cpi, worker, ask, serve"],
    ["tenderai/ows/client.py", "Async OWS client: GraphQL and REST, retries, cursor paging, error detection"],
    ["tenderai/ingest/", "queries.py (GraphQL field lists), backfill.py, incremental.py, store.py (raw upserts, run log)"],
    ["tenderai/db/", "migrate.py and numbered SQL migrations 001–010 (schemas, roles, CPI, miss tables)"],
    ["tenderai/etl/", "run.py (orchestrator), transform.sql, dq.sql, marts.sql, units.py, lot_names.py, cpi.py"],
    ["tenderai/analytics/", "fair_price.py, price_index.py, stats.py (robust statistics), volumes.py"],
    ["tenderai/agent/", "agent.py (orchestration), router.py, tools.py, llm.py, validator.py, composer.py, i18n.py"],
    ["tenderai/api/", "main.py (FastAPI) and static/chat.html (web chat)"],
    ["tenderai/worker/", "scheduler.py: APScheduler running the incremental sync every 3 hours"],
    ["deploy/", "Dockerfile, docker-compose.yml, k8s/ manifests (namespace and secrets, Postgres, Ollama, app)"],
    ["tests/", "93 unit tests (router, name matching, product variants, validator, composer, Fair Price, volumes, "
     "index, names, eval leakage) and OWS fixtures"],
    ["eval/", "Held-out set (23 fresh questions), corpus (54 hand-picked questions), golden set (40 templated) and "
     "paraphrases (24), all with SQL-derived reference answers; the evaluation runner, leakage check and saved reports"],
    ["docs/", "architecture, schema, analytics, data_scope, examples, risks; docs/report builds the PDFs"],
    ["cpi_dataset/", "Official stat.gov.kz T-15-02-M monthly CPI bulletins (.xlsx), loaded by load-cpi"],
], [42, 128])
p("The Python and SQL code is about 5,500 lines. The largest modules are the agent tools (tools.py), the answer "
  "composer and the Fair Price engine. TenderAI_Report.pdf in the repository root explains how the application "
  "meets each requirement of the assignment.")

# 4 ------------------------------------------------------------------------
h1("4. Data source: OWS v3")
p("OWS v3 exposes three interfaces. GraphQL is the primary ingest path: it carries complete lot data (names, "
  "quantities, ENSTRU lists, delivery KATO) and supports server-side filters by BIN, year and <font name='Mono'>"
  "indexDate</font>. REST is used only for dictionaries and the change journal.")
table(["Interface", "Used for", "Paging"], [
    ["POST /v3/graphql", "Plans, TrdBuy, Lots, Contract (+ ContractUnits), Subjects", "max 200 rows; cursor after = lastId"],
    ["GET /v3/refs/{name}", "12 dictionaries: units, KATO, trade methods, statuses, months, finance sources...", "500 rows per page"],
    ["GET /v3/journal", "National change log; used only to detect deletions (action = D)", "500 rows per page"],
], [34, 96, 40])
h2("Entities and why each is needed")
story.append(entity_diagram())
caption("Figure 2. How procurement records link together: plan line -> lot -> contract line, with announcements, "
        "contracts and organisations around them.")
table(["Entity", "Filter used", "Why it matters"], [
    ["Plans (plan_point)", "customer BIN + plan year 2024–2026", "ENSTRU code, unit, planned price and quantity, delivery KATO. Basis for volume anomalies"],
    ["TrdBuy (announcement)", "organiser BIN + publish date in period", "Publication date, trade method, status, portal link"],
    ["Lots (lot)", "customer BIN only (no date filter in the API)", "Announced price and quantity; links to plan line and announcement"],
    ["Contract + ContractUnits", "customer BIN + sign date in period", "Actual unit prices without VAT, supplier, status, amendment chain"],
    ["Subjects", "BIN, or IIN for sole traders", "Names, registered address KATO, supplier flags"],
    ["RefEnstru (nested)", "comes inside each plan record", "ENSTRU dictionary; the REST endpoint returns 'not found' in v3"],
], [36, 50, 84])
h2("API behaviour the code relies on")
bullets([
    "The token in the public documentation is a sample and returns 403. A working token is issued separately "
    "and lives only in <font name='Mono'>OWS_TOKEN</font>.",
    "Errors can arrive inside an HTTP 200 body, so the client checks the body as well as the status.",
    "Date filters accept <font name='Mono'>YYYY-MM-DD</font> or ISO with a <font name='Mono'>T</font>; timestamps "
    "are local time (UTC+5).",
    "Some REST text fields are double-encoded UTF-8; GraphQL text is correct.",
    "Timeouts and 5xx errors come in bursts; exponential backoff absorbs them.",
    "The journal is national and its volume swings: about 105,000 changes on a normal day, 4.6 million on "
    "12 October 2026 when the portal re-indexed plans in bulk. The incremental sync re-queries only the 27 BINs and "
    "reads the journal just for deletions, filtered on the server with <font name='Mono'>action=D</font> "
    "(746 pages that day instead of 9,276). <font name='Mono'>date_to</font> is exclusive.",
])

# 5 ------------------------------------------------------------------------
h1("5. Ingestion")
h2("5.1 OWS client (tenderai/ows/client.py)")
bullets([
    "Async <font name='Mono'>httpx</font> client with a bearer token from settings. The token is a "
    "<font name='Mono'>SecretStr</font>, and httpx request logging is silenced so URLs and headers never reach logs.",
    "A semaphore limits concurrency to 4 requests (<font name='Mono'>OWS_CONCURRENCY</font>).",
    "Retries up to 6 times on network errors, 429 and 5xx, with exponential backoff (1 s doubling to 30 s, plus "
    "jitter). 401/403 raise a clear 'check OWS_TOKEN' error immediately.",
    "<font name='Mono'>graphql_paginate</font> walks pages using <font name='Mono'>extensions.pageInfo.lastId</font> "
    "and fails loudly if the cursor stops advancing. <font name='Mono'>rest_paginate</font> follows "
    "<font name='Mono'>next_page</font>.",
])
h2("5.2 Backfill (tenderai/ingest/backfill.py)")
p("The full initial load, about 25 minutes. It runs in this order:")
bullets([
    "<b>1. Dictionaries</b>: the 12 REST reference tables into <font name='Mono'>raw.ref</font>.",
    "<b>2. Per-BIN entity loads</b>: plans, announcements, lots and contracts for each of the 27 BINs. For every "
    "(entity, BIN) the API's reported total is stored next to the rows fetched in <font name='Mono'>ops.ingest_count</font>.",
    "<b>3. Gap filling</b>: plan lines referenced by stored lots or contract lines but outside the BIN/year filter "
    "(about 134,000), and announcements organised by third parties for in-scope lots, are fetched by ID.",
    "<b>4. Subjects</b>: the 27 customers plus every supplier found on a contract.",
])
p("IDs that the API no longer returns (for example superseded plan versions) are recorded in "
  "<font name='Mono'>ops.object_miss</font>, and BIN/IINs with no Subjects result in <font name='Mono'>ops.subject_miss</font>. "
  "Both are skipped for 7 days rather than re-requested on every run.")
h2("5.3 Incremental sync (tenderai/ingest/incremental.py)")
p("Runs every 3 hours from the worker (or a Kubernetes CronJob). The freshness target is a data lag under 24 hours.")
bullets([
    "<b>Changed records</b>: for each of the 4 entities and 27 BINs, re-run the backfill filter plus "
    "<font name='Mono'>indexDate &gt;= watermark - 2 h</font>. The 2-hour overlap guards against late indexing.",
    "<b>Deletions</b>: page through <font name='Mono'>/v3/journal?action=D</font> for the same window (8 pages in "
    "parallel, progress logged). Deleted objects are soft-deleted (<font name='Mono'>is_deleted = true</font>), "
    "except objects whose stored version was re-indexed inside the window: this run has just fetched them live, so "
    "the deletion predates a re-publication.",
    "<b>Follow-ups</b>: fetch newly referenced plan lines, announcements and suppliers.",
    "<b>Watermarks</b>: update <font name='Mono'>incremental</font> (next window start) and "
    "<font name='Mono'>journal</font> (read by /health for freshness), then rebuild core and marts.",
])
p("If there is no watermark yet, the start time of the first successful backfill is used; with no backfill at "
  "all, the sync refuses to run. The latest run (13 October 2026) covered two days of the journal, 1,644 deletion "
  "pages, in about an hour, soft-deleting 503 lots and 20 plan lines; the ETL rebuild took 52 seconds.")
h2("5.4 Raw store (tenderai/ingest/store.py)")
bullets([
    "Upserts into <font name='Mono'>raw.ows_object</font> keyed by (entity, id). An md5 of the canonical JSON is "
    "stored, and the row is only rewritten when the hash differs.",
    "Rows repeated across page boundaries within one page batch are de-duplicated (last wins).",
    "<font name='Mono'>RunLog</font> records each run in <font name='Mono'>ops.ingest_run</font> with kind, status, "
    "stats and error.",
])
h2("5.5 Current volumes (13 October 2026)")
table(["Entity", "Rows"], [
    ["Plan line versions (in scope and referenced from other years or BINs)", "152,246"],
    ["Announcements", "65,835"],
    ["Lots, all years / in scope", "273,110 / 117,106"],
    ["Contract versions / contract line items", "36,831 / 105,324"],
    ["Organisations (customers and suppliers)", "8,849"],
    ["Priced observations (mart.price_obs)", "78,678"],
    ["Data-quality issues recorded", "236,601"],
], [110, 60])

# 6 ------------------------------------------------------------------------
h1("6. Storage schema")
p("PostgreSQL 16 holds four schemas, each with one job. Migrations live in "
  "<font name='Mono'>tenderai/db/sql</font> and are applied in order by <font name='Mono'>tenderai migrate</font>.")
table(["Schema", "Purpose", "Written by"], [
    ["raw", "Exact API payloads (JSONB), keyed by (entity, id), with change hash and soft-delete flag; plus dictionaries", "ingest"],
    ["core", "Typed, cleaned, linked entities, rebuilt deterministically from raw in one transaction", "etl/transform.sql, dq.sql"],
    ["mart", "Analytical tables the agent reads: prices, deviations, statistics, volumes, DQ summary", "etl/marts.sql"],
    ["ops", "Ingestion runs, per-BIN reconciliation, watermarks, miss lists", "ingest"],
], [18, 112, 40])
h2("Core tables")
table(["Table", "Grain", "Key columns and notes"], [
    ["core.plan_point", "plan line version", "is_latest per rootrecord_id; enstru_code; unit_code -> base_unit; count_base, price_base; kato_code + kato_source; url"],
    ["core.announcement", "announcement", "number_anno, org_bin, publish_date, status, url"],
    ["core.lot", "lot", "plan_point_id (pointList[0]); unit_price_base; in_scope (plan or publish year in 2024–2026); name_clean"],
    ["core.contract", "contract version", "is_latest (not superseded by a child); sum without VAT; supplier_biin; url"],
    ["core.contract_unit", "contract line", "item_price without VAT; unit_price_base; quantity_base; lot_id; plan_point_id"],
    ["core.subject", "organisation", "biin, name_ru, kato_code, is_target"],
    ["core.ref_unit / ref_kato / ref_enstru / ref_status", "dictionaries", "ref_unit has base_unit and factor from units.py"],
    ["core.cpi", "month × region × category", "Official CPI level (Dec 2020 = 100) and month-on-month change"],
    ["core.dq_issue", "(entity, obj_id, rule)", "severity error / warning; detail JSONB"],
    ["core.target_customer", "BIN", "The assignment's 27 customers"],
], [44, 30, 96])
h2("Marts")
table(["Mart", "Grain", "Used by"], [
    ["mart.price_obs", "one priced observation: a contract line, or a lot with no contract", "all price tools, Fair Price, search"],
    ["mart.price_stats", "ENSTRU × unit × (all years / per year)", "price_stats tool"],
    ["mart.price_deviation", "observation + leave-one-out peer weighted average + robust z", "price_anomalies tool"],
    ["mart.volume_yearly", "customer × ENSTRU × unit × year (planned)", "volume_anomalies tool"],
    ["mart.dq_summary", "entity × rule", "confidence blocks, monitoring"],
], [36, 84, 50])
h2("Access control")
bullets([
    "<font name='Mono'>tenderai</font> owns everything and is used by ingest and ETL.",
    "<font name='Mono'>tenderai_agent</font> (migration 004) can only SELECT from core and mart, with "
    "<font name='Mono'>default_transaction_read_only = on</font> and <font name='Mono'>statement_timeout = 15s</font>. "
    "All agent tools connect as this role through <font name='Mono'>AGENT_DATABASE_URL</font>.",
])

# 7 ------------------------------------------------------------------------
h1("7. ETL: raw to core to marts")
p("<font name='Mono'>tenderai/etl/run.py</font> rebuilds everything inside <b>one transaction</b>, so readers never "
  "see a half-built core. A full run takes about a minute.")
table(["Step", "What happens"], [
    ["1. Units", "Load MKEI units from raw.ref and map each to (base unit, factor) with units.py: g/kg/t -> kg, ml/L/m³ -> L, m/km -> m, thousand pcs -> pcs. Packaging units stay as their own base."],
    ["2. Targets", "Fill core.target_customer from TARGET_BINS in config.py."],
    ["3. transform.sql", "Build dictionaries, subjects, plan points (latest version per rootrecord), announcements, lots (in_scope flag), contracts (final version of each amendment chain) and contract lines. Infer a missing unit from the ENSTRU code only when at least 90% of at least 3 plan points agree."],
    ["4. Name cleaning", "lot_names.py normalises plan and lot names into name_clean: lot numbering, years, 'согласно технической спецификации', quotes, enumeration, Latin look-alike letters, and procedural lead words ('Приобретение', 'Закуп'). Contract lines inherit the plan's clean name."],
    ["5. dq.sql", "Apply data-quality rules into core.dq_issue: errors (non-positive price or quantity, missing unit or ENSTRU) are excluded from statistics; warnings are kept and lower confidence."],
    ["6. marts.sql", "Build price_obs, constant-price columns, price_stats, price_deviation, volume_yearly and dq_summary."],
    ["7. ANALYZE", "Refresh planner statistics after the rebuild."],
], [28, 142])
h2("Key cleaning rules")
bullets([
    "<b>Prices</b> are always without VAT, in KZT, per base unit (<font name='Mono'>price_base = price / factor</font>).",
    "<b>Observation priority</b>: a contract line (actual price) replaces the lot it came from; lots without a "
    "contract count as announced prices.",
    "<b>Versions</b>: only the final contract version and the latest non-deleted plan version are used.",
    "<b>Delivery location</b> falls back lot -> plan -> announcement -> customer's registered address, and "
    "<font name='Mono'>kato_source</font> records which one was used (about 70% of plan lines have no delivery KATO).",
    "<b>ENSTRU formats</b>: the old 37-character KPVED-based and new <font name='Mono'>NNNNNN.NNN.NNNNNN</font> "
    "codes are matched exactly first, then by prefix within the same format.",
])
h2("CPI loader (tenderai/etl/cpi.py)")
p("<font name='Mono'>tenderai load-cpi cpi_dataset</font> parses the official stat.gov.kz T-15-02-M monthly bulletins "
  "(33 workbooks covering January 2024 to September 2026). Sheet 1 gives national CPI and sheet 2 regional CPI, each in "
  "four categories: total, food, non-food and paid services. Columns are located by header text because layouts "
  "differ between years. The fixed-base level (December 2020 = 100) is stored, so no chaining error builds up. "
  "Region names map to 2-digit KATO codes (20 regions plus KZ for national). A CSV with a monthly index is "
  "accepted as an alternative source.")

# 8 ------------------------------------------------------------------------
h1("8. Analytics methods")
p("All metrics live in <font name='Mono'>tenderai/analytics</font> and <font name='Mono'>tenderai/etl/marts.sql</font>. "
  "<font name='Mono'>stats.py</font> provides the shared robust statistics: weighted quantiles and median, MAD, robust "
  "z = (x - median) / (1.4826 · MAD), and IQR bounds.")
h2("8.1 Fair Price (analytics/fair_price.py)")
p("For a target lot with ENSTRU code <i>e</i>, base unit <i>u</i>, delivery city <i>c</i> in region <i>r</i>, "
  "date <i>t</i> and unit price <i>p</i>:")
table(["Step", "Rule"], [
    ["1. Comparable set", "From mart.price_obs, take the first level with N ≥ 5: same city -> same region -> national -> coarser ENSTRU prefix. Always other customers only, within 18 months, price > 0. If no level reaches 5, use the largest set and mark the answer preliminary."],
    ["2. Time adjustment", "p' = p<sub>i</sub> · I(t) / I(t<sub>i</sub>) · S(m<sub>t</sub>) / S(m<sub>i</sub>). I is the official CPI of the comparable's region and the item's CPI category (food, non-food, services, total by ENSTRU division). Without CPI, an internal procurement price index is used. S is a seasonal index, applied only when N ≥ 30 across ≥ 6 months."],
    ["3. Regional coefficient", "K(r) = w · median(p' in r) / median(p') + (1 - w), with w = n<sub>r</sub> / (n<sub>r</sub> + 10). Prices are moved to the target region; shrinkage stops tiny regions producing extreme coefficients."],
    ["4. Fair price", "FairPrice = median of adjusted prices; fair band = [Q1, Q3]. The quantity-weighted mean is reported alongside."],
    ["5. Verdict", "deviation = (p - FairPrice) / FairPrice. Overpriced if > +30% or z > 3; underpriced (dumping risk) if < -30% or z < -3; fair if inside the band or within ±10%; otherwise elevated or below market."],
    ["6. Confidence", "0.35 · min(1, ln N / ln 30) + 0.25 · 1/(1 + IQR/median) + 0.20 · level score + 0.10 · (1 - DQ rate) + 0.10 · region known. High ≥ 0.70, medium ≥ 0.45, otherwise low; capped at low when N < 5."],
], [32, 138])
p("<b>Quantity of the lot.</b> The fairness answer also judges the lot's quantity: the customer's planned volume "
  "of that ENSTRU × unit in the lot's year against the median of its earlier years (mart.volume_yearly). Growth "
  "above 1.5× is reported as atypical; without an earlier year the answer says there is nothing to compare with.")
h2("8.2 Price anomalies (mart.price_deviation)")
bullets([
    "Every observation is converted to constant prices with the official CPI of its own region and category.",
    "The baseline is the quantity-weighted average of the <b>other</b> observations of the same ENSTRU × unit "
    "(leave-one-out), so an outlier cannot pull its own baseline up and hide itself. Groups need at least 3 peers.",
    "deviation = price / peer average - 1. The 30% rule alone flags about 30% of goods positions because ENSTRU "
    "codes are broad, so each observation also gets a robust z of log price within its group.",
    "<b>Confirmed</b> anomalies satisfy both the threshold and z &gt; 3. They are listed first, ranked by estimated "
    "overpayment (amount - peer average × quantity).",
    "Services and works priced per '1 service' are excluded by default, since a per-unit price is not comparable.",
    "'Below market' questions (\"ниже рынка\", \"демпинг\") use the same method in the other direction "
    "(deviation below −threshold, z &lt; −3), ranked by the deepest discount.",
])
h2("8.3 Volume anomalies (analytics/volumes.py)")
bullets([
    "Series: customer × ENSTRU × base unit × year, using <b>planned</b> quantity from the latest plan versions.",
    "baseline = median of previous years; flagged when ratio = qty / baseline &gt; 1.5 and, with ≥ 2 previous years, "
    "the robust z of log quantity against the customer's own history is &gt; 3.",
    "<b>Confirmed</b> tier: flagged, statistically extreme (z &gt; 3, or ≥ 3× growth with one previous year) and "
    "material (extra planned spend ≥ 1,000,000 KZT). On current data that is 482 of 12,898 series (3.7%).",
    "An Isolation Forest (scikit-learn) on [ln ratio, ln amount ratio, ln unit-price ratio, number of plan points] "
    "gives a secondary score that is shown but never flags anything by itself.",
])
h2("8.4 Data quality and confidence in every answer")
p("Each answer reports the DQ exclusion rate of the entities it uses; error rows never enter statistics. Fair Price "
  "has its own confidence (step 6). Every other answer carries an aggregate confidence: 0.6 · min(1, ln N / ln 30) "
  "+ 0.4 · (1 − DQ exclusion rate), labelled high ≥ 0.70, medium ≥ 0.45, low otherwise, and always low under 5 objects.")

# 9 ------------------------------------------------------------------------
h1("9. The agent")
p("<font name='Mono'>tenderai/agent/agent.py</font> orchestrates one question in six steps. Timings for routing, "
  "the tool and the verdict are returned with every answer.")
story.append(request_diagram())
caption("Figure 3. Lifecycle of one question through Agent.ask().")
h2("9.1 Router (agent/router.py)")
bullets([
    "<b>Rules always run first.</b> Language detection (Kazakh-specific letters or at least 2 Kazakh keywords) and "
    "regexes for hard facts: 12-digit BINs, ENSTRU codes in both formats, lot numbers (Russian and Kazakh word "
    "order), announcement numbers, percentage thresholds and 'top-N'. Years: '2025 compared with 2024' means 2025, "
    "a range keeps all years, and a year outside 2024–2026 is refused. A unit named in the question ('в пачках') "
    "filters by that unit; 'ниже рынка' / 'демпинг' sets the anomaly direction; questions with nothing about "
    "procurement are flagged as off-topic. Keyword rules give a fallback intent.",
    "<b>Then the LLM</b> fills a JSON schema (intent, item_text, customer_text, year, threshold_pct, top_k) using "
    "Ollama's constrained output, with a few-shot Russian system prompt. Kazakh item names are translated to Russian.",
    "<b>Hard facts override the LLM.</b> A detected lot number in a fairness question forces fair_price; a "
    "fairness intent without a lot number falls back to the rules; ranking questions ('какой заказчик больше всех') "
    "and 'сравни 2024 и 2025' are comparisons. Generic words such as 'goods' or 'purchases' are discarded as item "
    "names, and a lot number copied into the customer field is removed.",
    "If the LLM fails or returns invalid JSON, the rule route is used unchanged.",
])
h2("9.2 Resolve and dispatch")
bullets([
    "<b>Customers</b>: several names are split ('A и B', 'A; B'); each is matched word by word against the 27 "
    "customers after collapsing official forms ('Управления делами Президента' → 'УДП'), and yields its single best "
    "match (the organisation itself rather than its branch). A name that matches nothing is <b>refused</b>, never "
    "widened to all 27 customers, whose totals would otherwise be credited to it. Without the LLM, customer names "
    "are found in the question text by matching runs of words against the 27 names; naming another kind of "
    "organisation ('акимат', 'министерство') is refused.",
    "<b>Items</b>: matched to ENSTRU codes in tiers: the name equals the text, contains it as words, every word "
    "matches (endings ignored), or trigram similarity ≥ 0.45. Within a tier the most-purchased code wins, so "
    "'бензин' is motor petrol and not 'бензидамин'. Each code is represented by its most common name. Search "
    "covers every code sharing the item's name (laptops sit under four codes).",
])
p("Then exactly one tool runs:")
table(["Intent", "Tool (agent/tools.py)", "Notes"], [
    ["fair_price", "fair_price_for_lot(lot_ref)", "Needs a lot number; without one the answer asks for it"],
    ["price_anomaly", "price_anomalies(threshold, enstru, text, bins, year, direction, top_k)",
     "Default threshold 30%; direction 'under' for below-market questions"],
    ["volume_anomaly", "volume_anomalies(bins, enstru, text, year, top_k)", "Uses analytics/volumes.py"],
    ["compare", "compare_customers(bins, year, enstru, text, unit) / compare_years(years, …)",
     "Per-customer comparison, or per-year when two or more years are named"],
    ["analytics", "price_stats(enstru, unit, year)", "Falls back to search when the item is not confidently resolved"],
    ["search", "search_procurements(text, bins, codes, year, top_k, unit)", "Default for everything else"],
], [26, 76, 68])
p("Every tool returns a <font name='Mono'>ToolResult</font>: tool name, params, method, n_analyzed, summary "
  "figures, Top-K rows (each with an ID and portal URL), comparison baselines, DQ figures, limitations, a found "
  "flag, and for table-style answers the largest purchases behind the table. SQL is built only from fixed templates "
  "with bound parameters.")
h2("9.3 Verdict and fact validator (agent/validator.py)")
bullets([
    "The LLM receives a system prompt in the answer language, the question, a draft brief and the tool facts as "
    "JSON. It writes 1–3 sentences (max 220 tokens, temperature 0, fixed seed, thinking off).",
    "The validator extracts every number, BIN and date, including Russian and Kazakh formats such as "
    "'1 234 567,8', '12,5 млн' and '30%'. Each must match a number in the facts, allowing common derived forms "
    "(rounding, ×100 for shares, thousands, millions, billions) within a 0.6% tolerance. Small integers 0–3 are allowed.",
    "Claim checks then look at how numbers are used: the compared total cannot be presented as the flagged count "
    "('347 series grew' when 347 were compared), a price outside Q1–Q3 cannot be called inside it, and the Q1–Q3 "
    "range cannot be called a confidence interval. An organisation's number labelled 'ИНН' is corrected to 'БИН'.",
    "On failure the LLM gets one corrective retry that names the problem. If that also fails, the template verdict "
    "from composer.py is used and verdict_source is set to 'template'.",
])
h2("9.4 Composer (agent/composer.py, i18n.py)")
p("Renders Markdown in Russian or Kazakh with six fixed sections:")
table(["#", "Section", "Source"], [
    ["1", "Verdict", "LLM (validated) or template"],
    ["2", "Search parameters and data used: period, BINs, filters, item resolution, N", "tool result"],
    ["3", "Analytics and comparison: baselines, averages, medians, deviations", "tool result"],
    ["4", "Metric: the method in one paragraph", "tool result"],
    ["5", "Confidence and limitations: score, DQ rates, applicable limitations", "tool result"],
    ["6", "Top-K: object IDs with direct goszakup.gov.kz links", "tool result"],
], [8, 112, 50])
p("A refusal keeps the same structure: its parameters show what was asked and that no search was run, and the "
  "verdict states the reason.")
h2("9.5 LLM client (agent/llm.py)")
bullets([
    "Uses Ollama's native <font name='Mono'>/api/chat</font>, not the OpenAI-compatible endpoint, because it supports "
    "JSON-schema constrained output and switching Qwen3.5's thinking mode off.",
    "Default model <font name='Mono'>qwen3.5:9b</font>; <font name='Mono'>num_gpu = 99</font> forces every layer onto "
    "the GPU. On an RTX 5070 Laptop (8 GB) this uses about 6.4 GB and is about 25% faster than Ollama's auto-fit.",
    "<font name='Mono'>available()</font> checks /api/tags at start-up; if the model is missing, the agent runs "
    "without an LLM.",
    "Typical latency: about 1.5 s routing, 0.1–2 s tool, 2–4 s verdict. The first request after the model unloads "
    "takes about 45 s.",
])

# 10 -----------------------------------------------------------------------
h1("10. API, web chat and worker")
table(["Endpoint", "Behaviour"], [
    ["GET /", "Serves the static web chat (api/static/chat.html)"],
    ["POST /ask", "Body {question: 3–1000 chars}. Returns the full AgentAnswer: markdown, route, tool result, verdict_source, validation, timings. Requests are serialised with an asyncio lock because there is one local GPU. Database errors become HTTP 503."],
    ["GET /health", "Database status, data_as_of (journal watermark), lag_hours, freshness_sla_ok (lag ≤ 24 h), last ingest run, and LLM model and availability"],
], [26, 144])
p("The <b>worker</b> (<font name='Mono'>tenderai worker</font>) runs the incremental sync once at start-up, then every "
  "3 hours with APScheduler in the Asia/Almaty timezone. It allows a single instance, coalesces missed runs and "
  "logs failures without crashing, so the next tick retries.")

# 11 -----------------------------------------------------------------------
h1("11. Deployment")
h2("Docker Compose (deploy/docker-compose.yml)")
table(["Service", "Image", "Role"], [
    ["postgres", "postgres:16.4-alpine", "Database; 1 GB shm, tuned buffers; host port 5434"],
    ["ollama", "ollama/ollama:0.40.3", "Local LLM on the NVIDIA GPU; keeps the model loaded 30 min; port 11434"],
    ["migrate", "tenderai:0.1.0", "One-off: applies migrations once Postgres is healthy"],
    ["api", "tenderai:0.1.0", "FastAPI on port 8010; starts after migrate succeeds"],
    ["worker", "tenderai:0.1.0", "Incremental sync every 3 h"],
], [22, 42, 106])
p("All app services share one image built from <font name='Mono'>deploy/Dockerfile</font>: Python 3.12 slim, the "
  "package installed with pip, running as non-root user 10001, no secrets baked in. Host ports can be changed with "
  "PG_HOST_PORT, OLLAMA_HOST_PORT and API_HOST_PORT.")
h2("Kubernetes (deploy/k8s/)")
table(["Manifest", "Contents"], [
    ["00-namespace-secrets.yaml", "Namespace, Secret (OWS_TOKEN, DB passwords) and ConfigMap"],
    ["10-postgres.yaml", "Postgres StatefulSet (1 replica) with a PVC, and a Service"],
    ["20-ollama.yaml", "PVC for model weights; Ollama Deployment with nvidia.com/gpu: 1 and 16 Gi memory; Service"],
    ["30-app.yaml", "Migrate Job; API Deployment (1 replica, readiness on /health); Service; incremental CronJob '0 */3 * * *' with concurrencyPolicy Forbid"],
], [44, 126])
p("The API is kept at one replica because there is one local LLM; the LLM must be scaled before the API.")

# 12 -----------------------------------------------------------------------
h1("12. Security")
bullets([
    "The OWS token exists only in the environment, a Docker or Kubernetes secret, or the gitignored .env. It is a "
    "SecretStr in settings and never logged.",
    "The agent's database role is read-only, limited to core and mart, with a 15 s statement timeout.",
    "The LLM never writes SQL. Tools use fixed SQL templates with bound parameters.",
    "Prompt-injection surface is small: user-supplied lot names reach the LLM only inside a JSON facts block, the "
    "LLM has no tools, SQL or network access, and its output is validated.",
    "Containers run as non-root, and .dockerignore excludes .env, data and fixtures from the image.",
])

# 13 -----------------------------------------------------------------------
h1("13. Testing and evaluation")
h2("Unit tests (pytest)")
table(["Test file", "Covers"], [
    ["test_router.py", "Intent rules, language detection, hard facts, years, units, direction, off-topic"],
    ["test_name_matching.py", "Splitting customer names, collapsing official forms, word endings"],
    ["test_validator.py", "Number, BIN and date parsing in RU/KZ formats; claim checks; ИНН → БИН"],
    ["test_composer.py", "Six-section rendering, template verdicts, refusals, Top-K examples, quantity check"],
    ["test_fair_price.py", "Fair Price steps with hand-checkable numbers"],
    ["test_volumes_and_index.py", "Volume anomaly rules and the internal price index (mix-effect removal)"],
    ["test_lot_names.py", "Name cleaning"],
], [48, 122])
p("Fixtures in <font name='Mono'>tests/fixtures/ows</font> hold sample API responses and a GraphQL schema "
  "introspection snapshot, which also supports diffing if OWS changes its schema.")
h2("Evaluation (eval/)")
p("Question sets rebuilt from the current data with plain SQL: <font name='Mono'>build_heldout.py</font> holds 23 "
  "fresh questions written after the agent was finished, on customers, items and wordings used nowhere else; the agent "
  "is not changed to pass them. <font name='Mono'>build_corpus.py</font> holds 54 hand-picked questions (customers by "
  "name, everyday items, lots with a known verdict, refusals and traps) that were also used during development, see "
  "eval/corpus.md; <font name='Mono'>build_golden.py</font> generates 40 templated questions. "
  "<font name='Mono'>run_eval.py</font> checks each expected fact; facts the question already contains are not "
  "scored, and Fair Price verdicts and refusals are scored as separate checks. "
  "<font name='Mono'>check_leakage.py</font> verifies that no evaluation question is among the LLM's few-shot examples "
  "and no evaluation identifier or figure is in the code. The assignment's target is fact accuracy of at least 85%.")
table(["Set and mode", "Cases", "Fact accuracy", "Verdict / refusal checks", "Intent accuracy"], [
    ["Held-out, with Qwen3.5 9B (3 runs)", "23", "86.4% (171 / 198)", "100% (21 / 21)", "95.7%"],
    ["Corpus, with Qwen3.5 9B (3 runs)", "54", "97.3% (438 / 450)", "100% (39 / 39)", "96.3%"],
    ["Golden set, with Qwen3.5 9B", "40", "100% (89 / 89)", "—", "100%"],
    ["Held-out, rules only (no LLM)", "23", "54.5% (36 / 66)", "85.7% (6 / 7)", "82.6%"],
    ["Corpus, rules only (no LLM)", "54", "61.3% (92 / 150)", "100% (13 / 13)", "94.4%"],
    ["Golden set, rules only (no LLM)", "40", "100% (89 / 89)", "—", "100%"],
], [52, 16, 36, 36, 30])
p("One product is often split into codes that differ only in the plan description (petrol АИ-92 / АИ-95, summer / "
  "winter diesel, A4 / A3 paper, battery types). The agent reads these descriptions (mart.enstru_variant): a named "
  "variant selects its code, a bare product name selects every variant, and prices are reported per variant. Before "
  "this, the held-out set scored 71.0% and the corpus 80.7%. Remaining misses: Kazakh item names transliterated "
  "instead of translated ('пияз'), a customer name the LLM also copies into the item field, and abbreviations "
  "('дизтопливо').")
p("<b>Stability.</b> The LLM is not fully deterministic, so <font name='Mono'>run_eval.py --repeats 3</font> asks "
  "every question three times, and <font name='Mono'>build_paraphrases.py</font> words each mandatory question type "
  "(plus one analytics question) 5–7 ways in Russian and Kazakh, all expecting the same facts. Held-out and corpus: "
  "identical answers in all three runs. Paraphrases: 24 × 3, 94.8% (219 / 231), identical in all runs; the one miss is "
  "the abbreviation 'летнее дизтопливо'.")
p("Without the LLM, free-text item names cannot be turned into product codes, which is why the corpus scores "
  "lower in that mode; questions that give a code, a lot number or a BIN are unaffected. The corpus prompted the "
  "fixes behind its earlier 100% (first run with the LLM: 66.9%). A review on 14 October found reference answers that "
  "treated one variant code as the whole product and few-shot examples that repeated evaluation questions; after "
  "correcting both and tightening the scoring the corpus scored 80.7%; telling product variants apart raised it to "
  "97.3%. The held-out set is the honest measure.")

# 14 -----------------------------------------------------------------------
h1("14. Risks and limitations")
table(["Area", "Limitation", "Mitigation"], [
    ["Data", "Two ENSTRU formats with no mapping table", "Exact match, then prefix fallback; comparison level shown"],
    ["Data", "Broad ENSTRU codes: wide price spread within one code", "Leave-one-out baseline, robust z confirmation, median and IQR"],
    ["Data", "≈70% of plan lines lack a delivery location", "KATO fallback chain with recorded source; stated as a limitation"],
    ["Data", "Services priced per '1 service'", "Excluded from unit-price anomalies by default"],
    ["Data", "Volume anomalies use planned, not executed, quantities", "Latest plan versions; stated in every volume answer"],
    ["Data", "Short history: 2025 has a one-year baseline", "z-test only with ≥ 2 previous years; confirmed tier"],
    ["Data", "Only 27 customers, so peers are the other 26", "N and level shown; more BINs is a config change"],
    ["Data", "CPI bulletins cover 01.2024–09.2026 only", "Nearest covered month used; add new bulletins and re-run load-cpi"],
    ["API", "Token expiry or revocation (403)", "Clear error; /health shows last ingest status"],
    ["API", "Transient timeouts and 5xx", "Backoff, idempotent upserts, per-BIN reconciliation"],
    ["API", "Journal delays", "2 h overlap; a nightly backfill re-run reconciles"],
    ["API", "Journal volume spikes (4.6 M changes on one day)", "Deletions only (action=D); progress logged; a sync "
     "still fits the 3 h cycle"],
    ["AI", "Hallucinated figures", "LLM never computes; validator with one retry, then template"],
    ["AI", "Misclassified intent or wrong item match", "Hard-fact overrides; tiered item matching; resolved code shown to the user"],
    ["AI", "Wrong customer (unknown or similar names)", "One best match per name; an unknown name is refused, never widened to all 27"],
    ["AI", "Right number, wrong meaning in the verdict", "Claim checks with a corrective retry, then the template verdict"],
    ["AI", "Weaker Kazakh in a 9B model", "Fixed Kazakh templates; only the verdict is generated"],
    ["AI", "One 8 GB GPU", "Requests serialised; template answers if the LLM is down"],
], [14, 76, 80])
callout("A price 'anomaly' is a signal for review, not proof of a violation. The Fair Price band reflects what the "
        "other 26 customers actually paid; it is not an external market benchmark.")

# 15 -----------------------------------------------------------------------
h1("15. Operating the system")
h2("First run (local)")
code("""
cp .env.example .env                       # set OWS_TOKEN
docker compose -f deploy/docker-compose.yml up -d postgres ollama
docker compose -f deploy/docker-compose.yml exec ollama ollama pull qwen3.5:9b
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
python -m tenderai.cli migrate
python -m tenderai.cli backfill            # ~25 min
python -m tenderai.cli load-cpi cpi_dataset
python -m tenderai.cli etl                 # ~1 min
python -m tenderai.cli serve               # http://127.0.0.1:8010
python -m tenderai.cli worker              # incremental sync every 3 h
python eval/build_corpus.py && python eval/run_eval.py eval/corpus.jsonl
""")
h2("CLI reference")
table(["Command", "Purpose"], [
    ["migrate", "Apply SQL migrations from tenderai/db/sql"],
    ["backfill [--bins] [--entities] [--no-refs] [--no-subjects]", "Full load of the target BINs for 2024–2026"],
    ["incremental [--no-etl]", "Changes since the last run plus journal deletions, then rebuild core and marts"],
    ["etl [--no-marts]", "Rebuild core and marts from raw"],
    ["load-cpi &lt;path&gt;", "Load official CPI from a folder of bulletins, or a CSV"],
    ["worker", "Incremental sync every 3 hours"],
    ["ask \"...\" [--no-llm] [--json]", "Ask from the terminal; --json prints the full structured answer"],
    ["serve [--host] [--port]", "Run the HTTP API and web chat (default 127.0.0.1:8010)"],
], [70, 100])
h2("Configuration (environment variables)")
table(["Variable", "Default", "Meaning"], [
    ["OWS_TOKEN", "(required)", "Bearer token for the OWS API"],
    ["OWS_BASE_URL", "https://ows.goszakup.gov.kz", "API base URL"],
    ["OWS_CONCURRENCY / OWS_PAGE_SIZE", "4 / 200", "Parallel requests and GraphQL page size"],
    ["DATABASE_URL", "tenderai @ localhost:5434/tenderai", "Owner role for ingest and ETL"],
    ["AGENT_DATABASE_URL", "tenderai_agent @ localhost:5434/tenderai", "Read-only role for agent tools"],
    ["LLM_BASE_URL / LLM_MODEL", "http://localhost:11434 / qwen3.5:9b", "Ollama server and model"],
    ["LLM_NUM_GPU", "99", "Layers to place on the GPU"],
], [56, 62, 52])
h2("Monitoring")
bullets([
    "<font name='Mono'>GET /health</font>: database, LLM availability, lag_hours and the 24-hour freshness flag.",
    "<font name='Mono'>ops.ingest_run</font> and <font name='Mono'>ops.ingest_count</font>: run history and API total "
    "versus rows fetched per BIN.",
    "<font name='Mono'>mart.dq_summary</font>: data-quality issue counts by entity and rule.",
    "<font name='Mono'>ollama ps</font>: confirms the model is fully on the GPU (PROCESSOR column).",
])


def build(out):
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=20 * mm,
                            bottomMargin=18 * mm, title="TenderAI Architecture", author="TenderAI",
                            subject="Architecture of the TenderAI procurement analytics agent")
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)


if __name__ == "__main__":
    build(sys.argv[1])
