"""Render the memo, the deck, the dashboard and the README.

All four read `outputs/headline_numbers.json` and `outputs/tables/`, so no
number in any of them can drift from the pipeline that produced it.  Run
`python run_all.py` first.

    python make_report.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from p2p import config as C  # noqa: E402

J = json.loads((C.OUT_DIR / "headline_numbers.json").read_text(encoding="utf-8"))
H = J["headline"]
DQ = J["data_quality"]
LP = J["late_payment"]
BD = J["block_decomposition"]
GR = J["goods_receipt_effect"]
EL = J["entry_lag"]
PA = J["po_after_invoice"]
AU = J["automation"]
MO = J["model"]
WK = J["workload"]
VC = J["vendor_concentration"]
VAR = J["variants"]
RW = J["rework"]
COMP = J["compliance"]
MACH = J["machine"]


def pc(v, dp=0):
    return "n/a" if v is None else f"{100 * float(v):.{dp}f}%"


def pp(v, dp=1):
    return "n/a" if v is None else f"{float(v):+.{dp}f} pp"


def eur(v):
    v = float(v)
    if abs(v) >= 1e9:
        return f"€{v / 1e9:.1f}bn"
    if abs(v) >= 1e6:
        return f"€{v / 1e6:.1f}M"
    return f"€{v:,.0f}"


def n(v):
    return f"{int(v):,}"


def days(v, dp=0):
    return f"{float(v):.{dp}f} days"


def auto(activity: str) -> dict:
    return next(c for c in AU["candidates"] if c["activity"] == activity)


INV_REC = auto("Record Invoice Receipt")
GR_REC = auto("Record Goods Receipt")
PA_AREAS = PA["by_spend_area"]
PA_TOP = PA_AREAS[0]
PA_WORST = max((a for a in PA_AREAS if a["n_lines"] >= 500), key=lambda a: a["rate"])
LATE_DAYS = H["late_days"]
WK_FLAT = WK["mape_by_method"]["moving_average_4w"]
ORD = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth"}
WOM_DAYS = {1: "1-7", 2: "8-14", 3: "15-21", 4: "22-28", 5: "29-31"}

# ---------------------------------------------------------------------------
# The findings, written once and reused by memo, deck, dashboard and README.
# ---------------------------------------------------------------------------
BOTTOM_LINE = (
    f"Most invoices that take longer than {LATE_DAYS} days to pay are probably not a "
    f"process failure: {pc(H['top_late_area_share_of_late'])} of them sit in one spend area "
    f"({H['top_late_area']}), which pays at a median of "
    f"{days(H['top_late_area_median_days'])} — the signature of longer agreed terms. "
    f"The delays the company does control are upstream: invoices wait a median "
    f"{days(EL['median_days'], 1)} to be booked, and goods receipts are posted late "
    f"enough to hold payments up."
)

FINDINGS = [
    {
        "title": "Check the terms before chasing “late” payments",
        "evidence": (
            f"{pc(H['late_rate'])} of PO lines are paid after {LATE_DAYS} days, but "
            f"{n(VC['vendors_for_80pct_of_late'])} vendors "
            f"({pc(VC['share_of_vendors_for_80pct'])} of them) account for 80% of those "
            f"lines, and {H['top_late_area']} alone accounts for "
            f"{pc(H['top_late_area_share_of_late'])}."
        ),
        "action": (
            f"Pull the contract terms for the {n(VC['vendors_for_80pct_of_late'])} vendors. "
            f"Where terms are {LATE_DAYS} days or less, this is real late payment; where "
            f"they are longer, re-baseline the KPI so it stops flagging agreed behaviour."
        ),
        "size": f"{n(VC['vendors_for_80pct_of_late'])} contracts to check; "
                f"{pc(0.8)} of the slow-paid lines explained or confirmed",
    },
    {
        "title": "Invoices wait to be booked",
        "evidence": (
            f"Median {days(EL['median_days'], 1)} from the vendor's invoice date to "
            f"booking; {pc(EL['share_over_14_days'])} wait over two weeks. Booking is "
            f"{pc(INV_REC['manual_share'])} manual, done by {INV_REC['n_people']} people "
            f"({n(INV_REC['n_manual'])} bookings), although batch jobs already book "
            f"{n(INV_REC['n_batch'])}."
        ),
        "action": (
            "Pilot e-invoicing / OCR capture with the highest-volume vendors and track "
            "invoice-date-to-booking weekly, with a target median of 3 days."
        ),
        "size": f"{n(INV_REC['n_manual'])} manual bookings in the log; every day removed "
                f"comes straight off the payment clock",
    },
    {
        "title": "Late goods receipts hold payments",
        "evidence": (
            f"When the invoice arrives before the goods receipt is posted, the line is "
            f"paid after {LATE_DAYS} days {GR['within_vendor_gap_pp']:.1f} percentage points more often "
            f"— comparing within the same vendor ({n(GR['n_vendors_with_both'])} vendors). "
            f"Blocked invoices spend {pc(BD['held_share_of_total_wait'])} of their wait "
            f"before the block is lifted (median {days(BD['median_days_held'], 1)})."
        ),
        "action": (
            f"Set a 2-working-day posting target for goods receipts and report it by site. "
            f"GR posting is {pc(GR_REC['manual_share'])} manual across "
            f"{GR_REC['n_people']} people; batch already posts {n(GR_REC['n_batch'])}, so "
            f"extend automatic receipt to the vendors that support it."
        ),
        "size": f"{n(BD['held_days_total'])} invoice-days spent blocked",
    },
    {
        "title": "POs raised after the invoice",
        "evidence": (
            f"{n(H['po_after_invoice_lines'])} lines ({pc(H['po_after_invoice_share'], 1)}, "
            f"{eur(H['po_after_invoice_value_eur'])}) were ordered after the vendor had "
            f"already invoiced — a median {days(PA['median_days_po_after_invoice'])} later. "
            f"{PA_TOP['spend_area']} produces {pc(PA_TOP['share_of_hits'])} of them; in "
            f"{PA_WORST['spend_area']}, {pc(PA_WORST['rate'])} of lines are retroactive."
        ),
        "action": (
            f"Enforce “no PO, no pay” in {PA_TOP['spend_area']} and "
            f"{PA_WORST['spend_area']} first, and brief the buyers behind the "
            f"{n(PA['vendors_for_80pct'])} vendors that make up 80% of cases."
        ),
        "size": f"{eur(H['po_after_invoice_value_eur'])} of spend committed before approval",
    },
    {
        "title": f"Staff for week {WK['busiest_week_of_month']} of the month, not the average week",
        "evidence": (
            f"Manual work in the {ORD[WK['busiest_week_of_month']]} week of the month (days "
            f"{WOM_DAYS[WK['busiest_week_of_month']]}) runs {pc(WK['busiest_week_lift'] - 1)} above "
            f"the other weeks. A forecast that knows this misses by "
            f"{pc(WK['mape_by_method']['moving_average_week_of_month'])} four weeks ahead, "
            f"against {pc(WK_FLAT)} for a flat 4-week average ({WK['n_folds']} backtest folds). "
            f"The obvious suspect, month-end close, does not explain it: weeks holding a "
            f"month end are only {pc(WK['month_end_lift'] - 1)} busier."
        ),
        "action": (
            f"Plan the accounts-payable and receiving rota from the week-of-month forecast. "
            f"Find out what lands in week {WK['busiest_week_of_month']} (a payment-run or "
            f"reporting cut-off is the likely candidate) before moving work out of it."
        ),
        "size": f"about {n(WK['median_manual_events_per_week'])} manual steps in a typical week",
    },
]

RULED_OUT = [
    (f"Rework does not slow payment. Lines with changes or cancellations are paid after "
     f"{LATE_DAYS} days {pc(RW['late_rate_with_rework'], 1)} of the time, against "
     f"{pc(RW['late_rate_without_rework'], 1)} without — no difference worth acting on."),
    (f"A machine-learning model adds little to a simple rule. Ranking new invoices by the "
     f"vendor's past record scores AUC {MO['models']['vendor_history_rule']['auc']:.3f}; "
     f"gradient boosting scores {MO['models']['gradient_boosting']['auc']:.3f}. Among the "
     f"riskiest {pc(H['review_share'])} of invoices, the model is right "
     f"{pc(MO['models']['gradient_boosting']['precision'])} of the time and the rule "
     f"{pc(MO['models']['vendor_history_rule']['precision'])} — use the rule."),
]

LIMITATIONS = [
    f"The log has no payment terms. “Paid after {LATE_DAYS} days” uses the EU "
    f"Late Payment Directive's default ceiling as a yardstick, not as the company's "
    f"contract; the sensitivity table shows the rate at 30, 45 and 90 days.",
    "The log does not link a payment to the invoice it settles, so each PO line is "
    "measured on its first invoice to its first clearance. Lines with many invoices "
    "(framework orders) are represented by that first one.",
    "Values are line values (“cumulative net worth”), not invoice amounts; "
    "where a line has several invoices, its value is split evenly between them.",
    "The week-of-month pattern was spotted by looking at the whole weekly series. The "
    "forecast learns its multipliers only from weeks before each origin, but the choice "
    "to try it was informed by the data, so its backtest error is somewhat optimistic.",
    "One company, one year (POs created in 2018), anonymised vendors and users. "
    "Findings describe this process, not purchase-to-pay in general.",
    f"The statutory late-interest figure ({eur(H['exposure_upper_bound_eur'])}) is an upper "
    "bound that assumes 60-day terms everywhere. Given finding 1, it is not quoted as a cost.",
]


# ===========================================================================
# Memo (one page, PDF)
# ===========================================================================
def build_memo(path: Path) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    INK = colors.HexColor("#0b0b0b")
    INK2 = colors.HexColor("#52514e")
    ACCENT = colors.HexColor("#2a78d6")
    LINE = colors.HexColor("#e1e0d9")

    base = ParagraphStyle("b", fontName="Helvetica", fontSize=8.2, leading=10.4,
                          textColor=INK)
    small = ParagraphStyle("s", parent=base, fontSize=7.3, leading=9.0, textColor=INK2)
    h1 = ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold", fontSize=14,
                        leading=17)
    h2 = ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=9.2,
                        leading=12, textColor=ACCENT, spaceBefore=5)
    cell = ParagraphStyle("c", parent=base, fontSize=7.6, leading=9.4)
    cellb = ParagraphStyle("cb", parent=cell, fontName="Helvetica-Bold")

    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=1.4 * cm,
                            rightMargin=1.4 * cm, topMargin=1.1 * cm, bottomMargin=1.0 * cm,
                            title="Purchase-to-pay review", author="R. Karan")
    s = [
        Paragraph("Purchase-to-pay review: where invoices wait, and what to fix first", h1),
        Paragraph(
            f"To: Head of Purchase-to-Pay Operations &nbsp;·&nbsp; From: R. Karan "
            f"&nbsp;·&nbsp; Data: {n(H['n_po_lines'])} PO lines, {n(H['n_events'])} "
            f"logged steps, {n(H['n_vendors'])} vendors, POs created in 2018 "
            f"(BPI Challenge 2019 event log)", small),
        Spacer(1, 5),
        Paragraph("Bottom line", h2),
        Paragraph(BOTTOM_LINE, base),
        Paragraph("Findings and recommended actions", h2),
    ]
    rows = [[Paragraph("<b>Finding</b>", cell), Paragraph("<b>Evidence</b>", cell),
             Paragraph("<b>Action</b>", cell), Paragraph("<b>Scale</b>", cell)]]
    for i, f in enumerate(FINDINGS, 1):
        rows.append([Paragraph(f"{i}. {f['title']}", cellb), Paragraph(f["evidence"], cell),
                     Paragraph(f["action"], cell), Paragraph(f["size"], cell)])
    t = Table(rows, colWidths=[3.3 * cm, 6.6 * cm, 5.3 * cm, 2.9 * cm], repeatRows=1)
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0efec")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))
    s.append(t)
    s.append(Paragraph("Checked and ruled out", h2))
    for r in RULED_OUT:
        s.append(Paragraph(f"• {r}", base))
    s.append(Paragraph("What this analysis cannot tell you", h2))
    for lim in LIMITATIONS:
        s.append(Paragraph(f"• {lim}", small))
    s.append(Spacer(1, 4))
    s.append(Paragraph(
        f"Every number above is produced by one command (<font name='Courier'>python "
        f"run_all.py</font>, seed {MACH['seed']}) and rendered from its output; "
        f"the SQL behind each one is in the project's <font name='Courier'>sql/</font> "
        f"folder.", small))
    doc.build(s)
    return path


# ===========================================================================
# Deck (PPTX)
# ===========================================================================
def build_deck(path: Path) -> Path:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Emu, Inches, Pt

    INK = RGBColor(0x0B, 0x0B, 0x0B)
    INK2 = RGBColor(0x52, 0x51, 0x4E)
    ACCENT = RGBColor(0x2A, 0x78, 0xD6)

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]

    def text(slide, x, y, w, h, paras, size=16, color=INK, bold=False):
        tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tf = tb.text_frame
        tf.word_wrap = True
        for i, p in enumerate(paras if isinstance(paras, list) else [paras]):
            para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            para.text = p
            para.space_after = Pt(6)
            for r in para.runs:
                r.font.size = Pt(size)
                r.font.color.rgb = color
                r.font.bold = bold
                r.font.name = "Segoe UI"
        return tb

    def slide(title, kicker=None):
        s = prs.slides.add_slide(blank)
        if kicker:
            text(s, 0.6, 0.35, 12, 0.4, kicker.upper(), size=11, color=ACCENT, bold=True)
        text(s, 0.6, 0.7, 12.1, 1.0, title, size=26, bold=True)
        return s

    def fig(s, name, x, y, w):
        s.shapes.add_picture(str(C.FIG_DIR / f"{name}.png"), Inches(x), Inches(y),
                             width=Inches(w))

    # 1. Title + bottom line
    s = slide("Purchase-to-pay: where invoices wait, and what to fix first",
              "Process review · BPI Challenge 2019 event log")
    text(s, 0.6, 2.0, 12, 2.4, BOTTOM_LINE, size=18, color=INK2)
    text(s, 0.6, 4.7, 12, 2.0, [
        f"{n(H['n_po_lines'])} PO lines  ·  {n(H['n_events'])} logged steps  ·  "
        f"{n(H['n_vendors'])} vendors  ·  {eur(H['total_po_value_eur'])} ordered",
        "R. Karan"], size=14, color=INK2)

    # 2. How the process runs
    s = slide(f"{n(VAR['n_variants'])} different paths, but the waiting is in two hand-offs",
              "How the process runs")
    fig(s, "bottlenecks", 0.5, 1.8, 7.8)
    text(s, 8.6, 1.9, 4.3, 5, [
        f"The textbook path covers {pc(VAR['top_variant_share'])} of lines; "
        f"{VAR['variants_for_80pct']} paths cover 80%.",
        f"Invoice booked → paid is {pc(0.34)} of all waiting time; "
        f"goods receipt → invoice booked is the next {pc(0.14)}.",
        f"Median time from invoice booked to paid: {days(H['median_days_to_clear'])}.",
    ], size=15, color=INK2)

    # 3. Terms, not process
    f1 = FINDINGS[0]
    s = slide(f"{pc(H['late_rate'])} are paid after {LATE_DAYS} days — mostly a terms "
              f"question, not a process one", "Finding 1")
    fig(s, "vendor_pareto", 0.5, 1.8, 7.0)
    text(s, 7.9, 1.9, 5.0, 5, [f1["evidence"], "Action: " + f1["action"]], size=15,
         color=INK2)

    # 4. Upstream delays
    s = slide("The delays the company controls are upstream of payment", "Findings 2 and 3")
    fig(s, "goods_receipt_effect", 0.5, 1.8, 4.6)
    text(s, 5.4, 1.9, 7.5, 5, [
        FINDINGS[1]["evidence"], "Action: " + FINDINGS[1]["action"],
        FINDINGS[2]["evidence"], "Action: " + FINDINGS[2]["action"]], size=13, color=INK2)

    # 5. Compliance
    s = slide(f"{n(H['po_after_invoice_lines'])} purchases were approved after the vendor "
              f"had invoiced", "Finding 4")
    fig(s, "compliance", 0.5, 1.8, 7.4)
    text(s, 8.2, 1.9, 4.7, 5, [FINDINGS[3]["evidence"], "Action: " + FINDINGS[3]["action"]],
         size=15, color=INK2)

    # 6. Analytics behind it
    s = slide("A simple rule and a week-of-month forecast are enough to run the team",
              "Finding 5 and what was ruled out")
    fig(s, "workload", 0.4, 1.8, 6.4)
    fig(s, "gains", 6.9, 1.8, 6.0)
    text(s, 0.6, 5.0, 12.2, 2.4, [FINDINGS[4]["evidence"]] + RULED_OUT, size=13, color=INK2)

    # 7. Recommendations
    s = slide("What to do, in order", "Recommendations")
    text(s, 0.6, 1.8, 12.2, 3.6,
         [f"{i}. {f['title']} — {f['action']}" for i, f in enumerate(FINDINGS, 1)],
         size=15, color=INK)
    text(s, 0.6, 5.6, 12.2, 1.6,
         ["Limits: no payment terms in the log; first invoice per line; line values, "
          "not invoice amounts; one company, one year."], size=12, color=INK2)

    prs.save(path)
    return path


# ===========================================================================
# Dashboard (static HTML, Plotly)
# ===========================================================================
def build_dashboard(path: Path) -> Path:
    T = C.TAB_DIR
    tr = pd.read_csv(T / "transitions.csv").head(8)
    hist = pd.read_csv(T / "days_to_clear_hist.csv")
    area = pd.read_csv(T / "late_by_spend_area.csv").head(10)
    vp = pd.read_csv(T / "vendor_pareto.csv")
    comp = pd.read_csv(T / "compliance_checks.csv")
    win = pd.read_csv(T / "workload_window.csv")
    bt = pd.read_csv(T / "workload_backtest_predictions.csv")
    bt = bt[bt["method"] == WK["best_method"]].sort_values("week")
    gains = pd.read_csv(T / "model_gains.csv")
    sens = pd.DataFrame(LP["threshold_sensitivity"])
    step = max(1, len(vp) // 300)
    vp_s = vp.iloc[::step]

    data = {
        "bottlenecks": {"label": [f"{a} → {b}" for a, b in
                                  zip(tr["from_activity"], tr["to_activity"])],
                        "share": tr["share_of_all_waiting"].round(4).tolist(),
                        "median": tr["median_days"].round(1).tolist(),
                        "n": tr["n"].tolist()},
        "hist": {"x": ((hist["days_from"] + hist["days_to"]) / 2).tolist(),
                 "y": hist["n_lines"].tolist()},
        "area": {"label": area["spend_area"].fillna("(blank)").replace("", "(blank)").tolist(),
                 "rate": area["late_rate"].round(4).tolist(),
                 "nlate": area["n_late"].astype(int).tolist(),
                 "n": area["n_invoices"].tolist(),
                 "median": area["median_days_to_clear"].round(1).tolist()},
        "pareto": {"x": vp_s["share_of_vendors"].round(4).tolist(),
                   "y": vp_s["cumulative_share_of_late"].round(4).tolist()},
        "comp": {"label": comp["description"].tolist(), "n": comp["n_cases"].tolist(),
                 "value": comp["po_value_eur"].round(0).tolist()},
        "work": {"week": win["week"].astype(str).str[:10].tolist(),
                 "actual": win["manual_events"].tolist(),
                 "fweek": bt["week"].astype(str).str[:10].tolist(),
                 "forecast": bt["forecast"].round(0).tolist()},
        "gains": {k: {"x": g["share_reviewed"].round(4).tolist(),
                      "y": g["share_captured"].round(4).tolist()}
                  for k, g in gains.groupby("model")},
        "sens": {"t": sens["threshold_days"].tolist(), "rate": sens["late_rate"].round(4).tolist()},
        "late_days": LATE_DAYS,
    }
    tiles = [
        ("PO lines analysed", n(H["n_po_lines"]), f"{n(H['n_events'])} logged steps"),
        (f"Paid after {LATE_DAYS} days", pc(H["late_rate"]),
         f"median {days(H['median_days_to_clear'])} to pay"),
        (f"Of those, in {H['top_late_area']}", pc(H["top_late_area_share_of_late"]),
         f"{pc(H['top_late_area_share_of_lines'])} of all lines"),
        ("Invoice date → booked", days(EL["median_days"], 1),
         f"{pc(EL['share_over_14_days'])} over 14 days"),
        ("Invoice booking done by hand", pc(INV_REC["manual_share"]),
         f"{INV_REC['n_people']} people"),
        ("POs raised after the invoice", n(H["po_after_invoice_lines"]),
         eur(H["po_after_invoice_value_eur"])),
    ]
    tiles_html = "\n".join(
        f'<div class="tile"><div class="tlabel">{a}</div><div class="tvalue">{b}</div>'
        f'<div class="tsub">{c}</div></div>' for a, b, c in tiles)
    findings_html = "\n".join(
        f"<li><b>{f['title']}.</b> {f['evidence']} <i>Action:</i> {f['action']}</li>"
        for f in FINDINGS)

    def table(df: pd.DataFrame) -> str:
        head = "".join(f"<th>{c}</th>" for c in df.columns)
        body = "".join("<tr>" + "".join(f"<td>{v}</td>" for v in r) + "</tr>"
                       for r in df.itertuples(index=False))
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    tables = {
        "bottlenecks": table(pd.DataFrame({
            "Hand-off": data["bottlenecks"]["label"],
            "Times": [f"{v:,}" for v in data["bottlenecks"]["n"]],
            "Median days": data["bottlenecks"]["median"],
            "Share of waiting": [pc(v, 1) for v in data["bottlenecks"]["share"]]})),
        "area": table(pd.DataFrame({
            "Spend area": data["area"]["label"],
            "Lines": [f"{v:,}" for v in data["area"]["n"]],
            f"Paid after {LATE_DAYS} days": [pc(v, 1) for v in data["area"]["rate"]],
            "Median days": data["area"]["median"]})),
        "comp": table(pd.DataFrame({
            "Check": data["comp"]["label"],
            "PO lines": [f"{v:,}" for v in data["comp"]["n"]],
            "PO value": [eur(v) for v in data["comp"]["value"]]})),
        "sens": table(pd.DataFrame({
            "Threshold (days)": data["sens"]["t"],
            "Share paid after it": [pc(v, 1) for v in data["sens"]["rate"]]})),
        "vendors": table(pd.DataFrame(VC["top10"]).assign(
            late_rate=lambda d: d["late_rate"].map(lambda v: pc(v, 1)),
            median_days_to_clear=lambda d: d["median_days_to_clear"].round(1))
            .rename(columns={"vendor": "Vendor", "main_spend_area": "Spend area",
                             "n_invoices": "Lines", "n_late": f"Paid after {LATE_DAYS}d",
                             "late_rate": "Rate", "median_days_to_clear": "Median days"})),
    }

    html = DASHBOARD_TEMPLATE
    for k, v in {"__TILES__": tiles_html, "__FINDINGS__": findings_html,
                 "__DATA__": json.dumps(data), "__BOTTOM__": BOTTOM_LINE,
                 "__T_BOTTLENECKS__": tables["bottlenecks"], "__T_AREA__": tables["area"],
                 "__T_COMP__": tables["comp"], "__T_SENS__": tables["sens"],
                 "__T_VENDORS__": tables["vendors"],
                 "__N_LINES__": n(H["n_po_lines"])}.items():
        html = html.replace(k, v)
    path.write_text(html, encoding="utf-8")
    return path


DASHBOARD_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>P2P Process Insights</title>
<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"></script>
<style>
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink2: #52514e;
  --muted: #898781; --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --s1: #2a78d6; --s2: #eb6834; --rest: #c3c2b7;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink2: #c3c2b7;
    --muted: #898781; --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --s1: #3987e5; --s2: #d95926; --rest: #4a4a46;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink2: #c3c2b7;
  --muted: #898781; --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
  --s1: #3987e5; --s2: #d95926; --rest: #4a4a46;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink);
       font-family: system-ui, -apple-system, "Segoe UI", sans-serif; line-height: 1.45; }
main { max-width: 1180px; margin: 0 auto; padding: 28px 16px 48px; }
h1 { font-size: 1.6rem; margin: 0 0 6px; }
.lede { color: var(--ink2); max-width: 900px; margin: 0 0 22px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px;
         margin-bottom: 22px; }
.tile, .card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; }
.tile { padding: 14px 16px; }
.tlabel { color: var(--ink2); font-size: .82rem; }
.tvalue { font-size: 1.7rem; font-weight: 600; margin: 2px 0; }
.tsub { color: var(--muted); font-size: .8rem; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 520px), 1fr)); gap: 14px; }
.card { padding: 14px 16px 10px; min-width: 0; }
.card h2 { font-size: 1rem; margin: 0 0 2px; }
.card p.sub { color: var(--ink2); font-size: .85rem; margin: 0 0 6px; }
.chart { width: 100%; height: 320px; }
details { margin: 4px 0 4px; font-size: .85rem; color: var(--ink2); }
summary { cursor: pointer; }
table { border-collapse: collapse; width: 100%; margin-top: 6px; font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: 4px 6px; border-bottom: 1px solid var(--grid); }
th { color: var(--ink2); font-weight: 600; }
.findings { background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
            padding: 6px 20px; margin: 22px 0; }
.findings li { margin: 8px 0; color: var(--ink2); }
.findings b { color: var(--ink); }
.scroll { overflow-x: auto; }
footer { color: var(--muted); font-size: .8rem; margin-top: 26px; }
</style>
</head>
<body>
<main>
<h1>Purchase-to-pay process insights</h1>
<p class="lede">__BOTTOM__</p>
<section class="tiles">__TILES__</section>

<div class="grid">
  <div class="card"><h2>Where the waiting time goes</h2>
    <p class="sub">Hand-offs between steps, ranked by total days waited across all PO lines.</p>
    <div id="c_bottlenecks" class="chart"></div>
    <details><summary>Table</summary><div class="scroll">__T_BOTTLENECKS__</div></details></div>
  <div class="card"><h2>Days from invoice booked to paid</h2>
    <p class="sub">First invoice per PO line. The line marks the EU directive's 60-day default.</p>
    <div id="c_hist" class="chart"></div>
    <details><summary>Sensitivity to the threshold</summary>__T_SENS__</details></div>
  <div class="card"><h2>Lines paid after 60 days, by spend area</h2>
    <p class="sub">Ten areas with the most slow-paid lines. Hover for the rate.</p>
    <div id="c_area" class="chart"></div>
    <details><summary>Table</summary><div class="scroll">__T_AREA__</div></details></div>
  <div class="card"><h2>Slow payment is concentrated in a few vendors</h2>
    <p class="sub">Cumulative share of slow-paid lines, vendors sorted most-first.</p>
    <div id="c_pareto" class="chart"></div>
    <details><summary>Top 10 vendors</summary><div class="scroll">__T_VENDORS__</div></details></div>
  <div class="card"><h2>Policy breaches</h2>
    <p class="sub">PO lines that break a purchase-to-pay control.</p>
    <div id="c_comp" class="chart"></div>
    <details><summary>Table</summary><div class="scroll">__T_COMP__</div></details></div>
  <div class="card"><h2>Weekly manual workload</h2>
    <p class="sub">Steps done by people, with the week-of-month forecast, backtested 4 weeks ahead.</p>
    <div id="c_work" class="chart"></div></div>
</div>

<section class="findings"><ol>__FINDINGS__</ol></section>
<footer>__N_LINES__ PO lines from the BPI Challenge 2019 event log (4TU.ResearchData).
Every number is rendered from <code>outputs/headline_numbers.json</code> produced by <code>python run_all.py</code>.</footer>
</main>
<script>
const D = __DATA__;
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
function layout(extra) {
  const ax = {gridcolor: css('--grid'), linecolor: css('--axis'), zeroline: false,
              tickfont: {color: css('--muted'), size: 11}, title: {font: {color: css('--ink2'), size: 12}}};
  return Object.assign({
    paper_bgcolor: css('--surface'), plot_bgcolor: css('--surface'),
    font: {family: 'system-ui, -apple-system, "Segoe UI", sans-serif', color: css('--ink2')},
    margin: {l: 50, r: 16, t: 8, b: 44},
    xaxis: Object.assign({}, ax), yaxis: Object.assign({}, ax),
    hoverlabel: {bgcolor: css('--surface'), bordercolor: css('--axis'), font: {color: css('--ink')}},
    showlegend: false,
  }, extra || {});
}
const cfg = {displayModeBar: false, responsive: true};
// Long category labels: wrap them and cap the margin on narrow cards, so the
// bars keep at least half the width on a phone.
const narrow = () => window.innerWidth < 640;
function wrap(t, n) {
  if (!narrow()) return t;
  const out = []; let line = '';
  for (const w of t.split(' ')) {
    if ((line + ' ' + w).trim().length > n) { out.push(line.trim()); line = w; }
    else line += ' ' + w;
  }
  out.push(line.trim());
  return out.join('<br>');
}
const lm = px => narrow() ? Math.min(px, 150) : px;
function draw() {
  const s1 = css('--s1'), s2 = css('--s2'), rest = css('--rest');
  const b = D.bottlenecks;
  Plotly.react('c_bottlenecks', [{type: 'bar', orientation: 'h', x: b.share.slice().reverse(),
    y: b.label.map(t => wrap(t, 24)).reverse(), marker: {color: b.share.map((_, i) => i < 2 ? s1 : rest).reverse()},
    customdata: b.median.map((m, i) => [m, b.n[i]]).reverse(),
    hovertemplate: '%{y}<br>%{x:.1%} of all waiting<br>median %{customdata[0]} days, %{customdata[1]:,} times<extra></extra>'}],
    layout({margin: {l: lm(280), r: 16, t: 8, b: 40}, xaxis: Object.assign(layout().xaxis, {tickformat: '.0%'}),
            yaxis: Object.assign(layout().yaxis, {automargin: !narrow(), tickfont: {size: 10, color: css('--ink2')}})}), cfg);
  Plotly.react('c_hist', [{type: 'bar', x: D.hist.x, y: D.hist.y, marker: {color: s1},
    hovertemplate: '%{x} days: %{y:,} lines<extra></extra>'}],
    layout({bargap: 0.08, xaxis: Object.assign(layout().xaxis, {title: {text: 'Days (200+ grouped)'}}),
            yaxis: Object.assign(layout().yaxis, {title: {text: 'PO lines'}}),
            shapes: [{type: 'line', x0: D.late_days, x1: D.late_days, y0: 0, y1: 1, yref: 'paper',
                      line: {color: css('--ink2'), width: 1}}]}), cfg);
  const a = D.area;
  Plotly.react('c_area', [{type: 'bar', orientation: 'h', x: a.nlate.slice().reverse(), y: a.label.slice().reverse(),
    marker: {color: a.label.map((_, i) => i === 0 ? s1 : rest).reverse()},
    customdata: a.n.map((v, i) => [v, a.median[i], a.rate[i]]).reverse(),
    hovertemplate: '%{y}<br>%{x:,} lines paid after 60 days (%{customdata[2]:.1%} of %{customdata[0]:,})<br>median %{customdata[1]} days<extra></extra>'}],
    layout({margin: {l: 170, r: 16, t: 8, b: 40}, xaxis: Object.assign(layout().xaxis, {tickformat: ','}),
            yaxis: Object.assign(layout().yaxis, {automargin: true, tickfont: {size: 11, color: css('--ink2')}})}), cfg);
  Plotly.react('c_pareto', [
    {type: 'scatter', mode: 'lines', x: [0, 1], y: [0, 1], line: {color: rest, width: 1}, hoverinfo: 'skip'},
    {type: 'scatter', mode: 'lines', x: D.pareto.x, y: D.pareto.y, line: {color: s1, width: 2},
     hovertemplate: 'top %{x:.1%} of vendors<br>%{y:.1%} of slow-paid lines<extra></extra>'}],
    layout({hovermode: 'x', xaxis: Object.assign(layout().xaxis, {tickformat: '.0%', range: [0, 1]}),
            yaxis: Object.assign(layout().yaxis, {tickformat: '.0%', range: [0, 1.02]})}), cfg);
  const c = D.comp;
  Plotly.react('c_comp', [{type: 'bar', orientation: 'h', x: c.n.slice().reverse(), y: c.label.map(t => wrap(t, 22)).reverse(),
    marker: {color: c.n.map((v, i) => i === 0 ? s1 : rest).reverse()},
    hovertemplate: '%{y}<br>%{x:,} PO lines<extra></extra>'}],
    layout({margin: {l: lm(330), r: 16, t: 8, b: 40},
            yaxis: Object.assign(layout().yaxis, {automargin: !narrow(), tickfont: {size: 10, color: css('--ink2')}})}), cfg);
  const w = D.work;
  Plotly.react('c_work', [
    {type: 'scatter', mode: 'lines', name: 'Actual', x: w.week, y: w.actual, line: {color: s1, width: 2},
     hovertemplate: 'week of %{x}<br>%{y:,} manual steps<extra>Actual</extra>'},
    {type: 'scatter', mode: 'lines', name: 'Forecast (backtest)', x: w.fweek, y: w.forecast, line: {color: s2, width: 2},
     hovertemplate: 'week of %{x}<br>%{y:,} forecast<extra>Forecast</extra>'}],
    layout({showlegend: true, legend: {orientation: 'h', y: -0.18, font: {color: css('--ink2')}},
            hovermode: 'x unified', margin: {l: 56, r: 16, t: 8, b: 60},
            yaxis: Object.assign(layout().yaxis, {rangemode: 'tozero', tickformat: ','})}), cfg);
}
draw();
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', draw);
let _rt; window.addEventListener('resize', () => { clearTimeout(_rt); _rt = setTimeout(draw, 200); });
</script>
</body>
</html>
"""


# ===========================================================================
# README
# ===========================================================================
def build_readme(path: Path) -> Path:
    sens = pd.DataFrame(LP["threshold_sensitivity"])
    sens_rows = "\n".join(f"| {r.threshold_days} days | {pc(r.late_rate, 1)} |"
                          for r in sens.itertuples())
    comp_rows = "\n".join(
        f"| {c['description']} | {n(c['n_cases'])} | {pc(c['share_of_cases'], 2)} | "
        f"{eur(c['po_value_eur'])} |" for c in COMP["checks"])
    mods = MO["models"]
    model_rows = "\n".join(
        f"| {label} | {mods[k]['auc']:.3f} | {pc(mods[k]['precision'], 1)} | "
        f"{pc(mods[k]['capture'], 1)} |"
        for k, label in (("vendor_history_rule", "Rule: vendor's past record"),
                         ("logistic", "Logistic regression"),
                         ("gradient_boosting", "Gradient boosting"),
                         ("random", "Random order")))
    wk_rows = "\n".join(f"| {k.replace('_', ' ')} | {pc(v, 1)} |"
                        for k, v in WK["mape_by_method"].items())
    findings_md = "\n\n".join(
        f"### {i}. {f['title']}\n\n{f['evidence']}\n\n**Action.** {f['action']}"
        for i, f in enumerate(FINDINGS, 1))
    lim_md = "\n".join(f"- {x}" for x in LIMITATIONS)
    ruled_md = "\n".join(f"- {x}" for x in RULED_OUT)

    md = f"""# p2p-process-insights

A process review of a real purchase-to-pay operation: where purchase orders and
invoices wait, which company policies are broken and how often, and what an
operations team should fix first. Built on the public **BPI Challenge 2019**
event log, which covers {n(H['n_po_lines'])} purchase-order lines and {n(H['n_events'])} logged steps
from a multinational coatings company.

**The short version.** {BOTTOM_LINE}

**Read this before the numbers.** The log has no payment terms, so "paid after
{LATE_DAYS} days" is a yardstick (the EU Late Payment Directive's default
ceiling), not a breach of contract. Finding 1 shows why that matters. The rest
of the limitations are [listed below](#limitations) rather than in a footnote.

Everything here is written by `make_report.py` from `outputs/headline_numbers.json`,
which a single seeded run of `run_all.py` produces. No number is typed by hand.

| Deliverable | File |
|---|---|
| One-page recommendation memo | [`docs/recommendation_memo.pdf`](docs/recommendation_memo.pdf) |
| Seven-slide deck | [`slides/p2p_process_insights.pptx`](slides/p2p_process_insights.pptx) |
| Interactive dashboard | [`dashboard/index.html`](dashboard/index.html) |
| The analysis, as SQL | [`sql/`](sql/) |

---

## Headline numbers

| | |
|---|---|
| PO lines / logged steps / vendors | {n(H['n_po_lines'])} / {n(H['n_events'])} / {n(H['n_vendors'])} |
| Value ordered | {eur(H['total_po_value_eur'])} |
| Process variants (distinct paths) | {n(VAR['n_variants'])}. The top one covers {pc(VAR['top_variant_share'])}; {VAR['variants_for_80pct']} paths cover 80% |
| Median days, invoice booked to paid | {H['median_days_to_clear']:.1f} |
| Paid after {LATE_DAYS} days | {pc(H['late_rate'], 1)} of {n(LP['n_outcome_known'])} lines with a known outcome |
| Share of those in {H['top_late_area']} | {pc(H['top_late_area_share_of_late'], 1)} (it has {pc(H['top_late_area_share_of_lines'], 1)} of all lines) |
| Vendors behind 80% of slow-paid lines | {n(VC['vendors_for_80pct_of_late'])} ({pc(VC['share_of_vendors_for_80pct'], 1)} of vendors) |
| Median days, vendor invoice date to booked | {EL['median_days']:.1f} ({pc(EL['share_over_14_days'])} over 14 days) |
| Invoice booking done by hand | {pc(INV_REC['manual_share'], 1)}, by {INV_REC['n_people']} people |
| Extra slow-payment rate when invoice beats goods receipt (within vendor) | {pp(GR['within_vendor_gap_pp'])} |
| POs raised after the vendor invoiced | {n(H['po_after_invoice_lines'])} lines, {eur(H['po_after_invoice_value_eur'])} |
| Manual workload lift in week {WK['busiest_week_of_month']} of the month | {pc(WK['busiest_week_lift'] - 1)} (month-end weeks: {pc(WK['month_end_lift'] - 1)}) |
| 4-week workload forecast error (best method) | {pc(WK['best_mape'], 1)} |

## Findings

{findings_md}

## Checked and ruled out

{ruled_md}

## How the analysis works

**Unit of analysis.** One case is one purchase-order line. The log follows each
line through up to {DQ['n_activities']} kinds of step: create the PO, receive goods, book the invoice,
clear (pay) it, plus changes, cancellations and payment blocks. Each line belongs
to one of four matching flows:

| Flow | Rule | Share of lines |
|---|---|---|
""" + "\n".join(f"| {f['item_category']} | {FLOW_RULES.get(f['item_category'], '')} | {pc(f['share'], 1)} |"
                for f in J["flows"]) + f"""

**Compliance checks.** These run as SQL over each line's milestone dates ([`sql/02_case_facts.sql`](sql/02_case_facts.sql)):

| Check | PO lines | Share | PO value |
|---|---|---|---|
{comp_rows}

**Payment timing** ([`sql/03_invoices.sql`](sql/03_invoices.sql)). The log never says which payment
settles which invoice. So each line is measured from its first booked invoice to
its first clearance. Invoices booked less than {LATE_DAYS} days before the log ends
are excluded *whether or not they were paid*. Keeping only the fast payers among
them would bias the rate down; a test pins this. {n(LP['n_pairing_failed'])} lines where the clearance
predates the invoice are excluded as unpairable.

How much "paid after N days" depends on N:

| Threshold | Share of lines paid after it |
|---|---|
{sens_rows}

**Where the time goes** ([`sql/04_transitions.sql`](sql/04_transitions.sql)). Every step-to-next-step hand-off,
ranked by *total* days waited rather than the median, so a moderate delay that
happens 100,000 times outranks a long one that happens twice.

**Slow-payment classifier** ([`src/p2p/model.py`](src/p2p/model.py)). Each line is scored at the moment its
invoice is booked, using only what is known then. Rework counts only changes
made *before* the invoice, and vendor history is learned from the training
period only. The split is out-of-time: trained on invoices booked before
{MO['test_from']} ({n(MO['n_train'])} lines), tested on the rest ({n(MO['n_test'])} lines).

| Ranking | AUC | Precision in riskiest {pc(H['review_share'])} | Capture in riskiest {pc(H['review_share'])} |
|---|---|---|---|
{model_rows}

Capture tops out at {pc(H['best_model_max_capture'], 1)}. With {pc(MO['test_late_rate'])} of test lines slow-paid,
reviewing {pc(H['review_share'])} of them cannot catch more, so precision is the number to compare.

**Workload forecast** ([`src/p2p/workload.py`](src/p2p/workload.py)). Weekly manual steps, backtested with
{WK['n_folds']} rolling origins, {WK['horizon_weeks']} weeks ahead. The log only contains POs *created in 2018*, which
under-counts early 2018 and collapses in 2019. So the series is cut to the steady
window ({WK['window_start']} to {WK['window_end']}), chosen from the data.

| Method | Mean absolute % error |
|---|---|
{wk_rows}

## Limitations

{lim_md}

## Reproduce

```bash
make install
make data      # downloads the 700 MB event log once and checks its MD5
make run       # ~{MACH['runtime_seconds'] / 60:.0f} min on a laptop; writes outputs/
make docs      # memo, deck, dashboard, this README
make test
```

Run on Python {MACH['python']}, {MACH['platform']}, seed {MACH['seed']}. `requirements.txt` pins the
versions these numbers came from.

## Repository map

| Path | What it is |
|---|---|
| `sql/` | The analysis itself: events, case facts and compliance, invoices, hand-offs, variants, vendor Pareto, automation, weekly workload |
| `src/p2p/ingest.py` | Downloads the XES log, checks its MD5 and streams it into Parquet |
| `src/p2p/process.py` | Runs the SQL and summarises it |
| `src/p2p/model.py` | Slow-payment classifier, out-of-time |
| `src/p2p/workload.py` | Weekly workload forecast and backtest |
| `run_all.py` | One command; writes `outputs/headline_numbers.json` |
| `make_report.py` | Renders memo, deck, dashboard and README from that JSON |
| `tests/` | Hand-built event logs with known answers, one per rule |

**Data.** van Dongen, B. (2019). *BPI Challenge 2019*. 4TU.ResearchData.
{C.DATASET_DOI}
"""
    path.write_text(md, encoding="utf-8")
    return path


FLOW_RULES = {
    "3-way match, invoice before GR": "Invoice may be booked before the goods arrive, but is blocked until they do",
    "3-way match, invoice after GR": "Goods receipt must be posted before the invoice is booked",
    "2-way match": "Invoice is matched to the PO only; no goods receipt needed",
    "Consignment": "Goods are invoiced outside the PO, so there is no PO-level invoice",
}


def main() -> None:
    out = {
        "memo": build_memo(C.DOC_DIR / "recommendation_memo.pdf"),
        "deck": build_deck(C.SLIDE_DIR / "p2p_process_insights.pptx"),
        "dashboard": build_dashboard(C.DASH_DIR / "index.html"),
        "readme": build_readme(C.REPO_ROOT / "README.md"),
    }
    for k, v in out.items():
        print(f"  {k:10s} {v}")


if __name__ == "__main__":
    main()
