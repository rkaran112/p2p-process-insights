"""Run the SQL layer and turn its tables into the process findings.

The SQL in `sql/` builds every table; this module only fills in the
parameters, runs the files in order, and summarises what they produced.
Keeping the logic in SQL means each number can be checked by running a
query, not by reading Python.
"""
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from . import config as C

SQL_FILES = (
    "01_events.sql",
    "02_case_facts.sql",
    "03_invoices.sql",
    "04_transitions.sql",
    "05_variants.sql",
    "06_vendor_pareto.sql",
    "07_automation.sql",
    "08_weekly_workload.sql",
)


def sql_params(events_parquet=None, cases_parquet=None,
               late_days: int | None = None) -> dict:
    return {
        "events_parquet": str(events_parquet or C.EVENTS_PARQUET).replace("\\", "/"),
        "cases_parquet": str(cases_parquet or C.CASES_PARQUET).replace("\\", "/"),
        "valid_ts_start": C.POLICY.valid_ts_start,
        "valid_ts_end": C.POLICY.valid_ts_end,
        "vendor_user": C.POLICY.vendor_user,
        "batch_prefix": C.POLICY.batch_user_prefix,
        "late_days": int(late_days if late_days is not None else C.POLICY.late_days),
        "rework_list": ", ".join(f"'{a}'" for a in C.REWORK_ACTIVITIES),
    }


def build(con: duckdb.DuckDBPyConnection | None = None, **params) -> duckdb.DuckDBPyConnection:
    """Run every SQL file in order against one connection and return it."""
    con = con or duckdb.connect()
    p = sql_params(**params)
    for name in SQL_FILES:
        con.execute((C.SQL_DIR / name).read_text(encoding="utf-8").format(**p))
    return con


def table(con, name: str) -> pd.DataFrame:
    return con.execute(f"SELECT * FROM {name}").df()


# --------------------------------------------------------------------------
# Data quality
# --------------------------------------------------------------------------
def data_quality(con) -> dict:
    q = con.execute("""
        SELECT count(*) AS n_events,
               count(*) FILTER (WHERE NOT ts_valid) AS n_invalid_ts,
               min(ts) AS min_ts, max(ts) AS max_ts,
               min(ts) FILTER (WHERE ts_valid) AS min_valid_ts,
               max(ts) FILTER (WHERE ts_valid) AS max_valid_ts,
               count(DISTINCT case_id) AS n_cases,
               count(DISTINCT activity) AS n_activities,
               count(DISTINCT "user") FILTER (WHERE actor_type = 'human') AS n_people,
               count(DISTINCT "user") FILTER (WHERE actor_type = 'batch') AS n_batch_users
        FROM events
    """).df().iloc[0].to_dict()
    cases_with_invalid = con.execute(
        "SELECT count(DISTINCT case_id) FROM events WHERE NOT ts_valid").fetchone()[0]
    out = {k: (str(v) if isinstance(v, pd.Timestamp) else
               int(v) if isinstance(v, (np.integer, int)) else v) for k, v in q.items()}
    out["n_cases_with_invalid_ts"] = int(cases_with_invalid)
    out["n_documents"] = int(con.execute(
        "SELECT count(DISTINCT po_document) FROM cases").fetchone()[0])
    out["n_vendors"] = int(con.execute(
        "SELECT count(DISTINCT vendor) FROM cases").fetchone()[0])
    return out


# --------------------------------------------------------------------------
# Compliance
# --------------------------------------------------------------------------
CHECKS = {
    "po_after_invoice": "Vendor invoiced before the PO existed",
    "paid_before_goods_receipt": "Paid before goods were received (3-way match)",
    "invoice_before_gr_in_after_gr_flow": "Invoice booked before goods receipt (invoice-after-GR flow)",
    "invoice_on_consignment": "PO-level invoice on a consignment line",
}


def compliance(cf: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    """Violation counts and the value they touch, overall and by flow."""
    rows = []
    for col, label in CHECKS.items():
        hit = cf[col].fillna(False).astype(bool)
        rows.append({
            "check": col, "description": label,
            "n_cases": int(hit.sum()), "share_of_cases": float(hit.mean()),
            "po_value_eur": float(cf.loc[hit, "po_value_eur"].sum()),
        })
    tbl = pd.DataFrame(rows)
    by_flow = (cf.groupby("item_category")
                 .agg(n_cases=("case_id", "size"),
                      **{f"{c}_rate": (c, "mean") for c in CHECKS},
                      any_violation_rate=("any_violation", "mean"))
                 .reset_index())
    summary = {
        "n_cases": int(len(cf)),
        "any_violation_cases": int(cf["any_violation"].sum()),
        "any_violation_share": float(cf["any_violation"].mean()),
        "any_violation_value_eur": float(cf.loc[cf["any_violation"], "po_value_eur"].sum()),
        "checks": tbl.to_dict("records"),
        "by_flow": by_flow.to_dict("records"),
    }
    return summary, by_flow


def flows(cf: pd.DataFrame) -> pd.DataFrame:
    return (cf.groupby("item_category")
              .agg(n_cases=("case_id", "size"),
                   po_value_eur=("po_value_eur", "sum"),
                   median_events=("n_events", "median"),
                   rework_rate=("n_rework", lambda s: float((s > 0).mean())))
              .assign(share=lambda d: d["n_cases"] / d["n_cases"].sum())
              .sort_values("n_cases", ascending=False)
              .reset_index())


# --------------------------------------------------------------------------
# Late payment
# --------------------------------------------------------------------------
def late_flag(inv: pd.DataFrame, days: float) -> pd.Series:
    """Late at an arbitrary threshold, censoring-aware.  Mirrors 03_invoices.sql."""
    observable = (inv["days_observable"] > days) & inv["pairing_ok"].astype(bool)
    cleared = inv["cleared_at"].notna()
    out = pd.Series(np.nan, index=inv.index, dtype="float")
    m = observable & cleared
    out[m] = (inv.loc[m, "days_to_clear"] > days).astype(float)
    out[observable & ~cleared] = 1.0
    return out


def late_payment(inv: pd.DataFrame) -> dict:
    P = C.POLICY
    known = inv[inv["is_late"].notna()]
    late = known[known["is_late"].astype(bool)]
    # Exposure under the Directive: interest on each late invoice for the days
    # past the threshold, plus the fixed compensation.  Still-open invoices are
    # charged up to the end of the log only, so this is a floor.
    days_over = (late["days_to_clear"].fillna(late["days_open"]) - P.late_days).clip(lower=0)
    val = late["invoice_value_eur"].clip(lower=0)
    interest = float((val * P.statutory_interest_rate * days_over / 365).sum())
    fixed = float(len(late) * P.fixed_compensation_eur)

    sens = []
    for d in P.late_days_sensitivity:
        f = late_flag(inv, d)
        sens.append({"threshold_days": d, "late_rate": float(f.mean()),
                     "n_late": int(f.sum()), "n_known": int(f.notna().sum())})
    return {
        "n_invoices": int(len(inv)),
        "n_outcome_known": int(len(known)),
        "n_censored": int((inv["is_late"].isna() & inv["pairing_ok"]).sum()),
        "n_pairing_failed": int((~inv["pairing_ok"].astype(bool)).sum()),
        "n_still_open": int(inv["still_open"].sum()),
        "n_late": int(len(late)),
        "late_rate": float(known["is_late"].astype(float).mean()),
        "late_value_eur": float(val.sum()),
        "median_days_to_clear": float(inv.loc[inv["pairing_ok"], "days_to_clear"].median()),
        "p90_days_to_clear": float(inv.loc[inv["pairing_ok"], "days_to_clear"].quantile(0.9)),
        "statutory_interest_exposure_eur": interest,
        "fixed_compensation_exposure_eur": fixed,
        "total_exposure_eur": interest + fixed,
        "threshold_sensitivity": sens,
    }


def late_by(inv: pd.DataFrame, col: str, min_n: int = 200) -> pd.DataFrame:
    known = inv[inv["is_late"].notna()].copy()
    known["late"] = known["is_late"].astype(float)
    g = (known.groupby(col, dropna=False)
              .agg(n_invoices=("late", "size"), late_rate=("late", "mean"),
                   n_late=("late", "sum"),
                   median_days_to_clear=("days_to_clear", "median"))
              .reset_index())
    g = g[g["n_invoices"] >= min_n].sort_values("n_late", ascending=False)
    g["share_of_all_late"] = g["n_late"] / known["late"].sum()
    return g.reset_index(drop=True)


def gr_effect(inv: pd.DataFrame) -> dict:
    """Late rate for 3-way invoices that arrived before vs after the goods.

    The raw gap could just be vendor mix (slow-paid vendors may also ship
    late), so it is recomputed *within* each vendor that has both kinds of
    invoice, weighted by its invoice-first volume.
    """
    k = inv[inv["is_late"].notna() & inv["item_category"].str.startswith("3-way")].copy()
    k["late"] = k["is_late"].astype(float)
    g = k.groupby("gr_before_invoice")["late"].agg(["mean", "size"])
    before = g.loc[False] if False in g.index else None
    after = g.loc[True] if True in g.index else None

    pv = k.pivot_table(index="vendor", columns="gr_before_invoice", values="late",
                       aggfunc=["mean", "size"])
    both = pv.dropna()
    adj = None
    n_vendors = 0
    if len(both) and False in both[("mean",)].columns:
        diff = both[("mean", False)] - both[("mean", True)]
        w = both[("size", False)]
        adj = float((diff * w).sum() / w.sum())
        n_vendors = int(len(both))
    return {
        "late_rate_invoice_before_goods": None if before is None else float(before["mean"]),
        "n_invoice_before_goods": None if before is None else int(before["size"]),
        "late_rate_goods_before_invoice": None if after is None else float(after["mean"]),
        "n_goods_before_invoice": None if after is None else int(after["size"]),
        "raw_gap_pp": None if before is None or after is None
                      else float(100 * (before["mean"] - after["mean"])),
        "within_vendor_gap_pp": None if adj is None else 100 * adj,
        "n_vendors_with_both": n_vendors,
    }


def entry_lag(cf: pd.DataFrame) -> dict:
    """Days from the vendor's invoice date to the invoice being booked.

    Every day here is a day the payment clock is running (the EU directive
    counts from receipt) while the invoice sits in a pile, invisible to the
    payment run.  Lines where the booking predates the invoice date are
    excluded as a dating artefact.
    """
    d = cf.dropna(subset=["first_vendor_invoice", "first_invoice_receipt"])
    lag = (d["first_invoice_receipt"] - d["first_vendor_invoice"]).dt.total_seconds() / 86400
    ok = lag >= 0
    lag = lag[ok]
    by_area = (pd.DataFrame({"spend_area": d.loc[ok, "spend_area"], "lag": lag})
                 .groupby("spend_area")["lag"].agg(["median", "size"])
                 .query("size >= 1000").sort_values("median", ascending=False))
    return {
        "n_lines": int(len(lag)),
        "n_negative_excluded": int((~ok).sum()),
        "median_days": float(lag.median()),
        "p90_days": float(lag.quantile(0.9)),
        "share_over_14_days": float((lag > 14).mean()),
        "share_over_30_days": float((lag > 30).mean()),
        "slowest_areas": [{"spend_area": a, "median_days": float(r["median"]),
                           "n_lines": int(r["size"])} for a, r in by_area.head(5).iterrows()],
    }


def po_after_invoice_breakdown(cf: pd.DataFrame) -> dict:
    """Where retroactive POs come from: a few buyers' habit, or everywhere?"""
    hit = cf[cf["po_after_invoice"]]
    by_area = (cf.groupby("spend_area", dropna=False)
                 .agg(n_lines=("case_id", "size"), n_hits=("po_after_invoice", "sum"))
                 .assign(rate=lambda d: d["n_hits"] / d["n_lines"],
                         share_of_hits=lambda d: d["n_hits"] / max(len(hit), 1))
                 .sort_values("n_hits", ascending=False).reset_index())
    v = hit["vendor"].value_counts()
    cum = v.cumsum() / v.sum()
    days_early = (hit["po_created"] - hit["first_vendor_invoice"]).dt.total_seconds() / 86400
    return {
        "n_lines": int(len(hit)),
        "n_vendors": int(v.size),
        "vendors_for_80pct": int((cum < 0.8).sum() + 1),
        "median_days_po_after_invoice": float(days_early.median()),
        "by_spend_area": by_area.head(6).to_dict("records"),
    }


def block_decomposition(cf: pd.DataFrame) -> dict:
    """Split the wait on blocked invoices into 'held' and 'released' time.

    An invoice that arrives before its goods is booked with a payment block.
    Time from recording to block removal is the company's own process (the
    goods receipt has not been posted); time from removal to payment is the
    payment run working to terms.  Only the first part is a process defect.
    """
    d = cf.dropna(subset=["first_invoice_receipt", "first_block_removed",
                          "first_clear_invoice"])
    d = d[(d["first_invoice_receipt"] <= d["first_block_removed"])
          & (d["first_block_removed"] <= d["first_clear_invoice"])]
    held = (d["first_block_removed"] - d["first_invoice_receipt"]).dt.total_seconds() / 86400
    released = (d["first_clear_invoice"] - d["first_block_removed"]).dt.total_seconds() / 86400
    gr_late = (d["first_goods_receipt"].isna()
               | (d["first_goods_receipt"] > d["first_invoice_receipt"]))
    return {
        "n_lines": int(len(d)),
        "share_of_invoiced_lines": float(len(d) / cf["first_invoice_receipt"].notna().sum()),
        "median_days_held": float(held.median()),
        "median_days_after_release": float(released.median()),
        "held_share_of_total_wait": float(held.sum() / (held.sum() + released.sum())),
        "share_where_goods_arrived_after_invoice": float(gr_late.mean()),
        "held_days_total": float(held.sum()),
    }


# --------------------------------------------------------------------------
# Rework
# --------------------------------------------------------------------------
def rework(cf: pd.DataFrame, inv: pd.DataFrame) -> dict:
    """Do PO lines that needed rework take longer to pay?"""
    k = inv[inv["is_late"].notna()].merge(
        cf[["case_id", "n_rework", "n_price_changes"]], on="case_id")
    k["late"] = k["is_late"].astype(float)
    k["had_rework"] = k["n_rework"] > 0
    g = k.groupby("had_rework").agg(late_rate=("late", "mean"),
                                    median_days=("days_to_clear", "median"),
                                    n=("late", "size"))
    by_flow = (k.groupby(["item_category", "had_rework"])["late"].mean()
                .unstack().rename(columns={False: "no_rework", True: "rework"}))
    return {
        "share_cases_with_rework": float((cf["n_rework"] > 0).mean()),
        "rework_events": int(cf["n_rework"].sum()),
        "late_rate_with_rework": float(g.loc[True, "late_rate"]),
        "late_rate_without_rework": float(g.loc[False, "late_rate"]),
        "median_days_with_rework": float(g.loc[True, "median_days"]),
        "median_days_without_rework": float(g.loc[False, "median_days"]),
        "late_rate_by_flow": {i: {kk: (None if pd.isna(vv) else float(vv))
                                  for kk, vv in r.items()}
                              for i, r in by_flow.iterrows()},
    }


def automation_summary(auto: pd.DataFrame, n_cases: int) -> dict:
    """Steps that are mostly manual but that the batch jobs demonstrably do."""
    a = auto.copy()
    cand = a[(a["n_batch"] > 0) & (a["n_manual"] >= 1000)].sort_values(
        "n_manual", ascending=False)
    total_manual = int(a["n_manual"].sum())
    return {
        "manual_events": total_manual,
        "manual_share_of_company_events": float(
            total_manual / (a["n_manual"].sum() + a["n_batch"].sum())),
        "manual_events_per_case": float(total_manual / n_cases),
        "candidates": cand[["activity", "n_manual", "n_batch", "manual_share",
                            "n_people"]].head(8).to_dict("records"),
    }
