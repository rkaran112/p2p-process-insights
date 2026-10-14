"""Run the whole analysis end to end.

    python run_all.py

Everything the README, memo, deck and dashboard quote is produced here and
written to outputs/.  Seeded once, at the top, so the numbers reproduce.
"""
from __future__ import annotations

import json
import os
import platform
import random
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from p2p import config as C        # noqa: E402
from p2p import ingest             # noqa: E402
from p2p import model as M          # noqa: E402
from p2p import plots as P          # noqa: E402
from p2p import process as PR       # noqa: E402
from p2p import workload as W       # noqa: E402

random.seed(C.SEED)
np.random.seed(C.SEED)


def machine_info(runtime_s: float) -> dict:
    info = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "cpu_count_logical": os.cpu_count(),
        "runtime_seconds": round(runtime_s, 1),
        "seed": C.SEED,
    }
    for mod in ("numpy", "pandas", "duckdb", "sklearn", "statsmodels", "matplotlib"):
        try:
            info[f"pkg_{mod}"] = __import__(mod).__version__
        except Exception:
            info[f"pkg_{mod}"] = "n/a"
    return info


def _csv(df: pd.DataFrame, name: str) -> str:
    path = C.TAB_DIR / name
    df.to_csv(path, index=False)
    return str(path)


def main() -> dict:
    t0 = time.time()
    out: dict = {}
    print("=" * 78)
    print("Purchase-to-pay process insights - full pipeline")
    print("=" * 78)

    # ---------------------------------------------------------------- data
    print("\n[1/7] Data")
    out["data_source"] = ingest.ensure_parquet()
    con = PR.build()
    dq = PR.data_quality(con)
    out["data_quality"] = dq
    print(f"  {dq['n_cases']:,} PO lines | {dq['n_events']:,} events | "
          f"{dq['n_invalid_ts']} events with impossible dates set aside")

    cf = PR.table(con, "case_facts")
    inv = PR.table(con, "invoices")
    tr = PR.table(con, "transitions")
    var = PR.table(con, "variants")
    vp = PR.table(con, "vendor_pareto")
    auto = PR.table(con, "automation")
    weekly = PR.table(con, "weekly_workload")

    # ------------------------------------------------------------ the flow
    print("\n[2/7] Process flow")
    fl = PR.flows(cf)
    out["flows"] = fl.to_dict("records")
    top = var.iloc[0]
    out["variants"] = {
        "n_variants": int(len(var)),
        "top_variant": top["variant"],
        "top_variant_share": float(top["share"]),
        "variants_for_80pct": int((var["cumulative_share"] < 0.8).sum() + 1),
        "share_in_top_10": float(var["cumulative_share"].iloc[min(9, len(var) - 1)]),
        "singleton_variants": int((var["n_cases"] == 1).sum()),
    }
    print(f"  {len(var):,} variants; top one covers {top['share']:.1%}; "
          f"{out['variants']['variants_for_80pct']:,} needed for 80% of lines")
    _csv(fl, "flows.csv")
    _csv(var.head(50), "top_variants.csv")
    _csv(tr, "transitions.csv")

    # ---------------------------------------------------------- compliance
    print("\n[3/7] Compliance")
    comp, by_flow = PR.compliance(cf)
    out["compliance"] = comp
    for c in comp["checks"]:
        print(f"  {c['description']:62s} {c['n_cases']:>7,} ({c['share_of_cases']:.2%})")
    _csv(pd.DataFrame(comp["checks"]), "compliance_checks.csv")
    _csv(by_flow, "compliance_by_flow.csv")

    # -------------------------------------------------------- late payment
    print("\n[4/7] Payment timeliness")
    lp = PR.late_payment(inv)
    out["late_payment"] = lp
    out["block_decomposition"] = PR.block_decomposition(cf)
    out["goods_receipt_effect"] = PR.gr_effect(inv)
    out["rework"] = PR.rework(cf, inv)
    out["entry_lag"] = PR.entry_lag(cf)
    out["po_after_invoice"] = PR.po_after_invoice_breakdown(cf)
    el = out["entry_lag"]
    print(f"  invoice entry lag: median {el['median_days']:.1f} days, "
          f"{el['share_over_14_days']:.0%} over 14 days")
    by_area = PR.late_by(inv, "spend_area")
    by_doc = PR.late_by(inv, "document_type")
    by_flow_late = PR.late_by(inv, "item_category")
    k80 = vp.index[vp["cumulative_share_of_late"] >= 0.8][0]
    out["vendor_concentration"] = {
        "n_vendors_with_known_outcomes": int(len(vp)),
        "vendors_for_80pct_of_late": int(k80 + 1),
        "share_of_vendors_for_80pct": float(vp.loc[k80, "share_of_vendors"]),
        "top10_share_of_late": float(vp["cumulative_share_of_late"].iloc[min(9, len(vp) - 1)]),
        "top10": vp.head(10)[["vendor", "main_spend_area", "n_invoices", "n_late",
                              "late_rate", "median_days_to_clear"]].to_dict("records"),
    }
    print(f"  {lp['n_outcome_known']:,} lines with a known outcome; "
          f"{lp['late_rate']:.1%} paid after {C.POLICY.late_days} days "
          f"(median {lp['median_days_to_clear']:.0f} days)")
    bd = out["block_decomposition"]
    print(f"  blocked invoices: {bd['held_share_of_total_wait']:.0%} of the wait is "
          f"before the block is lifted")
    _csv(by_area, "late_by_spend_area.csv")
    _csv(by_doc, "late_by_document_type.csv")
    _csv(by_flow_late, "late_by_flow.csv")
    _csv(vp, "vendor_pareto.csv")
    _csv(pd.DataFrame(lp["threshold_sensitivity"]), "late_threshold_sensitivity.csv")
    ok = inv.loc[inv["pairing_ok"] & inv["days_to_clear"].notna(), "days_to_clear"]
    hist, edges = np.histogram(ok.clip(upper=200), bins=np.arange(0, 205, 5))
    _csv(pd.DataFrame({"days_from": edges[:-1], "days_to": edges[1:], "n_lines": hist}),
         "days_to_clear_hist.csv")

    # ---------------------------------------------------------- automation
    out["automation"] = PR.automation_summary(auto, len(cf))
    _csv(auto, "automation.csv")

    # --------------------------------------------------------------- model
    print("\n[5/7] Slow-payment classifier (out-of-time)")
    mr = M.run(inv)
    out["model"] = mr["summary"]
    for k, v in mr["summary"]["models"].items():
        print(f"  {k:22s} AUC {v['auc']:.3f}  capture@{v['review_share']:.0%} "
              f"{v['capture']:.1%}  precision {v['precision']:.1%}")
    out["model"]["top_drivers"] = mr["drivers"].head(10)[
        ["label", "odds_ratio"]].to_dict("records")
    out["model"]["permutation_importance"] = mr["importance"].head(8)[
        ["label", "auc_drop"]].to_dict("records")
    _csv(mr["drivers"], "model_logistic_drivers.csv")
    _csv(mr["importance"], "model_gbm_importance.csv")
    _csv(pd.concat([g.assign(model=k) for k, g in mr["gains"].items()]), "model_gains.csv")

    # ------------------------------------------------------------ workload
    print("\n[6/7] Workload forecast")
    wr = W.run(weekly)
    out["workload"] = wr["summary"]
    ws = wr["summary"]
    print(f"  window {ws['window_start']} .. {ws['window_end']} ({ws['n_weeks']} weeks); "
          f"best {ws['best_method']} MAPE {ws['best_mape']:.1%}")
    _csv(weekly, "weekly_workload.csv")
    _csv(wr["folds"], "workload_backtest_folds.csv")
    _csv(wr["window"], "workload_window.csv")
    _csv(wr["backtest_predictions"], "workload_backtest_predictions.csv")

    # ------------------------------------------------------------- figures
    print("\n[7/7] Figures")
    gr = out["goods_receipt_effect"]
    rw = out["rework"]
    figs = {
        "flows": P.flows(fl),
        "compliance": P.compliance(comp["checks"]),
        "days_to_clear": P.days_to_clear(inv),
        "bottlenecks": P.bottlenecks(tr),
        "vendor_pareto": P.vendor_pareto(vp),
        "goods_receipt_effect": P.two_group(
            "Invoices that arrive before the goods",
            ("Goods first", "Invoice first"),
            (gr["late_rate_goods_before_invoice"], gr["late_rate_invoice_before_goods"]),
            "goods_receipt_effect"),
        "rework_effect": P.two_group(
            "Lines that needed rework",
            ("No rework", "Rework"),
            (rw["late_rate_without_rework"], rw["late_rate_with_rework"]),
            "rework_effect"),
        "gains": P.gains(mr["gains"], C.MODEL.review_share),
        "workload": P.workload(wr["window"], wr["backtest_predictions"],
                               ws["best_method"]),
    }
    out["figures"] = {k: Path(v).name for k, v in figs.items()}
    for k, v in figs.items():
        print(f"  {k:22s} {Path(v).name}")

    # ------------------------------------------------------------ headline
    best = mr["summary"]["models"][mr["summary"]["best_model"]]
    rule = mr["summary"]["models"]["vendor_history_rule"]
    out["headline"] = {
        "n_po_lines": dq["n_cases"],
        "n_events": dq["n_events"],
        "n_vendors": dq["n_vendors"],
        "total_po_value_eur": float(cf["po_value_eur"].sum()),
        "late_days": C.POLICY.late_days,
        "late_rate": lp["late_rate"],
        "n_late": lp["n_late"],
        "median_days_to_clear": lp["median_days_to_clear"],
        "late_value_eur": lp["late_value_eur"],
        "exposure_upper_bound_eur": lp["total_exposure_eur"],
        # late_by() sorts by number of slow-paid lines, so row 0 is the area
        # that contributes most of them.
        "top_late_area": by_area.loc[0, "spend_area"],
        "top_late_area_share_of_late": float(by_area.loc[0, "share_of_all_late"]),
        "top_late_area_share_of_lines": float(
            by_area.loc[0, "n_invoices"] / lp["n_outcome_known"]),
        "top_late_area_median_days": float(by_area.loc[0, "median_days_to_clear"]),
        "top_late_area_late_rate": float(by_area.loc[0, "late_rate"]),
        "entry_lag_median_days": out["entry_lag"]["median_days"],
        "entry_lag_share_over_14": out["entry_lag"]["share_over_14_days"],
        "invoice_recording_manual_share": next(
            c["manual_share"] for c in out["automation"]["candidates"]
            if c["activity"] == "Record Invoice Receipt"),
        "invoice_recording_people": next(
            c["n_people"] for c in out["automation"]["candidates"]
            if c["activity"] == "Record Invoice Receipt"),
        "gr_gap_raw_pp": gr["raw_gap_pp"],
        "gr_gap_within_vendor_pp": gr["within_vendor_gap_pp"],
        "best_model_max_capture": best["max_possible_capture"],
        "rule_auc": rule["auc"],
        "held_share_of_blocked_wait": bd["held_share_of_total_wait"],
        "median_days_held": bd["median_days_held"],
        "median_days_after_release": bd["median_days_after_release"],
        "po_after_invoice_lines": next(c["n_cases"] for c in comp["checks"]
                                       if c["check"] == "po_after_invoice"),
        "po_after_invoice_share": next(c["share_of_cases"] for c in comp["checks"]
                                       if c["check"] == "po_after_invoice"),
        "po_after_invoice_value_eur": next(c["po_value_eur"] for c in comp["checks"]
                                           if c["check"] == "po_after_invoice"),
        "any_violation_share": comp["any_violation_share"],
        "vendors_for_80pct_of_late": out["vendor_concentration"]["vendors_for_80pct_of_late"],
        "share_of_vendors_for_80pct": out["vendor_concentration"]["share_of_vendors_for_80pct"],
        "late_rate_invoice_before_goods": gr["late_rate_invoice_before_goods"],
        "late_rate_goods_before_invoice": gr["late_rate_goods_before_invoice"],
        "late_rate_with_rework": rw["late_rate_with_rework"],
        "late_rate_without_rework": rw["late_rate_without_rework"],
        "best_model": mr["summary"]["best_model"],
        "best_model_auc": best["auc"],
        "best_model_capture": best["capture"],
        "best_model_precision": best["precision"],
        "rule_capture": rule["capture"],
        "review_share": C.MODEL.review_share,
        "manual_events_per_line": out["automation"]["manual_events_per_case"],
        "workload_mape": ws["best_mape"],
        "workload_method": ws["best_method"],
        "n_variants": out["variants"]["n_variants"],
        "top_variant_share": out["variants"]["top_variant_share"],
    }
    out["machine"] = machine_info(time.time() - t0)

    path = C.OUT_DIR / "headline_numbers.json"
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {path}")
    print(f"Total runtime: {out['machine']['runtime_seconds']}s")
    return out


if __name__ == "__main__":
    main()
